"""HTTP interface for the operational status of the edge gateway."""

from flask import Blueprint, current_app, jsonify

status_api = Blueprint("status_api", __name__)


@status_api.route("/api/v1/status", methods=["GET"])
def get_status():
    """Report the gateway status without requiring authentication."""
    edge_status = current_app.extensions["monitoring_query_service"].status()
    return jsonify({
        "status": "ok",
        "service": "ichu-edge-service",
        "rulesRevision": edge_status.rules_revision,
        "pendingReadings": edge_status.pending_readings,
        "pendingAlerts": edge_status.pending_alerts,
    }), 200
