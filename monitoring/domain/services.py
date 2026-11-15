"""Domain services for evaluating telemetry against cached monitoring rules."""

import uuid
from dataclasses import dataclass
from math import cos, hypot, inf, radians

from monitoring.domain.entities import MonitoringRuleSet, TelemetryReading
from monitoring.domain.value_objects import (
    AlertSeverity,
    AlertType,
    Coordinate,
    DeviceAssignment,
    MonitoredMetric,
)

EARTH_RADIUS_METERS = 6_371_000.0

_METRIC_ALERT_TYPES = {
    MonitoredMetric.BODY_TEMPERATURE: AlertType.TEMPERATURE,
    MonitoredMetric.ACTIVITY_INDEX: AlertType.ACTIVITY,
    MonitoredMetric.HEART_RATE: AlertType.HEART_RATE,
    MonitoredMetric.METHANE_INDEX: AlertType.METHANE,
}


@dataclass(frozen=True)
class RuleViolation:
    """A rule that a reading violates, ready to become a monitoring alert."""

    type: AlertType
    severity: AlertSeverity
    threshold_min_value: float | None = None
    threshold_max_value: float | None = None
    geofence_id: uuid.UUID | None = None
    distance_to_boundary_meters: float | None = None


def point_in_polygon(point: Coordinate, vertices: tuple[Coordinate, ...]) -> bool:
    """Return whether a point lies inside a polygon using ray casting."""
    inside = False
    previous = len(vertices) - 1
    for current, vertex in enumerate(vertices):
        crosses_latitude = (vertex.latitude > point.latitude) != (
            vertices[previous].latitude > point.latitude
        )
        if crosses_latitude:
            longitude_at_latitude = (
                (vertices[previous].longitude - vertex.longitude)
                * (point.latitude - vertex.latitude)
                / (vertices[previous].latitude - vertex.latitude)
                + vertex.longitude
            )
            if point.longitude < longitude_at_latitude:
                inside = not inside
        previous = current
    return inside


def distance_to_boundary_meters(point: Coordinate, vertices: tuple[Coordinate, ...]) -> float:
    """Return the distance in meters from a point to the nearest polygon edge."""
    # Planar projection about the point: accurate at lot scale; ignores ellipsoid and antimeridian.
    scale_x = EARTH_RADIUS_METERS * cos(radians(point.latitude))
    projected = [
        (
            radians(_wrap_longitude_delta(vertex.longitude - point.longitude)) * scale_x,
            radians(vertex.latitude - point.latitude) * EARTH_RADIUS_METERS,
        )
        for vertex in vertices
    ]
    nearest = inf
    for index, start in enumerate(projected):
        end = projected[(index + 1) % len(projected)]
        nearest = min(nearest, _distance_from_origin_to_segment(start, end))
    return nearest


def _wrap_longitude_delta(delta: float) -> float:
    """Normalize a longitude difference into the range [-180, 180)."""
    return (delta + 180.0) % 360.0 - 180.0


def _distance_from_origin_to_segment(
    start: tuple[float, float], end: tuple[float, float]
) -> float:
    """Return the distance from the projected origin (the point) to a segment."""
    start_x, start_y = start
    delta_x = end[0] - start_x
    delta_y = end[1] - start_y
    length_squared = delta_x * delta_x + delta_y * delta_y
    if length_squared == 0.0:
        return hypot(start_x, start_y)
    fraction = -(start_x * delta_x + start_y * delta_y) / length_squared
    fraction = max(0.0, min(1.0, fraction))
    return hypot(start_x + fraction * delta_x, start_y + fraction * delta_y)


class RuleEvaluator:
    """Evaluate a telemetry reading against cached thresholds and geofences."""

    def evaluate(
        self,
        reading: TelemetryReading,
        assignment: DeviceAssignment | None,
        rules: MonitoringRuleSet,
    ) -> list[RuleViolation]:
        """Return violated rules, most severe first; devices without an assignment are never evaluated."""
        if assignment is None:
            return []
        violations = self._threshold_violations(reading, assignment, rules)
        violations.extend(self._geofence_violations(reading, assignment, rules))
        violations.sort(key=lambda violation: violation.severity.urgency, reverse=True)
        return violations

    @staticmethod
    def _threshold_violations(
        reading: TelemetryReading,
        assignment: DeviceAssignment,
        rules: MonitoringRuleSet,
    ) -> list[RuleViolation]:
        if assignment.life_stage is None:
            return []
        life_stage = assignment.life_stage.casefold()
        measurements = (
            (
                MonitoredMetric.BODY_TEMPERATURE,
                None if reading.body_temperature is None else reading.body_temperature.celsius,
            ),
            (
                MonitoredMetric.ACTIVITY_INDEX,
                None if reading.activity_index is None else reading.activity_index.value,
            ),
            (
                MonitoredMetric.HEART_RATE,
                None if reading.heart_rate is None else reading.heart_rate.beats_per_minute,
            ),
            (
                MonitoredMetric.METHANE_INDEX,
                None if reading.methane_index is None else reading.methane_index.value,
            ),
        )
        violations: list[RuleViolation] = []
        for metric, value in measurements:
            if value is None:
                continue
            breached = [
                threshold
                for threshold in rules.thresholds
                if threshold.metric is metric
                and threshold.life_stage.casefold() == life_stage
                and (value < threshold.min_value or value > threshold.max_value)
            ]
            if breached:
                # The Warning and Critical rules of a metric share one alert, and the worst one wins.
                worst = max(breached, key=lambda threshold: threshold.severity.urgency)
                violations.append(
                    RuleViolation(
                        _METRIC_ALERT_TYPES[metric],
                        worst.severity,
                        worst.min_value,
                        worst.max_value,
                    )
                )
        return violations

    @staticmethod
    def _geofence_violations(
        reading: TelemetryReading,
        assignment: DeviceAssignment,
        rules: MonitoringRuleSet,
    ) -> list[RuleViolation]:
        if reading.position is None or assignment.lot_id is None:
            return []
        point = reading.position.coordinate
        violations: list[RuleViolation] = []
        for geofence in rules.geofences:
            if geofence.lot_id != assignment.lot_id:
                continue
            if point_in_polygon(point, geofence.vertices):
                continue
            distance = distance_to_boundary_meters(point, geofence.vertices)
            # GPS error can make a point inside the fence look outside; accuracy widens the tolerance.
            margin = geofence.tolerance_meters + (reading.position.accuracy_meters or 0.0)
            if distance > margin:
                violations.append(
                    RuleViolation(
                        AlertType.GEOFENCE,
                        geofence.severity,
                        geofence_id=geofence.geofence_id,
                        distance_to_boundary_meters=round(distance, 1),
                    )
                )
        return violations
