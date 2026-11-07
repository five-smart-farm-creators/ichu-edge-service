"""Environment-specific application configuration for the ICHU Edge Service."""

import os
import uuid
from pathlib import Path
from typing import TypedDict
from urllib.parse import urlparse

from dotenv import dotenv_values


class AppConfig(TypedDict):
    APP_ENV: str
    DEBUG: bool
    DATABASE_PATH: str
    HOST: str
    PORT: int
    SEED_DEVELOPMENT_DATA: bool
    DEVELOPMENT_DEVICE_API_KEY: str | None
    CLOUD_API_BASE_URL: str | None
    EDGE_GATEWAY_ID: str | None
    EDGE_GATEWAY_KEY: str | None
    ALERT_DEDUPLICATION_MINUTES: int
    SYNC_BATCH_SIZE: int
    CLOUD_TIMEOUT_SECONDS: int


def _as_bool(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _as_int(name: str, value: str, minimum: int, maximum: int) -> int:
    try:
        number = int(value.strip())
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if not minimum <= number <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return number


def _optional_uuid(name: str, value: str) -> str | None:
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return str(uuid.UUID(normalized))
    except ValueError as error:
        raise ValueError(f"{name} must be a UUID") from error


def _optional_base_url(value: str) -> str | None:
    normalized = value.strip().rstrip("/")
    if not normalized:
        return None
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("CLOUD_API_BASE_URL must be an http or https URL")
    return normalized


def load_config(project_root: Path) -> AppConfig:
    """Load the selected dotenv file, then apply process environment overrides."""
    environment_name = os.getenv("APP_ENV", "development").strip().lower()
    if environment_name not in {"development", "production"}:
        raise ValueError("APP_ENV must be 'development' or 'production'")

    values = {
        **{
            key: value
            for key, value in dotenv_values(project_root / f".env.{environment_name}").items()
            if value is not None
        },
        **os.environ,
    }

    configured_environment = _required(values, "APP_ENV")
    if configured_environment.strip().lower() != environment_name:
        raise ValueError(
            f"APP_ENV in .env.{environment_name} must be '{environment_name}'"
        )

    debug = _as_bool("DEBUG", _required(values, "DEBUG"))
    seed_development_data = _as_bool(
        "SEED_DEVELOPMENT_DATA", _required(values, "SEED_DEVELOPMENT_DATA")
    )
    if environment_name == "production" and debug:
        raise ValueError("DEBUG must be false in production")
    if environment_name == "production" and seed_development_data:
        raise ValueError("SEED_DEVELOPMENT_DATA must be false in production")

    database_path = _required(values, "DATABASE_PATH")
    if not database_path.strip():
        raise ValueError("DATABASE_PATH cannot be empty")
    database = Path(database_path).expanduser()
    if not database.is_absolute():
        database = project_root / database

    host = _required(values, "HOST").strip()
    if not host:
        raise ValueError("HOST cannot be empty")
    port = _as_int("PORT", _required(values, "PORT"), 1, 65535)

    device_api_key = _required(values, "DEVELOPMENT_DEVICE_API_KEY").strip() or None
    if seed_development_data and not device_api_key:
        raise ValueError("DEVELOPMENT_DEVICE_API_KEY is required when seeding is enabled")

    cloud_api_base_url = _optional_base_url(_required(values, "CLOUD_API_BASE_URL"))
    gateway_id = _optional_uuid("EDGE_GATEWAY_ID", _required(values, "EDGE_GATEWAY_ID"))
    gateway_key = _required(values, "EDGE_GATEWAY_KEY").strip() or None
    if cloud_api_base_url is not None:
        if gateway_id is None:
            raise ValueError("EDGE_GATEWAY_ID is required when CLOUD_API_BASE_URL is set")
        if gateway_key is None:
            raise ValueError("EDGE_GATEWAY_KEY is required when CLOUD_API_BASE_URL is set")

    return {
        "APP_ENV": environment_name,
        "DEBUG": debug,
        "DATABASE_PATH": str(database),
        "HOST": host,
        "PORT": port,
        "SEED_DEVELOPMENT_DATA": seed_development_data,
        "DEVELOPMENT_DEVICE_API_KEY": device_api_key,
        "CLOUD_API_BASE_URL": cloud_api_base_url,
        "EDGE_GATEWAY_ID": gateway_id,
        "EDGE_GATEWAY_KEY": gateway_key,
        "ALERT_DEDUPLICATION_MINUTES": _as_int(
            "ALERT_DEDUPLICATION_MINUTES",
            _required(values, "ALERT_DEDUPLICATION_MINUTES"),
            1,
            1440,
        ),
        "SYNC_BATCH_SIZE": _as_int(
            "SYNC_BATCH_SIZE", _required(values, "SYNC_BATCH_SIZE"), 1, 500
        ),
        "CLOUD_TIMEOUT_SECONDS": _as_int(
            "CLOUD_TIMEOUT_SECONDS", _required(values, "CLOUD_TIMEOUT_SECONDS"), 1, 60
        ),
    }


def _required(values: dict[str, str], name: str) -> str:
    """Return a configured value or report the missing environment setting."""
    try:
        return values[name]
    except KeyError as error:
        raise ValueError(f"{name} must be set in the selected dotenv file or environment") from error
