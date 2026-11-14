# Technical Stories — ICHU Edge Service

## Overview

This document captures the technical stories for the ICHU Edge Service, the edge
gateway software that receives telemetry from Cattle Band smart collars over the
local network and synchronizes it with the ICHU cloud API.

The stories are written from the perspective of the collar and gateway platform
developers. Interactions are unattended: a collar submits readings on its own, and
the gateway synchronizes with the cloud without an operator. The acceptance criteria
describe observable service behavior rather than user interface details, and they
match the bounded contexts IAM and Monitoring.

## Requirements Traceability Matrix (RTX)

| Story ID | Title | Bounded Context | Related Implementation Elements |
|---|---|---|---|
| TS-EDGE-001 | Ingest a Telemetry Reading | Monitoring; IAM | `monitoring/interfaces/services.py`: `TelemetryReadingCreateRequest`, `create_telemetry_reading`; `monitoring/application/services.py`: `TelemetryIngestionService.ingest`; `monitoring/domain/entities.py`: `TelemetryReading.record` |
| TS-EDGE-002 | Enforce Device Authentication by API Key | IAM; Monitoring | `iam/interfaces/services.py`: `authenticate_device`; `iam/application/services.py`: `AuthApplicationService.authenticate`; `iam/domain/entities.py`: `Device.authenticate`; `monitoring/integration/iam_anti_corruption_layer.py`: `IamAntiCorruptionLayer` |
| TS-EDGE-003 | Persist Readings Idempotently | Monitoring | `monitoring/infrastructure/repositories.py`: `TelemetryReadingRepository.save_idempotently`; `monitoring/infrastructure/models.py`: `TelemetryReadingModel` |
| TS-EDGE-004 | Raise Local Alerts from Cached Rules | Monitoring | `monitoring/domain/services.py`: `RuleEvaluator`, `point_in_polygon`, `distance_to_boundary_meters`; `monitoring/domain/entities.py`: `MonitoringRuleSet`, `MonitoringAlert.raise_for` |
| TS-EDGE-005 | Deduplicate Alerts Within a Window | Monitoring | `monitoring/application/services.py`: `TelemetryIngestionService.ingest`; `monitoring/infrastructure/repositories.py`: `MonitoringAlertRepository.find_recent` |
| TS-EDGE-006 | Synchronize with the ICHU Cloud API | Monitoring | `monitoring/application/services.py`: `SynchronizationService`; `monitoring/integration/cloud_anti_corruption_layer.py`: `IchuCloudAntiCorruptionLayer`, `DisabledCloudAntiCorruptionLayer`; `monitoring/infrastructure/cloud_client.py`: `IchuCloudClient`; `monitoring/interfaces/services.py`: `synchronize`; `monitoring/interfaces/commands.py`: `synchronize_command`, `run_synchronizer`, `next_delay_seconds` |
| TS-EDGE-007 | Bootstrap Storage and Configuration | Application bootstrap; shared infrastructure | `app.py`: `create_app`, `seed_development_data`; `config.py`: `load_config`; `shared/infrastructure/database.py`: `configure_database`, `init_db` |
| TS-EDGE-008 | Refresh Collar Assignments from the Cloud | Monitoring; IAM | `monitoring/integration/cloud_anti_corruption_layer.py`: `IchuCloudAntiCorruptionLayer.fetch_assignments`; `monitoring/application/services.py`: `SynchronizationService.refresh_assignments`; `monitoring/infrastructure/repositories.py`: `DeviceAssignmentRepository.replace_all`; `iam/interfaces/commands.py`: `register_device_command` |
| TS-EDGE-009 | Report Sensor Failures and GPS Accuracy | Monitoring | `monitoring/interfaces/services.py`: `PositionRequest`, `TelemetryReadingCreateRequest`; `monitoring/domain/value_objects.py`: `InvalidMeasurementReasons`; `monitoring/domain/entities.py`: `TelemetryReading.record`; `monitoring/domain/services.py`: `RuleEvaluator._geofence_violations`; `monitoring/integration/cloud_anti_corruption_layer.py`: `_reading_to_contract` |
| TS-EDGE-010 | Report Heart Rate and Methane Index | Monitoring | `monitoring/domain/value_objects.py`: `HeartRate`, `MethaneIndex`; `monitoring/domain/services.py`: `RuleEvaluator._threshold_violations`; `monitoring/domain/entities.py`: `TelemetryReading.record`; `monitoring/integration/cloud_anti_corruption_layer.py`: `_reading_to_contract`, `_threshold_from_contract` |

---

### TS-EDGE-001 — Ingest a Telemetry Reading

As a collar platform developer, I want each collar to submit its readings
autonomously to the gateway so that every measurement is validated, stored, and
evaluated locally.

Acceptance criteria:

- Scenario: New reading accepted
  - Given a collar submits `POST /api/v1/monitoring/telemetry-readings`
  - And the body has a `device_id`, an `event_id`, and at least one measurement
  - And the `X-API-Key` belongs to the device
  - When the gateway validates and stores the reading
  - Then the response is `201 Created`
  - And the body holds the stored `reading` and the `alerts` raised by it

- Scenario: Missing measurement
  - Given a collar submits a reading with no body temperature, activity index,
    battery level, or position
  - When the gateway validates the reading
  - Then the response is `400 Bad Request`

- Scenario: Measurement outside its range
  - Given a collar submits a body temperature of 46.0 Celsius
  - When the gateway validates the reading
  - Then the response is `400 Bad Request`

- Scenario: Missing or unknown credentials
  - Given a collar omits `device_id` or sends a wrong `X-API-Key`
  - When the gateway authenticates the request
  - Then the response is `401 Unauthorized`

---

### TS-EDGE-002 — Enforce Device Authentication by API Key

As a collar platform developer, I want every reading to carry the device's API key
so that only registered devices can submit telemetry.

Acceptance criteria:

- Scenario: Valid credential pair
  - Given a device identifier that is a registered canonical UUID
  - And the matching API key
  - When the gateway authenticates the device
  - Then the authentication endpoint returns `200 OK` with `"authenticated": true`

- Scenario: Unknown or revoked device
  - Given a device identifier that is not registered, or a device that was revoked
  - When the gateway authenticates the device
  - Then the response is `401 Unauthorized`

- Scenario: Alert listing by a registered device
  - Given a registered device identifier and that device's own API key
  - When the device requests `GET /api/v1/monitoring/alerts?device_id=<uuid>`
  - Then the response is `200 OK` with the newest alerts first
  - And a missing `device_id`, or a key that does not match the device, returns `401 Unauthorized`

---

### TS-EDGE-003 — Persist Readings Idempotently

As a collar platform developer, I want retries of the same reading to be safe so
that a lost response never creates a duplicate record.

Acceptance criteria:

- Scenario: Identical retry
  - Given a reading with `event_id` `E` was accepted for device `D`
  - When the collar resubmits the same `E` with the same telemetry
  - Then the response is `200 OK` with the original `reading_id`
  - And no second reading or alert is created

- Scenario: Event identifier reused for different telemetry
  - Given a reading with `event_id` `E` was accepted for device `D`
  - When the collar submits `E` with different measurements
  - Then the response is `409 Conflict`
  - And the stored reading is unchanged

- Scenario: Identifier scoped to the device
  - Given two devices submit readings with the same `event_id`
  - Then both readings are accepted with `201 Created`

---

### TS-EDGE-004 — Raise Local Alerts from Cached Rules

As a ranch operator, I want the gateway to raise alerts from the last known rules so
that a fever or a geofence exit is reported even when the cloud is unreachable.

Acceptance criteria:

- Scenario: Body temperature above the threshold of the animal's life stage
  - Given a device assigned to a life stage with a body temperature threshold
  - When the device submits a temperature above the threshold maximum
  - Then a `Temperature` alert is raised with the threshold bounds and the rule revision

- Scenario: Life stage without a matching threshold
  - Given a device whose life stage has no threshold for the measured metric
  - When the device submits a reading
  - Then no alert is raised for that metric

- Scenario: Position outside a geofence beyond its tolerance
  - Given a device assigned to a lot with a geofence of 50 m tolerance
  - When the device reports a position 100 m outside the polygon
  - Then a `Geofence` alert is raised with the geofence and the distance to its boundary

- Scenario: Position outside a geofence within its tolerance
  - Given the same geofence
  - When the device reports a position 30 m outside the polygon
  - Then no alert is raised

- Scenario: Device without assignment
  - Given a device with no lot or life-stage assignment
  - When the device submits a reading
  - Then no rule is evaluated and no alert is raised

---

### TS-EDGE-005 — Deduplicate Alerts Within a Window

As a ranch operator, I want one alert per device and type within a time window so
that a persistent fever does not flood the display panel.

Acceptance criteria:

- Scenario: Second violation inside the window
  - Given a `Temperature` alert was raised for device `D` within the last
    `ALERT_DEDUPLICATION_MINUTES` minutes
  - When device `D` submits another temperature violation of the same or lower severity
  - Then no new alert is raised
  - And the reading is still stored

- Scenario: Worsening violation inside the window
  - Given a `Warning` `Temperature` alert was raised for device `D` within the last
    `ALERT_DEDUPLICATION_MINUTES` minutes
  - When device `D` submits a temperature violation of `Critical` severity
  - Then a new `Critical` `Temperature` alert is raised

- Scenario: Less severe violation after a Critical alert
  - Given a `Critical` `Temperature` alert was raised for device `D` within the last
    `ALERT_DEDUPLICATION_MINUTES` minutes
  - When device `D` submits a temperature violation of `Warning` severity
  - Then no new alert is raised

- Scenario: Violation after the window
  - Given the previous `Temperature` alert is older than the deduplication window
  - When device `D` submits a temperature violation
  - Then a new `Temperature` alert is raised

---

### TS-EDGE-006 — Synchronize with the ICHU Cloud API

As a platform developer, I want pending readings, alerts, and cached rules to move
between the gateway and the cloud in batches so that intermittent connectivity
never loses data.

Acceptance criteria:

- Scenario: Rules refreshed when the cloud has a newer revision
  - Given the gateway caches revision 2
  - When it synchronizes and the cloud answers with revision 3
  - Then the cached rules are replaced
  - And the response reports `rulesUpdated: true` and `rulesRevision: 3`

- Scenario: Rules kept when not modified
  - Given the cloud answers `304 Not Modified`
  - Then the cached rules are kept and `rulesUpdated` is `false`

- Scenario: Entries acknowledged by the cloud
  - Given pending readings and alerts were sent in one batch
  - When the cloud answers `Accepted` or `Duplicate` for an entry
  - Then the entry is marked `synced`

- Scenario: Entries rejected by the cloud
  - Given a pending reading was sent
  - When the cloud answers `Rejected` with a reason
  - Then the reading is marked `rejected` with that reason

- Scenario: Entries missing from the answer
  - Given a pending reading was sent
  - When the cloud's answer does not mention it
  - Then the reading remains `pending`

- Scenario: Cloud unreachable
  - Given pending readings exist
  - When the cloud cannot be reached
  - Then the response is `503 Service Unavailable`
  - And every pending record remains `pending`

- Scenario: Conflict retried before rejection
  - Given a pending reading was sent
  - When the cloud answers `Conflict` with a reason
  - Then the reading remains `pending` with one more attempt counted
  - And after the fifth conflicting answer the reading is marked `rejected` with that reason

- Scenario: Refused batch isolated
  - Given pending readings `A`, `X`, and `B` were sent in one batch
  - When the cloud refuses the batch as invalid (`400` or `422`) because of `X`
  - Then `A` and `B` follow their own answers and are marked `synced`
  - And `X` remains `pending` with one more attempt counted
  - And after the fifth refusal `X` is marked `rejected` with `Refused by the ICHU cloud API as invalid`

- Scenario: Periodic run sends pending records
  - Given the gateway runs `flask --app app synchronize` with `SYNC_INTERVAL_SECONDS` set to 15
  - When a cycle sends a partial batch
  - Then the next cycle starts after 15 seconds
  - And when a cycle sends a full batch that settles records, the next cycle starts at once

- Scenario: Periodic run backs off while the cloud is down
  - Given the cloud cannot be reached
  - When consecutive cycles fail
  - Then each wait doubles, up to 5 minutes
  - And every pending record remains `pending`

- Scenario: Gateway credentials rejected
  - Given the cloud answers `401 Unauthorized` or `403 Forbidden`
  - When an operator calls `POST /api/v1/synchronizations`
  - Then the response is `502 Bad Gateway`
  - And every pending record remains `pending`

- Scenario: Synchronization not configured
  - Given `CLOUD_API_BASE_URL` is empty
  - When an operator calls `POST /api/v1/synchronizations`
  - Then the response is `503` with `Cloud synchronization is not configured`

---

### TS-EDGE-007 — Bootstrap Storage and Configuration

As a platform developer, I want the gateway to validate its configuration and prepare
its storage before serving requests so that a misconfigured gateway fails at startup.

Acceptance criteria:

- Scenario: Startup creates the schema
  - Given the gateway creates its Flask application
  - When the factory loads the configuration and initializes storage
  - Then every table is created if it is missing
  - And existing device credentials are migrated to salted verifiers

- Scenario: Unsafe production configuration
  - Given `APP_ENV=production`
  - When `DEBUG` or `SEED_DEVELOPMENT_DATA` is `true`
  - Then application creation fails with a configuration error

- Scenario: Development seeding
  - Given `SEED_DEVELOPMENT_DATA=true` and a development device key
  - When the application is created
  - Then the development devices exist without lot assignments
  - And existing devices are not overwritten

- Scenario: Requests do not trigger bootstrap work
  - Given the application has been created
  - When the gateway receives an HTTP request
  - Then request handling does not rerun schema creation or migrations

---

### TS-EDGE-008 — Refresh Collar Assignments from the Cloud

As a ranch operator, I want the lot and life stage of each collar to come from the cloud so that
thresholds and geofences apply to the animal that wears it.

Acceptance criteria:

- Scenario: Assignments replaced by the cloud list
  - Given the gateway caches assignments for collars `A` and `B`
  - When it synchronizes and the cloud lists only `B`, with a new lot
  - Then `B` takes the new lot and life stage
  - And `A` no longer has an assignment

- Scenario: Assignment applied to the next reading
  - Given collar `D` has no assignment on the gateway
  - And the cached rules have a body temperature threshold for `MilkingCow`
  - When a synchronization downloads an assignment of `D` to `MilkingCow`
  - And `D` then submits a body temperature above that threshold
  - Then a `Temperature` alert is raised

- Scenario: Collar without a cloud assignment
  - Given the cloud lists no assignment for collar `D`
  - When `D` submits a reading
  - Then no rule is evaluated and no alert is raised

- Scenario: Malformed assignment list
  - Given the cloud answers with an assignment whose `deviceId` is not a UUID
  - When an operator calls `POST /api/v1/synchronizations`
  - Then the response is `503 Service Unavailable`
  - And the cached assignments are kept
  - And every pending record remains `pending`

- Scenario: Collar known only to the cloud
  - Given the cloud assigns collar `D`, which the gateway has not registered
  - When an operator runs `flask --app app register-device D` with `DEVICE_API_KEY` set to the key of `D`
  - Then `D` authenticates with that key
  - And a second run reports that `D` is already registered and changes nothing

---

### TS-EDGE-009 — Report Sensor Failures and GPS Accuracy

As a ranch operator, I want a collar to report the measurements it could not capture and the GPS
accuracy of each position so that a sensor fault is not mistaken for a normal reading and a GPS
error does not raise a false geofence alert.

Acceptance criteria:

- Scenario: Failed temperature sensor
  - Given a device assigned to a life stage with a body temperature threshold
  - When the device submits a reading with an activity index and an `invalid_reasons` entry for
    `body_temperature`, without a body temperature
  - Then the response is `201 Created` and echoes the `invalid_reasons`
  - And no `Temperature` alert is raised
  - And the next synchronization sends the body temperature with a `null` value and the `invalidReason`

- Scenario: Measurement with both a value and a reason
  - Given a collar submits a body temperature of 38.5 with an `invalid_reasons` entry for `body_temperature`
  - When the gateway validates the reading
  - Then the response is `400 Bad Request`

- Scenario: Position within the geofence tolerance
  - Given a device assigned to a lot with a geofence of 25 m tolerance
  - When the device reports a position 20 m outside the polygon with an accuracy of 0 m
  - Then no alert is raised

- Scenario: Position beyond the tolerance but within the GPS accuracy
  - Given the same geofence
  - When the device reports a position 40 m outside the polygon with an accuracy of 30 m
  - Then no alert is raised

- Scenario: Position beyond the tolerance plus the GPS accuracy
  - Given the same geofence
  - When the device reports a position 40 m outside the polygon with an accuracy of 5 m
  - Then a `Geofence` alert is raised

---

### TS-EDGE-010 — Report Heart Rate and Methane Index

As a ranch operator, I want a collar to report its pulse and its methane index so that a fast heart
rate raises an alert, a failed pulse sensor is not mistaken for a normal rate, and the methane index
is never read as a calibrated concentration.

Acceptance criteria:

- Scenario: Heart rate above the threshold
  - Given a device assigned to a life stage with a `Warning` heart rate threshold of 48 to 84 bpm
    and a `Critical` heart rate threshold of 40 to 90 bpm
  - When the device submits a reading with a heart rate of 120 bpm
  - Then the response is `201 Created` and one `HeartRate` alert is raised at `Critical` severity

- Scenario: Failed pulse sensor
  - Given a collar submits a reading with an `invalid_reasons` entry for `heart_rate` and no heart rate
  - When the gateway validates the reading
  - Then the response is `201 Created` and no `HeartRate` alert is raised
  - And the next synchronization sends the heart rate with a `null` value and the `invalidReason`

- Scenario: Methane index above the Warning maximum
  - Given a device assigned to a life stage with a `Warning` methane index threshold of 0 to 60
    and a `Critical` methane index threshold of 0 to 80
  - When the device submits a reading with a methane index of 70
  - Then the response is `201 Created` and a `Methane` alert is raised at `Warning` severity

- Scenario: Methane index above the Critical maximum
  - Given the same methane index thresholds
  - When the device submits a reading with a methane index of 95
  - Then the response is `201 Created` and a `Methane` alert is raised at `Critical` severity

- Scenario: Methane index without a threshold
  - Given the cached rules have no methane index threshold
  - When the gateway evaluates a reading with a methane index of 95
  - Then the methane index is stored and synchronized
  - And no alert is raised

- Scenario: Heart rate outside the physiological range
  - Given a collar submits a heart rate of 250
  - When the gateway validates the reading
  - Then the response is `400 Bad Request`
