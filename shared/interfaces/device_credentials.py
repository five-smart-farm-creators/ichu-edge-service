"""HTTP parsing of the device API key, shared by the bounded contexts that authenticate devices."""

from collections.abc import Mapping

_API_KEY_HEADER = "X-API-Key"
_AUTHORIZATION_HEADER = "Authorization"
_API_KEY_SCHEME = "apikey"


def device_api_key_from_headers(headers: Mapping[str, str]) -> str | None:
    """Return the device key sent as X-API-Key or as Authorization: ApiKey <key>."""
    # X-API-Key wins so a proxy-added Authorization header cannot replace an explicit key.
    api_key = headers.get(_API_KEY_HEADER)
    if api_key:
        return api_key

    scheme, _, credentials = headers.get(_AUTHORIZATION_HEADER, "").partition(" ")
    if scheme.lower() != _API_KEY_SCHEME:
        return None
    return credentials.strip() or None
