# SentinelAI Backend: implementation plan

Status: **authoritative**. Every other document in `docs/backend/` expands one
section of this one. Written 2026-08-02, against the repo at tag `v2.9.2`.

---

## 0. What exists today, honestly

`sentinelai/api.py` is 291 lines and it is good code: hand-rolled HS256 JWTs with
`iss`/`aud`/`exp`/`nbf` all verified, constant-time signature comparison, a role
hierarchy enforced per route, a token bucket, secret redaction on every log line,
and hardened response headers. The tests around it are real -- tamper, expiry,
wrong-secret, wrong-audience, RBAC denial, live HTTP round-trips.

It is also **not a backend**. It is an HTTP wrapper around three Python objects
held in memory. Specifically:

| Property | Today | Consequence |
| --- | --- | --- |
| Alerts | `json.loads(alerts.json)` into a list at boot | Read-only. No alert can ever change state. |
| Feedback | appended to `self.feedback`, a list | Every analyst label is lost on restart. |
| Audit chain | in-memory list inside `ResponseEngine` | The tamper-evident log does not survive a process restart, which defeats the point of it being tamper-evident. |
| Model | `fit_pipeline()` at boot, ~136 s | The service is unavailable for over two minutes at every deploy, and two replicas would silently serve two *different* models. |
| Concurrency | `ThreadingHTTPServer`, shared mutable lists, no locks | Two simultaneous `POST /v1/feedback` calls race. |
| Observability | `logging` to stderr, unstructured | No request IDs, no latency data, nothing scrapeable. |
| Config | `os.environ` reads scattered at call sites | Misconfiguration is discovered at first request, not at boot. |

None of that is a criticism of the code that is there. It is the correct
prototype. This plan turns it into a service.

## 1. Constraints that shape every decision

1. **No new dependencies.** `requirements.txt` is `numpy` and `pandas`, and the
   README makes a load-bearing claim out of that. No FastAPI, no SQLAlchemy, no
   Pydantic, no `prometheus_client`. Everything below is stdlib.
2. **`sqlite3` is in the standard library.** This is the whole reason the
   persistence tier is achievable without breaking rule 1.
3. **The deployment target is a single Hugging Face Docker Space** -- one
   container, one process, ephemeral disk. This is why SQLite is not a
   compromise here: a networked database would be an unusable dependency in the
   actual deployment environment.
4. **The science must not move.** No change in this plan may alter a number in
   `artifacts/report.json`. The backend serves the model; it does not touch it.

## 2. Target architecture

```
                       HTTP (stdlib ThreadingHTTPServer)
                                  |
                        +---------v---------+
                        |   api.py (v2)     |  auth, RBAC, rate limit,
                        |   router          |  idempotency, correlation ID
                        +----+---------+----+
                             |         |
              +--------------+         +--------------+
              |                                       |
     +--------v--------+                     +--------v--------+
     |   cases.py      |                     |  registry.py    |
     |  alert -> case  |                     |  model versions |
     |  lifecycle      |                     |  + promotion    |
     +--------+--------+                     +--------+--------+
              |                                       |
     +--------v---------------------------------------v--------+
     |                       store.py                           |
     |     SQLite (WAL), schema versioning, repositories        |
     +--------+--------------------------------------+---------+
              |                                      |
     +--------v--------+                    +--------v--------+
     |     bus.py      |                    |     obs.py      |
     |  outbox events  |                    |  JSON logs +    |
     |  + offsets      |                    |  /metrics       |
     +--------+--------+                    +-----------------+
              |
     +--------v--------+
     |    worker.py    |  drift job, outbox drain, retention
     +-----------------+
```

Every arrow is a function call in one process. The boundaries are drawn where
they would be drawn in the distributed version, so that `store.py` can become
Postgres and `bus.py` can become Kafka without any caller changing.

## 3. Phases

Each phase is one or more topic branches under the model in `CONTRIBUTING.md`,
merged `--no-ff` into `main`.

### Phase 1 -- Configuration and observability (`config.py`, `obs.py`)

Foundation for everything else, so it lands first.

* `Settings.from_env()` reads every environment variable **once, at boot**, and
  raises on anything invalid. A service that starts with a bad config and fails
  on the first request is worse than one that refuses to start.
* Fail-closed on `SENTINELAI_JWT_SECRET`: absent secret is a boot error, never a
  generated default. (Already true in `api.py`; now it is true in one place.)
* Structured JSON logs on one line per event, with a `request_id` propagated via
  `contextvars` so the log line for a handler deep in `cases.py` carries the ID
  of the request that caused it, without threading a parameter through.
* A tiny Prometheus registry: `Counter`, `Gauge`, `Histogram` with the standard
  cumulative `_bucket` / `_sum` / `_count` exposition. Roughly 120 lines.

Detail: [`OBSERVABILITY.md`](./OBSERVABILITY.md).

### Phase 2 -- Persistence (`store.py`)

* SQLite in **WAL mode**, which is the specific pragma that makes readers not
  block the writer -- required because `ThreadingHTTPServer` will have several
  request threads reading while one writes.
* `PRAGMA foreign_keys=ON` (off by default in SQLite, which surprises people),
  `busy_timeout=5000`, `synchronous=NORMAL` (safe under WAL).
* One connection per thread via `threading.local`, because SQLite connections
  are not thread-safe to share.
* A `schema_version` table and an ordered list of migrations. Applied inside a
  transaction at boot.
* Repository functions, not an ORM.

Detail: [`DATA-MODEL.md`](./DATA-MODEL.md).

### Phase 3 -- Model registry (`registry.py`)

This is the phase that fixes the 136-second boot.

* A fitted ensemble is serialised to a versioned directory with a manifest:
  content hash, feature contract, training window, metrics, deployment prior.
* Stages: `staging` -> `production` -> `archived`, with promotion recorded as an
  event, never as a silent file overwrite.
* **Promotion gate**: a version cannot enter `production` unless its recorded
  PR-AUC is within a configured tolerance of the incumbent and its feature
  contract is identical. A registry that lets you promote a regression is just a
  directory.
* The service loads `production` at boot. Fitting becomes an offline command.

Detail: [`MODEL-REGISTRY.md`](./MODEL-REGISTRY.md).

### Phase 4 -- Case management (`cases.py`)

Alerts are immutable detections. Cases are the mutable analyst workflow, and
conflating them is the single most common modelling error in SOC tooling.

Lifecycle: `new -> triaging -> (escalated) -> resolved{true_positive |
false_positive | benign_expected} -> closed`, with illegal transitions rejected
by a transition table rather than by an `if` chain. Every transition writes a
row to `case_events` -- the case's history is the event list, not a mutable
`status` column that forgets.

A resolved case with a `false_positive` verdict **is** an active-learning label.
The feedback endpoint and the case resolution endpoint write the same row.

### Phase 5 -- Event log and workers (`bus.py`, `worker.py`)

* A transactional outbox: domain writes and their events commit in **one**
  SQLite transaction, so an alert can never exist without its event.
* Consumers hold a named offset; delivery is at-least-once, so consumers must be
  idempotent, which is stated in the module docstring rather than assumed.
* `worker.py` runs periodic jobs in a daemon thread: drain the outbox, run PSI
  drift over the recent window and raise `retrain_recommended`, apply retention.

### Phase 6 -- API v2 (`api.py`)

* `/v1` stays exactly as it is. Breaking a documented API to add features is a
  choice, and the wrong one. New surface lands on `/v2`.
* Cursor pagination, filtering, `ETag` + `If-None-Match`.
* `Idempotency-Key` on every mutating route, with the first response body and
  status stored and replayed -- including errors, per Stripe's semantics.
* `GET /metrics` (unauthenticated, bindable to a separate interface),
  `GET /openapi.json`, `GET /healthz` (liveness) and `GET /readyz` (readiness:
  is a production model loaded, is the DB writable).

Detail: [`API-V2.md`](./API-V2.md).

### Phase 7 -- Hardening and tests

Concurrency tests that actually spawn threads, a migration idempotency test, an
audit-chain-survives-restart test, an idempotency-replay test, and a promotion
gate test. Target: the suite stays under 30 s.

## 4. What this plan deliberately does not do

* **No async rewrite.** `ThreadingHTTPServer` is adequate at this scale and an
  asyncio port would be a large diff with no measurable benefit for a service
  whose slowest operation is a NumPy matrix multiply.
* **No multi-tenancy.** There is no second tenant. Adding the column now would
  be speculative.
* **No websockets.** The console's live feed can poll. A push channel is a real
  feature, but it belongs after the data model is settled.
* **No Postgres.** See constraint 3. The seam exists so the swap is cheap later.

## 5. Definition of done

1. `python3 -m sentinelai.serve` starts in **under 2 seconds** against a
   registered production model.
2. Killing and restarting the service preserves alerts, cases, feedback and a
   **verifying** audit chain.
3. `GET /metrics` returns a valid Prometheus exposition.
4. Two concurrent writers do not corrupt state, proven by a threaded test.
5. A replayed `Idempotency-Key` returns the original response and does not
   perform the action twice.
6. No number in `artifacts/report.json` has changed.
7. `requirements.txt` still contains exactly two packages.
