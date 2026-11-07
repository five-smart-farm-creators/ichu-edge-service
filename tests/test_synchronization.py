import os
import tempfile
import unittest
import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import requests

from monitoring.application.errors import (
    CloudCredentialsRejected,
    CloudUnavailable,
    SynchronizationDisabled,
)
from monitoring.application.services import (
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
    def __init__(self, rule_sets=(), batch_results=(), error=None):
        self.rule_sets = list(rule_sets)
        self.batch_results = list(batch_results)
        self.error = error
        self.fetch_requests = []

    def fetch_rules(self, after_revision):
        self.fetch_requests.append(after_revision)
        if self.error is not None:
            raise self.error
        return self.rule_sets.pop(0)

    def send_batch(self, readings, alerts):
        if self.error is not None:
            raise self.error
        return list(self.batch_results)


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

        self.assertEqual(result, SyncResult(sent_readings=3, sent_alerts=1, synced=2, rejected=1))
        self.assertEqual(readings.readings[accepted.reading_id].sync_status, SyncStatus.SYNCED)
        self.assertEqual(readings.readings[rejected.reading_id].sync_status, SyncStatus.REJECTED)
        self.assertEqual(readings.readings[rejected.reading_id].rejection_reason, "Unknown device")
        self.assertEqual(readings.readings[missing.reading_id].sync_status, SyncStatus.PENDING)
        self.assertEqual(alerts.alerts[alert.alert_id].sync_status, SyncStatus.SYNCED)

    def test_conflict_marks_entry_rejected_with_cloud_reason(self):
        reading = make_reading("event-5", 38.5)
        readings = FakeReadingRepository([reading])
        cloud = FakeCloudGateway(
            batch_results=(
                CloudEntryResult("Reading", str(reading.reading_id), "Conflict", "Different telemetry"),
            )
        )
        service = SynchronizationService(cloud, readings, FakeAlertRepository([]), FakeRuleRepository(), batch_size=10)

        result = service.push_pending()

        self.assertEqual(result, SyncResult(sent_readings=1, sent_alerts=0, synced=0, rejected=1))
        stored = readings.readings[reading.reading_id]
        self.assertEqual(stored.sync_status, SyncStatus.REJECTED)
        self.assertEqual(stored.rejection_reason, "Different telemetry")

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
            SynchronizationResult(False, 0, 0, 0, 0, 0),
        )

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


class SynchronizationCredentialsTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="ichu-edge-credentials-", ignore_cleanup_errors=True)
        environment = {
            "APP_ENV": "development",
            "DATABASE_PATH": os.path.join(cls.directory.name, "credentials.db"),
            "SEED_DEVELOPMENT_DATA": "false",
            "CLOUD_API_BASE_URL": "https://cloud.example.org",
            "EDGE_GATEWAY_KEY": "gateway-test-key",
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


if __name__ == "__main__":
    unittest.main()
