"""Repository implementation for IAM device aggregates."""

from datetime import UTC, datetime

import peewee

from iam.domain.credentials import ApiCredential
from iam.domain.entities import Device, canonical_device_id
from iam.infrastructure.models import Device as DeviceModel


def _as_utc(value: datetime | None) -> datetime | None:
    """Attach UTC to naive timestamps read back from SQLite."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _as_storage(value: datetime | None) -> datetime | None:
    """Store timestamps as naive UTC so SQLite text comparisons stay ordered."""
    if value is None:
        return None
    return value.astimezone(UTC).replace(tzinfo=None)


class DeviceRepository:
    """Persist and reconstruct IAM device aggregates."""

    def migrate_schema(self) -> None:
        """Add revocation state and hash credentials stored by older versions."""
        database = DeviceModel._meta.database
        with database.atomic():
            columns = {column.name for column in database.get_columns("devices")}
            if "revoked_at" not in columns:
                database.execute_sql(
                    'ALTER TABLE "devices" ADD COLUMN "revoked_at" DATETIME NULL'
                )

            for row in DeviceModel.select():
                stored = row.credential_verifier
                if ApiCredential.is_encoded(stored):
                    ApiCredential.from_encoded(stored)
                    continue
                row.credential_verifier = ApiCredential.create(stored).encode()
                row.save(only=[DeviceModel.credential_verifier])

    def find_by_device_id(self, device_id: str) -> Device | None:
        """Load a device by identity without embedding credential policy in SQL."""
        try:
            row = DeviceModel.get(DeviceModel.device_id == device_id)
        except peewee.DoesNotExist:
            return None
        return self._to_entity(row)

    def save(self, device: Device) -> Device:
        """Insert or update the persisted aggregate state."""
        values = {
            "credential_verifier": device.credential.encode(),
            "created_at": _as_storage(device.created_at),
            "revoked_at": _as_storage(device.revoked_at),
        }
        with DeviceModel._meta.database.atomic():
            row = DeviceModel.get_or_none(DeviceModel.device_id == device.device_id)
            if row is None:
                DeviceModel.create(device_id=device.device_id, **values)
            else:
                DeviceModel.update(**values).where(
                    DeviceModel.device_id == device.device_id
                ).execute()
        return device

    def get_or_create_device(self, device_id: str, api_key: str) -> Device:
        """Retrieve the identified device or register it if absent."""
        existing = DeviceModel.get_or_none(
            DeviceModel.device_id == canonical_device_id(device_id)
        )
        if existing is not None:
            return self._to_entity(existing)
        new_device = Device.register(device_id, api_key)
        return self.save(new_device)

    @staticmethod
    def _to_entity(row: DeviceModel) -> Device:
        return Device(
            row.device_id,
            ApiCredential.from_encoded(row.credential_verifier),
            _as_utc(row.created_at),
            _as_utc(row.revoked_at),
        )
