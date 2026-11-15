"""Application services for the IAM bounded context."""

from iam.domain.entities import Device, canonical_device_id
from iam.domain.repositories import DeviceRepository


class AuthApplicationService:
    """Application service coordinating IAM device lifecycle use cases."""

    def __init__(self, device_repository: DeviceRepository):
        """Initialize the service with its device repository."""
        self.device_repository = device_repository

    def authenticate(self, device_id: str, api_key: str) -> bool:
        """Check credentials against the identified device's access state."""
        try:
            canonical_id = canonical_device_id(device_id)
        except ValueError:
            return False
        device = self.device_repository.find_by_device_id(canonical_id)
        return device is not None and device.authenticate(api_key)

    def register_device(self, device_id: str, api_key: str) -> Device:
        """Register a device and persist its aggregate with a protected credential."""
        if self.device_repository.find_by_device_id(canonical_device_id(device_id)) is not None:
            raise ValueError("Device is already registered")
        return self.device_repository.save(Device.register(device_id, api_key))

    def revoke_device(self, device_id: str) -> Device:
        """Revoke and persist an active device."""
        device = self.device_repository.find_by_device_id(canonical_device_id(device_id))
        if device is None:
            raise ValueError("Device not found")
        return self.device_repository.save(device.revoke())

    def rotate_api_key(self, device_id: str, new_api_key: str) -> Device:
        """Replace and persist an active device's API-key verifier."""
        device = self.device_repository.find_by_device_id(canonical_device_id(device_id))
        if device is None:
            raise ValueError("Device not found")
        return self.device_repository.save(device.rotate_api_key(new_api_key))

    def get_or_create_device(self, device_id: str, api_key: str) -> tuple[Device, bool]:
        """Retrieve the identified device, creating it if absent; report whether it was created."""
        return self.device_repository.get_or_create_device(device_id, api_key)
