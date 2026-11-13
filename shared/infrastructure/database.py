"""Shared database infrastructure for the ICHU Edge Service."""

from pathlib import Path

from peewee import SqliteDatabase

# WAL lets the API keep reading while the synchronizer process writes.
db = SqliteDatabase(
    None, timeout=30, pragmas={"journal_mode": "wal", "busy_timeout": 30_000}
)


def configure_database(database_path: str) -> None:
    """Configure the shared database before the application is created."""
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db.init(str(path))


def init_db() -> None:
    """Open the database connection and create all required tables."""
    if db.is_closed():
        db.connect()
    from iam.infrastructure.models import Device
    from monitoring.infrastructure.models import (
        DeviceAssignmentModel,
        GeofenceModel,
        MonitoringAlertModel,
        MonitoringRuleSetModel,
        TelemetryReadingModel,
        ThresholdModel,
    )

    db.create_tables(
        [
            Device,
            TelemetryReadingModel,
            MonitoringAlertModel,
            MonitoringRuleSetModel,
            ThresholdModel,
            GeofenceModel,
            DeviceAssignmentModel,
        ],
        safe=True,
    )
