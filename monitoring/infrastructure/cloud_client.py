"""HTTP client for the ICHU cloud API (Operations & Monitoring)."""

from typing import Any

import requests

from monitoring.application.errors import CloudCredentialsRejected, CloudUnavailable


class IchuCloudClient:
    """Send authenticated requests to the ICHU cloud API."""

    def __init__(
        self,
        base_url: str,
        gateway_id: str,
        gateway_key: str,
        timeout_seconds: int,
        session: requests.Session | None = None,
    ):
        """Initialize the client with the cloud endpoint and gateway identity."""
        self._base_url = base_url.rstrip("/")
        self._gateway_id = gateway_id
        self._gateway_key = gateway_key
        self._timeout_seconds = timeout_seconds
        self._session = session or requests.Session()

    def fetch_monitoring_rules(self, after_revision: int) -> dict[str, Any] | None:
        """Fetch the monitoring rules newer than ``after_revision``."""
        url = f"{self._base_url}/api/v1/edge-gateways/{self._gateway_id}/monitoring-rules"
        response = self._send("GET", url, params={"afterRevision": after_revision})
        if response.status_code == 304:
            return None
        return self._json_object(response)

    def send_telemetry_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Push one batch of readings and alerts and return the cloud's answer."""
        url = f"{self._base_url}/api/v1/telemetry/batch"
        response = self._send("POST", url, json=payload)
        return self._json_object(response)

    def _send(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        try:
            response = self._session.request(
                method,
                url,
                headers={"X-Gateway-Key": self._gateway_key, "Accept": "application/json"},
                timeout=self._timeout_seconds,
                **kwargs,
            )
        except requests.RequestException as error:
            raise CloudUnavailable("The ICHU cloud API could not be reached") from error
        if response.status_code in (401, 403):
            raise CloudCredentialsRejected
        if response.status_code >= 400:
            raise CloudUnavailable(
                f"The ICHU cloud API answered with HTTP {response.status_code}"
            )
        return response

    @staticmethod
    def _json_object(response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as error:
            raise CloudUnavailable("The ICHU cloud API returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise CloudUnavailable("The ICHU cloud API returned an unexpected payload")
        return payload
