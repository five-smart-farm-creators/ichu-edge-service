"""HTTP interface for the Monitoring bounded context."""

from datetime import UTC, datetime
from typing import Any

from flask import Blueprint, current_app, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from monitoring.application.errors import (
    CloudCredentialsRejected,
    CloudUnavailable,
    DeviceAuthenticationFailed,
    SynchronizationDisabled,
)
from monitoring.domain.entities import MonitoringAlert, TelemetryReading
from monitoring.domain.errors import TelemetryEventConflict
from monitoring.domain.value_objects import GeoPosition

monitoring_api = Blueprint("monitoring_api", __name__)

_DEFAULT_ALERT_LIMIT = 20
_MIN_ALERT_LIMIT = 1
_MAX_ALERT_LIMIT = 100


class PositionRequest(BaseModel):
    """GPS position reported by a collar."""

    model_config = ConfigDict(extra="forbid")

    latitude: float = Field(..., description="Latitude in decimal degrees.")
    longitude: float = Field(..., description="Longitude in decimal degrees.")
    accuracy_meters: float | None = Field(None, description="Accuracy radius in meters.")


class TelemetryReadingCreateRequest(BaseModel):
    """Request contract for submitting one telemetry reading."""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(..., min_length=1, description="Canonical UUID of the device.")
    event_id: str = Field(..., min_length=1, max_length=128, description="Device-generated retry key.")
    recorded_at: datetime | None = Field(None, description="ISO 8601 observation time.")
    body_temperature: float | None = Field(None, description="Body temperature in Celsius.")
    activity_index: float | None = Field(None, description="Activity index.")
    battery_level: float | None = Field(None, description="Battery percentage.")
    position: PositionRequest | None = Field(None, description="GPS position.")


def _iso_utc(value: datetime) -> str:
    """Format an aware timestamp as ISO 8601 with a ``Z`` suffix."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _reading_payload(reading: TelemetryReading) -> dict[str, Any]:
    position = reading.position
    return {
        "reading_id": str(reading.reading_id),
        "device_id": reading.device_id,
        "event_id": reading.event_id.value,
        "recorded_at": _iso_utc(reading.recorded_at.value),
        "body_temperature": None
        if reading.body_temperature is None
        else reading.body_temperature.celsius,
        "activity_index": None if reading.activity_index is None else reading.activity_index.value,
        "battery_level": None if reading.battery_level is None else reading.battery_level.percent,
        "position": None
        if position is None
        else {
            "latitude": position.latitude,
            "longitude": position.longitude,
            "accuracy_meters": position.accuracy_meters,
        },
        "sync_status": reading.sync_status.value,
    }


def _alert_payload(alert: MonitoringAlert) -> dict[str, Any]:
    return {
        "alert_id": str(alert.alert_id),
        "device_id": alert.device_id,
        "type": alert.type.value,
        "severity": alert.severity.value,
        "reading_ids": [str(reading_id) for reading_id in alert.reading_ids],
        "configuration_revision": alert.configuration_revision,
        "raised_at": _iso_utc(alert.raised_at),
        "threshold_min_value": alert.threshold_min_value,
        "threshold_max_value": alert.threshold_max_value,
        "geofence_id": None if alert.geofence_id is None else str(alert.geofence_id),
        "distance_to_boundary_meters": alert.distance_to_boundary_meters,
        "sync_status": alert.sync_status.value,
    }


@monitoring_api.route("/api/v1/monitoring/telemetry-readings", methods=["POST"])
def create_telemetry_reading():
    """Accept one telemetry reading from an authenticated device."""
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 415

    request_data = request.get_json(silent=True)
    if request_data is None:
        return jsonify({"error": "Invalid JSON request body"}), 400
    if isinstance(request_data, dict) and not request_data.get("device_id"):
        return jsonify({"error": "Missing device_id"}), 401

    try:
        reading_request = TelemetryReadingCreateRequest.model_validate(request_data)
    except ValidationError:
        return jsonify({"error": "Invalid telemetry reading request"}), 400

    api_key = request.headers.get("X-API-Key")
    if not api_key:
        return jsonify({"error": "Missing X-API-Key"}), 401

    try:
        position = None
        if reading_request.position is not None:
            position = GeoPosition(
                reading_request.position.latitude,
                reading_request.position.longitude,
                reading_request.position.accuracy_meters,
            )
        result = current_app.extensions["telemetry_ingestion_service"].ingest(
            reading_request.device_id,
            api_key,
            reading_request.event_id,
            reading_request.recorded_at,
            reading_request.body_temperature,
            reading_request.activity_index,
            reading_request.battery_level,
            position,
        )
    except DeviceAuthenticationFailed:
        return jsonify({"error": "Invalid device_id or API key"}), 401
    except TelemetryEventConflict:
        return jsonify({"error": "event_id has already been used for different telemetry"}), 409
    except ValueError:
        return jsonify({"error": "Invalid telemetry reading data"}), 400

    body = {
        "reading": _reading_payload(result.reading),
        "alerts": [_alert_payload(alert) for alert in result.raised_alerts],
    }
    status_code = 201 if result.created else 200
    return jsonify(body), status_code


@monitoring_api.route("/api/v1/monitoring/alerts", methods=["GET"])
def list_alerts():
    """List the newest locally raised alerts for an authenticated device."""
    device_id = request.args.get("device_id")
    if not device_id:
        return jsonify({"error": "Missing device_id"}), 401
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        return jsonify({"error": "Missing X-API-Key"}), 401
    authenticated = current_app.extensions[
        "iam_anti_corruption_layer"
    ].authenticated_submitting_device(device_id, api_key)
    if authenticated is None:
        return jsonify({"error": "Invalid device_id or API key"}), 401

    try:
        limit = int(request.args.get("limit", _DEFAULT_ALERT_LIMIT))
    except ValueError:
        limit = 0
    if not _MIN_ALERT_LIMIT <= limit <= _MAX_ALERT_LIMIT:
        return jsonify({"error": "limit must be an integer between 1 and 100"}), 400

    alerts = current_app.extensions["monitoring_query_service"].latest_alerts(limit)
    return jsonify({"alerts": [_alert_payload(alert) for alert in alerts]}), 200


@monitoring_api.route("/api/v1/synchronizations", methods=["POST"])
def synchronize():
    """Refresh the cached rules and push pending records to the ICHU cloud."""
    try:
        result = current_app.extensions["synchronization_service"].synchronize()
    except SynchronizationDisabled:
        return jsonify({"error": "Cloud synchronization is not configured"}), 503
    except CloudCredentialsRejected:
        return jsonify({"error": "The ICHU cloud API rejected the edge gateway credentials"}), 502
    except CloudUnavailable:
        return jsonify({"error": "The ICHU cloud API is unavailable; pending records were kept"}), 503

    return jsonify({
        "rulesUpdated": result.rules_updated,
        "rulesRevision": result.rules_revision,
        "sentReadings": result.sent_readings,
        "sentAlerts": result.sent_alerts,
        "synced": result.synced,
        "rejected": result.rejected,
    }), 200
