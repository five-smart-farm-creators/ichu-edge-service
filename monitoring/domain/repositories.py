"""Repository abstractions for Monitoring domain aggregates."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from monitoring.domain.entities import MonitoringAlert, MonitoringRuleSet, TelemetryReading
from monitoring.domain.value_objects import AlertType, DeviceAssignment


@dataclass(frozen=True)
class ReadingSaveResult:
    """Persisted reading and whether this call created the telemetry event."""

    reading: TelemetryReading
    created: bool


class TelemetryReadingRepository(Protocol):
    """Persistence contract for telemetry readings."""

    def save_idempotently(self, reading: TelemetryReading) -> ReadingSaveResult:
        """Persist a reading or return its matching prior submission."""
        ...

    def find_pending(self, limit: int) -> list[TelemetryReading]:
        """Return up to ``limit`` readings not yet synchronized, oldest first."""
        ...

    def update_sync_status(self, reading: TelemetryReading) -> TelemetryReading:
        """Persist the synchronization state of a reading."""
        ...

    def count_pending(self) -> int:
        """Return the number of readings not yet synchronized."""
        ...


class MonitoringAlertRepository(Protocol):
    """Persistence contract for locally raised alerts."""

    def save(self, alert: MonitoringAlert) -> MonitoringAlert:
        """Persist a newly raised alert."""
        ...

    def find_recent(
        self, device_id: str, alert_type: AlertType, since: datetime
    ) -> MonitoringAlert | None:
        """Return the newest alert of a device and type raised at or after ``since``."""
        ...

    def find_pending(self, limit: int) -> list[MonitoringAlert]:
        """Return up to ``limit`` alerts not yet synchronized, oldest first."""
        ...

    def find_latest(self, limit: int) -> list[MonitoringAlert]:
        """Return up to ``limit`` alerts, newest first."""
        ...

    def update_sync_status(self, alert: MonitoringAlert) -> MonitoringAlert:
        """Persist the synchronization state of an alert."""
        ...

    def count_pending(self) -> int:
        """Return the number of alerts not yet synchronized."""
        ...


class MonitoringRuleSetRepository(Protocol):
    """Persistence contract for the cached monitoring rules."""

    def get(self) -> MonitoringRuleSet:
        """Return the cached rules, or the empty rule set when none are cached."""
        ...

    def replace(self, rule_set: MonitoringRuleSet) -> MonitoringRuleSet:
        """Replace the cached rules with a newer rule set."""
        ...


class DeviceAssignmentRepository(Protocol):
    """Persistence contract for device lot and life-stage assignments."""

    def find(self, device_id: str) -> DeviceAssignment | None:
        """Return the assignment of a device, if one is configured."""
        ...

    def save(self, assignment: DeviceAssignment) -> DeviceAssignment:
        """Insert or update the assignment of a device."""
        ...
