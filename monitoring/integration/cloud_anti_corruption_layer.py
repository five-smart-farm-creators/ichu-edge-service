"""DDD anti-corruption layer for the Monitoring-to-ICHU-cloud relationship."""

import uuid
from datetime import UTC, datetime
from typing import Any

from monitoring.application.errors import CloudUnavailable, SynchronizationDisabled
from monitoring.application.services import CloudEntryResult
from monitoring.domain.entities import MonitoringAlert, MonitoringRuleSet, TelemetryReading
from monitoring.domain.value_objects import (
    AlertSeverity,
    Coordinate,
    DeviceAssignment,
    Geofence,
    GeoPosition,
    MonitoredMetric,
    Threshold,
)
from monitoring.infrastructure.cloud_client import IchuCloudClient


class IchuCloudAntiCorruptionLayer:
    """Translate between the edge model and the ICHU cloud API contract."""

    def __init__(self, cloud_client: IchuCloudClient):
        """Keep the HTTP client used to reach the cloud API."""
        self._client = cloud_client

    def fetch_rules(self, after_revision: int) -> MonitoringRuleSet | None:
        """Return the cloud's rules newer than ``after_revision``, or ``None``."""
        payload = self._client.fetch_monitoring_rules(after_revision)
        if payload is None:
            return None
        try:
            return _rule_set_from_contract(payload)
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise CloudUnavailable(
                "The ICHU cloud API returned an unexpected monitoring rules payload"
            ) from error

    def fetch_assignments(self) -> list[DeviceAssignment]:
        """Return the collar assignments that the cloud lists for this gateway's ranch."""
        payload = self._client.fetch_device_assignments()
        try:
            return _assignments_from_contract(payload)
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise CloudUnavailable(
                "The ICHU cloud API returned an unexpected device assignments payload"
            ) from error

    def send_batch(
        self, readings: list[TelemetryReading], alerts: list[MonitoringAlert]
    ) -> list[CloudEntryResult]:
        """Push pending readings and alerts and return the per-entry outcomes."""
        payload = {
            "readings": [_reading_to_contract(reading) for reading in readings],
            "alerts": [_alert_to_contract(alert) for alert in alerts],
            "alertChanges": [],
        }
        return _entry_results_from_contract(self._client.send_telemetry_batch(payload))


class DisabledCloudAntiCorruptionLayer:
    """Stand-in used when no cloud API is configured; every call is refused."""

    def fetch_rules(self, after_revision: int) -> MonitoringRuleSet | None:
        """Refuse to fetch rules because synchronization is disabled."""
        raise SynchronizationDisabled

    def fetch_assignments(self) -> list[DeviceAssignment]:
        """Refuse to fetch assignments because synchronization is disabled."""
        raise SynchronizationDisabled

    def send_batch(
        self, readings: list[TelemetryReading], alerts: list[MonitoringAlert]
    ) -> list[CloudEntryResult]:
        """Refuse to send records because synchronization is disabled."""
        raise SynchronizationDisabled


def _iso_utc(value: datetime) -> str:
    """Format an aware timestamp as ISO 8601 with a ``Z`` suffix."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _reading_to_contract(reading: TelemetryReading) -> dict[str, Any]:
    reasons = reading.invalid_reasons
    return {
        "readingId": str(reading.reading_id),
        "deviceId": reading.device_id,
        "recordedAt": _iso_utc(reading.recorded_at.value),
        "bodyTemperature": _measurement_to_contract(
            None if reading.body_temperature is None else reading.body_temperature.celsius,
            reasons.reason_for("body_temperature"),
        ),
        "activityIndex": _measurement_to_contract(
            None if reading.activity_index is None else reading.activity_index.value,
            reasons.reason_for("activity_index"),
        ),
        "ruminationMinutes": None,
        "batteryLevel": _measurement_to_contract(
            None if reading.battery_level is None else reading.battery_level.percent,
            reasons.reason_for("battery_level"),
        ),
        "position": _position_to_contract(reading.position, reasons.reason_for("position")),
    }


def _measurement_to_contract(value: float | None, reason: str | None) -> dict[str, Any] | None:
    """A failed sensor keeps its envelope so the cloud sees the reason; an absent field is ``None``."""
    if value is not None:
        return {"value": value, "invalidReason": None}
    if reason is not None:
        return {"value": None, "invalidReason": reason}
    return None


def _position_to_contract(position: GeoPosition | None, reason: str | None) -> dict[str, Any] | None:
    """A failed fix keeps the cloud's four position keys, with the coordinates and accuracy null."""
    if position is not None:
        return {
            "latitude": position.latitude,
            "longitude": position.longitude,
            "accuracyMeters": position.accuracy_meters,
            "invalidReason": None,
        }
    if reason is not None:
        return {
            "latitude": None,
            "longitude": None,
            "accuracyMeters": None,
            "invalidReason": reason,
        }
    return None


def _alert_to_contract(alert: MonitoringAlert) -> dict[str, Any]:
    return {
        "alertId": str(alert.alert_id),
        "deviceId": alert.device_id,
        "type": alert.type.value,
        "severity": alert.severity.value,
        "readingIds": [str(reading_id) for reading_id in alert.reading_ids],
        "configurationRevision": alert.configuration_revision,
        "thresholdMinValue": alert.threshold_min_value,
        "thresholdMaxValue": alert.threshold_max_value,
        "geofenceId": None if alert.geofence_id is None else str(alert.geofence_id),
        "distanceToBoundaryMeters": alert.distance_to_boundary_meters,
    }


def _entry_results_from_contract(response: dict[str, Any]) -> list[CloudEntryResult]:
    entries = response.get("results", [])
    if not isinstance(entries, list):
        raise CloudUnavailable("The ICHU cloud API returned an unexpected batch answer")
    results: list[CloudEntryResult] = []
    for entry in entries:
        try:
            reason = entry.get("reason")
            results.append(
                CloudEntryResult(
                    kind=str(entry["kind"]),
                    entry_id=str(entry["id"]),
                    status=str(entry["status"]),
                    reason=None if reason is None else str(reason),
                )
            )
        except (KeyError, TypeError, AttributeError) as error:
            raise CloudUnavailable(
                "The ICHU cloud API returned an unexpected batch answer"
            ) from error
    return results


def _assignments_from_contract(payload: dict[str, Any]) -> list[DeviceAssignment]:
    return [
        DeviceAssignment(
            str(uuid.UUID(item["deviceId"])),
            None if item["lotId"] is None else uuid.UUID(item["lotId"]),
            item["lifeStage"],
        )
        for item in payload["assignments"]
    ]


def _rule_set_from_contract(payload: dict[str, Any]) -> MonitoringRuleSet:
    ranch_id = payload.get("ranchId")
    return MonitoringRuleSet(
        None if ranch_id is None else uuid.UUID(ranch_id),
        int(payload["revision"]),
        tuple(_threshold_from_contract(item) for item in payload["thresholds"]),
        tuple(_geofence_from_contract(item) for item in payload["geofences"]),
    )


def _threshold_from_contract(item: dict[str, Any]) -> Threshold:
    return Threshold(
        uuid.UUID(item["thresholdId"]),
        item["lifeStage"],
        MonitoredMetric(item["metric"]),
        float(item["minValue"]),
        float(item["maxValue"]),
        AlertSeverity(item["severity"]),
    )


def _geofence_from_contract(item: dict[str, Any]) -> Geofence:
    return Geofence(
        uuid.UUID(item["geofenceId"]),
        uuid.UUID(item["lotId"]),
        item["name"],
        int(item["toleranceMeters"]),
        AlertSeverity(item["severity"]),
        tuple(
            Coordinate(float(vertex["latitude"]), float(vertex["longitude"]))
            for vertex in item["vertices"]
        ),
    )
