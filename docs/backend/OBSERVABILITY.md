# Observability and configuration

Expands Phase 1 of [`BACKEND-PLAN.md`](./BACKEND-PLAN.md). Stdlib only:
`logging`, `contextvars`, `os`, `threading`, `time`.

## 1. Configuration (`config.py`)

One frozen dataclass, built once at boot by `Settings.from_env()`, passed
explicitly to everything that needs it. No module reaches into `os.environ`
after boot.

| Variable | Default | Notes |
| --- | --- | --- |
| `SENTINELAI_JWT_SECRET` | **none** | Boot error if absent or shorter than 32 chars. Never generated. |
| `SENTINELAI_DB` | `data/sentinelai.db` | SQLite path; parent created on boot. |
| `SENTINELAI_ARTIFACTS` | `artifacts` | Where `report.json` etc. live. |
| `SENTINELAI_MODELS` | `models` | Registry root. |
| `SENTINELAI_HOST` / `_PORT` | `127.0.0.1` / `8088` | Bind address. |
| `SENTINELAI_TOKEN_TTL` | `3600` | Seconds. Rejected if > 86400. |
| `SENTINELAI_RATE_CAPACITY` / `_REFILL` | `30` / `0.5` | Token bucket. |
| `SENTINELAI_IDEMPOTENCY_TTL` | `86400` | Seconds a key is replayable. |
| `SENTINELAI_LOG_LEVEL` | `INFO` | |
| `SENTINELAI_LOG_FORMAT` | `json` | `json` or `text` for local work. |
| `SENTINELAI_ENV` | `dev` | `dev` / `staging` / `prod`. |
| `SENTINELAI_WORKER_INTERVAL` | `60` | Seconds between worker sweeps. |

### Fail-closed, at boot

The secret rule is the important one. A service that invents a signing key when
the environment forgets to supply one issues tokens that verify against a
secret nobody knows, and it does so *silently*. `from_env()` raises
`ConfigError` listing **every** problem it found, not just the first -- fixing
misconfiguration one restart at a time is miserable.

`Settings.redacted()` returns the config with the secret replaced by
`sha256(secret)[:8]`, so boot logs can prove *which* secret is loaded without
leaking it. That fingerprint is genuinely useful when two replicas disagree.

## 2. Structured logging (`obs.py`)

One JSON object per line:

```json
{"ts":"2026-08-02T12:41:07.412+00:00","level":"INFO","logger":"api",
 "msg":"request completed","request_id":"01J...","route":"POST /v2/cases",
 "status":201,"duration_ms":14.2,"actor":"analyst-1","env":"prod"}
```

### Correlation IDs via `contextvars`

```python
request_id: ContextVar[str] = ContextVar("request_id", default="-")
```

A `logging.Filter` injects the current value into every record. This is the
reason for `contextvars` over `threading.local`: the ID is set once in the
handler and every log line emitted anywhere downstream -- `store.py`,
`cases.py`, `soar.py` -- carries it, with no parameter threaded through the call
stack. `ThreadingHTTPServer` gives each request its own thread, and
`contextvars` are per-thread by construction, so no cleanup is required.

Inbound `X-Request-ID` is honoured if it matches `^[A-Za-z0-9_-]{8,64}$` and
regenerated otherwise. Echoing an unvalidated client header into logs is a log
injection vector.

### Redaction is not optional

`redact()` already exists in `api.py` and is tested against bearer tokens and
API keys. It moves to `obs.py` and is applied by the **formatter**, so it cannot
be forgotten at a call site. A redaction function that each caller must remember
to invoke is a redaction function that will eventually be skipped.

## 3. Metrics (`obs.py`)

A ~120-line Prometheus registry. Three types, which is all we need:

* `Counter` -- monotonic. `inc(labels, n=1)`.
* `Gauge` -- arbitrary. `set(labels, v)`.
* `Histogram` -- cumulative buckets, per the exposition format: every
  `_bucket{le="x"}` includes all lower buckets, plus `_sum` and `_count`, plus a
  final `le="+Inf"` that must equal `_count`.

Default latency buckets, in seconds:
`(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)`.

### The metrics that will actually be looked at

| Metric | Type | Labels |
| --- | --- | --- |
| `sentinelai_http_requests_total` | counter | `route`, `method`, `status` |
| `sentinelai_http_request_duration_seconds` | histogram | `route`, `method` |
| `sentinelai_auth_failures_total` | counter | `reason` |
| `sentinelai_rate_limited_total` | counter | `route` |
| `sentinelai_score_duration_seconds` | histogram | -- |
| `sentinelai_alerts_total` | counter | `family` |
| `sentinelai_cases_open` | gauge | `status` |
| `sentinelai_soar_actions_total` | counter | `action` |
| `sentinelai_audit_chain_valid` | gauge | -- |
| `sentinelai_model_info` | gauge | `version`, `stage` (value 1) |
| `sentinelai_drift_psi_max` | gauge | -- |
| `sentinelai_outbox_lag` | gauge | `consumer` |

`sentinelai_audit_chain_valid` deserves a note: it is the one metric that should
page a human. It is 0 only if the hash chain fails verification, which means
someone edited the response history.

**Label cardinality.** `route` is the *pattern* (`/v2/cases/{id}`), never the
resolved path. Emitting the concrete path would create one time series per case
and melt any Prometheus that scraped it. Enforced by a test that asserts no
registered label value contains a digit run longer than four.

### Thread safety

One `threading.Lock` around the whole registry. Contention is irrelevant at this
rate, and per-metric locks would be premature.

## 4. Health endpoints

* `GET /healthz` -- **liveness**. Returns 200 if the process is running. Never
  touches the DB. A liveness probe that checks dependencies causes a restart
  loop when a dependency is down, which is the opposite of helpful.
* `GET /readyz` -- **readiness**. 200 only if a production model is loaded and
  the DB accepts a write. 503 otherwise, with a reason.

## 5. What is deliberately absent

**No OpenTelemetry, no tracing spans.** In a single-process service the
correlation ID plus a duration histogram gives essentially everything a trace
would, and OTel is a large dependency tree that would break the two-package
claim. When `store.py` becomes a network call, that calculus changes and this
decision should be revisited -- noted here so the reasoning is recoverable.


## Cardinality: the one deliberate exception

`Registry.gauge` refuses label values matching `[0-9]{5,}`, which is what stops
an entity id or a timestamp from being used as a label and quietly turning one
time series into a hundred thousand.

`MODEL_INFO{version,stage}` is exempt via `allow_identifier_labels=True`. A
registry version id such as `v20260803T061337Z-aecf27` contains eight
consecutive digits and would otherwise raise `CardinalityError` the first time a
model loaded -- the guard firing on a series whose cardinality is bounded by the
number of models ever promoted. The exemption is explicit and opt-in rather than
a caught exception or a mangled version string, so the guard still protects
every other metric.

## Outbox lag

`sentinelai_outbox_lag{consumer}` is the number of undelivered events per
consumer. It is the metric to alert on: a poison event makes a consumer block
rather than skip, so a stuck consumer shows as monotonically rising lag. That is
intentional. Skipping a failing event would keep the gauge at zero while quietly
discarding security events, which is the failure nobody notices.
