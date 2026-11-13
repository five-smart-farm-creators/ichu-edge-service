# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project intends to follow [Semantic Versioning](https://semver.org/) for published
releases. Implementation-only commits are consolidated instead of being listed
individually.

## [Unreleased]

### Added

- GitHub Actions `test` job that runs the unit test suite on pull requests.
- Periodic synchronizer command, `flask --app app synchronize`, with `--interval` and `--once`,
  back-off after failed cycles, and a clean stop on Ctrl+C.
- `SYNC_INTERVAL_SECONDS` configuration, from 5 to 3600 seconds (default 15).
- `deferred` count in the `POST /api/v1/synchronizations` response.
- Collar assignments (lot and life stage) are downloaded from the cloud on every synchronization, and a
  collar that the cloud no longer lists loses its assignment. Each cycle logs the number downloaded.
- `assignments` count in the `POST /api/v1/synchronizations` response.
- `flask --app app register-device DEVICE_ID` registers a collar with the key in the `DEVICE_API_KEY`
  environment variable, so a collar that exists only in the cloud can authenticate.
- `invalid_reasons` in telemetry readings: a collar reports why a measurement it could not capture is
  missing. The reason is stored, echoed in the response, and sent to the cloud as `invalidReason`.
  Existing databases gain the column when the service starts.
- Collar simulator, `scripts/simulate_collar.py`, which sends the firmware telemetry JSON with its
  retry rules to the edge, for tests without hardware.

### Changed

- Documented the backend gateway key format `{gatewayId}.{secret}` and the key of the
  seeded development gateway, which replaces the backend's development simulation.
- The gateway id is derived from `EDGE_GATEWAY_KEY`; `EDGE_GATEWAY_ID` is removed.
- Production requires an https `CLOUD_API_BASE_URL`, except for localhost.
- SQLite runs in WAL mode so reads continue while another process writes.
- A cloud `Conflict` keeps the entry pending and is retried up to 5 times before the entry is
  rejected with the cloud's reason.
- A batch that the cloud refuses with HTTP 400 or 422 is split, so only the invalid entries are retried and, after 5 refusals, rejected.
- Existing databases gain the `sync_attempts` column when the service starts.
- Development seeding registers the development devices only; their lots and life stages come from the cloud.
- `position.accuracy_meters` is required, and a geofence is violated only when the position is farther
  outside than the tolerance plus the GPS accuracy.

## [0.1.0] - 2026-11-07

### Added

- Device authentication with `device_id` and `X-API-Key` through the IAM bounded
  context, with salted PBKDF2 verifiers, rotation, and revocation at the domain level.
- Telemetry ingestion for body temperature, activity index, battery level, and GPS
  position, with idempotent retries and `409 Conflict` for reused event IDs.
- Local alerting against cached thresholds by life stage and geofences by lot, with
  alert deduplication per device and type.
- Store-and-forward synchronization with the ICHU cloud API: rules refresh with
  revision checks, and batched readings and alerts with per-entry outcomes.
- `GET /api/v1/status`, `GET /api/v1/monitoring/alerts` (authenticated per device), and
  `POST /api/v1/synchronizations` endpoints.
- Application factory, environment-specific configuration with validation, and
  development seeding of devices and lot assignments.
- Architecture decision records, technical user stories, and a class diagram.
- Cloud answers of `Conflict` mark entries rejected with the cloud's reason.
- Rejected gateway credentials (HTTP 401 or 403) return `502 Bad Gateway` from synchronization
  and keep records pending.
- Cloud synchronization is disabled in the development profile until `CLOUD_API_BASE_URL` is set.
