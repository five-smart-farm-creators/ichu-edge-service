# Architecture Decision Records

This document records the decisions that shape the ICHU Edge Service. The records
are intentionally concise and focus on decisions that affect system boundaries,
integration contracts, or operational behavior.

New records should be appended rather than rewriting an accepted decision. If a
decision changes, add a new ADR that supersedes the earlier record and links the two.

## ADR format

Each record includes:

- **Status**: `Accepted`, `Superseded`, or `Deprecated`
- **Date**: the date the decision was made
- **Context**: the problem and constraints
- **Decision**: the chosen approach
- **Consequences**: important benefits and trade-offs
- **Alternatives considered**: credible options that were not selected

## ADR-0001: Organize the service around DDD bounded contexts

- **Status**: Accepted
- **Date**: 2026-11-07

### Context

Device identity and cattle monitoring have different responsibilities and evolve
independently. Mixing them in one module would let authentication concerns leak
into the telemetry and rules model.

### Decision

Structure the service as two bounded contexts:

- **IAM** owns device identity and API-key verification.
- **Monitoring** owns telemetry validation, local alerting, and cloud synchronization.

Each context uses domain, application, infrastructure, and interface layers, and
Monitoring asks IAM to verify devices only through its anti-corruption layer.

### Consequences

- Business responsibilities and ownership are explicit.
- Domain code remains independent of Flask, Peewee, and `requests`.
- The structure adds more modules than a single-module Flask application.

### Alternatives considered

- One application module containing routes, validation, and persistence.
- A package structure organized only by framework layer.

## ADR-0002: Use SQLite with Peewee for local edge persistence

- **Status**: Accepted
- **Date**: 2026-11-07

### Context

The gateway must keep every reading durably while the cloud is unreachable, on a
small single-board computer with no database server.

### Decision

Use SQLite as the local database and Peewee as the ORM. Keep ORM models and
repository implementations in the infrastructure layer so domain entities and
protocols do not depend on either technology.

### Consequences

- The gateway has no external database dependency.
- The repository protocols allow the persistence technology to be replaced.
- SQLite is not suited to high-write concurrency or several writer processes.

### Alternatives considered

- PostgreSQL, which needs a database server on the gateway.
- An in-memory store, which would lose readings during a power cut.

## ADR-0003: Authenticate devices with a per-device API key

- **Status**: Accepted
- **Date**: 2026-11-07

### Context

Collars and the display panel send requests without an interactive user session. The gateway
must reject unknown devices before it accepts telemetry or serves alerts.

### Decision

Require the device identifier (a canonical UUID) and that device's own secret. Telemetry
submissions carry the identifier in the JSON body. The alert listing carries it in the
`device_id` query parameter, so the display panel authenticates as itself. The Monitoring
anti-corruption layer verifies the pair with IAM. No endpoint accepts a key without naming its
device.

### Consequences

- Every request is attributable to one registered device.
- Each device needs its own key, which must be provisioned, rotated, and revoked.
- The display panel must be registered and must hold its own key.
- API keys must be stored and transmitted securely.

### Alternatives considered

- A key accepted from any registered device, which needs a check against every device on each
  request.
- Client certificates or signed requests, which need device provisioning that the ranch
  deployment does not yet have.

## ADR-0004: Validate request payloads at the HTTP boundary with Pydantic

- **Status**: Accepted
- **Date**: 2026-11-07

### Context

Malformed JSON and out-of-range measurements should be rejected before the
application layer runs.

### Decision

Use Pydantic request models in the Flask interface layer, with `extra="forbid"`.
The interface maps validation failures to `400 Bad Request`. Domain value objects
keep the invariants that apply beyond the transport.

### Consequences

- HTTP contracts are explicit and errors are handled close to the boundary.
- Application and domain services do not depend on Flask request objects.
- Pydantic becomes a runtime dependency.

### Alternatives considered

- Manual dictionary checks in each route.
- Validation only in the domain, which would report generic errors for transport issues.

## ADR-0005: Normalize timestamps to UTC

- **Status**: Accepted
- **Date**: 2026-11-07

### Context

Collars and gateways may use different local offsets, while stored readings must be
comparable across the ranch and the cloud.

### Decision

Accept ISO 8601 timestamps, convert offset-aware values to UTC, and use the current
UTC time when `recorded_at` is omitted. Store naive UTC values in SQLite and return
ISO 8601 values with a `Z` suffix.

### Consequences

- Queries, deduplication windows, and cloud batches use one time basis.
- The original offset is not retained.
- Naive timestamps are rejected rather than guessed.

### Alternatives considered

- Storing each device's local timestamp and offset unchanged.

## ADR-0006: Initialize through an application factory

- **Status**: Accepted
- **Date**: 2026-11-07

### Context

Configuration and database failures should surface when the service starts, not when
the first reading arrives.

### Decision

Create the Flask application through `create_app()`. The factory loads and validates
the configuration, configures the database, creates the tables, wires the services,
registers the Blueprints, seeds development data when enabled, and closes the Peewee
connection in `teardown_appcontext`.

### Consequences

- Configuration and initialization failures occur during application creation.
- Tests can create the application with a temporary database and different settings.
- Schema changes still require a deliberate migration workflow.

### Alternatives considered

- Initializing on the first request, which delays failures until traffic arrives.
- A separate manual initialization command without factory integration.

## ADR-0007: Isolate Monitoring from IAM with an anti-corruption layer

- **Status**: Accepted
- **Date**: 2026-11-07

### Context

Monitoring must confirm that a device is authorized, but depending on IAM's
repository and credential model would couple monitoring use cases to another
context's internals.

### Decision

Define a Monitoring-owned `SubmittingDevice` concept and an `IamAntiCorruptionLayer`
that delegates verification to IAM's application service. The layer translates a
successful verification into a `SubmittingDevice`, and returns no device otherwise.

### Consequences

- Monitoring application code does not import IAM repositories or domain types.
- The identity provider can change without changing monitoring use cases.

### Alternatives considered

- Injecting IAM's device repository into the monitoring service.
- Calling IAM's HTTP endpoint from inside the gateway, which adds network overhead
  to an in-process call.

## ADR-0008: Synchronize with the cloud using store-and-forward batches

- **Status**: Accepted, partly superseded by ADR-0013 and ADR-0014
- **Date**: 2026-11-07

### Context

The ranch link is intermittent. Readings and alerts must survive outages and reach the ICHU
cloud exactly once from the cloud's point of view.

### Decision

Store every reading and alert with a `pending` status. A synchronization pushes up to
`SYNC_BATCH_SIZE` pending entries of each kind in one batch. The cloud's per-entry answer decides
the new status: `Accepted` and `Duplicate` become `synced`; `Rejected` and `Conflict` become
`rejected` with the cloud's reason. Entries missing from the answer stay `pending`. An HTTP 401 or
403 from the cloud is reported as rejected gateway credentials (502), and the records stay
`pending`. The `IchuCloudAntiCorruptionLayer` translates between the cloud's camelCase contract and
the domain model. A disabled layer refuses every call when `CLOUD_API_BASE_URL` is empty, which is
the default in the development profile.

### Consequences

- Connection loss delays synchronization but never loses data.
- Retried batches are safe because the cloud recognizes duplicate entries.
- A rejected or conflicting entry is kept with its reason for operator review.
- Synchronization is triggered through the API; no background scheduler exists yet.

### Alternatives considered

- Sending each reading immediately, which fails during outages.
- A background scheduler inside the service, which is deferred to a later version.

## ADR-0009: Evaluate monitoring rules locally against cached rules

- **Status**: Accepted
- **Date**: 2026-11-07

### Context

Alerts about body temperature and geofence exits matter most when the gateway cannot
reach the cloud, so the rules must run at the edge with the last known configuration.

### Decision

Cache thresholds and geofences from the cloud with their revision. `RuleEvaluator`
checks each reading against the thresholds of the device's life stage and, for a
position, against the geofences of the device's lot. A geofence is violated when the
position is outside its polygon and farther than its tolerance from the boundary. The
distance uses a planar equirectangular projection centered on the reading, which is
accurate at lot scale. Each alert records the revision that was in effect.

### Consequences

- Alerts are raised while the gateway is offline.
- Rule changes take effect at the next synchronization.
- The planar approximation is not valid for polygons that cross the antimeridian.

### Alternatives considered

- Calling a cloud rule engine for every reading, which requires connectivity.
- Exact geodesic distances, which add complexity that lot-scale fences do not need.

## ADR-0010: Make telemetry ingestion idempotent with device event IDs

- **Status**: Accepted
- **Date**: 2026-11-07

### Context

A collar may lose the response after the gateway stores a reading. Retrying a
creation request must not duplicate the reading or its alerts.

### Decision

Require each device to supply a stable `event_id`, and scope uniqueness to
`(device_id, event_id)` with a unique database index. A retry with the same telemetry
returns the stored reading with `200 OK` and raises no alerts. Reusing the identifier
for different telemetry returns `409 Conflict`.

### Consequences

- Devices must keep the event identifier until the gateway acknowledges it.
- Retries never duplicate readings or alerts.
- Changed telemetry under an old identifier is rejected rather than merged.

### Alternatives considered

- Time-window deduplication, which can merge distinct readings.
- A separate idempotency header, which makes the device contract less explicit.

## ADR-0011: Deduplicate alerts within a configurable window

- **Status**: Accepted
- **Date**: 2026-11-07

### Context

A cow with a fever produces a reading every few minutes. One alert per reading would
flood the display panel and the cloud.

### Decision

Before saving an alert, look for an alert of the same device and type raised within
the last `ALERT_DEDUPLICATION_MINUTES` (1 to 1440, default 15). If one exists, the new
violation is recorded only as part of the reading and raises no alert. Violations are
evaluated most severe first, so the most severe alert wins within one reading.

### Consequences

- Each device raises at most one alert per type within the window.
- A worsening condition after the window raises a new alert.
- A severe violation may be hidden by a recent alert of the same type.

### Alternatives considered

- One alert per reading, which floods operators.
- Deduplication by severity change, which needs extra state per alert.

## ADR-0012: Derive the gateway identity from the issued key

- **Status**: Accepted
- **Date**: 2026-11-12

### Context

The backend issues each gateway key as `{gatewayId}.{secret}`. A separate `EDGE_GATEWAY_ID`
setting could disagree with the key it accompanies, and telemetry would then be sent under a
gateway identity that the key does not match.

### Decision

Read the gateway id from the part of `EDGE_GATEWAY_KEY` before the first `.`, which must be a
UUID. `EDGE_GATEWAY_ID` is no longer a setting.

### Consequences

- The configuration holds one value that identifies the gateway, so the two cannot drift.
- A malformed key fails at startup, even when cloud synchronization is disabled.

### Alternatives considered

- Keeping `EDGE_GATEWAY_ID` and checking it against the key, which keeps two values that must agree.

## ADR-0013: Run the periodic synchronizer as a separate CLI process

- **Status**: Accepted
- **Date**: 2026-11-12

### Context

Pending readings must reach the cloud without an operator calling
`POST /api/v1/synchronizations`, and the cloud can stay down for hours. A loop started from
`create_app()` would run inside the web server: the Flask development reloader runs the factory
twice, so the loop would start twice against the same database, and stopping it would stop ingestion too.

### Decision

Run the loop as the `synchronize` command of the Flask CLI (`flask --app app synchronize`), in its
own process next to the web server. Each cycle calls the same use case as the manual endpoint.
`SYNC_INTERVAL_SECONDS` sets the pause between cycles, and `--once` runs a single cycle for scripts
or cron. The manual endpoint stays available.

### Consequences

- The web server and the synchronizer restart independently, and stopping one never stops the other.
- The development reloader does not start a second synchronizer.
- Both processes write the same SQLite file; the busy timeout in `shared/infrastructure/database.py`
  makes their writes wait for each other.
- Operators must start and supervise a second process on the gateway.

### Alternatives considered

- A thread started in `create_app()`, which runs twice under the reloader and ends with the web server.
- A cron job that calls `POST /api/v1/synchronizations`, which cannot back off between failed attempts.

## ADR-0014: Retry conflicts a bounded number of times and isolate refused batches

- **Status**: Accepted
- **Date**: 2026-11-12

### Context

The cloud answers `Conflict` for a transient race, such as a reading that was being stored at the same
moment, and the same entry becomes `Duplicate` on the next try. Rejecting on the first conflict would
discard telemetry the cloud would accept. The cloud also refuses a whole batch with `400` or `422` when
one entry is invalid, so one bad reading would block every entry sent with it.

### Decision

A `Conflict` keeps the entry `pending` and increments its `sync_attempts`; the fifth conflicting answer
rejects it with the cloud's reason. A refused batch is split into two halves, and the first half is sent
before the second. Readings precede alerts in the split, so an alert never reaches the cloud before a
reading it cites. The split repeats until the invalid entry is alone; that entry counts an attempt like a conflict and
is rejected with `Refused by the ICHU cloud API as invalid` after the fifth refusal. Outages and rejected credentials still stop the cycle, and
every unanswered record stays `pending`.

### Consequences

- A transient race resolves on a later attempt instead of rejecting telemetry.
- A batch with one invalid entry takes about two requests per halving step, so roughly `2 log2(n)`
  requests for `n` entries.
- A `400` or `422` caused by something other than one entry, such as a contract mismatch, refuses each
  record on its own; the five attempts give time to fix the cloud before records are rejected.
- Rejected entries keep their reason, as in ADR-0008.

### Alternatives considered

- Rejecting a conflict on the first answer, which discards telemetry a retry would accept.
- Resending a refused batch unchanged, which the cloud refuses again.
- Dropping an invalid entry without a reason, which hides it from operators.
