"""Repository implementations for the Monitoring bounded context."""

import json
import uuid
from datetime import UTC, datetime

import peewee

from monitoring.domain.entities import MonitoringAlert, MonitoringRuleSet, SubmittingDevice, TelemetryReading
from monitoring.domain.errors import TelemetryEventConflict
from monitoring.domain.repositories import ReadingSaveResult
from monitoring.domain.value_objects import (
    ActivityIndex,
    AlertSeverity,
    AlertType,
    BatteryLevel,
    BodyTemperature,
    Coordinate,
    DeviceAssignment,
    Geofence,
    GeoPosition,
    MonitoredMetric,
    RecordedAt,
    SyncStatus,
    TelemetryEventId,
    Threshold,
    canonical_device_identifier,
)
from monitoring.infrastructure.models import (
    DeviceAssignmentModel,
    GeofenceModel,
    MonitoringAlertModel,
    MonitoringRuleSetModel,
    TelemetryReadingModel,
    ThresholdModel,
)

_RULE_SET_KEY = "current"


def _naive_utc(value: datetime) -> datetime:
    """Convert an aware timestamp to naive UTC for storage."""
    return value.astimezone(UTC).replace(tzinfo=None)


def _as_utc(value: datetime) -> datetime:
    """Attach UTC to a naive timestamp read back from storage."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class TelemetryReadingRepository:
    """Persist and reconstruct telemetry readings with idempotent submission."""

    def migrate_schema(self) -> None:
        """Add the attempt counter that databases created by v0.1.0 lack."""
        database = TelemetryReadingModel._meta.database
        with database.atomic():
            columns = {column.name for column in database.get_columns("telemetry_readings")}
            if "sync_attempts" not in columns:
                database.execute_sql(
                    'ALTER TABLE "telemetry_readings" '
                    'ADD COLUMN "sync_attempts" INTEGER NOT NULL DEFAULT 0'
                )

    def save_idempotently(self, reading: TelemetryReading) -> ReadingSaveResult:
        """Persist a reading or return the matching prior submission; the unique index guards retries."""
        database = TelemetryReadingModel._meta.database
        try:
            with database.atomic("IMMEDIATE"):
                existing = self._find_event(reading.device_id, reading.event_id.value)
                if existing is not None:
                    return ReadingSaveResult(self._matching_event(existing, reading), False)
                TelemetryReadingModel.create(**self._values(reading))
                return ReadingSaveResult(reading, True)
        except peewee.IntegrityError:
            existing = self._find_event(reading.device_id, reading.event_id.value)
            if existing is None:
                raise
            return ReadingSaveResult(self._matching_event(existing, reading), False)

    def find_pending(self, limit: int) -> list[TelemetryReading]:
        """Return up to ``limit`` unsynchronized readings, oldest first."""
        rows = (
            TelemetryReadingModel.select()
            .where(TelemetryReadingModel.sync_status == SyncStatus.PENDING.value)
            .order_by(TelemetryReadingModel.recorded_at.asc())
            .limit(limit)
        )
        return [self._to_entity(row) for row in rows]

    def update_sync_status(self, reading: TelemetryReading) -> TelemetryReading:
        """Persist the synchronization state of a reading."""
        TelemetryReadingModel.update(
            sync_status=reading.sync_status.value,
            rejection_reason=reading.rejection_reason,
            sync_attempts=reading.sync_attempts,
        ).where(TelemetryReadingModel.reading_id == str(reading.reading_id)).execute()
        return reading

    def count_pending(self) -> int:
        """Return the number of unsynchronized readings."""
        return (
            TelemetryReadingModel.select()
            .where(TelemetryReadingModel.sync_status == SyncStatus.PENDING.value)
            .count()
        )

    @staticmethod
    def _find_event(device_id: str, event_id: str):
        return TelemetryReadingModel.get_or_none(
            (TelemetryReadingModel.device_id == device_id)
            & (TelemetryReadingModel.event_id == event_id)
        )

    def _matching_event(
        self, existing: TelemetryReadingModel, submitted: TelemetryReading
    ) -> TelemetryReading:
        persisted = self._to_entity(existing)
        if not persisted.same_telemetry_as(submitted):
            raise TelemetryEventConflict
        return persisted

    @staticmethod
    def _values(reading: TelemetryReading) -> dict:
        position = reading.position
        return {
            "reading_id": str(reading.reading_id),
            "device_id": reading.device_id,
            "event_id": reading.event_id.value,
            "recorded_at": _naive_utc(reading.recorded_at.value),
            "timestamp_supplied": reading.timestamp_supplied,
            "body_temperature": None if reading.body_temperature is None else reading.body_temperature.celsius,
            "activity_index": None if reading.activity_index is None else reading.activity_index.value,
            "battery_level": None if reading.battery_level is None else reading.battery_level.percent,
            "latitude": None if position is None else position.latitude,
            "longitude": None if position is None else position.longitude,
            "accuracy_meters": None if position is None else position.accuracy_meters,
            "sync_status": reading.sync_status.value,
            "rejection_reason": reading.rejection_reason,
            "sync_attempts": reading.sync_attempts,
        }

    @staticmethod
    def _to_entity(row: TelemetryReadingModel) -> TelemetryReading:
        position = None
        if row.latitude is not None and row.longitude is not None:
            position = GeoPosition(row.latitude, row.longitude, row.accuracy_meters)
        return TelemetryReading.reconstitute(
            uuid.UUID(row.reading_id),
            SubmittingDevice(row.device_id),
            TelemetryEventId(row.event_id),
            RecordedAt(_as_utc(row.recorded_at)),
            row.timestamp_supplied,
            None if row.body_temperature is None else BodyTemperature(row.body_temperature),
            None if row.activity_index is None else ActivityIndex(row.activity_index),
            None if row.battery_level is None else BatteryLevel(row.battery_level),
            position,
            SyncStatus(row.sync_status),
            row.rejection_reason,
            row.sync_attempts,
        )


class MonitoringAlertRepository:
    """Persist and reconstruct locally raised monitoring alerts."""

    def migrate_schema(self) -> None:
        """Add the attempt counter that databases created by v0.1.0 lack."""
        database = MonitoringAlertModel._meta.database
        with database.atomic():
            columns = {column.name for column in database.get_columns("monitoring_alerts")}
            if "sync_attempts" not in columns:
                database.execute_sql(
                    'ALTER TABLE "monitoring_alerts" '
                    'ADD COLUMN "sync_attempts" INTEGER NOT NULL DEFAULT 0'
                )

    def save(self, alert: MonitoringAlert) -> MonitoringAlert:
        """Persist a newly raised alert."""
        MonitoringAlertModel.create(
            alert_id=str(alert.alert_id),
            device_id=alert.device_id,
            alert_type=alert.type.value,
            severity=alert.severity.value,
            reading_ids=json.dumps([str(item) for item in alert.reading_ids]),
            configuration_revision=alert.configuration_revision,
            raised_at=_naive_utc(alert.raised_at),
            threshold_min_value=alert.threshold_min_value,
            threshold_max_value=alert.threshold_max_value,
            geofence_id=None if alert.geofence_id is None else str(alert.geofence_id),
            distance_to_boundary_meters=alert.distance_to_boundary_meters,
            sync_status=alert.sync_status.value,
            rejection_reason=alert.rejection_reason,
            sync_attempts=alert.sync_attempts,
        )
        return alert

    def find_recent(
        self, device_id: str, alert_type: AlertType, since: datetime
    ) -> MonitoringAlert | None:
        """Return the newest alert of a device and type raised at or after ``since``."""
        row = (
            MonitoringAlertModel.select()
            .where(
                (MonitoringAlertModel.device_id == device_id)
                & (MonitoringAlertModel.alert_type == alert_type.value)
                & (MonitoringAlertModel.raised_at >= _naive_utc(since))
            )
            .order_by(MonitoringAlertModel.raised_at.desc())
            .first()
        )
        return None if row is None else self._to_entity(row)

    def find_pending(self, limit: int) -> list[MonitoringAlert]:
        """Return up to ``limit`` unsynchronized alerts, oldest first."""
        rows = (
            MonitoringAlertModel.select()
            .where(MonitoringAlertModel.sync_status == SyncStatus.PENDING.value)
            .order_by(MonitoringAlertModel.raised_at.asc())
            .limit(limit)
        )
        return [self._to_entity(row) for row in rows]

    def find_latest(self, limit: int) -> list[MonitoringAlert]:
        """Return up to ``limit`` alerts, newest first."""
        rows = (
            MonitoringAlertModel.select()
            .order_by(MonitoringAlertModel.raised_at.desc())
            .limit(limit)
        )
        return [self._to_entity(row) for row in rows]

    def update_sync_status(self, alert: MonitoringAlert) -> MonitoringAlert:
        """Persist the synchronization state of an alert."""
        MonitoringAlertModel.update(
            sync_status=alert.sync_status.value,
            rejection_reason=alert.rejection_reason,
            sync_attempts=alert.sync_attempts,
        ).where(MonitoringAlertModel.alert_id == str(alert.alert_id)).execute()
        return alert

    def count_pending(self) -> int:
        """Return the number of unsynchronized alerts."""
        return (
            MonitoringAlertModel.select()
            .where(MonitoringAlertModel.sync_status == SyncStatus.PENDING.value)
            .count()
        )

    @staticmethod
    def _to_entity(row: MonitoringAlertModel) -> MonitoringAlert:
        return MonitoringAlert.reconstitute(
            uuid.UUID(row.alert_id),
            row.device_id,
            AlertType(row.alert_type),
            AlertSeverity(row.severity),
            tuple(uuid.UUID(item) for item in json.loads(row.reading_ids)),
            row.configuration_revision,
            _as_utc(row.raised_at),
            row.threshold_min_value,
            row.threshold_max_value,
            None if row.geofence_id is None else uuid.UUID(row.geofence_id),
            row.distance_to_boundary_meters,
            SyncStatus(row.sync_status),
            row.rejection_reason,
            row.sync_attempts,
        )


class MonitoringRuleSetRepository:
    """Persist and reconstruct the cached monitoring rules."""

    def get(self) -> MonitoringRuleSet:
        """Return the cached rules, or the empty rule set when none are cached."""
        row = MonitoringRuleSetModel.get_or_none(MonitoringRuleSetModel.key == _RULE_SET_KEY)
        if row is None:
            return MonitoringRuleSet.empty()
        thresholds = tuple(self._threshold_to_entity(item) for item in ThresholdModel.select())
        geofences = tuple(self._geofence_to_entity(item) for item in GeofenceModel.select())
        return MonitoringRuleSet(
            None if row.ranch_id is None else uuid.UUID(row.ranch_id),
            row.revision,
            thresholds,
            geofences,
        )

    def replace(self, rule_set: MonitoringRuleSet) -> MonitoringRuleSet:
        """Replace all cached thresholds, geofences and the revision atomically."""
        database = MonitoringRuleSetModel._meta.database
        with database.atomic("IMMEDIATE"):
            ThresholdModel.delete().execute()
            GeofenceModel.delete().execute()
            threshold_rows = [
                {
                    "threshold_id": str(threshold.threshold_id),
                    "life_stage": threshold.life_stage,
                    "metric": threshold.metric.value,
                    "min_value": threshold.min_value,
                    "max_value": threshold.max_value,
                    "severity": threshold.severity.value,
                }
                for threshold in rule_set.thresholds
            ]
            geofence_rows = [
                {
                    "geofence_id": str(geofence.geofence_id),
                    "lot_id": str(geofence.lot_id),
                    "name": geofence.name,
                    "tolerance_meters": geofence.tolerance_meters,
                    "severity": geofence.severity.value,
                    "vertices": json.dumps(
                        [[vertex.latitude, vertex.longitude] for vertex in geofence.vertices]
                    ),
                }
                for geofence in rule_set.geofences
            ]
            if threshold_rows:
                ThresholdModel.insert_many(threshold_rows).execute()
            if geofence_rows:
                GeofenceModel.insert_many(geofence_rows).execute()
            MonitoringRuleSetModel.replace(
                key=_RULE_SET_KEY,
                ranch_id=None if rule_set.ranch_id is None else str(rule_set.ranch_id),
                revision=rule_set.revision,
            ).execute()
        return rule_set

    @staticmethod
    def _threshold_to_entity(row: ThresholdModel) -> Threshold:
        return Threshold(
            uuid.UUID(row.threshold_id),
            row.life_stage,
            MonitoredMetric(row.metric),
            row.min_value,
            row.max_value,
            AlertSeverity(row.severity),
        )

    @staticmethod
    def _geofence_to_entity(row: GeofenceModel) -> Geofence:
        vertices = tuple(
            Coordinate(latitude, longitude) for latitude, longitude in json.loads(row.vertices)
        )
        return Geofence(
            uuid.UUID(row.geofence_id),
            uuid.UUID(row.lot_id),
            row.name,
            row.tolerance_meters,
            AlertSeverity(row.severity),
            vertices,
        )


class DeviceAssignmentRepository:
    """Persist and reconstruct device lot and life-stage assignments."""

    def find(self, device_id: str) -> DeviceAssignment | None:
        """Return the assignment of a device, if one is configured."""
        try:
            canonical_id = canonical_device_identifier(device_id)
        except ValueError:
            return None
        row = DeviceAssignmentModel.get_or_none(DeviceAssignmentModel.device_id == canonical_id)
        if row is None:
            return None
        return DeviceAssignment(
            row.device_id,
            None if row.lot_id is None else uuid.UUID(row.lot_id),
            row.life_stage,
        )

    def save(self, assignment: DeviceAssignment) -> DeviceAssignment:
        """Insert or update the assignment of a device."""
        DeviceAssignmentModel.replace(
            device_id=assignment.device_id,
            lot_id=None if assignment.lot_id is None else str(assignment.lot_id),
            life_stage=assignment.life_stage,
        ).execute()
        return assignment
