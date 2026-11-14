import os
import tempfile
import unittest
import uuid

_test_directory = tempfile.TemporaryDirectory(prefix="ichu-edge-test-", ignore_cleanup_errors=True)
os.environ["APP_ENV"] = "development"
os.environ["DATABASE_PATH"] = os.path.join(_test_directory.name, "test.db")
os.environ["SEED_DEVELOPMENT_DATA"] = "true"
os.environ["DEVELOPMENT_DEVICE_API_KEY"] = "test-only-key"
os.environ["CLOUD_API_BASE_URL"] = ""

from app import create_app
from monitoring.domain.entities import MonitoringRuleSet
from monitoring.domain.value_objects import AlertSeverity, DeviceAssignment, MonitoredMetric, Threshold
from monitoring.infrastructure.repositories import DeviceAssignmentRepository, MonitoringRuleSetRepository
from shared.infrastructure.database import db


DEVICE_KEY = "test-only-key"
MILKING_COW_ID = "9d4a5f3b-3a6e-4f84-8daa-4e5f6a7b8c93"
READINGS_PATH = "/api/v1/monitoring/telemetry-readings"


def reading_payload(event_id, device_id=MILKING_COW_ID, **measurements):
    payload = {
        "device_id": device_id,
        "event_id": event_id,
        "recorded_at": "2026-11-07T10:00:00-05:00",
        "body_temperature": 38.5,
    }
    payload.update(measurements)
    return payload


class ApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.testing = True

    def setUp(self):
        self.client = self.app.test_client()

    def post_reading(self, payload, api_key=DEVICE_KEY):
        return self.client.post(READINGS_PATH, json=payload, headers={"X-API-Key": api_key})


class StatusApiTestCase(ApiTestCase):
    def test_reports_gateway_status(self):
        response = self.client.get("/api/v1/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "ok")
        self.assertEqual(response.json["service"], "ichu-edge-service")
        self.assertIsInstance(response.json["rulesRevision"], int)
        self.assertIsInstance(response.json["pendingReadings"], int)
        self.assertIsInstance(response.json["pendingAlerts"], int)


class DatabaseTestCase(ApiTestCase):
    def test_uses_write_ahead_logging(self):
        self.assertEqual(db.execute_sql("PRAGMA journal_mode").fetchone()[0], "wal")


class IamApiTestCase(ApiTestCase):
    def test_authenticates_registered_device(self):
        response = self.client.post(
            "/api/v1/iam/authentications",
            json={"device_id": MILKING_COW_ID},
            headers={"X-API-Key": DEVICE_KEY},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"device_id": MILKING_COW_ID, "authenticated": True})

    def test_rejects_invalid_or_unknown_device_credentials(self):
        for device_id, api_key in (
            (MILKING_COW_ID, "invalid-key"),
            (str(uuid.uuid4()), DEVICE_KEY),
            ("smart-band-001", DEVICE_KEY),
        ):
            with self.subTest(device_id=device_id):
                response = self.client.post(
                    "/api/v1/iam/authentications",
                    json={"device_id": device_id},
                    headers={"X-API-Key": api_key},
                )
                self.assertEqual(response.status_code, 401)

    def test_rejects_missing_api_key(self):
        response = self.client.post(
            "/api/v1/iam/authentications", json={"device_id": MILKING_COW_ID}
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json, {"error": "Missing X-API-Key"})

    def test_rejects_malformed_or_extra_fields(self):
        for body in ({}, {"device_id": MILKING_COW_ID, "bpm": 70}):
            with self.subTest(body=body):
                response = self.client.post(
                    "/api/v1/iam/authentications",
                    json=body,
                    headers={"X-API-Key": DEVICE_KEY},
                )
                self.assertEqual(response.status_code, 400)

    def test_rejects_non_json_and_malformed_json(self):
        response = self.client.post(
            "/api/v1/iam/authentications",
            data="{}",
            content_type="text/plain",
            headers={"X-API-Key": DEVICE_KEY},
        )
        self.assertEqual(response.status_code, 415)

        response = self.client.post(
            "/api/v1/iam/authentications",
            data="{",
            content_type="application/json",
            headers={"X-API-Key": DEVICE_KEY},
        )
        self.assertEqual(response.status_code, 400)


class TelemetryApiTestCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        MonitoringRuleSetRepository().replace(MonitoringRuleSet.empty())

    def test_creates_reading_with_utc_timestamp(self):
        response = self.post_reading(reading_payload("event-create-1"))

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["reading"]["event_id"], "event-create-1")
        self.assertEqual(response.json["reading"]["recorded_at"], "2026-11-07T15:00:00Z")
        self.assertEqual(response.json["reading"]["invalid_reasons"], {})
        self.assertIsNone(response.json["reading"]["heart_rate"])
        self.assertIsNone(response.json["reading"]["methane_index"])
        self.assertEqual(response.json["reading"]["sync_status"], "pending")
        self.assertEqual(response.json["alerts"], [])

    def test_identical_retry_returns_the_original_reading(self):
        payload = reading_payload("event-retry-1")
        first = self.post_reading(payload)
        retry = self.post_reading(payload)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json["reading"]["reading_id"], first.json["reading"]["reading_id"])
        self.assertEqual(retry.json["alerts"], [])

    def test_reusing_event_id_for_different_telemetry_conflicts(self):
        self.post_reading(reading_payload("event-conflict-1"))
        conflicting = self.post_reading(reading_payload("event-conflict-1", body_temperature=38.6))

        self.assertEqual(conflicting.status_code, 409)

    def test_rejects_reading_without_measurement(self):
        response = self.post_reading({"device_id": MILKING_COW_ID, "event_id": "event-empty-1"})

        self.assertEqual(response.status_code, 400)

    def test_rejects_out_of_range_temperature(self):
        response = self.post_reading(reading_payload("event-hot-1", body_temperature=46.0))

        self.assertEqual(response.status_code, 400)

    def test_rejects_wrong_api_key(self):
        response = self.post_reading(reading_payload("event-auth-1"), api_key="invalid-key")

        self.assertEqual(response.status_code, 401)

    def test_rejects_missing_device_id(self):
        response = self.post_reading({"event_id": "event-anonymous-1", "body_temperature": 38.5})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json, {"error": "Missing device_id"})

    def test_rejects_naive_timestamp(self):
        response = self.post_reading(
            reading_payload("event-naive-1", recorded_at="2026-11-07T10:00:00")
        )

        self.assertEqual(response.status_code, 400)

    def test_rejects_position_outside_its_range(self):
        position = {"latitude": 95.0, "longitude": -68.1, "accuracy_meters": 4.0}
        response = self.post_reading(reading_payload("event-position-1", position=position))

        self.assertEqual(response.status_code, 400)

    def test_rejects_negative_position_accuracy(self):
        position = {"latitude": -16.5, "longitude": -68.1, "accuracy_meters": -1.0}
        response = self.post_reading(reading_payload("event-accuracy-1", position=position))

        self.assertEqual(response.status_code, 400)

    def test_rejects_non_json_requests(self):
        response = self.client.post(
            READINGS_PATH,
            data="{}",
            content_type="text/plain",
            headers={"X-API-Key": DEVICE_KEY},
        )

        self.assertEqual(response.status_code, 415)

    def test_rejects_position_without_accuracy(self):
        position = {"latitude": -16.5, "longitude": -68.1}
        response = self.post_reading(reading_payload("event-no-accuracy-1", position=position))

        self.assertEqual(response.status_code, 400)

    def test_accepts_failed_sensor_with_its_reason_and_echoes_it(self):
        response = self.post_reading(
            reading_payload(
                "event-invalid-1",
                body_temperature=None,
                activity_index=42.0,
                invalid_reasons={"body_temperature": "Sensor did not respond"},
            )
        )

        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.json["reading"]["body_temperature"])
        self.assertEqual(
            response.json["reading"]["invalid_reasons"], {"body_temperature": "Sensor did not respond"}
        )

    def test_identical_retry_with_invalid_reasons_returns_the_original_reading(self):
        payload = reading_payload(
            "event-invalid-retry-1",
            body_temperature=None,
            invalid_reasons={"body_temperature": "Sensor did not respond"},
        )
        first = self.post_reading(payload)
        retry = self.post_reading(payload)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json["reading"]["reading_id"], first.json["reading"]["reading_id"])

    def test_reusing_event_id_with_another_reason_conflicts(self):
        self.post_reading(
            reading_payload(
                "event-invalid-conflict-1",
                body_temperature=None,
                invalid_reasons={"body_temperature": "Sensor did not respond"},
            )
        )
        conflicting = self.post_reading(
            reading_payload(
                "event-invalid-conflict-1",
                body_temperature=None,
                invalid_reasons={"body_temperature": "Probe unplugged"},
            )
        )

        self.assertEqual(conflicting.status_code, 409)

    def test_rejects_unknown_field_and_blank_or_overlong_reason(self):
        for invalid_reasons in (
            {"blood_pressure": "Sensor did not respond"},
            {"body_temperature": "   "},
            {"body_temperature": "x" * 201},
        ):
            with self.subTest(invalid_reasons=invalid_reasons):
                response = self.post_reading(
                    reading_payload("event-invalid-field-1", body_temperature=None, invalid_reasons=invalid_reasons)
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json, {"error": "Invalid telemetry reading data"})

    def test_rejects_value_and_reason_for_the_same_field(self):
        response = self.post_reading(
            reading_payload(
                "event-invalid-both-1", invalid_reasons={"body_temperature": "Sensor did not respond"}
            )
        )

        self.assertEqual(response.status_code, 400)

    def test_accepts_pulse_and_methane_index_and_echoes_them(self):
        response = self.post_reading(
            reading_payload("event-pulse-1", heart_rate=72.0, methane_index=18.5)
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["reading"]["heart_rate"], 72.0)
        self.assertEqual(response.json["reading"]["methane_index"], 18.5)

    def test_identical_retry_with_pulse_returns_the_original_reading(self):
        payload = reading_payload("event-pulse-retry-1", heart_rate=72.0, methane_index=18.5)
        first = self.post_reading(payload)
        retry = self.post_reading(payload)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json["reading"]["reading_id"], first.json["reading"]["reading_id"])

    def test_reusing_event_id_with_another_pulse_conflicts(self):
        self.post_reading(reading_payload("event-pulse-conflict-1", heart_rate=72.0))
        conflicting = self.post_reading(reading_payload("event-pulse-conflict-1", heart_rate=80.0))

        self.assertEqual(conflicting.status_code, 409)

    def test_rejects_pulse_and_methane_index_outside_their_ranges(self):
        for measurements in (
            {"heart_rate": 29.9},
            {"heart_rate": 200.1},
            {"methane_index": -0.1},
            {"methane_index": 100.1},
        ):
            with self.subTest(measurements=measurements):
                response = self.post_reading(reading_payload("event-gas-range-1", **measurements))

                self.assertEqual(response.status_code, 400)

    def test_still_rejects_unknown_top_level_fields(self):
        response = self.post_reading(reading_payload("event-unknown-field-1", spo2=98.0))

        self.assertEqual(response.status_code, 400)


class AlertsApiTestCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.calf_device_id = str(uuid.uuid4())
        self.app.extensions["iam_authentication_service"].register_device(
            self.calf_device_id, "calf-test-key"
        )
        DeviceAssignmentRepository().save(DeviceAssignment(self.calf_device_id, None, "Calf"))
        MonitoringRuleSetRepository().replace(
            MonitoringRuleSet(
                uuid.uuid4(),
                4,
                (
                    Threshold(
                        uuid.uuid4(),
                        "Calf",
                        MonitoredMetric.BODY_TEMPERATURE,
                        37.0,
                        39.5,
                        AlertSeverity.WARNING,
                    ),
                ),
                (),
            )
        )

    def test_raises_alert_once_and_deduplicates_within_the_window(self):
        first = self.post_reading(
            reading_payload("calf-event-1", device_id=self.calf_device_id, body_temperature=40.2),
            api_key="calf-test-key",
        )
        second = self.post_reading(
            reading_payload("calf-event-2", device_id=self.calf_device_id, body_temperature=40.4),
            api_key="calf-test-key",
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(len(first.json["alerts"]), 1)
        alert = first.json["alerts"][0]
        self.assertEqual(alert["type"], "Temperature")
        self.assertEqual(alert["severity"], "Warning")
        self.assertEqual(alert["configuration_revision"], 4)
        self.assertEqual(alert["reading_ids"], [first.json["reading"]["reading_id"]])
        self.assertEqual(second.status_code, 201)
        self.assertEqual(second.json["alerts"], [])

    def test_lists_latest_alerts_for_the_authenticated_device(self):
        self.post_reading(
            reading_payload("calf-list-1", device_id=self.calf_device_id, body_temperature=40.2),
            api_key="calf-test-key",
        )

        response = self.client.get(
            f"/api/v1/monitoring/alerts?device_id={self.calf_device_id}&limit=5",
            headers={"X-API-Key": "calf-test-key"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.calf_device_id, [alert["device_id"] for alert in response.json["alerts"]])
        raised_times = [alert["raised_at"] for alert in response.json["alerts"]]
        self.assertEqual(raised_times, sorted(raised_times, reverse=True))

    def test_failed_temperature_sensor_raises_no_alert_even_with_a_rule(self):
        response = self.post_reading(
            reading_payload(
                "calf-sensor-fault-1",
                device_id=self.calf_device_id,
                body_temperature=None,
                invalid_reasons={"body_temperature": "Sensor did not respond"},
            ),
            api_key="calf-test-key",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["alerts"], [])

    def test_alert_listing_requires_an_authenticated_device_and_valid_limit(self):
        url = "/api/v1/monitoring/alerts"
        display_panel_id = "bf6c7b5d-5c8a-4ba6-8fcc-6a7b8c9daeb5"
        self.assertEqual(self.client.get(url, headers={"X-API-Key": DEVICE_KEY}).status_code, 401)
        self.assertEqual(self.client.get(f"{url}?device_id={MILKING_COW_ID}").status_code, 401)
        self.assertEqual(
            self.client.get(
                f"{url}?device_id={MILKING_COW_ID}", headers={"X-API-Key": "invalid-key"}
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.get(
                f"{url}?device_id={uuid.uuid4()}", headers={"X-API-Key": DEVICE_KEY}
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.get(
                f"{url}?device_id={display_panel_id}", headers={"X-API-Key": DEVICE_KEY}
            ).status_code,
            200,
        )
        for limit in ("0", "101", "abc"):
            with self.subTest(limit=limit):
                response = self.client.get(
                    f"{url}?device_id={MILKING_COW_ID}&limit={limit}",
                    headers={"X-API-Key": DEVICE_KEY},
                )
                self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
