"""Peewee ORM model for the IAM bounded context."""

from peewee import CharField, DateTimeField, Model

from shared.infrastructure.database import db


class Device(Model):
    """ORM mapping for the ``devices`` table."""

    device_id = CharField(primary_key=True)
    credential_verifier = CharField(column_name="api_key")
    created_at = DateTimeField()
    revoked_at = DateTimeField(null=True)

    class Meta:
        database = db
        table_name = "devices"
