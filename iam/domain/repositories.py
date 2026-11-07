"""Repository abstraction for IAM device aggregates."""

from typing import Protocol

from iam.domain.entities import Device


class DeviceRepository(Protocol):
    """Persistence contract for registered devices."""

    def find_by_device_id(self, device_id: str) -> Device | None:
        """Load a device aggregate by its identity."""
        ...

    def save(self, device: Device) -> Device:
        """Persist a device aggregate's current state."""
        ...

    def get_or_create_device(self, device_id: str, api_key: str) -> Device:
        """Retrieve or create the identified device."""
        ...
