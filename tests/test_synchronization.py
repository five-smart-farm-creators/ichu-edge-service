import os
import tempfile
import unittest
import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import requests

from monitoring.application.errors import (
    CloudBatchRejected,
    CloudCredentialsRejected,
    CloudUnavailable,
    SynchronizationDisabled,
)
from monitoring.application.services import (
    MAX_SYNC_ATTEMPTS,
    CloudEntryResult,
    RuleRefreshResult,
    SynchronizationService,
    SynchronizationResult,
    SyncResult,
)
from monitoring.domain.entities import MonitoringAlert, MonitoringRuleSet, SubmittingDevice, TelemetryReading
from monitoring.domain.value_objects import AlertSeverity, AlertType, SyncStatus
from monitoring.infrastructure.cloud_client import IchuCloudClient
from monitoring.infrastructure.repositories import (
    MonitoringAlertRepository,
    MonitoringRuleSetRepository,
    TelemetryReadingRepository,
)
from monitoring.integration.cloud_anti_corruption_layer import (
    DisabledCloudAntiCorruptionLayer,
    IchuCloudAntiCorruptionLayer,
)
from shared.infrastructure.database import configure_database, db


DEVICE_ID = "9d4a5f3b-3a6e-4f84-8daa-4e5f6a7b8c93"
NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)
GATEWAY_ID = "c07d8c6e-6d9b-4cb7-9add-7b8c9daebfc6"


class FakeReadingRepository:
    def __init__(self, readings):
        self.readings = {reading.reading_id: reading for reading in readings}

    def find_pending(self, limit):
        pending = [reading for reading in self.readings.values() if reading.sync_status is SyncStatus.PENDING]
        return pending[:limit]

    def update_sync_status(self, reading):
        self.readings[reading.reading_id] = reading
        return reading


class FakeAlertRepository:
    def __init__(self, alerts):
        self.alerts = {alert.alert_id: alert for alert in alerts}

    def find_pending(self, limit):
        pending = [alert for alert in self.alerts.values() if alert.sync_status is SyncStatus.PENDING]
        return pending[:limit]

    def update_sync_status(self, alert):
        self.alerts[alert.alert_id] = alert
        return alert


class FakeRuleRepository:
    def __init__(self):
        self.rule_set = MonitoringRuleSet.empty()

    def get(self):
        return self.rule_set

    def replace(self, rule_set):
        self.rule_set = rule_set
        return rule_set


class FakeCloudGateway:
    def __init__(self, rule_sets=(), batch_results=None, error=None, refused_reading_ids=()):
        self.rule_sets = list(rule_sets)
        self.batch_results = batch_results
        self.error = error
        self.refused_reading_ids = set(refused_reading_ids)
        self.fetch_requests = []
        self.sent_batches = []

    def fetch_rules(self, after_revision):
        self.fetch_requests.append(after_revision)
        if self.error is not None:
            raise self.error
        return self.rule_sets.pop(0)

    def send_batch(self, readings, alerts):
        self.sent_batches.append((list(readings), list(alerts)))
        if self.error is not None:
            raise self.error
        if any(reading.reading_id in self.refused_reading_ids for reading in readings):
            raise CloudBatchRejected("Refused by fake cloud")
        if self.batch_results is not None:
            return list(self.batch_results)
        return [
            *(CloudEntryResult("Reading", str(reading.reading_id), "Accepted") for reading in readings),
            *(CloudEntryResult("Alert", str(alert.alert_id), "Accepted") for alert in alerts),
        ]


def make_reading(event_id, temperature):
    return TelemetryReading.record(SubmittingDevice(DEVICE_ID), event_id, NOW, temperature, None, None, None)


class SynchronizationServiceTestCase(unittest.TestCase):
    def test_rules_are_replaced_when_newer_and_kept_when_not_modified(self):
        rule_repository = FakeRuleRepository()
        newer = MonitoringRuleSet(uuid.uuid4(), 3, (), ())
        cloud = FakeCloudGateway(rule_sets=(newer, None))
        service = SynchronizationService(
            cloud, FakeReadingRepository([]), FakeAlertRepository([]), rule_repository, batch_size=10
        )

        first = service.refresh_rules()
        second = service.refresh_rules()

        self.assertEqual(first, RuleRefreshResult(True, 3))
        self.assertEqual(second, RuleRefreshResult(False, 3))
        self.assertEqual(cloud.fetch_requests, [0, 3])
        self.assertEqual(rule_repository.get().revision, 3)

    def test_push_applies_per_entry_outcomes_and_keeps_missing_entries_pending(self):
        accepted = make_reading("event-1", 38.5)
        rejected = make_reading("event-2", 38.6)
        missing = make_reading("event-3", 38.7)
        alert = MonitoringAlert.raise_for(
            DEVICE_ID, AlertType.TEMPERATURE, AlertSeverity.WARNING, (accepted.reading_id,), 3, NOW, 37.5, 39.5
        )
        readings = FakeReadingRepository([accepted, rejected, missing])
        alerts = FakeAlertRepository([alert])
        cloud = FakeCloudGateway(
            batch_results=(
                CloudEntryResult("Reading", str(accepted.reading_id), "Accepted"),
                CloudEntryResult("Reading", str(rejected.reading_id), "Rejected", "Unknown device"),
                CloudEntryResult("Alert", str(alert.alert_id), "Duplicate"),
            )
        )
        service = SynchronizationService(cloud, readings, alerts, FakeRuleRepository(), batch_size=10)

        result = service.push_pending()

        self.assertEqual(result, SyncResult(sent_readings=3, sent_alerts=1, synced=2, rejected=1, deferred=0))
        self.assertEqual(readings.readings[accepted.reading_id].sync_status, SyncStatus.SYNCED)
        self.assertEqual(readings.readings[rejected.reading_id].sync_status, SyncStatus.REJECTED)
        self.assertEqual(readings.readings[rejected.reading_id].rejection_reason, "Unknown device")
        self.assertEqual(readings.readings[missing.reading_id].sync_status, SyncStatus.PENDING)
        self.assertEqual(alerts.alerts[alert.alert_id].sync_status, SyncStatus.SYNCED)

    def test_conflict_keeps_entry_pending_until_its_attempts_run_out(self):
        reading = make_reading("event-5", 38.5)
        readings = FakeReadingRepository([reading])
        cloud = FakeCloudGateway(
            batch_results=(
                CloudEntryResult("Reading", str(reading.reading_id), "Conflict", "Different telemetry"),
            )
        )
        service = SynchronizationService(cloud, readings, FakeAlertRepository([]), FakeRuleRepository(), batch_size=10)

        first = service.push_pending()

        self.assertEqual(first, SyncResult(sent_readings=1, sent_alerts=0, synced=0, rejected=0, deferred=1))
        self.assertEqual(readings.readings[reading.reading_id].sync_status, SyncStatus.PENDING)
        self.assertEqual(readings.readings[reading.reading_id].sync_attempts, 1)

        later = [service.push_pending() for _ in range(MAX_SYNC_ATTEMPTS - 1)]

        self.assertEqual(later[-1], SyncResult(sent_readings=1, sent_alerts=0, synced=0, rejected=1, deferred=0))
        stored = readings.readings[reading.reading_id]
        self.assertEqual(stored.sync_status, SyncStatus.REJECTED)
        self.assertEqual(stored.rejection_reason, "Different telemetry")
        self.assertEqual(stored.sync_attempts, MAX_SYNC_ATTEMPTS)

    def test_refused_batch_is_split_and_only_the_invalid_reading_is_retried_then_rejected(self):
        first = make_reading("event-a", 38.5)
        refused = make_reading("event-x", 38.6)
        last = make_reading("event-b", 38.7)
        readings = FakeReadingRepository([first, refused, last])
        cloud = FakeCloudGateway(refused_reading_ids=(refused.reading_id,))
        service = SynchronizationService(cloud, readings, FakeAlertRepository([]), FakeRuleRepository(), batch_size=10)

        result = service.push_pending()

        self.assertEqual(result, SyncResult(sent_readings=3, sent_alerts=0, synced=2, rejected=0, deferred=1))
        self.assertEqual(readings.readings[first.reading_id].sync_status, SyncStatus.SYNCED)
        self.assertEqual(readings.readings[last.reading_id].sync_status, SyncStatus.SYNCED)
        self.assertEqual(readings.readings[refused.reading_id].sync_status, SyncStatus.PENDING)

        for _ in range(MAX_SYNC_ATTEMPTS - 1):
            service.push_pending()

        self.assertEqual(readings.readings[refused.reading_id].sync_status, SyncStatus.REJECTED)
        self.assertEqual(
            readings.readings[refused.reading_id].rejection_reason,
            "Refused by the ICHU cloud API as invalid",
        )

    def test_outage_keeps_records_pending_and_recovery_sends_them_once(self):
        reading = make_reading("event-r", 38.5)
        readings = FakeReadingRepository([reading])
        cloud = FakeCloudGateway(error=CloudUnavailable("offline"))
        service = SynchronizationService(cloud, readings, FakeAlertRepository([]), FakeRuleRepository(), batch_size=10)

        with self.assertRaises(CloudUnavailable):
            service.push_pending()
        self.assertEqual(readings.readings[reading.reading_id].sync_status, SyncStatus.PENDING)

        cloud.error = None
        self.assertEqual(service.push_pending(), SyncResult(sent_readings=1, sent_alerts=0, synced=1, rejected=0, deferred=0))
        self.assertEqual(readings.readings[reading.reading_id].sync_status, SyncStatus.SYNCED)

        sent_before = len(cloud.sent_batches)
        self.assertEqual(service.push_pending(), SyncResult(0, 0, 0, 0, 0))
        self.assertEqual(len(cloud.sent_batches), sent_before)

    def test_duplicate_answer_counts_as_synced(self):
        reading = make_reading("event-d", 38.5)
        readings = FakeReadingRepository([reading])
        cloud = FakeCloudGateway(batch_results=(CloudEntryResult("Reading", str(reading.reading_id), "Duplicate"),))
        service = SynchronizationService(cloud, readings, FakeAlertRepository([]), FakeRuleRepository(), batch_size=10)

        result = service.push_pending()

        self.assertEqual(result, SyncResult(sent_readings=1, sent_alerts=0, synced=1, rejected=0, deferred=0))
        self.assertEqual(readings.readings[reading.reading_id].sync_status, SyncStatus.SYNCED)

    def test_unavailable_cloud_keeps_every_record_pending(self):
        reading = make_reading("event-4", 38.5)
        readings = FakeReadingRepository([reading])
        service = SynchronizationService(
            FakeCloudGateway(error=CloudUnavailable("offline")),
            readings,
            FakeAlertRepository([]),
            FakeRuleRepository(),
            batch_size=10,
        )

        with self.assertRaises(CloudUnavailable):
            service.synchronize()

        self.assertEqual(readings.readings[reading.reading_id].sync_status, SyncStatus.PENDING)

    def test_synchronize_combines_rule_refresh_and_push(self):
        service = SynchronizationService(
            FakeCloudGateway(rule_sets=(None,)),
            FakeReadingRepository([]),
            FakeAlertRepository([]),
            FakeRuleRepository(),
            batch_size=10,
        )

        self.assertEqual(
            service.synchronize(),
            SynchronizationResult(False, 0, 0, 0, 0, 0, 0),
        )

    def test_batch_limit_applies_to_readings_and_alerts_together(self):
        readings = [make_reading(f"limit-{index}", 38.5) for index in range(3)]
        alerts = [MonitoringAlert.raise_for(
            DEVICE_ID, AlertType.TEMPERATURE, AlertSeverity.WARNING,
            (readings[0].reading_id,), 3, NOW, 37.5, 39.5
        ) for _ in range(2)]
        cloud = FakeCloudGateway()
        service = SynchronizationService(cloud, FakeReadingRepository(readings),
            FakeAlertRepository(alerts), FakeRuleRepository(), batch_size=4)

        result = service.push_pending()

        self.assertEqual((result.sent_readings, result.sent_alerts), (3, 1))
        sent_readings, sent_alerts = cloud.sent_batches[0]
        self.assertEqual(4, len(sent_readings) + len(sent_alerts))

    def test_full_reading_batch_leaves_alerts_pending(self):
        reading = make_reading("full", 38.5)
        alert = MonitoringAlert.raise_for(DEVICE_ID, AlertType.TEMPERATURE,
            AlertSeverity.WARNING, (reading.reading_id,), 3, NOW, 37.5, 39.5)
        cloud = FakeCloudGateway()
        alerts = FakeAlertRepository([alert])
        service = SynchronizationService(cloud, FakeReadingRepository([reading]),
            alerts, FakeRuleRepository(), batch_size=1)

        result = service.push_pending()

        self.assertEqual((result.sent_readings, result.sent_alerts), (1, 0))
        self.assertEqual(SyncStatus.PENDING, alerts.alerts[alert.alert_id].sync_status)

    def test_retryable_conflict_keeps_entry_pending(self):
        reading = make_reading("retryable", 38.5)
        readings = FakeReadingRepository([reading])
        cloud = FakeCloudGateway(batch_results=[CloudEntryResult("Reading",
            str(reading.reading_id), "Conflict", "Concurrent save",
            code="OperationsMonitoring.ReadingConcurrentlyStored", retryable=True)])
        service = SynchronizationService(cloud, readings, FakeAlertRepository([]),
            FakeRuleRepository(), batch_size=10)

        result = service.push_pending()

        self.assertEqual((result.synced, result.rejected, result.deferred), (0, 0, 1))
        self.assertEqual(1, readings.readings[reading.reading_id].sync_attempts)
        self.assertEqual(SyncStatus.PENDING, readings.readings[reading.reading_id].sync_status)

    def test_explicit_permanent_conflict_is_not_retried(self):
        reading = make_reading("permanent", 38.5)
        readings = FakeReadingRepository([reading])
        cloud = FakeCloudGateway(batch_results=[CloudEntryResult("Reading",
            str(reading.reading_id), "Conflict", "Different capture",
            code="OperationsMonitoring.ReadingIdentifierConflict", retryable=False)])
        service = SynchronizationService(cloud, readings, FakeAlertRepository([]),
            FakeRuleRepository(), batch_size=10)

        result = service.push_pending()

        self.assertEqual((result.rejected, result.deferred), (1, 0))
        self.assertEqual(SyncStatus.REJECTED, readings.readings[reading.reading_id].sync_status)
        self.assertEqual(0, readings.readings[reading.reading_id].sync_attempts)

    def test_disabled_gateway_refuses_every_call(self):
        gateway = DisabledCloudAntiCorruptionLayer()

        with self.assertRaises(SynchronizationDisabled):
            gateway.fetch_rules(0)
        with self.assertRaises(SynchronizationDisabled):
            gateway.send_batch([], [])


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("No JSON body")
        return self._payload


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.error is not None:
            raise self.error
        return self.response


def make_client(session):
    return IchuCloudClient("https://cloud.example.org/", GATEWAY_ID, "gateway-test-key", 5, session=session)


class IchuCloudClientTestCase(unittest.TestCase):
    def test_unauthorized_and_forbidden_answers_raise_credentials_rejected(self):
        for status in (401, 403):
            with self.subTest(status=status):
                client = make_client(FakeSession(response=FakeResponse(status)))
                with self.assertRaises(CloudCredentialsRejected):
                    client.fetch_monitoring_rules(0)
                with self.assertRaises(CloudCredentialsRejected):
                    client.send_telemetry_batch({"readings": [], "alerts": [], "alertChanges": []})

    def test_other_error_answers_raise_cloud_unavailable(self):
        client = make_client(FakeSession(response=FakeResponse(500)))

        with self.assertRaises(CloudUnavailable):
            client.send_telemetry_batch({"readings": [], "alerts": [], "alertChanges": []})

    def test_refused_batch_answers_raise_batch_rejected(self):
        for status in (400, 422):
            with self.subTest(status=status):
                client = make_client(FakeSession(response=FakeResponse(status)))
                with self.assertRaises(CloudBatchRejected):
                    client.send_telemetry_batch({"readings": [], "alerts": [], "alertChanges": []})

    def test_network_failures_raise_cloud_unavailable(self):
        client = make_client(FakeSession(error=requests.ConnectionError("unreachable")))

        with self.assertRaises(CloudUnavailable):
            client.fetch_monitoring_rules(0)

    def test_not_modified_rules_answer_returns_none(self):
        client = make_client(FakeSession(response=FakeResponse(304)))

        self.assertIsNone(client.fetch_monitoring_rules(4))

    def test_batch_is_posted_with_the_gateway_key(self):
        session = FakeSession(response=FakeResponse(200, {"results": []}))

        make_client(session).send_telemetry_batch({"readings": [], "alerts": [], "alertChanges": []})

        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://cloud.example.org/api/v1/telemetry/batch")
        self.assertEqual(kwargs["headers"]["X-Gateway-Key"], "gateway-test-key")


class SynchronizationContractTestCase(unittest.TestCase):
    def test_retryable_outcomes_cross_the_cloud_contract_boundary(self):
        session = FakeSession(response=FakeResponse(200, {"results": [{
            "kind": "Reading", "id": str(uuid.uuid4()), "status": "Conflict",
            "code": "OperationsMonitoring.ReadingConcurrentlyStored", "retryable": True,
        }]}))
        gateway = IchuCloudAntiCorruptionLayer(make_client(session))

        outcome = gateway.send_batch([], [])[0]

        self.assertTrue(outcome.retryable)
        self.assertEqual("OperationsMonitoring.ReadingConcurrentlyStored", outcome.code)

    def test_malformed_outcome_metadata_and_missing_results_are_refused(self):
        invalid = {"results": [{"kind": "Reading", "id": str(uuid.uuid4()),
            "status": "Conflict", "retryable": "false"}]}
        for payload in ({}, invalid):
            with self.subTest(payload=payload):
                gateway = IchuCloudAntiCorruptionLayer(make_client(
                    FakeSession(response=FakeResponse(200, payload))))
                with self.assertRaises(CloudUnavailable):
                    gateway.send_batch([], [])


class SynchronizationCredentialsTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="ichu-edge-credentials-", ignore_cleanup_errors=True)
        environment = {
            "APP_ENV": "development",
            "DATABASE_PATH": os.path.join(cls.directory.name, "credentials.db"),
            "SEED_DEVELOPMENT_DATA": "false",
            "CLOUD_API_BASE_URL": "https://cloud.example.org",
            "EDGE_GATEWAY_KEY": f"{GATEWAY_ID}.gateway-test-key",
        }
        with patch.dict(os.environ, environment, clear=True):
            from app import create_app

            cls.app = create_app()
        cls.app.testing = True

    def test_rejected_credentials_return_502_and_keep_records_pending(self):
        readings = TelemetryReadingRepository()
        reading = TelemetryReading.record(
            SubmittingDevice(DEVICE_ID), "event-credentials-1", NOW, 38.5, None, None, None
        )
        readings.save_idempotently(reading)
        cloud = IchuCloudAntiCorruptionLayer(make_client(FakeSession(response=FakeResponse(401))))
        self.app.extensions["synchronization_service"] = SynchronizationService(
            cloud, readings, MonitoringAlertRepository(), MonitoringRuleSetRepository(), batch_size=10
        )

        response = self.app.test_client().post("/api/v1/synchronizations")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json, {"error": "The ICHU cloud API rejected the edge gateway credentials"}
        )
        self.assertIn(reading.reading_id, [pending.reading_id for pending in readings.find_pending(10)])


class SynchronizationEndpointTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="ichu-edge-sync-", ignore_cleanup_errors=True)
        environment = {
            "APP_ENV": "development",
            "DATABASE_PATH": os.path.join(cls.directory.name, "sync.db"),
            "SEED_DEVELOPMENT_DATA": "false",
            "CLOUD_API_BASE_URL": "",
        }
        with patch.dict(os.environ, environment, clear=True):
            from app import create_app

            cls.app = create_app()
        cls.app.testing = True

    def test_returns_503_when_cloud_synchronization_is_disabled(self):
        response = self.app.test_client().post("/api/v1/synchronizations")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json, {"error": "Cloud synchronization is not configured"})


LEGACY_READINGS_TABLE = """
    CREATE TABLE telemetry_readings (
        reading_id VARCHAR(36) NOT NULL PRIMARY KEY,
        device_id VARCHAR(36) NOT NULL,
        event_id VARCHAR(128) NOT NULL,
        recorded_at DATETIME NOT NULL,
        timestamp_supplied INTEGER NOT NULL,
        body_temperature REAL,
        activity_index REAL,
        battery_level REAL,
        latitude REAL,
        longitude REAL,
        accuracy_meters REAL,
        sync_status VARCHAR(16) NOT NULL,
        rejection_reason TEXT
    )
"""

LEGACY_ALERTS_TABLE = """
    CREATE TABLE monitoring_alerts (
        alert_id VARCHAR(36) NOT NULL PRIMARY KEY,
        device_id VARCHAR(36) NOT NULL,
        alert_type VARCHAR(16) NOT NULL,
        severity VARCHAR(16) NOT NULL,
        reading_ids TEXT NOT NULL,
        configuration_revision INTEGER NOT NULL,
        raised_at DATETIME NOT NULL,
        threshold_min_value REAL,
        threshold_max_value REAL,
        geofence_id VARCHAR(36),
        distance_to_boundary_meters REAL,
        sync_status VARCHAR(16) NOT NULL,
        rejection_reason TEXT
    )
"""


class LegacySchemaMigrationTestCase(unittest.TestCase):
    def test_databases_created_before_sync_attempts_gain_the_column_and_load_with_zero(self):
        with tempfile.TemporaryDirectory(prefix="ichu-edge-migration-", ignore_cleanup_errors=True) as directory:
            configure_database(os.path.join(directory, "v010.db"))
            try:
                db.execute_sql(LEGACY_READINGS_TABLE)
                db.execute_sql(
                    "INSERT INTO telemetry_readings (reading_id, device_id, event_id, recorded_at, "
                    "timestamp_supplied, body_temperature, sync_status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), DEVICE_ID, "legacy-event", "2026-11-07 12:00:00", 1, 38.5, "pending"),
                )
                db.execute_sql(LEGACY_ALERTS_TABLE)

                TelemetryReadingRepository().migrate_schema()
                MonitoringAlertRepository().migrate_schema()
                reading_columns = {column.name for column in db.get_columns("telemetry_readings")}
                alert_columns = {column.name for column in db.get_columns("monitoring_alerts")}
                pending = TelemetryReadingRepository().find_pending(10)
            finally:
                db.close()

        self.assertIn("sync_attempts", reading_columns)
        self.assertIn("sync_attempts", alert_columns)
        self.assertEqual([reading.sync_attempts for reading in pending], [0])


if __name__ == "__main__":
    unittest.main()
