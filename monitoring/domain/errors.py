"""Domain errors for Monitoring telemetry ingestion."""


class TelemetryEventConflict(Exception):
    """Raised when one device reuses an event ID for different telemetry."""
