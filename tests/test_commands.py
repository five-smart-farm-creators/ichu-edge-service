import os
import tempfile
import unittest
import uuid
from unittest.mock import patch

import click

from monitoring.application.errors import (
    CloudCredentialsRejected,
    CloudUnavailable,
    SynchronizationDisabled,
)
from monitoring.application.services import SynchronizationResult
from monitoring.interfaces.commands import next_delay_seconds, run_synchronizer


class FakeSynchronizationService:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)

    def synchronize(self):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def cycle_result(sent_readings=0, sent_alerts=0):
    return SynchronizationResult(False, 0, sent_readings, sent_alerts, sent_readings, 0, 0, 0)


class NextDelaySecondsTestCase(unittest.TestCase):
    def test_delay_doubles_with_each_failure_and_stops_at_five_minutes(self):
        self.assertEqual(next_delay_seconds(15, 0), 15)
        self.assertEqual(next_delay_seconds(15, 1), 30)
        self.assertEqual(next_delay_seconds(15, 2), 60)
        self.assertEqual(next_delay_seconds(15, 10), 300)


class RunSynchronizerTestCase(unittest.TestCase):
    def test_backs_off_while_the_cloud_is_down_and_skips_the_sleep_after_a_full_batch(self):
        service = FakeSynchronizationService(
            [
                CloudUnavailable("offline"),
                CloudUnavailable("offline"),
                cycle_result(sent_readings=100),
                cycle_result(sent_readings=3),
            ]
        )
        delays = []

        run_synchronizer(service, 15, 100, sleep=delays.append, cycles=4)

        self.assertEqual(delays, [30, 60, 15])

    def test_full_batch_that_settles_nothing_waits_for_the_interval(self):
        unanswered = SynchronizationResult(False, 0, 100, 0, 0, 0, 0, 0)
        service = FakeSynchronizationService([unanswered])
        delays = []

        run_synchronizer(service, 15, 100, sleep=delays.append, cycles=1)

        self.assertEqual(delays, [15])

    def test_rejected_credentials_back_off_like_an_outage(self):
        service = FakeSynchronizationService([CloudCredentialsRejected(), cycle_result(sent_readings=1)])
        delays = []

        run_synchronizer(service, 15, 100, sleep=delays.append, cycles=2)

        self.assertEqual(delays, [30, 15])

    def test_disabled_synchronization_stops_with_a_command_error(self):
        service = FakeSynchronizationService([SynchronizationDisabled()])

        with self.assertRaisesRegex(click.ClickException, "not configured"):
            run_synchronizer(service, 15, 100, sleep=lambda seconds: None, cycles=1)


class SynchronizeCommandTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="ichu-edge-commands-", ignore_cleanup_errors=True)
        environment = {
            "APP_ENV": "development",
            "DATABASE_PATH": os.path.join(cls.directory.name, "commands.db"),
            "SEED_DEVELOPMENT_DATA": "false",
            "CLOUD_API_BASE_URL": "",
        }
        with patch.dict(os.environ, environment, clear=True):
            from app import create_app

            cls.app = create_app()
        cls.app.testing = True

    def invoke_with(self, service, *args):
        original = self.app.extensions["synchronization_service"]
        self.app.extensions["synchronization_service"] = service
        try:
            return self.app.test_cli_runner().invoke(args=["synchronize", *args])
        finally:
            self.app.extensions["synchronization_service"] = original

    def test_once_reports_missing_cloud_configuration_with_a_failing_exit_code(self):
        result = self.app.test_cli_runner().invoke(args=["synchronize", "--once"])

        self.assertEqual(result.exit_code, 1)
        self.assertIn("Cloud synchronization is not configured", result.output)

    def test_once_exits_with_failure_when_the_cloud_is_unavailable(self):
        result = self.invoke_with(FakeSynchronizationService([CloudUnavailable("offline")]), "--once")

        self.assertEqual(result.exit_code, 1)
        self.assertIn("records remain pending", result.output)

    def test_once_exits_successfully_after_one_cycle(self):
        result = self.invoke_with(FakeSynchronizationService([cycle_result(sent_readings=1)]), "--once")

        self.assertEqual(result.exit_code, 0)

    def test_interrupt_ends_the_loop_with_exit_code_zero(self):
        result = self.invoke_with(FakeSynchronizationService([KeyboardInterrupt()]))

        self.assertEqual(result.exit_code, 0)


class RegisterDeviceCommandTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="ichu-edge-register-", ignore_cleanup_errors=True)
        environment = {
            "APP_ENV": "development",
            "DATABASE_PATH": os.path.join(cls.directory.name, "register.db"),
            "SEED_DEVELOPMENT_DATA": "false",
            "CLOUD_API_BASE_URL": "",
        }
        with patch.dict(os.environ, environment, clear=True):
            from app import create_app

            cls.app = create_app()
        cls.app.testing = True

    def test_registers_a_collar_once_with_the_key_from_the_environment(self):
        device_id = str(uuid.uuid4())
        with patch.dict(os.environ, {"DEVICE_API_KEY": "collar-test-key"}):
            first = self.app.test_cli_runner().invoke(args=["register-device", device_id])
            second = self.app.test_cli_runner().invoke(args=["register-device", device_id])

        self.assertEqual(first.exit_code, 0)
        self.assertEqual(first.output, f"Device {device_id} registered\n")
        self.assertEqual(second.exit_code, 0)
        self.assertEqual(second.output, f"Device {device_id} already registered\n")
        self.assertNotIn("collar-test-key", first.output + second.output)
        self.assertTrue(
            self.app.extensions["iam_authentication_service"].authenticate(device_id, "collar-test-key")
        )

    def test_missing_device_api_key_is_a_command_error(self):
        with patch.dict(os.environ):
            os.environ.pop("DEVICE_API_KEY", None)
            result = self.app.test_cli_runner().invoke(args=["register-device", str(uuid.uuid4())])

        self.assertEqual(result.exit_code, 1)
        self.assertIn("DEVICE_API_KEY must be set", result.output)

    def test_device_identifier_must_be_a_uuid(self):
        with patch.dict(os.environ, {"DEVICE_API_KEY": "collar-test-key"}):
            result = self.app.test_cli_runner().invoke(args=["register-device", "band-42"])

        self.assertEqual(result.exit_code, 2)
        self.assertIn("is not a valid UUID", result.output)


if __name__ == "__main__":
    unittest.main()
