"""Command-line interface for the Monitoring bounded context."""

import itertools
import logging
import time
from collections.abc import Callable

import click
from flask import current_app
from flask.cli import with_appcontext

from monitoring.application.errors import (
    CloudCredentialsRejected,
    CloudUnavailable,
    SynchronizationDisabled,
)
from monitoring.application.services import SynchronizationResult, SynchronizationService

logger = logging.getLogger(__name__)

_MAX_BACKOFF_SECONDS = 300
_NOT_CONFIGURED_MESSAGE = (
    "Cloud synchronization is not configured; set CLOUD_API_BASE_URL and EDGE_GATEWAY_KEY"
)
_FAILED_CYCLE_MESSAGE = "Cloud synchronization failed; records remain pending"


def next_delay_seconds(interval_seconds: int, consecutive_failures: int) -> int:
    """Back off while the cloud is down or waking up (Render free plan), instead of hammering it."""
    if consecutive_failures == 0:
        return interval_seconds
    return min(interval_seconds * 2**consecutive_failures, _MAX_BACKOFF_SECONDS)


def run_synchronizer(
    service: SynchronizationService,
    interval_seconds: int,
    batch_size: int,
    sleep: Callable[[float], None] = time.sleep,
    cycles: int | None = None,
) -> None:
    """Run synchronization cycles every interval, or only ``cycles`` of them when a limit is given."""
    failures = 0
    for _ in (itertools.count() if cycles is None else range(cycles)):
        try:
            result = _synchronize(service)
        except (CloudUnavailable, CloudCredentialsRejected):
            failures += 1
            sleep(next_delay_seconds(interval_seconds, failures))
            continue
        failures = 0
        # A full batch that settled records means more are waiting, so the next cycle starts at once.
        full_batch = result.sent_readings == batch_size or result.sent_alerts == batch_size
        if full_batch and result.synced + result.rejected > 0:
            continue
        sleep(interval_seconds)


def _synchronize(service: SynchronizationService) -> SynchronizationResult:
    """Run one cycle, reporting a missing cloud configuration as a command error."""
    try:
        return service.synchronize()
    except SynchronizationDisabled as error:
        raise click.ClickException(_NOT_CONFIGURED_MESSAGE) from error


@click.command("synchronize")
@click.option(
    "--interval",
    type=click.IntRange(5, 3600),
    help="Seconds between cycles. Defaults to SYNC_INTERVAL_SECONDS.",
)
@click.option("--once", is_flag=True, help="Run a single cycle and exit.")
@with_appcontext
def synchronize_command(interval: int | None, once: bool) -> None:
    """Push pending telemetry and alerts to the ICHU cloud, repeatedly unless --once is given."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    service = current_app.extensions["synchronization_service"]
    if once:
        try:
            _synchronize(service)
        except (CloudUnavailable, CloudCredentialsRejected) as error:
            raise click.ClickException(_FAILED_CYCLE_MESSAGE) from error
        return
    if interval is None:
        interval = current_app.config["SYNC_INTERVAL_SECONDS"]
    try:
        run_synchronizer(service, interval, current_app.config["SYNC_BATCH_SIZE"])
    except KeyboardInterrupt:
        logger.info("Synchronizer stopped")
