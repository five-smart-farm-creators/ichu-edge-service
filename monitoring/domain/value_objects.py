"""Value objects and enumerations for the Monitoring bounded context."""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from math import isfinite


class MonitoredMetric(Enum):
    """Measurements that monitoring thresholds can be defined for."""

    BODY_TEMPERATURE = "BodyTemperature"
    ACTIVITY_INDEX = "ActivityIndex"
    HEART_RATE = "HeartRate"
    METHANE_INDEX = "MethaneIndex"


class AlertType(Enum):
    """Kinds of local alert raised by the edge service."""

    TEMPERATURE = "Temperature"
    ACTIVITY = "Activity"
    GEOFENCE = "Geofence"
    HEART_RATE = "HeartRate"
    METHANE = "Methane"


class AlertSeverity(Enum):
    """Severity levels shared with the ICHU cloud contract, declared from least to most urgent."""

    INFORMATIVE = "Informative"
    WARNING = "Warning"
    CRITICAL = "Critical"

    @property
    def urgency(self) -> int:
        """Return the urgency rank; a higher rank is more urgent."""
        return list(AlertSeverity).index(self)


class SyncStatus(Enum):
    """Synchronization state of a reading or alert with the cloud."""

    PENDING = "pending"
    SYNCED = "synced"
    REJECTED = "rejected"


def canonical_device_identifier(value: str) -> str:
    """Return the lowercase canonical form of a device identifier UUID."""
    if not isinstance(value, str):
        raise ValueError("Device identifier must be a UUID string")
    try:
        return str(uuid.UUID(value.strip()))
    except ValueError as error:
        raise ValueError("Device identifier must be a UUID") from error


def _finite_number(name: str, value: object) -> float:
    """Return a finite float, rejecting booleans, non-numeric and non-finite values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _bounded_number(name: str, value: object, minimum: float, maximum: float) -> float:
    """Return a finite float within an inclusive range."""
    number = _finite_number(name, value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return number


def _required_text(name: str, value: object) -> str:
    """Return a stripped, non-empty string."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} cannot be empty")
    return value.strip()


@dataclass(frozen=True)
class TelemetryEventId:
    """Device-generated identity used to safely retry a telemetry event."""

    value: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.value, str)
            or not self.value.strip()
            or len(self.value) > 128
        ):
            raise ValueError("Event ID must contain 1 to 128 non-whitespace characters")


@dataclass(frozen=True)
class RecordedAt:
    """An offset-aware observation time normalized to UTC."""

    value: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.value, datetime):
            raise TypeError("Observation time must be a datetime")
        if self.value.tzinfo is None or self.value.utcoffset() is None:
            raise ValueError("Observation time must include a timezone")
        object.__setattr__(self, "value", self.value.astimezone(UTC))

    @classmethod
    def now(cls) -> "RecordedAt":
        """Create a value for the current UTC instant."""
        return cls(datetime.now(UTC))

    @classmethod
    def from_optional(cls, value: datetime | None) -> "RecordedAt":
        """Normalize an observation time or use the current instant."""
        return cls.now() if value is None else cls(value)


@dataclass(frozen=True)
class BodyTemperature:
    """A body temperature in degrees Celsius within the collar's measuring range."""

    celsius: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "celsius", _bounded_number("Body temperature", self.celsius, 30.0, 45.0)
        )


@dataclass(frozen=True)
class ActivityIndex:
    """A unitless activity index between 0 and 100."""

    value: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "value", _bounded_number("Activity index", self.value, 0.0, 100.0)
        )


@dataclass(frozen=True)
class HeartRate:
    """A pulse rate in beats per minute within the pulse sensor's measuring range."""

    beats_per_minute: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "beats_per_minute",
            _bounded_number("Heart rate", self.beats_per_minute, 30.0, 200.0),
        )


@dataclass(frozen=True)
class MethaneIndex:
    """A relative methane index from 0 to 100 on the uncalibrated MQ-2 scale, never ppm."""

    value: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "value", _bounded_number("Methane index", self.value, 0.0, 100.0)
        )


@dataclass(frozen=True)
class BatteryLevel:
    """The collar battery charge as a percentage."""

    percent: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "percent", _bounded_number("Battery level", self.percent, 0.0, 100.0)
        )


@dataclass(frozen=True)
class Coordinate:
    """A WGS-84 latitude and longitude pair in decimal degrees."""

    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "latitude", _bounded_number("Latitude", self.latitude, -90.0, 90.0)
        )
        object.__setattr__(
            self, "longitude", _bounded_number("Longitude", self.longitude, -180.0, 180.0)
        )


@dataclass(frozen=True)
class GeoPosition:
    """A GPS position reported by a collar, with an optional accuracy radius."""

    latitude: float
    longitude: float
    accuracy_meters: float | None = None

    def __post_init__(self) -> None:
        coordinate = Coordinate(self.latitude, self.longitude)
        object.__setattr__(self, "latitude", coordinate.latitude)
        object.__setattr__(self, "longitude", coordinate.longitude)
        if self.accuracy_meters is not None:
            accuracy = _finite_number("Accuracy", self.accuracy_meters)
            if accuracy < 0:
                raise ValueError("Accuracy cannot be negative")
            object.__setattr__(self, "accuracy_meters", accuracy)

    @property
    def coordinate(self) -> Coordinate:
        """Return the position without its accuracy."""
        return Coordinate(self.latitude, self.longitude)


MEASUREMENT_FIELDS = frozenset(
    {"body_temperature", "activity_index", "battery_level", "position", "heart_rate", "methane_index"}
)
_MAX_REASON_LENGTH = 200


@dataclass(frozen=True, init=False)
class InvalidMeasurementReasons:
    """Why the collar could not capture some measurements, keyed by measurement field."""

    _pairs: tuple[tuple[str, str], ...]

    def __init__(self, reasons: Mapping[str, str] | None = None) -> None:
        """Validate each reason and store the pairs sorted, so equality ignores insertion order."""
        pairs: list[tuple[str, str]] = []
        for field, reason in (reasons or {}).items():
            if field not in MEASUREMENT_FIELDS:
                raise ValueError(f"Unknown measurement field: {field}")
            if not isinstance(reason, str):
                raise ValueError(f"Invalid reason for {field}: must be a string")
            stripped = reason.strip()
            if not stripped or len(stripped) > _MAX_REASON_LENGTH:
                raise ValueError(f"Invalid reason for {field}: must have 1 to 200 characters")
            pairs.append((field, stripped))
        object.__setattr__(self, "_pairs", tuple(sorted(pairs)))

    def reason_for(self, field: str) -> str | None:
        """Return the reason a measurement is invalid, or ``None`` when it is not."""
        return self.as_dict().get(field)

    def fields(self) -> frozenset[str]:
        """Return the measurement fields that carry a reason."""
        return frozenset(field for field, _ in self._pairs)

    def as_dict(self) -> dict[str, str]:
        """Return the reasons keyed by field, in field order."""
        return dict(self._pairs)

    def __bool__(self) -> bool:
        """Return whether any measurement carries a reason."""
        return bool(self._pairs)


@dataclass(frozen=True)
class Threshold:
    """A cached acceptable range for one metric and life stage."""

    threshold_id: uuid.UUID
    life_stage: str
    metric: MonitoredMetric
    min_value: float
    max_value: float
    severity: AlertSeverity

    def __post_init__(self) -> None:
        if not isinstance(self.threshold_id, uuid.UUID):
            raise TypeError("Threshold identifier must be a UUID")
        object.__setattr__(self, "life_stage", _required_text("Life stage", self.life_stage))
        if not isinstance(self.metric, MonitoredMetric):
            raise TypeError("Threshold metric must be a MonitoredMetric")
        minimum = _finite_number("Threshold minimum", self.min_value)
        maximum = _finite_number("Threshold maximum", self.max_value)
        if minimum >= maximum:
            raise ValueError("Threshold minimum must be lower than its maximum")
        object.__setattr__(self, "min_value", minimum)
        object.__setattr__(self, "max_value", maximum)
        if not isinstance(self.severity, AlertSeverity):
            raise TypeError("Threshold severity must be an AlertSeverity")


@dataclass(frozen=True)
class Geofence:
    """A cached polygon that a lot's animals should stay within."""

    geofence_id: uuid.UUID
    lot_id: uuid.UUID
    name: str
    tolerance_meters: int
    severity: AlertSeverity
    vertices: tuple[Coordinate, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.geofence_id, uuid.UUID):
            raise TypeError("Geofence identifier must be a UUID")
        if not isinstance(self.lot_id, uuid.UUID):
            raise TypeError("Geofence lot identifier must be a UUID")
        object.__setattr__(self, "name", _required_text("Geofence name", self.name))
        if (
            isinstance(self.tolerance_meters, bool)
            or not isinstance(self.tolerance_meters, int)
            or self.tolerance_meters < 0
        ):
            raise ValueError("Geofence tolerance must be a non-negative integer")
        if not isinstance(self.severity, AlertSeverity):
            raise TypeError("Geofence severity must be an AlertSeverity")
        vertices = tuple(self.vertices)
        if len(vertices) < 3:
            raise ValueError("Geofence requires at least three vertices")
        if not all(isinstance(vertex, Coordinate) for vertex in vertices):
            raise TypeError("Geofence vertices must be Coordinate values")
        object.__setattr__(self, "vertices", vertices)


@dataclass(frozen=True)
class DeviceAssignment:
    """The lot and life stage of the animal wearing a device (local configuration)."""

    device_id: str
    lot_id: uuid.UUID | None
    life_stage: str | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "device_id", canonical_device_identifier(self.device_id)
        )
        if self.lot_id is not None and not isinstance(self.lot_id, uuid.UUID):
            raise TypeError("Assignment lot identifier must be a UUID")
        if self.life_stage is not None:
            object.__setattr__(
                self, "life_stage", _required_text("Life stage", self.life_stage)
            )
