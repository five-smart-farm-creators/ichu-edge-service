"""Domain entities for the Monitoring bounded context."""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from monitoring.domain.value_objects import (
    ActivityIndex,
    AlertSeverity,
    AlertType,
    BatteryLevel,
    BodyTemperature,
    Geofence,
    GeoPosition,
    HeartRate,
    InvalidMeasurementReasons,
    MethaneIndex,
    RecordedAt,
    SyncStatus,
    TelemetryEventId,
    Threshold,
    canonical_device_identifier,
)


def _check_rejection(sync_status: SyncStatus, rejection_reason: str | None) -> None:
    """Require a rejection reason exactly when a record was rejected by the cloud."""
    if sync_status is SyncStatus.REJECTED:
        if not isinstance(rejection_reason, str) or not rejection_reason.strip():
            raise ValueError("Rejected records require a rejection reason")
    elif rejection_reason is not None:
        raise ValueError("Only rejected records carry a rejection reason")


def _check_sync_attempts(sync_attempts: int) -> None:
    """Require a count of failed synchronization attempts that is a non-negative integer."""
    if isinstance(sync_attempts, bool) or not isinstance(sync_attempts, int) or sync_attempts < 0:
        raise ValueError("Synchronization attempts must be a non-negative integer")


@dataclass(frozen=True)
class SubmittingDevice:
    """Monitoring's local representation of a device allowed to submit telemetry."""

    device_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "device_id", canonical_device_identifier(self.device_id)
        )


@dataclass(frozen=True)
class TelemetryReading:
    """A valid immutable telemetry reading from one cattle band."""

    reading_id: uuid.UUID
    submitting_device: SubmittingDevice
    event_id: TelemetryEventId
    recorded_at: RecordedAt
    timestamp_supplied: bool
    body_temperature: BodyTemperature | None = None
    activity_index: ActivityIndex | None = None
    battery_level: BatteryLevel | None = None
    position: GeoPosition | None = None
    invalid_reasons: InvalidMeasurementReasons = InvalidMeasurementReasons()
    heart_rate: HeartRate | None = None
    methane_index: MethaneIndex | None = None
    sync_status: SyncStatus = SyncStatus.PENDING
    rejection_reason: str | None = None
    sync_attempts: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.reading_id, uuid.UUID):
            raise TypeError("Reading identifier must be a UUID")
        if not isinstance(self.submitting_device, SubmittingDevice):
            raise TypeError("TelemetryReading requires a SubmittingDevice")
        if not isinstance(self.event_id, TelemetryEventId):
            raise TypeError("TelemetryReading requires a TelemetryEventId")
        if not isinstance(self.recorded_at, RecordedAt):
            raise TypeError("TelemetryReading requires a RecordedAt value")
        if not isinstance(self.timestamp_supplied, bool):
            raise TypeError("TelemetryReading timestamp_supplied must be a boolean")
        if self.body_temperature is not None and not isinstance(
            self.body_temperature, BodyTemperature
        ):
            raise TypeError("Body temperature must be a BodyTemperature value")
        if self.activity_index is not None and not isinstance(
            self.activity_index, ActivityIndex
        ):
            raise TypeError("Activity index must be an ActivityIndex value")
        if self.battery_level is not None and not isinstance(
            self.battery_level, BatteryLevel
        ):
            raise TypeError("Battery level must be a BatteryLevel value")
        if self.position is not None and not isinstance(self.position, GeoPosition):
            raise TypeError("Position must be a GeoPosition value")
        if not isinstance(self.invalid_reasons, InvalidMeasurementReasons):
            raise TypeError("Invalid reasons must be an InvalidMeasurementReasons value")
        if self.heart_rate is not None and not isinstance(self.heart_rate, HeartRate):
            raise TypeError("Heart rate must be a HeartRate value")
        if self.methane_index is not None and not isinstance(self.methane_index, MethaneIndex):
            raise TypeError("Methane index must be a MethaneIndex value")
        measurements = {
            "body_temperature": self.body_temperature,
            "activity_index": self.activity_index,
            "battery_level": self.battery_level,
            "position": self.position,
            "heart_rate": self.heart_rate,
            "methane_index": self.methane_index,
        }
        if any(measurements[field] is not None for field in self.invalid_reasons.fields()):
            raise ValueError("A measurement cannot be both captured and invalid")
        if not self.invalid_reasons and all(value is None for value in measurements.values()):
            raise ValueError(
                "Telemetry reading requires at least one measurement value or invalid reason"
            )
        if not isinstance(self.sync_status, SyncStatus):
            raise TypeError("TelemetryReading sync status must be a SyncStatus")
        _check_rejection(self.sync_status, self.rejection_reason)
        _check_sync_attempts(self.sync_attempts)

    @classmethod
    def record(
        cls,
        submitting_device: SubmittingDevice,
        event_id: str,
        recorded_at: datetime | None,
        body_temperature: float | None,
        activity_index: float | None,
        battery_level: float | None,
        position: GeoPosition | None,
        invalid_reasons: Mapping[str, str] | None = None,
        heart_rate: float | None = None,
        methane_index: float | None = None,
    ) -> "TelemetryReading":
        """Create a new pending reading, enforcing domain invariants."""
        return cls(
            uuid.uuid4(),
            submitting_device,
            TelemetryEventId(event_id),
            RecordedAt.from_optional(recorded_at),
            recorded_at is not None,
            None if body_temperature is None else BodyTemperature(body_temperature),
            None if activity_index is None else ActivityIndex(activity_index),
            None if battery_level is None else BatteryLevel(battery_level),
            position,
            InvalidMeasurementReasons(invalid_reasons),
            None if heart_rate is None else HeartRate(heart_rate),
            None if methane_index is None else MethaneIndex(methane_index),
        )

    @classmethod
    def reconstitute(
        cls,
        reading_id: uuid.UUID,
        submitting_device: SubmittingDevice,
        event_id: TelemetryEventId,
        recorded_at: RecordedAt,
        timestamp_supplied: bool,
        body_temperature: BodyTemperature | None,
        activity_index: ActivityIndex | None,
        battery_level: BatteryLevel | None,
        position: GeoPosition | None,
        invalid_reasons: InvalidMeasurementReasons,
        heart_rate: HeartRate | None,
        methane_index: MethaneIndex | None,
        sync_status: SyncStatus,
        rejection_reason: str | None,
        sync_attempts: int,
    ) -> "TelemetryReading":
        """Rebuild a persisted reading with its stored synchronization state."""
        return cls(
            reading_id,
            submitting_device,
            event_id,
            recorded_at,
            timestamp_supplied,
            body_temperature,
            activity_index,
            battery_level,
            position,
            invalid_reasons,
            heart_rate,
            methane_index,
            sync_status,
            rejection_reason,
            sync_attempts,
        )

    @property
    def device_id(self) -> str:
        """Return the submitting device's identifier."""
        return self.submitting_device.device_id

    def mark_synced(self) -> "TelemetryReading":
        """Return this reading as accepted or recognized by the cloud."""
        return replace(self, sync_status=SyncStatus.SYNCED, rejection_reason=None)

    def mark_rejected(self, reason: str) -> "TelemetryReading":
        """Return this reading as rejected by the cloud, with the cloud's reason."""
        return replace(self, sync_status=SyncStatus.REJECTED, rejection_reason=reason)

    def defer_retry(self, reason: str, max_attempts: int) -> "TelemetryReading":
        """Keep the record pending for another attempt, rejecting it once the attempts run out."""
        if max_attempts < 1:
            raise ValueError("Maximum synchronization attempts must be at least one")
        attempts = self.sync_attempts + 1
        if attempts >= max_attempts:
            return replace(
                self,
                sync_status=SyncStatus.REJECTED,
                rejection_reason=reason,
                sync_attempts=attempts,
            )
        return replace(self, sync_attempts=attempts)

    def same_telemetry_as(self, other: "TelemetryReading") -> bool:
        """Return whether two submissions carry the same telemetry; time counts only when device-supplied."""
        if self.timestamp_supplied != other.timestamp_supplied:
            return False
        if self.timestamp_supplied and self.recorded_at != other.recorded_at:
            return False
        return (
            self.body_temperature,
            self.activity_index,
            self.battery_level,
            self.position,
            self.invalid_reasons,
            self.heart_rate,
            self.methane_index,
        ) == (
            other.body_temperature,
            other.activity_index,
            other.battery_level,
            other.position,
            other.invalid_reasons,
            other.heart_rate,
            other.methane_index,
        )


@dataclass(frozen=True)
class MonitoringAlert:
    """A local alert raised when a reading violates a cached rule."""

    alert_id: uuid.UUID
    device_id: str
    type: AlertType
    severity: AlertSeverity
    reading_ids: tuple[uuid.UUID, ...]
    configuration_revision: int
    raised_at: datetime
    threshold_min_value: float | None = None
    threshold_max_value: float | None = None
    geofence_id: uuid.UUID | None = None
    distance_to_boundary_meters: float | None = None
    sync_status: SyncStatus = SyncStatus.PENDING
    rejection_reason: str | None = None
    sync_attempts: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.alert_id, uuid.UUID):
            raise TypeError("Alert identifier must be a UUID")
        object.__setattr__(
            self, "device_id", canonical_device_identifier(self.device_id)
        )
        if not isinstance(self.type, AlertType):
            raise TypeError("Alert type must be an AlertType")
        if not isinstance(self.severity, AlertSeverity):
            raise TypeError("Alert severity must be an AlertSeverity")
        reading_ids = tuple(self.reading_ids)
        if not reading_ids or not all(isinstance(item, uuid.UUID) for item in reading_ids):
            raise ValueError("Alert requires at least one reading identifier")
        object.__setattr__(self, "reading_ids", reading_ids)
        if (
            isinstance(self.configuration_revision, bool)
            or not isinstance(self.configuration_revision, int)
            or self.configuration_revision < 0
        ):
            raise ValueError("Configuration revision must be a non-negative integer")
        if not isinstance(self.raised_at, datetime):
            raise TypeError("Alert raise time must be a datetime")
        if self.raised_at.tzinfo is None or self.raised_at.utcoffset() is None:
            raise ValueError("Alert raise time must include a timezone")
        object.__setattr__(self, "raised_at", self.raised_at.astimezone(UTC))
        self._check_type_specific_fields()
        _check_rejection(self.sync_status, self.rejection_reason)
        _check_sync_attempts(self.sync_attempts)

    def _check_type_specific_fields(self) -> None:
        """Enforce which threshold or geofence fields each alert type carries."""
        if self.type in (AlertType.TEMPERATURE, AlertType.ACTIVITY, AlertType.HEART_RATE):
            if self.threshold_min_value is None or self.threshold_max_value is None:
                raise ValueError("Threshold alerts require threshold minimum and maximum")
            if self.threshold_min_value >= self.threshold_max_value:
                raise ValueError("Threshold minimum must be lower than its maximum")
            if self.geofence_id is not None or self.distance_to_boundary_meters is not None:
                raise ValueError("Threshold alerts cannot carry geofence fields")
            return
        if self.geofence_id is None or self.distance_to_boundary_meters is None:
            raise ValueError("Geofence alerts require a geofence and a boundary distance")
        if self.threshold_min_value is not None or self.threshold_max_value is not None:
            raise ValueError("Geofence alerts cannot carry threshold fields")
        if self.distance_to_boundary_meters < 0:
            raise ValueError("Boundary distance cannot be negative")

    @classmethod
    def raise_for(
        cls,
        device_id: str,
        alert_type: AlertType,
        severity: AlertSeverity,
        reading_ids: tuple[uuid.UUID, ...],
        configuration_revision: int,
        raised_at: datetime,
        threshold_min_value: float | None = None,
        threshold_max_value: float | None = None,
        geofence_id: uuid.UUID | None = None,
        distance_to_boundary_meters: float | None = None,
    ) -> "MonitoringAlert":
        """Create a pending alert for a rule violation."""
        return cls(
            uuid.uuid4(),
            device_id,
            alert_type,
            severity,
            reading_ids,
            configuration_revision,
            raised_at,
            threshold_min_value,
            threshold_max_value,
            geofence_id,
            distance_to_boundary_meters,
        )

    @classmethod
    def reconstitute(
        cls,
        alert_id: uuid.UUID,
        device_id: str,
        alert_type: AlertType,
        severity: AlertSeverity,
        reading_ids: tuple[uuid.UUID, ...],
        configuration_revision: int,
        raised_at: datetime,
        threshold_min_value: float | None,
        threshold_max_value: float | None,
        geofence_id: uuid.UUID | None,
        distance_to_boundary_meters: float | None,
        sync_status: SyncStatus,
        rejection_reason: str | None,
        sync_attempts: int,
    ) -> "MonitoringAlert":
        """Rebuild a persisted alert with its stored synchronization state."""
        return cls(
            alert_id,
            device_id,
            alert_type,
            severity,
            reading_ids,
            configuration_revision,
            raised_at,
            threshold_min_value,
            threshold_max_value,
            geofence_id,
            distance_to_boundary_meters,
            sync_status,
            rejection_reason,
            sync_attempts,
        )

    def mark_synced(self) -> "MonitoringAlert":
        """Return this alert as accepted or recognized by the cloud."""
        return replace(self, sync_status=SyncStatus.SYNCED, rejection_reason=None)

    def mark_rejected(self, reason: str) -> "MonitoringAlert":
        """Return this alert as rejected by the cloud, with the cloud's reason."""
        return replace(self, sync_status=SyncStatus.REJECTED, rejection_reason=reason)

    def defer_retry(self, reason: str, max_attempts: int) -> "MonitoringAlert":
        """Keep the record pending for another attempt, rejecting it once the attempts run out."""
        if max_attempts < 1:
            raise ValueError("Maximum synchronization attempts must be at least one")
        attempts = self.sync_attempts + 1
        if attempts >= max_attempts:
            return replace(
                self,
                sync_status=SyncStatus.REJECTED,
                rejection_reason=reason,
                sync_attempts=attempts,
            )
        return replace(self, sync_attempts=attempts)


@dataclass(frozen=True)
class MonitoringRuleSet:
    """The cached monitoring rules delivered by the ICHU cloud."""

    ranch_id: uuid.UUID | None
    revision: int
    thresholds: tuple[Threshold, ...]
    geofences: tuple[Geofence, ...]

    def __post_init__(self) -> None:
        if self.ranch_id is not None and not isinstance(self.ranch_id, uuid.UUID):
            raise TypeError("Ranch identifier must be a UUID")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 0
        ):
            raise ValueError("Rule set revision must be a non-negative integer")
        thresholds = tuple(self.thresholds)
        if not all(isinstance(threshold, Threshold) for threshold in thresholds):
            raise TypeError("Rule set thresholds must be Threshold values")
        geofences = tuple(self.geofences)
        if not all(isinstance(geofence, Geofence) for geofence in geofences):
            raise TypeError("Rule set geofences must be Geofence values")
        object.__setattr__(self, "thresholds", thresholds)
        object.__setattr__(self, "geofences", geofences)

    @classmethod
    def empty(cls) -> "MonitoringRuleSet":
        """Return the rule set used before the cloud has delivered any rules."""
        return cls(None, 0, (), ())
