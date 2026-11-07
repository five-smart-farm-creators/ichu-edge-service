# ICHU Edge Service

`ichu_edge_service` is the edge gateway software of the ICHU ranch-monitoring
solution. It runs on the **Portable Edge Gateway** installed on the ranch, where
connectivity to the cloud is intermittent. The service receives telemetry from
**Cattle Band** smart collars over the local network, stores every reading
locally, raises alerts from cached rules even while offline, and synchronizes
pending data with the ICHU cloud API (Operations & Monitoring) when the
connection is available.

The service follows a Domain-Driven Design (DDD) approach. It separates device
authentication (IAM) from cattle monitoring (Monitoring), and both contexts reach
other systems only through anti-corruption layers.

## Current Scope

Version `0.1.0` implements the edge side of the monitoring capability:

- **Implemented**
  - device authentication with `device_id` and `X-API-Key`
  - ingestion of body temperature, activity index, battery level and GPS position
  - idempotent persistence in SQLite with a per-reading synchronization status
  - local alerting from cached thresholds and geofences, with deduplication
  - store-and-forward synchronization of readings and alerts in batches
  - refresh of the cached monitoring rules from the cloud, with revision checks
  - an operational status endpoint and a local alert listing for the display panel
  - development seeding of devices and lot assignments
- **Not implemented yet**
  - water trough controller
  - alert acknowledgement
  - Bluetooth Low Energy (BLE) communication with the collars
  - a background scheduler for synchronization (it is triggered through the API)

## Why DDD for an IoT Edge Service?

Collars, rules, alerts, and cloud contracts change at different speeds. DDD keeps
those concerns separate: the monitoring rules can evolve without touching how
devices authenticate, and the cloud contract can change without touching the
domain model.

### 1. IAM (Identity and Access Management)

- **Core concept**: `Device`
- **Primary responsibility**: verify the API key of a registered device

### 2. Monitoring

- **Core concepts**: `TelemetryReading`, `MonitoringAlert`, `MonitoringRuleSet`
- **Primary responsibility**: validate and persist readings, evaluate them against
  the cached rules, and synchronize the results with the cloud

Monitoring delegates device authentication to IAM through the
`IamAntiCorruptionLayer`. It talks to the ICHU cloud API through the
`IchuCloudAntiCorruptionLayer`, which translates the camelCase cloud contract into
Monitoring's domain language.

## Layered Architecture

Each bounded context follows the same structure:

- **Domain**: value objects, aggregates, domain services, and repository protocols;
  no Flask, Peewee, or `requests` imports
- **Application**: use-case services that coordinate aggregates and repositories
- **Infrastructure**: Peewee models, repository implementations, and the cloud HTTP client
- **Integration**: anti-corruption layers to other contexts and to the cloud
- **Interfaces**: Flask Blueprints and Pydantic request models

### Project Structure

```text
ichu-edge-service/
├── app.py                      application factory and development runner
├── config.py                   environment configuration loader
├── shared/
│   ├── infrastructure/         SQLite database singleton and table creation
│   └── interfaces/             status endpoint
├── iam/
│   ├── domain/
│   ├── application/
│   ├── infrastructure/
│   └── interfaces/
├── monitoring/
│   ├── domain/
│   ├── application/
│   ├── infrastructure/
│   ├── integration/
│   └── interfaces/
├── tests/
└── docs/
```

## Technical Stories

Technical stories describe the integration contract from the perspective of the
collar and gateway firmware and of the platform developers.

- Source of truth: [`docs/user-stories.md`](docs/user-stories.md)
- Story style: `As a ... I want to ... so that ...` with Gherkin acceptance
  criteria (`Given/When/Then`)

### Story-to-Context Mapping

- **Monitoring bounded context**: `TS-EDGE-001`, `TS-EDGE-003`, `TS-EDGE-004`,
  `TS-EDGE-005`, `TS-EDGE-006`
- **IAM bounded context**: `TS-EDGE-002`
- **Application bootstrap and shared infrastructure**: `TS-EDGE-007`

## Technology Stack

- Python 3.13
- Flask 3.1 (Blueprints and application factory)
- Peewee 4.5 and SQLite
- Pydantic 2
- python-dotenv
- requests

Exact dependency versions are declared in [`requirements.txt`](requirements.txt).

## Getting Started

### 1. Create a virtual environment and install dependencies

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

bash:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure local development settings

The development profile starts without any cloud or secret: cloud synchronization is
disabled (`CLOUD_API_BASE_URL` is empty) and development seeding is off. To seed the
development devices, set the values below. Use placeholder values that are unique to
your machine and never commit them.

Windows PowerShell:

```powershell
$env:APP_ENV = "development"
$env:SEED_DEVELOPMENT_DATA = "true"
$env:DEVELOPMENT_DEVICE_API_KEY = "replace-with-a-unique-local-device-key"
```

bash:

```sh
export APP_ENV=development
export SEED_DEVELOPMENT_DATA=true
export DEVELOPMENT_DEVICE_API_KEY='replace-with-a-unique-local-device-key'
```

To synchronize with a local ICHU backend, see [Synchronization with the Cloud](#synchronization-with-the-cloud).

### 3. Run the service

Windows PowerShell:

```powershell
.\.venv\Scripts\python app.py
```

bash:

```sh
python app.py
```

### 4. Run the test suite

Windows PowerShell:

```powershell
.\.venv\Scripts\python -m unittest discover -s tests -v
```

bash:

```sh
python -m unittest discover -s tests -v
```

## Configuration

The checked-in environment files define the settings for each environment.
`APP_ENV` selects `.env.development` or `.env.production`, and process environment
variables override values in that file. `config.py` parses and validates every
setting; it does not supply defaults for missing settings.

| Key | Development | Production | Rule |
|---|---|---|---|
| `APP_ENV` | `development` | `production` | Must match the selected file |
| `DEBUG` | `true` | `false` | Must be `false` in production |
| `DATABASE_PATH` | `ichu_edge.db` | `var/ichu_edge.db` | Non-empty; relative paths resolve from the project root |
| `HOST` | `127.0.0.1` | `0.0.0.0` | Non-empty |
| `PORT` | `5000` | `5000` | Integer from 1 to 65535 |
| `SEED_DEVELOPMENT_DATA` | `false` | `false` | Must be `false` in production |
| `DEVELOPMENT_DEVICE_API_KEY` | empty | empty | Required when seeding is enabled |
| `CLOUD_API_BASE_URL` | empty | empty | Empty disables synchronization; otherwise an http(s) URL |
| `EDGE_GATEWAY_ID` | `c07d8c6e-6d9b-4cb7-9add-7b8c9daebfc6` | empty | UUID; required when `CLOUD_API_BASE_URL` is set |
| `EDGE_GATEWAY_KEY` | empty | empty | Secret; required when `CLOUD_API_BASE_URL` is set; never committed |
| `ALERT_DEDUPLICATION_MINUTES` | `15` | `15` | Integer from 1 to 1440 |
| `SYNC_BATCH_SIZE` | `100` | `100` | Integer from 1 to 500 |
| `CLOUD_TIMEOUT_SECONDS` | `10` | `10` | Integer from 1 to 60 |

For production, set `APP_ENV=production` in the process environment and supply
secrets through the deployment's secret mechanism. Production disables debug mode
and development seeding, and should run behind a production WSGI server rather
than Flask's built-in server.

## Runtime Behavior

Before the first request is served, `create_app()`:

- loads and validates the configuration
- initializes the SQLite database and creates the `devices`, `telemetry_readings`,
  `monitoring_alerts`, `monitoring_rule_sets`, `monitoring_thresholds`,
  `monitoring_geofences`, and `device_assignments` tables when they are missing
- migrates stored device credentials to salted PBKDF2 verifiers and adds the
  revocation column to existing device tables
- seeds the development devices and lot assignments only when explicitly enabled

Monitoring then processes each reading in this order:

1. The device is authenticated through the IAM anti-corruption layer.
2. The reading is created through the domain factory and persisted idempotently.
3. A retry of an accepted reading returns the stored reading and raises no alerts.
4. A new reading is evaluated against the cached thresholds and geofences of its
   device assignment.
5. Each violation becomes an alert unless an alert of the same type was raised for
   the device within `ALERT_DEDUPLICATION_MINUTES`.

Every reading and alert starts with the `pending` synchronization status and is
marked `synced` or `rejected` once the cloud answers.

## Development Devices

When `SEED_DEVELOPMENT_DATA=true`, the following devices are registered with the
`DEVELOPMENT_DEVICE_API_KEY` value. Their identifiers match the cloud development
simulation.

| Device ID | Lot | Life stage |
|---|---|---|
| `9d4a5f3b-3a6e-4f84-8daa-4e5f6a7b8c93` | `6a1f2c0e-0d3b-4c51-9a77-1b2c3d4e5f60` | `MilkingCow` |
| `ae5b6a4c-4b7f-4a95-9ebb-5f6a7b8c9da4` | `6a1f2c0e-0d3b-4c51-9a77-1b2c3d4e5f60` | `Heifer` |
| `bf6c7b5d-5c8a-4ba6-8fcc-6a7b8c9daeb5` | none (display panel) | none |

The display panel authenticates the alert listing with its own device identifier and key.

Life stages come from the cloud Cattle Information values (`Calf`, `Heifer`,
`MilkingCow`, `FatteningBull`). Threshold matching is case-insensitive.

> [!WARNING]
> These devices are for local development only. Do not enable development seeding
> or reuse development credentials in production or on a real ranch deployment.

## API Contract

Device-facing payloads use `snake_case`. The status and synchronization responses
use `camelCase`, as the cloud contract does. Datetimes are ISO 8601 with a `Z`
suffix. Error bodies have the form `{"error": "<message>"}`.

### Status

`GET /api/v1/status` (no authentication)

`200 OK`

```json
{
  "status": "ok",
  "service": "ichu-edge-service",
  "rulesRevision": 0,
  "pendingReadings": 0,
  "pendingAlerts": 0
}
```

```sh
curl http://127.0.0.1:5000/api/v1/status
```

### Authenticate a device

`POST /api/v1/iam/authentications`

Checks that the device is registered, active, and presents its matching API key.

#### Required headers

- `Content-Type: application/json`
- `X-API-Key: <device api key>`

#### Request body

```json
{
  "device_id": "9d4a5f3b-3a6e-4f84-8daa-4e5f6a7b8c93"
}
```

#### Responses

- `200 OK`: credentials are valid; body includes `device_id` and `"authenticated": true`
- `400 Bad Request`: invalid JSON shape or unexpected fields
- `401 Unauthorized`: missing or invalid API key, unknown or revoked device, or a
  `device_id` that is not a UUID
- `415 Unsupported Media Type`: request is not JSON

```sh
curl -X POST http://127.0.0.1:5000/api/v1/iam/authentications \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: ${DEVELOPMENT_DEVICE_API_KEY}" \
  --data-binary @- <<'JSON'
{
  "device_id": "9d4a5f3b-3a6e-4f84-8daa-4e5f6a7b8c93"
}
JSON
```

### Submit a telemetry reading

`POST /api/v1/monitoring/telemetry-readings`

#### Required headers

- `Content-Type: application/json`
- `X-API-Key: <device api key>`

#### Request body

```json
{
  "device_id": "9d4a5f3b-3a6e-4f84-8daa-4e5f6a7b8c93",
  "event_id": "collar-9d4a-000042",
  "recorded_at": "2026-11-07T10:00:00-05:00",
  "body_temperature": 38.5,
  "activity_index": 42.0,
  "battery_level": 87.0,
  "position": {
    "latitude": -16.5,
    "longitude": -68.1,
    "accuracy_meters": 4.0
  }
}
```

#### Request fields

- `device_id` (`string`, required): canonical UUID of the device
- `event_id` (`string`, required, 1 to 128 characters): device-generated identity,
  reused unchanged when the same reading is retried
- `recorded_at` (`string`, optional): ISO 8601 observation time; when omitted, the
  service uses the current UTC time
- `body_temperature` (`number`, optional): Celsius, from 30.0 to 45.0
- `activity_index` (`number`, optional): from 0 to 100
- `battery_level` (`number`, optional): percentage, from 0 to 100
- `position` (`object`, optional): `latitude` from -90 to 90, `longitude` from -180
  to 180, and optional `accuracy_meters` of zero or more

At least one measurement (`body_temperature`, `activity_index`, `battery_level`, or
`position`) is required.

#### Success response

`201 Created` for a new reading; `200 OK` for an identical retry, which returns the
original reading and no alerts.

```json
{
  "reading": {
    "reading_id": "a2c9b852-d5f2-458f-9225-e344e2a99056",
    "device_id": "9d4a5f3b-3a6e-4f84-8daa-4e5f6a7b8c93",
    "event_id": "collar-9d4a-000042",
    "recorded_at": "2026-11-07T15:00:00Z",
    "body_temperature": 38.5,
    "activity_index": 42.0,
    "battery_level": 87.0,
    "position": {
      "latitude": -16.5,
      "longitude": -68.1,
      "accuracy_meters": 4.0
    },
    "sync_status": "pending"
  },
  "alerts": []
}
```

#### Error responses

- `400 Bad Request`: invalid JSON or payload, missing measurement, a value out of
  range, or a naive timestamp
- `401 Unauthorized`: missing `device_id` or `X-API-Key`, or an invalid device and key pair
- `409 Conflict`: the `event_id` was already used for different telemetry
- `415 Unsupported Media Type`: request is not JSON

```sh
curl -X POST http://127.0.0.1:5000/api/v1/monitoring/telemetry-readings \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: ${DEVELOPMENT_DEVICE_API_KEY}" \
  --data-binary @- <<'JSON'
{
  "device_id": "9d4a5f3b-3a6e-4f84-8daa-4e5f6a7b8c93",
  "event_id": "collar-9d4a-000042",
  "recorded_at": "2026-11-07T10:00:00-05:00",
  "body_temperature": 38.5
}
JSON
```

### List recent alerts

`GET /api/v1/monitoring/alerts?device_id=<uuid>&limit=20`

Returns the newest alerts of the gateway, newest first. The caller identifies itself with its
`device_id` and presents that device's API key, for example the display panel. The listing is
not filtered by device; `device_id` only authenticates the caller.

#### Required headers

- `X-API-Key: <device api key>`

#### Query parameters

- `device_id` (`string`, required): canonical UUID of the calling device
- `limit` (`integer`, optional, default `20`): from 1 to 100

#### Responses

- `200 OK`: body holds `alerts`, each with `alert_id`, `device_id`, `type`
  (`Temperature`, `Activity`, or `Geofence`), `severity` (`Informative`, `Warning`, or
  `Critical`), `reading_ids`, `configuration_revision`, `raised_at`, the threshold or
  geofence fields, and `sync_status`
- `400 Bad Request`: `limit` is not an integer from 1 to 100
- `401 Unauthorized`: missing `device_id` or `X-API-Key`, or a device and key pair that does not match

```sh
curl "http://127.0.0.1:5000/api/v1/monitoring/alerts?device_id=bf6c7b5d-5c8a-4ba6-8fcc-6a7b8c9daeb5&limit=5" \
  -H "X-API-Key: ${DEVELOPMENT_DEVICE_API_KEY}"
```

### Synchronize with the cloud

`POST /api/v1/synchronizations`

This is an operator action on the local network. It carries no device credentials. The service
refreshes the cached rules when the cloud has a newer revision, then pushes pending readings and
alerts in one batch.

#### Responses

- `200 OK`: body holds `rulesUpdated`, `rulesRevision`, `sentReadings`, `sentAlerts`, `synced`,
  and `rejected`
- `502 Bad Gateway`: the ICHU cloud API rejected the edge gateway credentials; pending records
  are kept
- `503 Service Unavailable`: synchronization is not configured
  (`"Cloud synchronization is not configured"`), or the cloud API cannot be reached (pending
  records are kept)

```sh
curl -X POST http://127.0.0.1:5000/api/v1/synchronizations
```

## Synchronization with the Cloud

Synchronization is disabled in the development profile. To synchronize with the ICHU backend
running locally (development simulation), set these variables in the process environment before
starting the service.

Windows PowerShell:

```powershell
$env:CLOUD_API_BASE_URL = "http://localhost:5099"
$env:EDGE_GATEWAY_KEY = "development-gateway-key"
```

bash:

```sh
export CLOUD_API_BASE_URL=http://localhost:5099
export EDGE_GATEWAY_KEY=development-gateway-key
```

`development-gateway-key` is the development simulation gateway key of the ICHU backend. It is for
local development only and must never be used on a real deployment.

- **Rules**: the gateway requests `GET /api/v1/edge-gateways/{gatewayId}/monitoring-rules` with the
  `afterRevision` of its cached rules. A `304 Not Modified` answer keeps the cache; a `200 OK`
  answer replaces thresholds, geofences, and revision.
- **Batches**: pending readings and alerts are sent to `POST /api/v1/telemetry/batch` with the
  `X-Gateway-Key` header. The cloud's per-entry status decides the local status: `Accepted` and
  `Duplicate` mark the entry `synced`; `Rejected` and `Conflict` mark it `rejected` with the
  cloud's reason. Entries missing from the answer stay `pending`.
- **Credentials**: an HTTP `401` or `403` from the cloud is reported as rejected gateway
  credentials (`502` from the synchronization endpoint). Records stay `pending`.
- **Store and forward**: when the cloud cannot be reached, nothing is marked and no record is lost.
  The next synchronization sends the same records again.

## Operational Notes

- **Credential management**: development keys are for local use only. Real devices
  need a secure provisioning flow. IAM stores salted verifiers, not the API keys, and
  supports rotation and revocation through application services.
- **Persistence**: SQLite suits a single gateway. It is not designed for high-write
  concurrency or several writer processes.
- **Clock**: the gateway evaluates deduplication with its own UTC clock. Keep the
  gateway time synchronized.
- **Geofences**: distances use a local planar approximation around the reading. It is
  accurate at lot scale; polygons that cross the antimeridian are not supported.
- **Alert listing**: the listing is authenticated per device; it returns the alerts of every
  device on the gateway.
- **Observability**: the service logs alert creation and synchronization outcomes.
  Add structured logging and a process supervisor before production use.
- **Schema changes**: tables are created when missing. Use a versioned migration
  workflow and back up the SQLite database before changing a deployed schema.

## Documentation

Additional documentation is available in [`docs/`](docs):

- [`docs/user-stories.md`](docs/user-stories.md): technical stories and acceptance
  criteria for the collar, gateway, and cloud integration
- [`docs/class-diagram.puml`](docs/class-diagram.puml): PlantUML diagram of the
  bounded contexts, layers, and relationships
- [`docs/adrs.md`](docs/adrs.md): architecture decisions and their consequences

Project history is tracked in [`CHANGELOG.md`](CHANGELOG.md) using the Keep a
Changelog format.

Environment configuration defaults:

- [`.env.development`](.env.development): local development defaults without secrets
- [`.env.production`](.env.production): production defaults without secrets; provide
  real secrets externally

## License

This project is proprietary. See [`LICENSE.md`](LICENSE.md) for details.
