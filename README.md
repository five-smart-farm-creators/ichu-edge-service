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

The service implements the edge side of the monitoring capability:

- **Implemented**
  - device authentication with `device_id` and `X-API-Key`
  - ingestion of body temperature, activity index, battery level and GPS position
  - idempotent persistence in SQLite with a per-reading synchronization status
  - local alerting from cached thresholds and geofences, with deduplication
  - store-and-forward synchronization of readings and alerts in batches
  - periodic synchronization as a separate command (`flask --app app synchronize`)
  - refresh of the cached monitoring rules from the cloud, with revision checks
  - refresh of the collar assignments (lot and life stage) from the cloud
  - registration of collars with `flask --app app register-device`
  - an operational status endpoint and a local alert listing for the display panel
  - development seeding of devices
- **Not implemented yet**
  - water trough controller
  - alert acknowledgement
  - Bluetooth Low Energy (BLE) communication with the collars

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
  `TS-EDGE-005`, `TS-EDGE-006`, `TS-EDGE-008`, `TS-EDGE-009`
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
| `CLOUD_API_BASE_URL` | empty | empty | Empty disables synchronization; otherwise an http(s) URL, which must be https in production except for localhost |
| `EDGE_GATEWAY_KEY` | empty | empty | `{gatewayId}.{secret}` issued by the backend; the gateway id is read from the key; required when `CLOUD_API_BASE_URL` is set; never committed |
| `ALERT_DEDUPLICATION_MINUTES` | `15` | `15` | Integer from 1 to 1440 |
| `SYNC_BATCH_SIZE` | `100` | `100` | Integer from 1 to 500 |
| `SYNC_INTERVAL_SECONDS` | `15` | `15` | Integer from 5 to 3600; the pause between synchronizer cycles |
| `CLOUD_TIMEOUT_SECONDS` | `10` | `10` | Integer from 1 to 60; a Render cold start can take about a minute, so a timed-out cycle is retried by the next one |

For production, set `APP_ENV=production` in the process environment and supply
secrets through the deployment's secret mechanism. Production disables debug mode
and development seeding, and should run behind a production WSGI server rather
than Flask's built-in server.

Keep `EDGE_GATEWAY_KEY` and `DEVELOPMENT_DEVICE_API_KEY` out of the `.env.*` files. Set them as
user environment variables, as shown in [Synchronization with the Cloud](#synchronization-with-the-cloud).

## Runtime Behavior

Before the first request is served, `create_app()`:

- loads and validates the configuration
- initializes the SQLite database and creates the `devices`, `telemetry_readings`,
  `monitoring_alerts`, `monitoring_rule_sets`, `monitoring_thresholds`,
  `monitoring_geofences`, and `device_assignments` tables when they are missing
- migrates stored device credentials to salted PBKDF2 verifiers, adds the
  revocation column to existing device tables, adds the `sync_attempts`
  column to existing reading and alert tables, and adds the `invalid_reasons`,
  `heart_rate`, and `methane_index` columns to existing reading tables
- seeds the development devices only when explicitly enabled

Monitoring then processes each reading in this order:

1. The device is authenticated through the IAM anti-corruption layer.
2. The reading is created through the domain factory and persisted idempotently.
3. A retry of an accepted reading returns the stored reading and raises no alerts.
4. A new reading is evaluated against the cached thresholds and geofences of its
   device assignment.
5. Each violation becomes an alert unless an alert of the same type was raised for
   the device within `ALERT_DEDUPLICATION_MINUTES`.

A measurement with an invalid reason has no value, so it is not evaluated. A geofence
is violated only when the position is farther outside its polygon than the geofence
tolerance plus the reported `accuracy_meters`, so the GPS error alone does not raise an alert.

Every reading and alert starts with the `pending` synchronization status and is
marked `synced` or `rejected` once the cloud answers. A conflicting answer keeps it
`pending` until its attempts run out (see [Synchronize with the cloud](#synchronize-with-the-cloud)).

## Development Devices

When `SEED_DEVELOPMENT_DATA=true`, the following devices are registered with the
`DEVELOPMENT_DEVICE_API_KEY` value. Their identifiers match the collars seeded in the
backend's development data. The gateway seeds no lots or life stages for them: they arrive
with the cloud assignments at the next synchronization, and a collar is evaluated only after that.

| Device ID | Role |
|---|---|
| `9d4a5f3b-3a6e-4f84-8daa-4e5f6a7b8c93` | collar |
| `ae5b6a4c-4b7f-4a95-9ebb-5f6a7b8c9da4` | collar |
| `bf6c7b5d-5c8a-4ba6-8fcc-6a7b8c9daeb5` | display panel |

The display panel authenticates the alert listing with its own device identifier and key.

Life stages come from the cloud Cattle Information values (`Calf`, `Heifer`,
`MilkingCow`, `FatteningBull`). Threshold matching is case-insensitive.

> [!WARNING]
> These devices are for local development only. Do not enable development seeding
> or reuse development credentials in production or on a real ranch deployment.

## Collar Simulator

`scripts/simulate_collar.py` sends the telemetry JSON of the collar firmware
(`TelemetryPayload.cpp`) to the edge, with the same retry rules: the `event_id` stays the
same on every retry, a `200` or `201` confirms the reading, and a `400` or `409` is a final
rejection. Use it to test the collar, edge, and cloud path without hardware or Wokwi. It only
needs `requests`, which is already in `requirements.txt`.

Modes (`--mode`):

- `normal`: body temperature from 38.3 to 39.0 °C and heart rate from 55 to 75 bpm, inside the
  development lot.
- `fever`: body temperature from 40.0 to 40.6 °C, above the 39.5 °C maximum of the development
  rule, and heart rate from 55 to 75 bpm.
- `escape`: a position about 2 km outside the development lot, and heart rate from 55 to 75 bpm.
- `sensor-failure`: no `body_temperature` or `heart_rate`, and `invalid_reasons` reports both
  failed sensors.
- `tachycardia`: heart rate from 110 to 130 bpm, above the 84 bpm maximum of the development rule.

Every mode sends a `methane_index` from 10 to 30. It is an index from an uncalibrated MQ-2 sensor,
not a concentration in ppm.

Run `python scripts/simulate_collar.py --help` for `--edge-url`, `--device-id`, `--count`,
`--interval`, and `--seed`.

The simulator reads the collar key from `DEVICE_API_KEY`, or from `DEVELOPMENT_DEVICE_API_KEY`
when that is not set, and never prints it. The default device is the development collar
`9d4a5f3b-3a6e-4f84-8daa-4e5f6a7b8c93`, so its key must be the one registered with seeding.

Store the key once as a user environment variable (replace the placeholder):

```powershell
[Environment]::SetEnvironmentVariable("DEVICE_API_KEY", "<collar api key>", "User")
```

Then load it into each new PowerShell session and run the simulator:

```powershell
$env:DEVICE_API_KEY = [Environment]::GetEnvironmentVariable('DEVICE_API_KEY','User')
.\.venv\Scripts\python scripts\simulate_collar.py --mode fever
```

bash: the prompt reads the key without echoing it, so it stays out of the command line and the
shell history.

```sh
read -rsp 'Collar API key: ' DEVICE_API_KEY && echo
export DEVICE_API_KEY
python scripts/simulate_collar.py --mode fever
```

Expected output for `--mode fever`:

```text
sim-9d4a5f3b-3f2a9c1e-000001 201 alerts=Temperature/Critical
```

The edge answers `201` with a `Temperature/Critical` alert when the collar has a synchronized
assignment and rules (see [Synchronization with the Cloud](#synchronization-with-the-cloud)).
Without an assignment, it answers `201` with `alerts=-`. The script exits with `0` when every
reading gets `200` or `201`, with `1` when a reading fails, and with `2` when no key is set.

## Local End-to-End Demo

This walk-through shows the whole path on one computer: collar, edge, ICHU backend, and web
application. Each step checks one segment, so a failure points to the segment that broke.

Before you start:

- The ICHU backend (`ichu-web-service`) runs in the Development environment on `http://localhost:5099`
  with its development seed, which creates the ranch, the collars, the gateway, and the monitoring rules.
- The web application (`ichu-web-app`) runs with `npm start`, and you are signed in as the demo
  manager described in the backend README.
- The development gateway key is listed in the backend README. Store it as the `EDGE_GATEWAY_KEY` user
  environment variable, as shown in [Synchronization with the Cloud](#synchronization-with-the-cloud).
- The `DEVICE_API_KEY` user environment variable holds the device key that the edge seeds and that the
  simulator sends. To use the Wokwi firmware too, give it the `kDeviceApiKey` value of the collar and panel
  secrets files, which is `development-device-key` in their example files.

### 1. Start the edge

In a first terminal, load the gateway key, enable seeding, and start the service:

```powershell
$env:APP_ENV = "development"
$env:SEED_DEVELOPMENT_DATA = "true"
$env:DEVELOPMENT_DEVICE_API_KEY = [Environment]::GetEnvironmentVariable('DEVICE_API_KEY','User')
$env:CLOUD_API_BASE_URL = "http://localhost:5099"
$env:EDGE_GATEWAY_KEY = [Environment]::GetEnvironmentVariable('EDGE_GATEWAY_KEY','User')
.\.venv\Scripts\python app.py
```

`curl http://127.0.0.1:5000/api/v1/status` answers `"status": "ok"` with `rulesRevision` `0`.

### 2. Download the rules and assignments

In a second terminal, with the same variables, run one cycle and then leave the synchronizer running:

```powershell
.\.venv\Scripts\flask --app app synchronize --once
.\.venv\Scripts\flask --app app synchronize
```

The status now shows a `rulesRevision` greater than `0`. Without this step the collars have no
assignment, so the edge stores their readings but raises no alerts.

### 3. Send a normal reading

In a third terminal, run `.\.venv\Scripts\python scripts\simulate_collar.py --mode normal`. The edge
answers `201` with `alerts=-`. Within one synchronizer cycle (15 s by default), the reading appears in the
animal's telemetry in the web application.

### 4. Raise an alert

Run the simulator with `--mode fever`. The edge answers `201` with `alerts=Temperature/Critical` at once,
before the cloud knows about the reading. After the next cycle, the alert is listed on
the web application's alert screen. `--mode escape` raises a `Geofence/Warning` alert in the same way.
The edge does not raise the same type of alert again for that collar within `ALERT_DEDUPLICATION_MINUTES`.

### 5. Lose and recover the cloud

1. Stop the backend.
2. Send three readings with `--mode normal --count 3 --interval 2`. The edge still answers `201`, the
   synchronizer logs failed cycles, and `pendingReadings` in the status rises to `3`.
3. Start the backend again. Within the back-off wait, which is at most 5 minutes, the synchronizer sends the
   pending readings and `pendingReadings` returns to `0`.
4. In the web application, each of the three readings appears once. A retried batch is answered as
   `Duplicate`, so nothing is stored twice.

### 6. Use the real firmware

The Wokwi projects of the collar
([`ichu-cattle-band-embedded-app`](https://github.com/five-smart-farm-creators/ichu-cattle-band-embedded-app))
and the alert panel
([`ichu-alert-panel-embedded-app`](https://github.com/five-smart-farm-creators/ichu-alert-panel-embedded-app))
reach this edge at `http://host.wokwi.internal:5000` from Wokwi for VS Code. Repeat steps 3 and 4 with the
simulated collar. The panel turns from `NORMAL` to `URGENT` and sounds its buzzer when a Critical alert arrives.

### Troubleshooting

| Symptom | Cause and fix |
|---|---|
| A physical ESP32 cannot reach `localhost` | `localhost` is the ESP32 itself. Use the computer's LAN address, for example `http://192.168.1.20:5000`, and start the edge with `HOST=0.0.0.0`, only on a trusted local network. |
| The ESP32 connects to Wi-Fi but gets no answer | Allow Python through Windows Firewall for private networks, and make sure the network profile is private. |
| The ESP32 never joins the network | The ESP32 supports only 2.4 GHz Wi-Fi. |
| The ESP32 and the computer are on the university network but cannot see each other | Campus networks isolate clients. Use a phone hotspot. |
| Readings are accepted but raise no alerts | The collar has no assignment or the rules are not downloaded. Run step 2 and check `rulesRevision`. |
| A reading gets `400` | A value is outside the contract, for example a body temperature outside 30–45 °C. The error body names the field. |
| The synchronizer reports rejected gateway credentials | `EDGE_GATEWAY_KEY` does not match a gateway registered in the backend, or the variable was not loaded into this terminal. |
| The first cycle against Render times out | The free plan sleeps when idle and can take about a minute to wake up. The next cycle retries. |
| Alerts appear at the wrong time | Every component uses UTC. Check the clock of the computer and the NTP access of the devices. |

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
  "heart_rate": 72.0,
  "methane_index": 18.5,
  "position": {
    "latitude": -16.5,
    "longitude": -68.1,
    "accuracy_meters": 4.0
  },
  "invalid_reasons": {
    "battery_level": "Battery sensor did not respond"
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
- `heart_rate` (`number`, optional): pulse in beats per minute, from 30 to 200
- `methane_index` (`number`, optional): relative index from 0 to 100 from an uncalibrated MQ-2
  sensor; it is not ppm
- `position` (`object`, optional): `latitude` from -90 to 90, `longitude` from -180
  to 180, and `accuracy_meters` (required) of zero or more
- `invalid_reasons` (`object`, optional): why a measurement could not be captured, keyed by
  `body_temperature`, `activity_index`, `battery_level`, `position`, `heart_rate`, or
  `methane_index`; each reason is text of 1 to 200 characters after trimming. A field cannot
  have both a value and a reason.

At least one measurement value or invalid reason is required.

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
    "battery_level": null,
    "heart_rate": 72.0,
    "methane_index": 18.5,
    "position": {
      "latitude": -16.5,
      "longitude": -68.1,
      "accuracy_meters": 4.0
    },
    "invalid_reasons": {
      "battery_level": "Battery sensor did not respond"
    },
    "sync_status": "pending"
  },
  "alerts": []
}
```

#### Error responses

- `400 Bad Request`: invalid JSON or payload, no measurement value or invalid reason, a value out
  of range, a naive timestamp, a `position` without `accuracy_meters`, an `invalid_reasons` key
  that is not a measurement field, a blank or longer-than-200 reason, or a field that has both a
  value and a reason
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
refreshes the cached rules when the cloud has a newer revision, replaces the collar assignments with
the cloud's list, then pushes pending readings and alerts in one batch. The periodic synchronizer runs
the same cycle on a timer; see [Run the synchronizer](#run-the-synchronizer).

#### Responses

- `200 OK`: body holds `rulesUpdated`, `rulesRevision`, `sentReadings`, `sentAlerts`, `synced`,
  `rejected`, `deferred` (conflicting entries that stay pending for another attempt), and
  `assignments` (collar assignments received from the cloud)
- `502 Bad Gateway`: the ICHU cloud API rejected the edge gateway credentials; pending records
  are kept
- `503 Service Unavailable`: synchronization is not configured
  (`"Cloud synchronization is not configured"`), or the cloud API cannot be reached (pending
  records are kept)

```sh
curl -X POST http://127.0.0.1:5000/api/v1/synchronizations
```

## Synchronization with the Cloud

Synchronization is disabled in the development profile. To synchronize with an ICHU backend, set
`CLOUD_API_BASE_URL` to the backend address and set the gateway key as a user environment variable.

| Backend | `CLOUD_API_BASE_URL` |
|---|---|
| Local ICHU backend | `http://localhost:5099` |
| Deployed ICHU backend | `https://ichu-web-service.onrender.com` |

The deployed backend runs on Render's free plan, so its first request after an idle period can take
about a minute. A synchronization that times out keeps its records pending, and the next one retries them.

Windows PowerShell:

```powershell
$env:CLOUD_API_BASE_URL = "http://localhost:5099"
[Environment]::SetEnvironmentVariable("EDGE_GATEWAY_KEY", "<gatewayId>.<secret>", "User")
```

bash:

```sh
export CLOUD_API_BASE_URL=http://localhost:5099
export EDGE_GATEWAY_KEY='<gatewayId>.<secret>'
```

The PowerShell user variable is available in new terminals; the bash `export` lasts for the current
shell only.

A gateway key has the form `{gatewayId}.{secret}`. The backend returns it once, when the gateway is
registered with `POST /api/v1/ranches/{ranchId}/edge-gateways`, and stores only a hash of the secret.
The service reads the gateway id from the key, so no separate gateway id is configured. The key of the
gateway seeded in the backend's development data is for local development only and must never be used
on a real deployment.

- **Rules**: the gateway requests `GET /api/v1/edge-gateways/{gatewayId}/monitoring-rules` with the
  `afterRevision` of its cached rules. A `304 Not Modified` answer keeps the cache; a `200 OK`
  answer replaces thresholds, geofences, and revision.
- **Assignments**: the gateway requests `GET /api/v1/edge-gateways/{gatewayId}/device-assignments`
  with the `X-Gateway-Key` header, after the rules and before the batches. The answer replaces the
  local assignments: each collar gets its lot and life stage, and a collar that the cloud no longer
  lists loses its assignment. A collar without an assignment is stored but not evaluated.
- **Batches**: pending readings and alerts are sent to `POST /api/v1/telemetry/batch` with the
  `X-Gateway-Key` header. The cloud's per-entry status decides the local status: `Accepted` and
  `Duplicate` mark the entry `synced`; `Rejected` marks it `rejected` with the cloud's reason.
  Entries missing from the answer stay `pending`.
- **Conflicts**: a `Conflict` keeps the entry `pending` and counts it as `deferred`, because the
  cloud also reports a transient race as a conflict. After 5 conflicting answers the entry is
  `rejected` with the cloud's reason.
- **Refused batches**: a `400` or `422` answer refuses a whole batch. The service splits it in
  halves and sends each half, readings before alerts, until the invalid entry is alone. That entry
  stays `pending` as `deferred` and, after 5 refusals, is `rejected` with `Refused by the ICHU cloud
  API as invalid`; the other entries follow their own answers.
- **Credentials**: an HTTP `401` or `403` from the cloud is reported as rejected gateway
  credentials (`502` from the synchronization endpoint). Records stay `pending`.
- **Store and forward**: when the cloud cannot be reached, nothing is marked and no record is lost.
  The next synchronization sends the same records again.

### Register a collar

The gateway authenticates only the collars it has registered, so each collar that the cloud assigns
must be registered here once, with the API key set in that collar's firmware. The key is read from the `DEVICE_API_KEY`
process environment variable and is never printed.

Windows PowerShell:

```powershell
$env:DEVICE_API_KEY = "<collar api key>"
.\.venv\Scripts\flask --app app register-device 9d4a5f3b-3a6e-4f84-8daa-4e5f6a7b8c93
Remove-Item Env:DEVICE_API_KEY
```

bash:

```sh
export DEVICE_API_KEY='<collar api key>'
flask --app app register-device 9d4a5f3b-3a6e-4f84-8daa-4e5f6a7b8c93
unset DEVICE_API_KEY
```

The command prints `Device <deviceId> registered`, or `Device <deviceId> already registered` when the
collar is known and changes nothing. An identifier that is not a UUID is rejected.

### Run the synchronizer

The periodic synchronizer is a separate process, so the web server and the synchronizer can be
stopped and restarted on their own. Run it in a second terminal, with the same configuration.

Windows PowerShell:

```powershell
.\.venv\Scripts\flask --app app synchronize
.\.venv\Scripts\flask --app app synchronize --interval 30
.\.venv\Scripts\flask --app app synchronize --once
```

bash:

```sh
flask --app app synchronize
flask --app app synchronize --interval 30
flask --app app synchronize --once
```

`--interval` sets the seconds between cycles, from 5 to 3600; it defaults to `SYNC_INTERVAL_SECONDS`.
Each cycle logs its outcome. After a failed cycle the wait doubles with every consecutive failure, up
to 5 minutes, so a cloud that is down or waking up is not hammered. A full batch that settled records starts the next cycle
at once. `--once` runs a single cycle and exits with status 1 when synchronization is not configured or
the cloud cannot be reached. Ctrl+C stops the synchronizer with status 0.

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
- **Synchronizer process**: the web server does not start `flask --app app synchronize`. Run it under
  a process supervisor next to the service.
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
