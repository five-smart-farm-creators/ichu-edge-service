"""IAM aggregate roots."""

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from iam.domain.credentials import ApiCredential


def canonical_device_id(value: str) -> str:
    """Return the lowercase canonical form of a device identifier UUID."""
    if not isinstance(value, str):
        raise ValueError("Device identifier must be a UUID string")
    try:
        return str(uuid.UUID(value.strip()))
    except ValueError as error:
        raise ValueError("Device identifier must be a UUID") from error


@dataclass(frozen=True)
class Device:
    """Registered device aggregate with credential and access lifecycle rules."""

    device_id: str
    credential: ApiCredential
    created_at: datetime
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "device_id", canonical_device_id(self.device_id))
        if not isinstance(self.credential, ApiCredential):
            raise TypeError("Device requires an API credential verifier")
        if not isinstance(self.created_at, datetime):
            raise TypeError("Device creation time must be a datetime")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("Device creation time must include a timezone")
        object.__setattr__(self, "created_at", self.created_at.astimezone(UTC))
        if self.revoked_at is not None:
            if not isinstance(self.revoked_at, datetime):
                raise TypeError("Revocation time must be a datetime")
            if self.revoked_at.tzinfo is None or self.revoked_at.utcoffset() is None:
                raise ValueError("Revocation time must include a timezone")
            object.__setattr__(self, "revoked_at", self.revoked_at.astimezone(UTC))
            if self.revoked_at < self.created_at:
                raise ValueError("Revocation cannot predate device registration")

    @classmethod
    def register(
        cls,
        device_id: str,
        api_key: str,
        created_at: datetime | None = None,
    ) -> "Device":
        """Register a device and protect its API key with a one-way verifier."""
        return cls(
            device_id,
            ApiCredential.create(api_key),
            created_at or datetime.now(UTC),
        )

    def authenticate(self, api_key: str) -> bool:
        """Accept credentials only while this device is active."""
        return self.revoked_at is None and self.credential.matches(api_key)

    def revoke(self, at: datetime | None = None) -> "Device":
        """Return a revoked device; a revoked device cannot be revoked twice."""
        if self.revoked_at is not None:
            raise ValueError("Device is already revoked")
        revoked_at = at or datetime.now(UTC)
        if revoked_at.tzinfo is None or revoked_at.utcoffset() is None:
            raise ValueError("Revocation time must include a timezone")
        return replace(self, revoked_at=revoked_at)

    def rotate_api_key(self, new_api_key: str) -> "Device":
        """Return an active device with a newly protected API key."""
        if self.revoked_at is not None:
            raise ValueError("Cannot rotate credentials for a revoked device")
        return replace(self, credential=ApiCredential.create(new_api_key))
