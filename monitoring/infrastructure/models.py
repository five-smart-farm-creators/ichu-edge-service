"""Peewee ORM models for the Monitoring bounded context."""

from peewee import BooleanField, CharField, DateTimeField, FloatField, IntegerField, Model, TextField

from shared.infrastructure.database import db


class TelemetryReadingModel(Model):
    """ORM mapping for the ``telemetry_readings`` table."""

    reading_id = CharField(primary_key=True, max_length=36)
    device_id = CharField(max_length=36)
    event_id = CharField(max_length=128)
    recorded_at = DateTimeField()
    timestamp_supplied = BooleanField()
    body_temperature = FloatField(null=True)
    activity_index = FloatField(null=True)
    battery_level = FloatField(null=True)
    latitude = FloatField(null=True)
    longitude = FloatField(null=True)
    accuracy_meters = FloatField(null=True)
    invalid_reasons = TextField(null=True)
    sync_status = CharField(max_length=16)
    rejection_reason = TextField(null=True)
    sync_attempts = IntegerField(default=0)

    class Meta:
        database = db
        table_name = "telemetry_readings"
        indexes = (
            (("device_id", "event_id"), True),
            (("sync_status", "recorded_at"), False),
        )


class MonitoringAlertModel(Model):
    """ORM mapping for the ``monitoring_alerts`` table."""

    alert_id = CharField(primary_key=True, max_length=36)
    device_id = CharField(max_length=36)
    alert_type = CharField(max_length=16)
    severity = CharField(max_length=16)
    reading_ids = TextField()
    configuration_revision = IntegerField()
    raised_at = DateTimeField()
    threshold_min_value = FloatField(null=True)
    threshold_max_value = FloatField(null=True)
    geofence_id = CharField(max_length=36, null=True)
    distance_to_boundary_meters = FloatField(null=True)
    sync_status = CharField(max_length=16)
    rejection_reason = TextField(null=True)
    sync_attempts = IntegerField(default=0)

    class Meta:
        database = db
        table_name = "monitoring_alerts"
        indexes = (
            (("device_id", "alert_type", "raised_at"), False),
            (("sync_status", "raised_at"), False),
        )


class MonitoringRuleSetModel(Model):
    """ORM mapping for the ``monitoring_rule_sets`` table, which holds one row."""

    key = CharField(primary_key=True, max_length=16)
    ranch_id = CharField(max_length=36, null=True)
    revision = IntegerField()

    class Meta:
        database = db
        table_name = "monitoring_rule_sets"


class ThresholdModel(Model):
    """ORM mapping for the ``monitoring_thresholds`` table."""

    threshold_id = CharField(primary_key=True, max_length=36)
    life_stage = CharField(max_length=64)
    metric = CharField(max_length=32)
    min_value = FloatField()
    max_value = FloatField()
    severity = CharField(max_length=16)

    class Meta:
        database = db
        table_name = "monitoring_thresholds"


class GeofenceModel(Model):
    """ORM mapping for the ``monitoring_geofences`` table."""

    geofence_id = CharField(primary_key=True, max_length=36)
    lot_id = CharField(max_length=36, index=True)
    name = CharField(max_length=200)
    tolerance_meters = IntegerField()
    severity = CharField(max_length=16)
    vertices = TextField()

    class Meta:
        database = db
        table_name = "monitoring_geofences"


class DeviceAssignmentModel(Model):
    """ORM mapping for the ``device_assignments`` table."""

    device_id = CharField(primary_key=True, max_length=36)
    lot_id = CharField(max_length=36, null=True)
    life_stage = CharField(max_length=64, null=True)

    class Meta:
        database = db
        table_name = "device_assignments"
