# Data model

Expands Phase 2 of [`BACKEND-PLAN.md`](./BACKEND-PLAN.md). Target: SQLite via
the stdlib `sqlite3` module, written so the schema is portable to Postgres.

## 1. Connection policy

```sql
PRAGMA journal_mode = WAL;      -- readers do not block the writer
PRAGMA synchronous  = NORMAL;   -- safe under WAL; FULL is a needless fsync per commit
PRAGMA foreign_keys = ON;       -- OFF by default in SQLite, which is a trap
PRAGMA busy_timeout = 5000;     -- wait for the write lock instead of raising immediately
```

`journal_mode=WAL` is a **property of the database file**, not of the
connection, so it is set once at creation. The other three are per-connection
and must be re-applied on every new connection.

Connections are held in `threading.local()`. SQLite connection objects are not
safe to share across threads, and `check_same_thread=False` suppresses the
warning without fixing the problem.

SQLite serialises writers. Under WAL a writer does not block readers, but a
second writer waits. With `busy_timeout` this is a latency concern, not a
correctness one, at our write volume (tens per minute).

## 2. Migrations

```
MIGRATIONS: list[tuple[int, str, str]]   # (version, name, sql)
```

Applied in order inside one transaction each; `schema_version` records the
applied version, name and timestamp. Migrations are append-only and never
edited after they ship. `migrate()` is idempotent -- running it twice is a
no-op, which is asserted by a test, because a migration runner that is only
safe the first time is a runner that will eventually destroy a database.

## 3. Tables

### 3.1 `alerts` -- immutable detections

| Column | Type | Notes |
| --- | --- | --- |
| `alert_id` | TEXT PK | `AL-<window>-<entity>` |
| `entity` | TEXT NOT NULL | host or user id |
| `window_start` | INTEGER NOT NULL | epoch seconds |
| `score` | REAL NOT NULL | calibrated probability |
| `threshold` | REAL NOT NULL | the threshold *at detection time* |
| `model_version` | TEXT NOT NULL | FK -> `model_versions.version` |
| `suspected_family` | TEXT | heuristic, may be wrong (README §7.5) |
| `truth_family` | TEXT | present only for the synthetic corpus |
| `attribution` | TEXT | JSON, top-k Shapley |
| `created_at` | TEXT NOT NULL | ISO-8601 UTC |

Nothing in this table is ever updated. `threshold` and `model_version` are
denormalised onto the row on purpose: an alert must remain interpretable after
the threshold moves or the model is retrained. Storing only a foreign key would
make last month's alerts silently re-interpret themselves.

Indices: `(entity, window_start)`, `(created_at)`, `(score DESC)`.

### 3.2 `cases` -- mutable analyst workflow

| Column | Type | Notes |
| --- | --- | --- |
| `case_id` | TEXT PK | `CASE-<ulid>` |
| `alert_id` | TEXT NOT NULL | FK -> `alerts` |
| `status` | TEXT NOT NULL | see transition table |
| `verdict` | TEXT | null until resolved |
| `assignee` | TEXT | JWT `sub` |
| `priority` | INTEGER NOT NULL | 1-4, derived from score x asset criticality |
| `opened_at` / `updated_at` / `closed_at` | TEXT | ISO-8601 UTC |
| `version` | INTEGER NOT NULL | optimistic-concurrency counter |

`version` increments on every write and is checked on update. Two analysts
resolving the same case concurrently: the second gets `409 Conflict` instead of
silently overwriting the first. This is the cheapest correct answer -- row
locking in SQLite would mean holding a write transaction across an HTTP
round-trip, which is worse.

**Transitions** (anything absent is rejected with 422):

```
new        -> triaging, closed
triaging   -> escalated, resolved, new
escalated  -> resolved
resolved   -> closed, triaging      (reopen is legal; analysts are wrong sometimes)
closed     -> (terminal)
```

`verdict` in `{true_positive, false_positive, benign_expected}` and is required
to enter `resolved`.

### 3.3 `case_events` -- append-only history

`(event_id, case_id, at, actor, kind, from_status, to_status, note)`. The case's
history *is* this table. `cases.status` is a materialised convenience column; if
the two ever disagree, this table wins. A test asserts they agree after a
randomised transition sequence.

### 3.4 `feedback` -- active-learning labels

`(feedback_id, alert_id, actor, label, weight, source, created_at)`.

`source` in `{api, case_resolution}`. `weight` defaults to 3.0, matching
`active_learning.merge_feedback()`, which weights analyst labels 3x against the
historical set. The weight is stored per row rather than applied at merge time
so that a future change to the policy does not silently re-weight history.

### 3.5 `audit_log` -- the hash chain, persisted

`(seq INTEGER PK AUTOINCREMENT, at, actor, action, target, payload, prev_hash,
hash)`.

This is the table that matters most. The chain already exists in `soar.py`; it
has simply never survived a restart. On boot, `load_chain()` reads the last row
and seeds `ResponseEngine` with its hash so the chain continues rather than
restarting. `verify()` walks the table and recomputes.

A `BEFORE UPDATE` and a `BEFORE DELETE` trigger both `RAISE(ABORT)`. Append-only
is enforced by the database, not by convention -- convention is not a control.

### 3.6 `outbox` -- transactional events

`(offset INTEGER PK AUTOINCREMENT, topic, key, payload, created_at,
published_at)`. Written in the same transaction as the domain row.

### 3.7 `consumer_offsets`

`(consumer TEXT PK, offset INTEGER, updated_at)`. At-least-once: the offset
commits *after* the handler returns. A crash between the two replays the event,
so handlers must be idempotent.

### 3.8 `idempotency_keys`

`(key TEXT PK, route, request_hash, status_code, response_body, created_at,
expires_at)`.

`request_hash` is a SHA-256 of the canonicalised body. A replay with the **same**
key and a **different** body is a client bug and returns `422`, not the cached
response -- silently returning the first response to a different request is how
idempotency turns into data loss.

### 3.9 `model_versions`

See [`MODEL-REGISTRY.md`](./MODEL-REGISTRY.md).

## 4. Time

Every timestamp is ISO-8601 with an explicit `+00:00`, stored as TEXT. SQLite
has no date type; TEXT in this format sorts lexicographically in chronological
order, which makes range queries work without a conversion function. Naive
local-time strings would not, and are banned.

## 5. What is not stored here

Feature vectors and raw telemetry stay in `artifacts/`. They are bulk numeric
data with no transactional requirement, and putting 45,340 x 40 floats in SQLite
to satisfy a sense of tidiness would slow every query for no benefit. The
production mapping in README §5 sends them to TimescaleDB for exactly this
reason.
