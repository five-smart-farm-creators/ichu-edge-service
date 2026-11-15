import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import load_config


GATEWAY_KEY = "c07d8c6e-6d9b-4cb7-9add-7b8c9daebfc6.gateway-test-key"

_ENV_FILE_VALUES = {
    "development": {
        "APP_ENV": "development",
        "DEBUG": "true",
        "DATABASE_PATH": "ichu_edge.db",
        "HOST": "127.0.0.1",
        "PORT": "5000",
        "SEED_DEVELOPMENT_DATA": "false",
        "DEVELOPMENT_DEVICE_API_KEY": "",
        "CLOUD_API_BASE_URL": "",
        "EDGE_GATEWAY_KEY": "",
        "ALERT_DEDUPLICATION_MINUTES": "15",
        "SYNC_BATCH_SIZE": "100",
        "SYNC_INTERVAL_SECONDS": "15",
        "CLOUD_TIMEOUT_SECONDS": "10",
    },
    "production": {
        "APP_ENV": "production",
        "DEBUG": "false",
        "DATABASE_PATH": "var/ichu_edge.db",
        "HOST": "0.0.0.0",
        "PORT": "5000",
        "SEED_DEVELOPMENT_DATA": "false",
        "DEVELOPMENT_DEVICE_API_KEY": "",
        "CLOUD_API_BASE_URL": "",
        "EDGE_GATEWAY_KEY": "",
        "ALERT_DEDUPLICATION_MINUTES": "15",
        "SYNC_BATCH_SIZE": "100",
        "SYNC_INTERVAL_SECONDS": "15",
        "CLOUD_TIMEOUT_SECONDS": "10",
    },
}


def write_environment_file(root, environment, **overrides):
    values = {**_ENV_FILE_VALUES[environment], **overrides}
    (root / f".env.{environment}").write_text(
        "\n".join(f"{name}={value}" for name, value in values.items()) + "\n",
        encoding="utf-8",
    )


class LoadConfigTestCase(unittest.TestCase):
    def test_loads_selected_dotenv_and_process_environment_overrides_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_environment_file(root, "development", DATABASE_PATH="local.db", PORT="4000")
            with patch.dict(
                os.environ,
                {
                    "APP_ENV": "development",
                    "DATABASE_PATH": "override.db",
                    "EDGE_GATEWAY_KEY": GATEWAY_KEY,
                },
                clear=True,
            ):
                config = load_config(root)

        self.assertTrue(config["DEBUG"])
        self.assertEqual(config["PORT"], 4000)
        self.assertEqual(config["DATABASE_PATH"], str(root / "override.db"))
        self.assertIsNone(config["CLOUD_API_BASE_URL"])
        self.assertEqual(config["EDGE_GATEWAY_ID"], "c07d8c6e-6d9b-4cb7-9add-7b8c9daebfc6")
        self.assertEqual(config["SYNC_BATCH_SIZE"], 100)
        self.assertEqual(config["SYNC_INTERVAL_SECONDS"], 15)

    def test_production_defaults_disable_debug_seeding_and_cloud_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_environment_file(root, "production")
            with patch.dict(os.environ, {"APP_ENV": "production"}, clear=True):
                config = load_config(root)

        self.assertFalse(config["DEBUG"])
        self.assertFalse(config["SEED_DEVELOPMENT_DATA"])
        self.assertIsNone(config["DEVELOPMENT_DEVICE_API_KEY"])
        self.assertIsNone(config["CLOUD_API_BASE_URL"])
        self.assertIsNone(config["EDGE_GATEWAY_ID"])
        self.assertEqual(config["DATABASE_PATH"], str(root / "var/ichu_edge.db"))

    def test_loading_another_environment_does_not_reuse_dotenv_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_environment_file(root, "development", EDGE_GATEWAY_KEY=GATEWAY_KEY)
            write_environment_file(root, "production")
            with patch.dict(os.environ, {}, clear=True):
                os.environ["APP_ENV"] = "development"
                development = load_config(root)
                os.environ["APP_ENV"] = "production"
                production = load_config(root)

        self.assertTrue(development["DEBUG"])
        self.assertFalse(production["DEBUG"])

    def test_requires_settings_to_be_defined_in_file_or_process_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env.development").write_text("APP_ENV=development\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(
                ValueError, "DEBUG must be set"
            ):
                load_config(root)

    def test_rejects_debug_or_seeding_in_production(self):
        for setting in (
            {"DEBUG": "true"},
            {"SEED_DEVELOPMENT_DATA": "true", "DEVELOPMENT_DEVICE_API_KEY": "local-secret"},
        ):
            with self.subTest(setting=setting), tempfile.TemporaryDirectory() as directory:
                write_environment_file(Path(directory), "production")
                with patch.dict(
                    os.environ, {"APP_ENV": "production", **setting}, clear=True
                ), self.assertRaises(ValueError):
                    load_config(Path(directory))

    def test_requires_key_for_development_data_seeding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_environment_file(root, "development", EDGE_GATEWAY_KEY=GATEWAY_KEY)
            with patch.dict(
                os.environ,
                {"APP_ENV": "development", "SEED_DEVELOPMENT_DATA": "true"},
                clear=True,
            ), self.assertRaisesRegex(ValueError, "DEVELOPMENT_DEVICE_API_KEY"):
                load_config(root)

    def test_requires_gateway_key_when_cloud_url_is_set(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_environment_file(root, "development")
            with patch.dict(
                os.environ,
                {"APP_ENV": "development", "CLOUD_API_BASE_URL": "http://localhost:5099"},
                clear=True,
            ), self.assertRaisesRegex(
                ValueError, "EDGE_GATEWAY_KEY"
            ):
                load_config(root)

    def test_gateway_id_is_read_from_the_key_in_lowercase(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_environment_file(root, "development")
            with patch.dict(
                os.environ,
                {
                    "APP_ENV": "development",
                    "EDGE_GATEWAY_KEY": "C07D8C6E-6D9B-4CB7-9ADD-7B8C9DAEBFC6.gateway-test-key",
                },
                clear=True,
            ):
                config = load_config(root)

        self.assertEqual(config["EDGE_GATEWAY_ID"], "c07d8c6e-6d9b-4cb7-9add-7b8c9daebfc6")

    def test_rejects_malformed_gateway_keys(self):
        for key in (
            "gateway-test-key",
            "not-a-uuid.secret",
            "c07d8c6e-6d9b-4cb7-9add-7b8c9daebfc6.",
            "c07d8c6e-6d9b-4cb7-9add-7b8c9daebfc6.   ",
        ):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                write_environment_file(root, "development")
                with patch.dict(
                    os.environ, {"APP_ENV": "development", "EDGE_GATEWAY_KEY": key}, clear=True
                ), self.assertRaisesRegex(ValueError, "EDGE_GATEWAY_KEY"):
                    load_config(root)

    def test_development_profile_disables_cloud_synchronization_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_environment_file(root, "development")
            with patch.dict(os.environ, {"APP_ENV": "development"}, clear=True):
                config = load_config(root)

        self.assertIsNone(config["CLOUD_API_BASE_URL"])
        self.assertIsNone(config["EDGE_GATEWAY_KEY"])

    def test_cloud_synchronization_is_enabled_by_url_and_gateway_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_environment_file(root, "development")
            with patch.dict(
                os.environ,
                {
                    "APP_ENV": "development",
                    "CLOUD_API_BASE_URL": "http://localhost:5099/",
                    "EDGE_GATEWAY_KEY": GATEWAY_KEY,
                },
                clear=True,
            ):
                config = load_config(root)

        self.assertEqual(config["CLOUD_API_BASE_URL"], "http://localhost:5099")
        self.assertEqual(config["EDGE_GATEWAY_ID"], "c07d8c6e-6d9b-4cb7-9add-7b8c9daebfc6")
        self.assertEqual(config["EDGE_GATEWAY_KEY"], GATEWAY_KEY)

    def test_development_accepts_plain_http_cloud_url_on_local_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_environment_file(root, "development")
            with patch.dict(
                os.environ,
                {
                    "APP_ENV": "development",
                    "CLOUD_API_BASE_URL": "http://192.168.1.20:5099",
                    "EDGE_GATEWAY_KEY": GATEWAY_KEY,
                },
                clear=True,
            ):
                config = load_config(root)

        self.assertEqual(config["CLOUD_API_BASE_URL"], "http://192.168.1.20:5099")

    def test_production_accepts_https_and_localhost_cloud_urls(self):
        for url in ("https://ichu-web-service.onrender.com", "http://localhost:5099"):
            with self.subTest(url=url), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                write_environment_file(root, "production")
                with patch.dict(
                    os.environ,
                    {
                        "APP_ENV": "production",
                        "CLOUD_API_BASE_URL": url,
                        "EDGE_GATEWAY_KEY": GATEWAY_KEY,
                    },
                    clear=True,
                ):
                    config = load_config(root)

                self.assertEqual(config["CLOUD_API_BASE_URL"], url)

    def test_production_rejects_plain_http_cloud_url(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_environment_file(root, "production")
            with patch.dict(
                os.environ,
                {
                    "APP_ENV": "production",
                    "CLOUD_API_BASE_URL": "http://ichu.example.com",
                    "EDGE_GATEWAY_KEY": GATEWAY_KEY,
                },
                clear=True,
            ), self.assertRaisesRegex(ValueError, "must use https in production"):
                load_config(root)

    def test_rejects_dotenv_file_declaring_another_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_environment_file(root, "development", APP_ENV="production", EDGE_GATEWAY_KEY=GATEWAY_KEY)
            with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(
                ValueError, "APP_ENV in .env.development must be 'development'"
            ):
                load_config(root)

    def test_rejects_invalid_values(self):
        invalid_files = (
            {"DEBUG": "sometimes"},
            {"PORT": "70000"},
            {"ALERT_DEDUPLICATION_MINUTES": "0"},
            {"SYNC_BATCH_SIZE": "501"},
            {"SYNC_INTERVAL_SECONDS": "4"},
            {"SYNC_INTERVAL_SECONDS": "3601"},
            {"CLOUD_TIMEOUT_SECONDS": "61"},
            {"CLOUD_API_BASE_URL": "ftp://example.org"},
        )
        for overrides in invalid_files:
            with self.subTest(overrides=overrides), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                write_environment_file(root, "development", EDGE_GATEWAY_KEY=GATEWAY_KEY, **overrides)
                with patch.dict(os.environ, {"APP_ENV": "development"}, clear=True), self.assertRaises(
                    ValueError
                ):
                    load_config(root)


if __name__ == "__main__":
    unittest.main()
