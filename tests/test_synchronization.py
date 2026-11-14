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
from monitoring.domain.value_objects import (
    AlertSeverity,
    AlertType,
    DeviceAssignment,
    GeoPosition,
    HeartRate,
    MethaneIndex,
    MonitoredMetric,
    SyncStatus,
    Threshold,
)
from monitoring.infrastructure.cloud_client import IchuCloudClient
from monitoring.infrastructure.repositories import (
    DeviceAssignmentRepository,
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
OTHER_DEVICE_ID = "ae5b6a4c-4b7f-4a95-9ebb-5f6a7b8c9da4"
LOT_ID = "6a1f2c0e-0d3b-4c51-9a77-1b2c3d4e5f60"
CATTLE_ID = "4f1e7c2b-9a3d-4e8f-8b6c-2d5a7e9f1b03"
RANCH_ID = "a3b7c9d1-2e4f-4a6b-8c8d-0e1f2a3b4c5d"
NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)
GATEWAY_ID = "c07d8c6e-6d9b-4cb7-9add-7b8c9daebfc6"
DEVICE_KEY = "device-test-key"
READINGS_PATH = "/api/v1/monitoring/telemetry-readings"


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


class FakeAssignmentRepository:
    def __init__(self):
        self.assignments = []

    def replace_all(self, assignments):
        self.assignments = list(assignments)


class FakeCloudGateway:
    def __init__(self, rule_sets=(), batch_results=None, error=None, refused_reading_ids=(), assignments=()):
        self.rule_sets = list(rule_sets)
        self.batch_results = batch_results
        self.error = error
        self.refused_reading_ids = set(refused_reading_ids)
        self.assignments = list(assignments)
        self.fetch_requests = []
        self.sent_batches = []

    def fetch_rules(self, after_revision):
        self.fetch_requests.append(after_revision)
        if self.error is not None:
            raise self.error
        return self.rule_sets.pop(0)

    def fetch_assignments(self):
        if self.error is not None:
            raise self.error
        return list(self.assignments)

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
            cloud,
            FakeReadingRepository([]),
            FakeAlertRepository([]),
            rule_repository,
            FakeAssignmentRepository(),
            batch_size=10,
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
        service = SynchronizationService(cloud, readings, alerts, FakeRuleRepository(), FakeAssignmentRepository(), batch_size=10)

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
        service = SynchronizationService(cloud, readings, FakeAlertRepository([]), FakeRuleRepository(), FakeAssignmentRepository(), batch_size=10)

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
        service = SynchronizationService(cloud, readings, FakeAlertRepository([]), FakeRuleRepository(), FakeAssignmentRepository(), batch_size=10)

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
        service = SynchronizationService(cloud, readings, FakeAlertRepository([]), FakeRuleRepository(), FakeAssignmentRepository(), batch_size=10)

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
        service = SynchronizationService(cloud, readings, FakeAlertRepository([]), FakeRuleRepository(), FakeAssignmentRepository(), batch_size=10)

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
            FakeAssignmentRepository(),
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
            FakeAssignmentRepository(),
            batch_size=10,
        )

        self.assertEqual(
            service.synchronize(),
            SynchronizationResult(False, 0, 0, 0, 0, 0, 0, 0),
        )

    def test_assignments_are_replaced_by_the_cloud_list_and_counted(self):
        assignments = FakeAssignmentRepository()
        cloud = FakeCloudGateway(assignments=(DeviceAssignment(DEVICE_ID, None, "Heifer"),))
        service = SynchronizationService(
            cloud, FakeReadingRepository([]), FakeAlertRepository([]), FakeRuleRepository(), assignments, batch_size=10
        )

        self.assertEqual(service.refresh_assignments(), 1)
        self.assertEqual(assignments.assignments, [DeviceAssignment(DEVICE_ID, None, "Heifer")])

    def test_disabled_gateway_refuses_every_call(self):
        gateway = DisabledCloudAntiCorruptionLayer()

        with self.assertRaises(SynchronizationDisabled):
            gateway.fetch_rules(0)
        with self.assertRaises(SynchronizationDisabled):
            gateway.send_batch([], [])
        with self.assertRaises(SynchronizationDisabled):
            gateway.fetch_assignments()


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
                    client.fetch_device_assignments()
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

    def test_assignments_are_fetched_from_the_gateway_path_with_the_key(self):
        session = FakeSession(response=FakeResponse(200, {"ranchId": RANCH_ID, "assignments": []}))

        make_client(session).fetch_device_assignments()

        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(
            url, f"https://cloud.example.org/api/v1/edge-gateways/{GATEWAY_ID}/device-assignments"
        )
        self.assertEqual(kwargs["headers"]["X-Gateway-Key"], "gateway-test-key")


def cloud_assignment(device_id, lot_id, life_stage):
    return {"deviceId": device_id, "cattleId": CATTLE_ID, "lotId": lot_id, "lifeStage": life_stage}


class CloudAssignmentTranslationTestCase(unittest.TestCase):
    def fetch(self, payload):
        client = make_client(FakeSession(response=FakeResponse(200, payload)))
        return IchuCloudAntiCorruptionLayer(client).fetch_assignments()

    def test_translates_the_cloud_list_into_device_assignments(self):
        payload = {
            "ranchId": RANCH_ID,
            "assignments": [
                cloud_assignment(DEVICE_ID.upper(), LOT_ID, "MilkingCow"),
                cloud_assignment(OTHER_DEVICE_ID, None, "Calf"),
            ],
        }

        self.assertEqual(
            self.fetch(payload),
            [
                DeviceAssignment(DEVICE_ID, uuid.UUID(LOT_ID), "MilkingCow"),
                DeviceAssignment(OTHER_DEVICE_ID, None, "Calf"),
            ],
        )

    def test_malformed_payloads_raise_cloud_unavailable(self):
        malformed = (
            {"ranchId": RANCH_ID},
            {"assignments": None},
            {"assignments": [cloud_assignment("band-42", None, "Calf")]},
            {"assignments": [cloud_assignment(DEVICE_ID, "not-a-uuid", "Calf")]},
            {"assignments": [{"deviceId": DEVICE_ID, "cattleId": CATTLE_ID, "lotId": None}]},
        )
        for payload in malformed:
            with self.subTest(payload=payload):
                with self.assertRaises(CloudUnavailable):
                    self.fetch(payload)


class ReadingContractTranslationTestCase(unittest.TestCase):
    def contract_of(self, reading):
        session = FakeSession(response=FakeResponse(200, {"results": []}))
        IchuCloudAntiCorruptionLayer(make_client(session)).send_batch([reading], [])
        return session.calls[0][2]["json"]["readings"][0]

    def test_failed_fields_are_sent_with_their_invalid_reason_and_no_value(self):
        reading = TelemetryReading.record(
            SubmittingDevice(DEVICE_ID),
            "event-contract-1",
            NOW,
            None,
            42.0,
            None,
            None,
            {"body_temperature": "Sensor did not respond", "position": "GPS module offline"},
        )

        contract = self.contract_of(reading)

        self.assertEqual(
            contract["bodyTemperature"], {"value": None, "invalidReason": "Sensor did not respond"}
        )
        self.assertEqual(contract["activityIndex"], {"value": 42.0, "invalidReason": None})
        self.assertIsNone(contract["batteryLevel"])
        self.assertEqual(
            contract["position"],
            {
                "latitude": None,
                "longitude": None,
                "accuracyMeters": None,
                "invalidReason": "GPS module offline",
            },
        )

    def test_captured_position_is_sent_with_its_accuracy(self):
        reading = TelemetryReading.record(
            SubmittingDevice(DEVICE_ID),
            "event-contract-2",
            NOW,
            38.5,
            None,
            None,
            GeoPosition(-16.5, -68.1, 4.0),
        )

        contract = self.contract_of(reading)

        self.assertEqual(
            contract["position"],
            {"latitude": -16.5, "longitude": -68.1, "accuracyMeters": 4.0, "invalidReason": None},
        )

    def test_captured_pulse_and_methane_index_are_sent_with_their_values(self):
        reading = TelemetryReading.record(
            SubmittingDevice(DEVICE_ID),
            "event-contract-3",
            NOW,
            38.5,
            None,
            None,
            None,
            heart_rate=72.0,
            methane_index=18.5,
        )

        contract = self.contract_of(reading)

        self.assertEqual(contract["heartRate"], {"value": 72.0, "invalidReason": None})
        self.assertEqual(contract["methaneIndex"], {"value": 18.5, "invalidReason": None})

    def test_failed_pulse_and_gas_sensors_are_sent_with_their_reasons_and_no_value(self):
        reading = TelemetryReading.record(
            SubmittingDevice(DEVICE_ID),
            "event-contract-4",
            NOW,
            38.5,
            None,
            None,
            None,
            {"heart_rate": "Pulse sensor without contact", "methane_index": "Gas sensor warming up"},
        )

        contract = self.contract_of(reading)

        self.assertEqual(
            contract["heartRate"], {"value": None, "invalidReason": "Pulse sensor without contact"}
        )
        self.assertEqual(
            contract["methaneIndex"], {"value": None, "invalidReason": "Gas sensor warming up"}
        )


def cloud_threshold(metric):
    return {
        "thresholdId": str(uuid.uuid4()),
        "lifeStage": "MilkingCow",
        "metric": metric,
        "minValue": 48.0,
        "maxValue": 84.0,
        "severity": "Warning",
    }


class CloudRulesTranslationTestCase(unittest.TestCase):
    def test_threshold_for_an_unknown_metric_is_skipped_and_the_rest_are_kept(self):
        payload = {
            "ranchId": RANCH_ID,
            "revision": 5,
            "thresholds": [cloud_threshold("HeartRate"), cloud_threshold("Hydration")],
            "geofences": [],
        }
        client = make_client(FakeSession(response=FakeResponse(200, payload)))

        with self.assertLogs("monitoring.integration.cloud_anti_corruption_layer", level="WARNING") as logs:
            rules = IchuCloudAntiCorruptionLayer(client).fetch_rules(0)

        self.assertEqual(rules.revision, 5)
        self.assertEqual([threshold.metric for threshold in rules.thresholds], [MonitoredMetric.HEART_RATE])
        self.assertEqual((rules.thresholds[0].min_value, rules.thresholds[0].max_value), (48.0, 84.0))
        self.assertIn("Hydration", logs.output[0])


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
            cloud,
            readings,
            MonitoringAlertRepository(),
            MonitoringRuleSetRepository(),
            DeviceAssignmentRepository(),
            batch_size=10,
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

    def test_databases_created_before_invalid_reasons_load_them_as_empty(self):
        with tempfile.TemporaryDirectory(prefix="ichu-edge-migration-", ignore_cleanup_errors=True) as directory:
            configure_database(os.path.join(directory, "v020.db"))
            try:
                db.execute_sql(LEGACY_READINGS_TABLE)
                db.execute_sql(
                    "INSERT INTO telemetry_readings (reading_id, device_id, event_id, recorded_at, "
                    "timestamp_supplied, body_temperature, sync_status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), DEVICE_ID, "legacy-event-2", "2026-11-07 12:00:00", 1, 38.5, "pending"),
                )

                TelemetryReadingRepository().migrate_schema()
                reading_columns = {column.name for column in db.get_columns("telemetry_readings")}
                pending = TelemetryReadingRepository().find_pending(10)
            finally:
                db.close()

        self.assertIn("invalid_reasons", reading_columns)
        self.assertEqual([reading.invalid_reasons.as_dict() for reading in pending], [{}])

    def test_databases_created_before_pulse_and_methane_index_load_them_as_absent(self):
        with tempfile.TemporaryDirectory(prefix="ichu-edge-migration-", ignore_cleanup_errors=True) as directory:
            configure_database(os.path.join(directory, "v030.db"))
            try:
                db.execute_sql(LEGACY_READINGS_TABLE)
                db.execute_sql(
                    "INSERT INTO telemetry_readings (reading_id, device_id, event_id, recorded_at, "
                    "timestamp_supplied, body_temperature, sync_status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), DEVICE_ID, "legacy-event-3", "2026-11-07 12:00:00", 1, 38.5, "pending"),
                )

                TelemetryReadingRepository().migrate_schema()
                reading_columns = {column.name for column in db.get_columns("telemetry_readings")}
                pending = TelemetryReadingRepository().find_pending(10)
            finally:
                db.close()

        self.assertIn("heart_rate", reading_columns)
        self.assertIn("methane_index", reading_columns)
        self.assertEqual([(reading.heart_rate, reading.methane_index) for reading in pending], [(None, None)])


class TelemetryReadingRepositoryTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="ichu-edge-readings-", ignore_cleanup_errors=True)
        environment = {
            "APP_ENV": "development",
            "DATABASE_PATH": os.path.join(cls.directory.name, "readings.db"),
            "SEED_DEVELOPMENT_DATA": "false",
            "CLOUD_API_BASE_URL": "",
        }
        with patch.dict(os.environ, environment, clear=True):
            from app import create_app

            create_app()

    def test_invalid_reasons_survive_a_round_trip_and_are_stored_as_sorted_json(self):
        repository = TelemetryReadingRepository()
        reading = TelemetryReading.record(
            SubmittingDevice(DEVICE_ID),
            "event-stored-reasons-1",
            NOW,
            None,
            42.0,
            None,
            None,
            {"position": "GPS module offline", "body_temperature": "Sensor did not respond"},
        )
        repository.save_idempotently(reading)

        stored = next(
            item for item in repository.find_pending(100) if item.reading_id == reading.reading_id
        )
        raw = db.execute_sql(
            "SELECT invalid_reasons FROM telemetry_readings WHERE event_id = ?",
            ("event-stored-reasons-1",),
        ).fetchone()[0]

        self.assertEqual(stored.invalid_reasons, reading.invalid_reasons)
        self.assertEqual(
            raw, '{"body_temperature": "Sensor did not respond", "position": "GPS module offline"}'
        )

    def test_reading_without_invalid_reasons_stores_null(self):
        reading = TelemetryReading.record(
            SubmittingDevice(DEVICE_ID), "event-stored-reasons-2", NOW, 38.5, None, None, None
        )
        TelemetryReadingRepository().save_idempotently(reading)

        raw = db.execute_sql(
            "SELECT invalid_reasons FROM telemetry_readings WHERE event_id = ?",
            ("event-stored-reasons-2",),
        ).fetchone()[0]

        self.assertIsNone(raw)

    def test_pulse_and_methane_index_survive_a_round_trip(self):
        repository = TelemetryReadingRepository()
        reading = TelemetryReading.record(
            SubmittingDevice(DEVICE_ID),
            "event-stored-pulse-1",
            NOW,
            38.5,
            None,
            None,
            None,
            heart_rate=72.0,
            methane_index=18.5,
        )
        repository.save_idempotently(reading)

        stored = next(
            item for item in repository.find_pending(100) if item.reading_id == reading.reading_id
        )

        self.assertEqual(stored.heart_rate, HeartRate(72.0))
        self.assertEqual(stored.methane_index, MethaneIndex(18.5))

    def test_absent_pulse_and_methane_index_store_null(self):
        reading = TelemetryReading.record(
            SubmittingDevice(DEVICE_ID), "event-stored-pulse-2", NOW, 38.5, None, None, None
        )
        TelemetryReadingRepository().save_idempotently(reading)

        raw = db.execute_sql(
            "SELECT heart_rate, methane_index FROM telemetry_readings WHERE event_id = ?",
            ("event-stored-pulse-2",),
        ).fetchone()

        self.assertEqual(raw, (None, None))


class DeviceAssignmentRepositoryTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="ichu-edge-assignments-", ignore_cleanup_errors=True)
        environment = {
            "APP_ENV": "development",
            "DATABASE_PATH": os.path.join(cls.directory.name, "assignments.db"),
            "SEED_DEVELOPMENT_DATA": "false",
            "CLOUD_API_BASE_URL": "",
        }
        with patch.dict(os.environ, environment, clear=True):
            from app import create_app

            create_app()

    def test_replace_all_drops_collars_missing_from_the_cloud_list(self):
        repository = DeviceAssignmentRepository()
        repository.replace_all(
            [
                DeviceAssignment(DEVICE_ID, uuid.UUID(LOT_ID), "MilkingCow"),
                DeviceAssignment(OTHER_DEVICE_ID, None, "Calf"),
            ]
        )

        repository.replace_all([DeviceAssignment(OTHER_DEVICE_ID, uuid.UUID(LOT_ID), "Heifer")])

        self.assertIsNone(repository.find(DEVICE_ID))
        self.assertEqual(
            repository.find(OTHER_DEVICE_ID), DeviceAssignment(OTHER_DEVICE_ID, uuid.UUID(LOT_ID), "Heifer")
        )

    def test_replace_all_with_an_empty_list_clears_every_assignment(self):
        repository = DeviceAssignmentRepository()
        repository.replace_all([DeviceAssignment(DEVICE_ID, None, "MilkingCow")])

        repository.replace_all([])

        self.assertIsNone(repository.find(DEVICE_ID))


def feverish_reading(event_id):
    return {
        "device_id": DEVICE_ID,
        "event_id": event_id,
        "recorded_at": "2026-11-07T10:00:00-05:00",
        "body_temperature": 40.2,
    }


class AssignmentSynchronizationTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="ichu-edge-assignment-sync-", ignore_cleanup_errors=True)
        environment = {
            "APP_ENV": "development",
            "DATABASE_PATH": os.path.join(cls.directory.name, "assignment-sync.db"),
            "SEED_DEVELOPMENT_DATA": "false",
            "CLOUD_API_BASE_URL": "",
        }
        with patch.dict(os.environ, environment, clear=True):
            from app import create_app

            cls.app = create_app()
        cls.app.testing = True

    def test_assignment_downloaded_by_synchronization_applies_to_the_next_reading(self):
        self.app.extensions["iam_authentication_service"].register_device(DEVICE_ID, DEVICE_KEY)
        threshold = Threshold(
            uuid.uuid4(), "MilkingCow", MonitoredMetric.BODY_TEMPERATURE, 37.5, 39.5, AlertSeverity.WARNING
        )
        gateway = FakeCloudGateway(
            rule_sets=(MonitoringRuleSet(uuid.uuid4(), 4, (threshold,), ()),),
            assignments=(DeviceAssignment(DEVICE_ID, None, "MilkingCow"),),
        )
        self.app.extensions["synchronization_service"] = SynchronizationService(
            gateway,
            TelemetryReadingRepository(),
            MonitoringAlertRepository(),
            MonitoringRuleSetRepository(),
            DeviceAssignmentRepository(),
            batch_size=10,
        )
        client = self.app.test_client()
        headers = {"X-API-Key": DEVICE_KEY}

        before = client.post(READINGS_PATH, json=feverish_reading("event-before-assignment"), headers=headers)
        synchronized = client.post("/api/v1/synchronizations")
        after = client.post(READINGS_PATH, json=feverish_reading("event-after-assignment"), headers=headers)

        self.assertEqual(before.json["alerts"], [])
        self.assertEqual(synchronized.status_code, 200)
        self.assertEqual(synchronized.json["assignments"], 1)
        self.assertEqual([alert["type"] for alert in after.json["alerts"]], ["Temperature"])


if __name__ == "__main__":
    unittest.main()
