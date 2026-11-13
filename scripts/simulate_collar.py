"""Simulate a Cattle Band collar that sends telemetry to the edge over HTTP.

It mirrors the firmware payload (TelemetryPayload.cpp) and its retry rules. Like the
collar, it is only an HTTP client, so it does not import the application.
"""

import argparse
import os
import random
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

READINGS_PATH = "/api/v1/monitoring/telemetry-readings"
DEFAULT_EDGE_URL = "http://127.0.0.1:5000"
DEFAULT_DEVICE_ID = "9d4a5f3b-3a6e-4f84-8daa-4e5f6a7b8c93"
MODES = ("normal", "fever", "escape", "sensor-failure")
REQUEST_TIMEOUT_SECONDS = 10
SEND_ATTEMPTS = 3

# Development lot center as (latitude, longitude, jitter in degrees).
LOT_CENTER = (-16.5, -68.1, 0.002)
# About 2 km south of the lot, outside its geofence.
ESCAPE_POINT = (-16.52, -68.10, 0.0005)


@dataclass
class SendOutcome:
    status: int | None
    alerts: list[str]
    error: str | None


class CollarRejected(Exception):
    """The edge refused the device key, so retrying cannot succeed."""


def build_reading(mode, device_id, event_id, sequence, rng, now):
    """Build one reading with the same fields and rounding as the collar firmware."""
    reading = {
        "device_id": device_id,
        "event_id": event_id,
        "recorded_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "battery_level": round(max(5.0, 95.0 - 0.1 * (sequence - 1)), 2),
        "activity_index": round(rng.uniform(30.0, 60.0), 2),
    }
    if mode == "fever":
        # Above the 39.5 C maximum of the development rule, so the edge raises a Critical alert.
        reading["body_temperature"] = round(rng.uniform(40.0, 40.6), 2)
    elif mode == "sensor-failure":
        reading["invalid_reasons"] = {"body_temperature": "Temperature sensor not responding"}
    else:
        reading["body_temperature"] = round(rng.uniform(38.3, 39.0), 2)

    latitude, longitude, jitter = ESCAPE_POINT if mode == "escape" else LOT_CENTER
    reading["position"] = {
        "latitude": round(latitude + rng.uniform(-jitter, jitter), 6),
        "longitude": round(longitude + rng.uniform(-jitter, jitter), 6),
        "accuracy_meters": round(rng.uniform(3.0, 8.0), 2),
    }
    return reading


def send_reading(session, url, api_key, reading, sleep=time.sleep, attempts=SEND_ATTEMPTS):
    """Send one reading, retrying transient failures with the same body and event_id."""
    status = None
    error = None
    for attempt in range(attempts):
        if attempt:
            sleep(2 ** (attempt - 1))
        try:
            response = session.post(
                url,
                json=reading,
                headers={"X-API-Key": api_key},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            status, error = None, str(exc)
            continue

        body = _json_body(response)
        status = response.status_code
        if status in (200, 201):
            alerts = [f"{alert['type']}/{alert['severity']}" for alert in body.get("alerts", [])]
            return SendOutcome(status, alerts, None)
        if status == 401:
            raise CollarRejected()
        if status == 429 or status >= 500:
            error = f"HTTP {status}"
            continue
        return SendOutcome(status, [], body.get("error", f"HTTP {status}"))
    return SendOutcome(status, [], error)


def main(argv=None, session=None) -> int:
    """Send the requested readings and return 0 only if the edge accepted all of them."""
    args = _parse_arguments(argv)
    api_key = os.environ.get("DEVICE_API_KEY") or os.environ.get("DEVELOPMENT_DEVICE_API_KEY")
    if not api_key:
        print("Set DEVICE_API_KEY or DEVELOPMENT_DEVICE_API_KEY", file=sys.stderr)
        return 2

    if session is None:
        session = requests.Session()
    url = args.edge_url.rstrip("/") + READINGS_PATH
    # A random run token keeps two runs started in the same second from reusing an event_id.
    run = uuid.uuid4().hex[:8]
    rng = random.Random(args.seed)
    all_accepted = True
    try:
        for sequence in range(1, args.count + 1):
            if sequence > 1 and args.interval:
                time.sleep(args.interval)
            event_id = f"sim-{args.device_id[:8]}-{run}-{sequence:06d}"
            reading = build_reading(
                args.mode, args.device_id, event_id, sequence, rng, datetime.now(timezone.utc)
            )
            outcome = send_reading(session, url, api_key, reading)
            if outcome.status not in (200, 201):
                all_accepted = False
            print(_format_outcome(event_id, outcome))
    except CollarRejected:
        print("The edge rejected the device credentials", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0 if all_accepted else 1


def _json_body(response) -> dict:
    """Return the JSON object of a response, or an empty one when the body is not JSON."""
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _format_outcome(event_id: str, outcome: SendOutcome) -> str:
    if outcome.status in (200, 201):
        alerts = ",".join(outcome.alerts) or "-"
        return f"{event_id} {outcome.status} alerts={alerts}"
    return f"{event_id} failed: {outcome.error}"


def _parse_arguments(argv):
    parser = argparse.ArgumentParser(description="Send simulated collar readings to the edge.")
    parser.add_argument("--edge-url", default=DEFAULT_EDGE_URL, help="edge base URL, without a trailing slash")
    parser.add_argument("--device-id", type=_device_id, default=DEFAULT_DEVICE_ID, help="collar UUID")
    parser.add_argument("--mode", choices=MODES, default="normal", help="kind of reading to simulate")
    parser.add_argument("--count", type=_bounded_int(1, 10_000), default=1, help="number of readings")
    parser.add_argument("--interval", type=_bounded_int(0, 3600), default=5, help="seconds between readings")
    parser.add_argument("--seed", type=int, help="random seed that repeats the same values")
    return parser.parse_args(argv)


def _device_id(text: str) -> str:
    try:
        return str(uuid.UUID(text))
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{text!r} is not a UUID") from error


def _bounded_int(low: int, high: int):
    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError as error:
            raise argparse.ArgumentTypeError(f"expected an integer, got {text!r}") from error
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"must be from {low} to {high}")
        return value

    return parse


if __name__ == "__main__":
    sys.exit(main())
