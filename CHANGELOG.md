# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project intends to follow [Semantic Versioning](https://semver.org/) for published
releases. Implementation-only commits are consolidated instead of being listed
individually.

## [Unreleased]

### Changed

- Documented the backend gateway key format `{gatewayId}.{secret}` and the key of the
  seeded development gateway, which replaces the backend's development simulation.

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
