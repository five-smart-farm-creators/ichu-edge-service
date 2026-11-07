"""HTTP interface for the IAM bounded context."""

from flask import Blueprint, current_app, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

iam_api = Blueprint("iam_api", __name__)


class AuthRequest(BaseModel):
    """Request contract for authenticating one registered device."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    device_id: str = Field(..., min_length=1, description="Canonical UUID of the device.")


@iam_api.route("/api/v1/iam/authentications", methods=["POST"])
def authenticate_device():
    """Authenticate a device using its identity and API key."""
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 415

    request_data = request.get_json(silent=True)
    if request_data is None:
        return jsonify({"error": "Invalid JSON request body"}), 400

    try:
        auth_request = AuthRequest.model_validate(request_data)
    except ValidationError:
        return jsonify({"error": "Invalid authentication request"}), 400

    api_key = request.headers.get("X-API-Key")
    if not api_key:
        return jsonify({"error": "Missing X-API-Key"}), 401

    if not current_app.extensions["iam_authentication_service"].authenticate(
        auth_request.device_id, api_key
    ):
        return jsonify({"error": "Invalid device_id or API key"}), 401

    return jsonify({"device_id": auth_request.device_id, "authenticated": True}), 200
