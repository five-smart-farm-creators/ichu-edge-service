"""Application services for the Monitoring bounded context."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable, Protocol

from monitoring.application.errors import (
    CloudCredentialsRejected,
    CloudUnavailable,
    DeviceAuthenticationFailed,
)
from monitoring.domain.entities import MonitoringAlert, MonitoringRuleSet, TelemetryReading
from monitoring.domain.repositories import (
    DeviceAssignmentRepository,
    MonitoringAlertRepository,
    MonitoringRuleSetRepository,
    TelemetryReadingRepository,
)
from monitoring.domain.services import RuleEvaluator
from monitoring.domain.value_objects import GeoPosition, SyncStatus
from monitoring.integration.iam_anti_corruption_layer import IamAntiCorruptionLayer

logger = logging.getLogger(__name__)

_ACCEPTED_STATUSES = frozenset({"Accepted", "Duplicate"})
_REJECTED_STATUSES = frozenset({"Rejected", "Conflict"})
_DEFAULT_REJECTION_REASON = "Rejected by the ICHU cloud API"


def _utc_now() -> datetime:
    """Return the current UTC instant."""
    return datetime.now(UTC)


@dataclass(frozen=True)
class IngestionResult:
    """Outcome of accepting one telemetry submission."""

    reading: TelemetryReading
    created: bool
    raised_alerts: list[MonitoringAlert]


@dataclass(frozen=True)
class RuleRefreshResult:
    """Outcome of asking the cloud for newer monitoring rules."""

    updated: bool
    revision: int


@dataclass(frozen=True)
class SyncResult:
    """Outcome of pushing one batch of pending records to the cloud."""

    sent_readings: int
    sent_alerts: int
    synced: int
    rejected: int


@dataclass(frozen=True)
class SynchronizationResult:
    """Combined outcome of a rules refresh and a batch push."""

    rules_updated: bool
    rules_revision: int
    sent_readings: int
    sent_alerts: int
    synced: int
    rejected: int


@dataclass(frozen=True)
class EdgeStatus:
    """Operational snapshot of the gateway."""

    rules_revision: int
    pending_readings: int
    pending_alerts: int


@dataclass(frozen=True)
class CloudEntryResult:
    """Per-entry outcome reported by the cloud for one pushed reading or alert."""

    kind: str
    entry_id: str
    status: str
    reason: str | None = None


class CloudGateway(Protocol):
    """Outbound port to the ICHU cloud API used by the synchronization service."""

    def fetch_rules(self, after_revision: int) -> MonitoringRuleSet | None:
        """Return rules newer than ``after_revision``, or ``None`` when unchanged."""
        ...

    def send_batch(
        self, readings: list[TelemetryReading], alerts: list[MonitoringAlert]
    ) -> list[CloudEntryResult]:
        """Push a batch of readings and alerts and return the per-entry outcomes."""
        ...


class TelemetryIngestionService:
    """Coordinate the use case for accepting one device telemetry submission."""

    def __init__(
        self,
        iam_anti_corruption_layer: IamAntiCorruptionLayer,
        reading_repository: TelemetryReadingRepository,
        alert_repository: MonitoringAlertRepository,
        rule_repository: MonitoringRuleSetRepository,
        assignment_repository: DeviceAssignmentRepository,
        evaluator: RuleEvaluator,
        deduplication_window: timedelta,
        clock: Callable[[], datetime] = _utc_now,
    ):
        """Initialize the service with its context integration and collaborators."""
        self._iam = iam_anti_corruption_layer
        self._readings = reading_repository
        self._alerts = alert_repository
        self._rules = rule_repository
        self._assignments = assignment_repository
        self._evaluator = evaluator
        self._deduplication_window = deduplication_window
        self._clock = clock

    def ingest(
        self,
        device_id: str,
        api_key: str,
        event_id: str,
        recorded_at: datetime | None,
        body_temperature: float | None,
        activity_index: float | None,
        battery_level: float | None,
        position: GeoPosition | None,
    ) -> IngestionResult:
        """Accept one telemetry submission and raise local alerts; retries return the stored reading."""
        submitting_device = self._iam.authenticated_submitting_device(device_id, api_key)
        if submitting_device is None:
            raise DeviceAuthenticationFailed
        reading = TelemetryReading.record(
            submitting_device,
            event_id,
            recorded_at,
            body_temperature,
            activity_index,
            battery_level,
            position,
        )
        saved = self._readings.save_idempotently(reading)
        if not saved.created:
            return IngestionResult(saved.reading, False, [])

        rules = self._rules.get()
        assignment = self._assignments.find(submitting_device.device_id)
        violations = self._evaluator.evaluate(saved.reading, assignment, rules)
        now = self._clock()
        since = now - self._deduplication_window
        raised: list[MonitoringAlert] = []
        for violation in violations:
            recent = self._alerts.find_recent(submitting_device.device_id, violation.type, since)
            if recent is not None:
                continue
            alert = MonitoringAlert.raise_for(
                submitting_device.device_id,
                violation.type,
                violation.severity,
                (saved.reading.reading_id,),
                rules.revision,
                now,
                violation.threshold_min_value,
                violation.threshold_max_value,
                violation.geofence_id,
                violation.distance_to_boundary_meters,
            )
            raised.append(self._alerts.save(alert))
            logger.info(
                "Raised %s alert for device %s", alert.type.value, alert.device_id
            )
        return IngestionResult(saved.reading, True, raised)


class MonitoringQueryService:
    """Answer read-only questions about alerts and the gateway's state."""

    def __init__(
        self,
        reading_repository: TelemetryReadingRepository,
        alert_repository: MonitoringAlertRepository,
        rule_repository: MonitoringRuleSetRepository,
    ):
        """Initialize the service with its repositories."""
        self._readings = reading_repository
        self._alerts = alert_repository
        self._rules = rule_repository

    def latest_alerts(self, limit: int) -> list[MonitoringAlert]:
        """Return the newest alerts first, up to ``limit``."""
        return self._alerts.find_latest(limit)

    def status(self) -> EdgeStatus:
        """Return the cached rules revision and the pending synchronization counts."""
        return EdgeStatus(
            self._rules.get().revision,
            self._readings.count_pending(),
            self._alerts.count_pending(),
        )


class SynchronizationService:
    """Coordinate store-and-forward synchronization with the ICHU cloud API."""

    def __init__(
        self,
        cloud_gateway: CloudGateway,
        reading_repository: TelemetryReadingRepository,
        alert_repository: MonitoringAlertRepository,
        rule_repository: MonitoringRuleSetRepository,
        batch_size: int,
    ):
        """Initialize the service with the cloud port and its repositories."""
        self._cloud = cloud_gateway
        self._readings = reading_repository
        self._alerts = alert_repository
        self._rules = rule_repository
        self._batch_size = batch_size

    def refresh_rules(self) -> RuleRefreshResult:
        """Replace the cached rules when the cloud has a newer revision."""
        current = self._rules.get()
        fetched = self._cloud.fetch_rules(after_revision=current.revision)
        if fetched is None:
            return RuleRefreshResult(False, current.revision)
        self._rules.replace(fetched)
        return RuleRefreshResult(True, fetched.revision)

    def push_pending(self) -> SyncResult:
        """Send one batch of pending records and apply the per-entry answer; missing entries stay pending."""
        readings = self._readings.find_pending(self._batch_size)
        alerts = self._alerts.find_pending(self._batch_size)
        if not readings and not alerts:
            return SyncResult(0, 0, 0, 0)

        entries = self._cloud.send_batch(readings, alerts)
        readings_by_id = {str(reading.reading_id): reading for reading in readings}
        alerts_by_id = {str(alert.alert_id): alert for alert in alerts}
        handled: set[str] = set()
        synced = 0
        rejected = 0
        for entry in entries:
            status = self._resolved_status(entry.status)
            if status is None or entry.entry_id in handled:
                continue
            kind = entry.kind.casefold()
            if kind == "reading" and entry.entry_id in readings_by_id:
                reading = readings_by_id[entry.entry_id]
                if status is SyncStatus.SYNCED:
                    self._readings.update_sync_status(reading.mark_synced())
                else:
                    self._readings.update_sync_status(
                        reading.mark_rejected(self._rejection_reason(entry.reason))
                    )
            elif kind == "alert" and entry.entry_id in alerts_by_id:
                alert = alerts_by_id[entry.entry_id]
                if status is SyncStatus.SYNCED:
                    self._alerts.update_sync_status(alert.mark_synced())
                else:
                    self._alerts.update_sync_status(
                        alert.mark_rejected(self._rejection_reason(entry.reason))
                    )
            else:
                continue
            handled.add(entry.entry_id)
            if status is SyncStatus.SYNCED:
                synced += 1
            else:
                rejected += 1
        return SyncResult(len(readings), len(alerts), synced, rejected)

    def synchronize(self) -> SynchronizationResult:
        """Refresh the cached rules, then push pending records."""
        try:
            refresh = self.refresh_rules()
            push = self.push_pending()
        except CloudUnavailable:
            logger.warning("Cloud synchronization deferred; records remain pending")
            raise
        except CloudCredentialsRejected:
            logger.warning("ICHU cloud rejected the edge gateway credentials; records remain pending")
            raise
        logger.info(
            "Synchronized %d readings and %d alerts (%d synced, %d rejected)",
            push.sent_readings,
            push.sent_alerts,
            push.synced,
            push.rejected,
        )
        return SynchronizationResult(
            refresh.updated,
            refresh.revision,
            push.sent_readings,
            push.sent_alerts,
            push.synced,
            push.rejected,
        )

    @staticmethod
    def _resolved_status(status: str) -> SyncStatus | None:
        """Map a cloud status to a synchronization state, or ``None`` if unknown."""
        if status in _ACCEPTED_STATUSES:
            return SyncStatus.SYNCED
        if status in _REJECTED_STATUSES:
            return SyncStatus.REJECTED
        return None

    @staticmethod
    def _rejection_reason(reason: str | None) -> str:
        """Return the cloud's reason, or a default when the cloud gave none."""
        if reason is None or not reason.strip():
            return _DEFAULT_REJECTION_REASON
        return reason.strip()
