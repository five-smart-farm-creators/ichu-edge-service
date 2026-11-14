import io
import os
import random
import tempfile
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from unittest.mock import patch
from urllib.parse import urlsplit

import requests
from requests.adapters import BaseAdapter

from monitoring.domain.entities import MonitoringRuleSet
from monitoring.domain.value_objects import AlertSeverity, DeviceAssignment, MonitoredMetric, Threshold
from monitoring.infrastructure.repositories import DeviceAssignmentRepository, MonitoringRuleSetRepository
from scripts.simulate_collar import MODES, CollarRejected, build_reading, main, send_reading


API_KEY = "simulator-test-key"
DEVICE_ID = "9d4a5f3b-3a6e-4f84-8daa-4e5f6a7b8c93"
URL = "http://127.0.0.1:5000/api/v1/monitoring/telemetry-readings"
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def sample_reading(mode="normal", sequence=1, seed=1):
    return build_reading(mode, DEVICE_ID, f"sim-9d4a5f3b-1-{sequence:06d}", sequence, random.Random(seed), NOW)


def inside_lot(position):
    return -16.505 <= position["latitude"] <= -16.495 and -68.105 <= position["longitude"] <= -68.095


class FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


class ScriptedSession:
    """Returns scripted answers in order; an exception instance is raised instead."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def post(self, url, json, headers, timeout):
        self.calls.append({"json": json, "headers": headers, "timeout": timeout})
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


class FlaskTransportAdapter(BaseAdapter):
    """Answers requests.Session calls from the Flask test client, so no socket is opened."""

    def __init__(self, client):
        super().__init__()
        self.client = client

    def send(self, request, **kwargs):
        flask_response = self.client.post(
            urlsplit(request.url).path, data=request.body, headers=dict(request.headers)
        )
        response = requests.Response()
        response.status_code = flask_response.status_code
        response._content = flask_response.data
        response.request = request
        response.url = request.url
        return response

    def close(self):
        """Nothing to release; the test client holds no socket."""


class BuildReadingTestCase(unittest.TestCase):
    def test_fever_temperature_is_above_the_critical_maximum(self):
        for seed in range(10):
            with self.subTest(seed=seed):
                temperature = sample_reading("fever", seed=seed)["body_temperature"]

                self.assertGreaterEqual(temperature, 40.0)
                self.assertLessEqual(temperature, 40.6)

    def test_normal_position_is_inside_the_lot(self):
        for seed in range(10):
            with self.subTest(seed=seed):
                self.assertTrue(inside_lot(sample_reading("normal", seed=seed)["position"]))

    def test_escape_position_is_outside_the_lot(self):
        for seed in range(10):
            with self.subTest(seed=seed):
                self.assertFalse(inside_lot(sample_reading("escape", seed=seed)["position"]))

    def test_sensor_failure_omits_temperature_and_pulse_and_reports_both_reasons(self):
        reading = sample_reading("sensor-failure")

        self.assertNotIn("body_temperature", reading)
        self.assertNotIn("heart_rate", reading)
        self.assertEqual(
            reading["invalid_reasons"],
            {
                "body_temperature": "Temperature sensor not responding",
                "heart_rate": "Pulse sensor without contact",
            },
        )

    def test_tachycardia_heart_rate_is_above_the_demo_maximum(self):
        for seed in range(10):
            with self.subTest(seed=seed):
                heart_rate = sample_reading("tachycardia", seed=seed)["heart_rate"]

                self.assertGreaterEqual(heart_rate, 110.0)
                self.assertLessEqual(heart_rate, 130.0)

    def test_every_mode_uses_utc_timestamps_and_battery_drops_with_the_sequence(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                first = sample_reading(mode, sequence=1)
                later = sample_reading(mode, sequence=101)

                self.assertEqual(first["recorded_at"], "2026-11-13T12:00:00Z")
                self.assertEqual(first["battery_level"], 95.0)
                self.assertEqual(later["battery_level"], 85.0)
                self.assertEqual(sample_reading(mode, sequence=10_000)["battery_level"], 5.0)

    def test_normal_fever_and_escape_send_a_heart_rate_from_55_to_75(self):
        for mode in ("normal", "fever", "escape"):
            for seed in range(10):
                with self.subTest(mode=mode, seed=seed):
                    heart_rate = sample_reading(mode, seed=seed)["heart_rate"]

                    self.assertGreaterEqual(heart_rate, 55.0)
                    self.assertLessEqual(heart_rate, 75.0)

    def test_every_mode_sends_a_methane_index_from_10_to_30(self):
        for mode in MODES:
            for seed in range(10):
                with self.subTest(mode=mode, seed=seed):
                    methane_index = sample_reading(mode, seed=seed)["methane_index"]

                    self.assertGreaterEqual(methane_index, 10.0)
                    self.assertLessEqual(methane_index, 30.0)

    def test_same_seed_gives_the_same_reading(self):
        first = build_reading("escape", DEVICE_ID, "sim-9d4a5f3b-1-000001", 1, random.Random(42), NOW)
        second = build_reading("escape", DEVICE_ID, "sim-9d4a5f3b-1-000001", 1, random.Random(42), NOW)

        self.assertEqual(first, second)


class SendReadingTestCase(unittest.TestCase):
    def test_connection_error_is_retried_with_the_same_body_and_event_id(self):
        session = ScriptedSession(
            [requests.ConnectionError("offline"), FakeResponse(201, {"reading": {}, "alerts": []})]
        )
        reading = sample_reading()
        sleeps = []

        outcome = send_reading(session, URL, API_KEY, reading, sleep=sleeps.append)

        self.assertEqual(outcome.status, 201)
        self.assertIsNone(outcome.error)
        self.assertEqual(sleeps, [1])
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(session.calls[0]["json"], session.calls[1]["json"])
        self.assertEqual(session.calls[1]["json"]["event_id"], reading["event_id"])
        self.assertEqual(session.calls[1]["headers"], {"X-API-Key": API_KEY})

    def test_created_reading_reports_its_alerts(self):
        alert = {"type": "Temperature", "severity": "Critical"}
        session = ScriptedSession([FakeResponse(201, {"reading": {}, "alerts": [alert]})])

        outcome = send_reading(session, URL, API_KEY, sample_reading("fever"), sleep=lambda seconds: None)

        self.assertEqual(outcome.alerts, ["Temperature/Critical"])

    def test_bad_request_is_a_final_rejection_without_retry(self):
        session = ScriptedSession([FakeResponse(400, {"error": "Invalid telemetry reading data"})])
        sleeps = []

        outcome = send_reading(session, URL, API_KEY, sample_reading(), sleep=sleeps.append)

        self.assertEqual(outcome.status, 400)
        self.assertEqual(outcome.error, "Invalid telemetry reading data")
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(sleeps, [])

    def test_unauthorized_raises_collar_rejected(self):
        session = ScriptedSession([FakeResponse(401, {"error": "Invalid device credentials"})])

        with self.assertRaises(CollarRejected):
            send_reading(session, URL, API_KEY, sample_reading(), sleep=lambda seconds: None)

    def test_three_server_errors_exhaust_the_attempts(self):
        session = ScriptedSession([FakeResponse(503), FakeResponse(503), FakeResponse(503)])
        sleeps = []

        outcome = send_reading(session, URL, API_KEY, sample_reading(), sleep=sleeps.append)

        self.assertEqual(outcome.status, 503)
        self.assertEqual(outcome.error, "HTTP 503")
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(sleeps, [1, 2])


class MainTestCase(unittest.TestCase):
    def run_main(self, argv, session=None, environment=None):
        if environment is None:
            environment = {"DEVICE_API_KEY": API_KEY}
        output = io.StringIO()
        with patch.dict(os.environ, environment, clear=True), redirect_stdout(output), redirect_stderr(output):
            code = main(argv, session=session)
        return code, output.getvalue()

    def test_missing_key_exits_with_status_two(self):
        code, output = self.run_main([], environment={})

        self.assertEqual(code, 2)
        self.assertIn("Set DEVICE_API_KEY or DEVELOPMENT_DEVICE_API_KEY", output)

    def test_accepted_readings_exit_zero_without_printing_the_key(self):
        created = FakeResponse(201, {"reading": {}, "alerts": []})
        session = ScriptedSession([created, created])

        code, output = self.run_main(["--count", "2", "--interval", "0", "--seed", "5"], session=session)

        self.assertEqual(code, 0)
        self.assertEqual(len(session.calls), 2)
        self.assertRegex(output, r"sim-9d4a5f3b-[0-9a-f]{8}-000001 201 alerts=-\n")
        self.assertRegex(output, r"sim-9d4a5f3b-[0-9a-f]{8}-000002 201 alerts=-\n")
        self.assertNotIn(API_KEY, output)

    def test_rejected_reading_exits_one_and_the_next_reading_is_still_sent(self):
        session = ScriptedSession(
            [FakeResponse(400, {"error": "Invalid telemetry reading data"}), FakeResponse(201, {"alerts": []})]
        )

        code, output = self.run_main(["--count", "2", "--interval", "0"], session=session)

        self.assertEqual(code, 1)
        self.assertEqual(len(session.calls), 2)
        self.assertIn("failed: Invalid telemetry reading data", output)

    def test_collar_rejected_exits_one_with_a_message_and_no_key(self):
        session = ScriptedSession([FakeResponse(401, {"error": "Invalid device credentials"})])

        code, output = self.run_main([], session=session)

        self.assertEqual(code, 1)
        self.assertIn("The edge rejected the device credentials", output)
        self.assertNotIn(API_KEY, output)


class EdgeIntegrationTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="ichu-edge-simulator-", ignore_cleanup_errors=True)
        environment = {
            "APP_ENV": "development",
            "DATABASE_PATH": os.path.join(cls.directory.name, "simulator.db"),
            "SEED_DEVELOPMENT_DATA": "true",
            "DEVELOPMENT_DEVICE_API_KEY": API_KEY,
            "CLOUD_API_BASE_URL": "",
        }
        with patch.dict(os.environ, environment, clear=True):
            from app import create_app

            cls.app = create_app()
        cls.app.testing = True
        DeviceAssignmentRepository().save(DeviceAssignment(DEVICE_ID, None, "MilkingCow"))
        MonitoringRuleSetRepository().replace(
            MonitoringRuleSet(
                uuid.uuid4(),
                1,
                (
                    Threshold(
                        uuid.uuid4(),
                        "MilkingCow",
                        MonitoredMetric.BODY_TEMPERATURE,
                        37.5,
                        39.5,
                        AlertSeverity.CRITICAL,
                    ),
                    Threshold(
                        uuid.uuid4(),
                        "MilkingCow",
                        MonitoredMetric.HEART_RATE,
                        48.0,
                        84.0,
                        AlertSeverity.WARNING,
                    ),
                ),
                (),
            )
        )

    def test_fever_reading_is_accepted_and_raises_a_critical_temperature_alert(self):
        session = requests.Session()
        session.mount("http://", FlaskTransportAdapter(self.app.test_client()))
        output = io.StringIO()

        with patch.dict(os.environ, {"DEVICE_API_KEY": API_KEY}), redirect_stdout(output):
            code = main(["--mode", "fever", "--seed", "11"], session=session)

        self.assertEqual(code, 0)
        self.assertRegex(output.getvalue(), r" 201 alerts=Temperature/Critical\n")

    def test_tachycardia_reading_is_accepted_and_raises_a_heart_rate_warning(self):
        session = requests.Session()
        session.mount("http://", FlaskTransportAdapter(self.app.test_client()))
        output = io.StringIO()

        with patch.dict(os.environ, {"DEVICE_API_KEY": API_KEY}), redirect_stdout(output):
            code = main(["--mode", "tachycardia", "--seed", "11"], session=session)

        self.assertEqual(code, 0)
        self.assertRegex(output.getvalue(), r" 201 alerts=HeartRate/Warning\n")


if __name__ == "__main__":
    unittest.main()
