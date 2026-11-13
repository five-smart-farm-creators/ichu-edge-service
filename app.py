"""Flask application entry point for the ICHU Edge Service."""

import uuid
from datetime import timedelta
from pathlib import Path

from flask import Flask

from config import AppConfig, load_config
from iam.application.services import AuthApplicationService
from iam.infrastructure.repositories import DeviceRepository
from iam.interfaces.services import iam_api
from monitoring.application.services import (
    CloudGateway,
    MonitoringQueryService,
    SynchronizationService,
    TelemetryIngestionService,
)
from monitoring.domain.services import RuleEvaluator
from monitoring.domain.value_objects import DeviceAssignment
from monitoring.infrastructure.cloud_client import IchuCloudClient
from monitoring.infrastructure.repositories import (
    DeviceAssignmentRepository,
    MonitoringAlertRepository,
    MonitoringRuleSetRepository,
    TelemetryReadingRepository,
)
from monitoring.integration.cloud_anti_corruption_layer import (
    DisabledCloudAntiCorruptionLayer,
    IchuCloudAntiCorruptionLayer,
)
from monitoring.integration.iam_anti_corruption_layer import IamAntiCorruptionLayer
from monitoring.interfaces.commands import synchronize_command
from monitoring.interfaces.services import monitoring_api
from shared.infrastructure.database import configure_database, db, init_db
from shared.interfaces.status import status_api

DEVELOPMENT_LOT_ID = uuid.UUID("6a1f2c0e-0d3b-4c51-9a77-1b2c3d4e5f60")
DEVELOPMENT_DEVICES = (
    ("9d4a5f3b-3a6e-4f84-8daa-4e5f6a7b8c93", DEVELOPMENT_LOT_ID, "MilkingCow"),
    ("ae5b6a4c-4b7f-4a95-9ebb-5f6a7b8c9da4", DEVELOPMENT_LOT_ID, "Heifer"),
    ("bf6c7b5d-5c8a-4ba6-8fcc-6a7b8c9daeb5", None, None),
)


def create_app() -> Flask:
    """Create and initialize the Flask application."""
    app = Flask(__name__)
    settings = load_config(Path(__file__).resolve().parent)
    app.config.from_mapping(settings)
    configure_database(settings["DATABASE_PATH"])
    init_db()
    device_repository = DeviceRepository()
    device_repository.migrate_schema()
    TelemetryReadingRepository().migrate_schema()
    MonitoringAlertRepository().migrate_schema()

    app.register_blueprint(status_api)
    app.register_blueprint(iam_api)
    app.register_blueprint(monitoring_api)
    app.cli.add_command(synchronize_command)

    iam_service = AuthApplicationService(device_repository)
    iam_acl = IamAntiCorruptionLayer(iam_service)
    reading_repository = TelemetryReadingRepository()
    alert_repository = MonitoringAlertRepository()
    rule_repository = MonitoringRuleSetRepository()
    assignment_repository = DeviceAssignmentRepository()

    app.extensions["iam_authentication_service"] = iam_service
    app.extensions["iam_anti_corruption_layer"] = iam_acl
    app.extensions["telemetry_ingestion_service"] = TelemetryIngestionService(
        iam_acl,
        reading_repository,
        alert_repository,
        rule_repository,
        assignment_repository,
        RuleEvaluator(),
        timedelta(minutes=settings["ALERT_DEDUPLICATION_MINUTES"]),
    )
    app.extensions["monitoring_query_service"] = MonitoringQueryService(
        reading_repository, alert_repository, rule_repository
    )
    app.extensions["synchronization_service"] = SynchronizationService(
        _cloud_gateway(settings),
        reading_repository,
        alert_repository,
        rule_repository,
        settings["SYNC_BATCH_SIZE"],
    )

    if settings["SEED_DEVELOPMENT_DATA"]:
        seed_development_data(
            iam_service,
            assignment_repository,
            settings["DEVELOPMENT_DEVICE_API_KEY"],
        )

    @app.teardown_appcontext
    def close_database(_exception: BaseException | None) -> None:
        if not db.is_closed():
            db.close()

    return app


def seed_development_data(
    iam_service: AuthApplicationService,
    assignment_repository: DeviceAssignmentRepository,
    api_key: str,
) -> None:
    """Register the development devices and their lot assignments."""
    for device_id, lot_id, life_stage in DEVELOPMENT_DEVICES:
        iam_service.get_or_create_device(device_id, api_key)
        if lot_id is not None and assignment_repository.find(device_id) is None:
            assignment_repository.save(DeviceAssignment(device_id, lot_id, life_stage))


def _cloud_gateway(settings: AppConfig) -> CloudGateway:
    """Return the cloud port: an HTTP adapter when a cloud URL is configured."""
    if settings["CLOUD_API_BASE_URL"] is None:
        return DisabledCloudAntiCorruptionLayer()
    client = IchuCloudClient(
        settings["CLOUD_API_BASE_URL"],
        settings["EDGE_GATEWAY_ID"],
        settings["EDGE_GATEWAY_KEY"],
        settings["CLOUD_TIMEOUT_SECONDS"],
    )
    return IchuCloudAntiCorruptionLayer(client)


app = create_app()

if __name__ == "__main__":
    if app.config["APP_ENV"] != "development":
        raise RuntimeError(
            "The built-in Flask server is development-only; use a production WSGI server"
        )
    app.run(
        debug=app.config["DEBUG"],
        host=app.config["HOST"],
        port=app.config["PORT"],
    )
