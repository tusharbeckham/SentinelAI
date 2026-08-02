"""SQLite persistence: alerts, cases, feedback, a tamper-evident audit log,
an outbox and idempotency keys.

sqlite3 ships with Python, so this adds no dependency. In WAL mode readers do
not block the writer and the writer does not block readers, which is the whole
reason a single-file database is adequate for a single-replica service.

Three things here are worth knowing before editing:

* WAL is a property of the database *file*, not the connection. It is set once,
  at migrate time, and persists.
* Connections live in threading.local. A sqlite3 connection is not safe to share
  across threads, and ThreadingHTTPServer hands each request to a new thread.
* The audit log is append-only and enforced by triggers, not by convention. A
  hash chain that the application can quietly rewrite is decoration.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "Store", "StoreError", "ConflictError", "NotFound", "InvalidTransition",
    "IdempotencyConflict", "IdempotencyInFlight", "MIGRATIONS",
    "TRANSITIONS", "VERDICTS", "encode_cursor", "decode_cursor",
]

GENESIS = "0" * 64


class StoreError(Exception):
    pass


class NotFound(StoreError):
    pass


class ConflictError(StoreError):
    """Optimistic concurrency check failed."""


class InvalidTransition(StoreError):
    pass


class IdempotencyConflict(StoreError):
    """Same key replayed with a different body."""


class IdempotencyInFlight(StoreError):
    """Same key replayed while the first request is still running."""


# Case lifecycle. Closed is terminal: reopening creates a new case that links to
# the old one, so the history of a closed investigation cannot be edited after
# the fact.
TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    "new": ("triaging", "closed"),
    "triaging": ("escalated", "resolved", "new"),
    "escalated": ("resolved",),
    "resolved": ("closed", "triaging"),
    "closed": (),
}

VERDICTS = ("true_positive", "false_positive", "benign_expected")


# ---------------------------------------------------------------------------
# Migrations. Append only, never edit a shipped entry.
# ---------------------------------------------------------------------------

MIGRATIONS: List[Tuple[int, str, str]] = [
    (1, "core", """
CREATE TABLE IF NOT EXISTS alerts (
    id             TEXT PRIMARY KEY,
    entity         TEXT NOT NULL,
    window_start   INTEGER NOT NULL,
    score          REAL NOT NULL,
    probability    REAL NOT NULL,
    threshold      REAL NOT NULL,
    model_version  TEXT NOT NULL,
    suspected      TEXT,
    truth          TEXT,
    payload        TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS alerts_created ON alerts(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS alerts_entity  ON alerts(entity, window_start DESC);
CREATE INDEX IF NOT EXISTS alerts_prob    ON alerts(probability DESC, id DESC);

CREATE TABLE IF NOT EXISTS cases (
    id           TEXT PRIMARY KEY,
    alert_id     TEXT NOT NULL REFERENCES alerts(id),
    status       TEXT NOT NULL DEFAULT 'new',
    priority     TEXT NOT NULL DEFAULT 'medium',
    assignee     TEXT,
    verdict      TEXT,
    version      INTEGER NOT NULL DEFAULT 1,
    opened_by    TEXT NOT NULL,
    opened_at    TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    closed_at    TEXT
);
CREATE INDEX IF NOT EXISTS cases_status ON cases(status, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS cases_alert  ON cases(alert_id);

CREATE TABLE IF NOT EXISTS case_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id    TEXT NOT NULL REFERENCES cases(id),
    kind       TEXT NOT NULL,
    actor      TEXT NOT NULL,
    body       TEXT,
    from_state TEXT,
    to_state   TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS case_events_case ON case_events(case_id, id);

CREATE TABLE IF NOT EXISTS feedback (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id   TEXT NOT NULL,
    label      INTEGER NOT NULL,
    actor      TEXT NOT NULL,
    note       TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(alert_id, actor)
);
"""),
    (2, "audit", """
CREATE TABLE IF NOT EXISTS audit_log (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    actor      TEXT NOT NULL,
    action     TEXT NOT NULL,
    subject    TEXT,
    payload    TEXT NOT NULL,
    prev_hash  TEXT NOT NULL,
    hash       TEXT NOT NULL UNIQUE
);
CREATE TRIGGER IF NOT EXISTS audit_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;
"""),
    (3, "outbox", """
CREATE TABLE IF NOT EXISTS outbox (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    topic      TEXT NOT NULL,
    payload    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS outbox_topic ON outbox(topic, id);

CREATE TABLE IF NOT EXISTS consumer_offsets (
    consumer   TEXT PRIMARY KEY,
    last_id    INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key         TEXT PRIMARY KEY,
    route       TEXT NOT NULL,
    body_hash   TEXT NOT NULL,
    state       TEXT NOT NULL,
    status_code INTEGER,
    response    TEXT,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idem_expiry ON idempotency_keys(expires_at);
"""),
    (4, "registry", """
CREATE TABLE IF NOT EXISTS model_versions (
    version     TEXT PRIMARY KEY,
    stage       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    promoted_at TEXT,
    manifest    TEXT NOT NULL,
    pr_auc      REAL,
    ece         REAL,
    notes       TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_production
    ON model_versions(stage) WHERE stage = 'production';
"""),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "+00:00"


def canonical(payload: Any) -> str:
    """Deterministic JSON. The hash chain is only meaningful if two processes
    serialise the same payload to the same bytes."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def body_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def encode_cursor(sort_key: Any, row_id: Any) -> str:
    raw = canonical({"k": sort_key, "id": row_id}).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> Tuple[Any, Any]:
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        obj = json.loads(raw)
        return obj["k"], obj["id"]
    except Exception as exc:
        raise StoreError("malformed cursor") from exc


def _rows(cur: sqlite3.Cursor) -> List[Dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class Store:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._local = threading.local()
        self._write_lock = threading.Lock()
        # Every connection handed out, so close_all() can reach the ones
        # belonging to threads that have already finished.
        self._conns: set = set()
        self._conns_lock = threading.Lock()

    # -- connection handling ------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            # check_same_thread=False is safe here precisely because the
            # connection is kept in threading.local and is therefore still only
            # ever used by one thread. It exists so close_all() can shut a
            # connection down from the thread doing the cleanup, which the
            # default guard forbids.
            conn = sqlite3.connect(str(self.db_path), timeout=5.0,
                                   isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            # foreign_keys is per-connection and off by default; the others are
            # cheap to reassert.
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA synchronous = NORMAL")
            self._local.conn = conn
            with self._conns_lock:
                self._conns.add(conn)
        return conn

    def close(self) -> None:
        """Close this thread's connection only.

        Note the asymmetry: connections live in threading.local, so a caller
        that spawned worker threads cannot release their handles with this.
        Use close_all() for that.
        """
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            with self._conns_lock:
                self._conns.discard(conn)
            self._local.conn = None

    def close_all(self) -> int:
        """Close every connection this Store has opened, on any thread.

        Needed for orderly shutdown, and on Windows for correctness of cleanup:
        an open handle blocks deletion of the underlying file. POSIX will
        happily unlink a file that is still open, which hides the leak rather
        than fixing it.
        """
        with self._conns_lock:
            conns = list(self._conns)
            self._conns.clear()
        closed = 0
        for conn in conns:
            try:
                conn.close()
                closed += 1
            except Exception:
                pass
        self._local = threading.local()
        return closed

    def migrate(self) -> int:
        """Apply pending migrations. Idempotent, safe to call at every boot."""
        conn = self.connect()
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
        done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        applied = 0
        for version, name, sql in MIGRATIONS:
            if version in done:
                continue
            # executescript() issues an implicit COMMIT before it runs, so an
            # explicit BEGIN wrapped around the call is discarded and the
            # following COMMIT fails with 'no transaction is active'. The
            # transaction has to live inside the script instead. Bookkeeping is
            # inlined for the same reason: executescript takes no parameters.
            # Every value interpolated here is a module constant, never input.
            bookkeeping = (
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES ("
                + str(int(version)) + ", '" + name.replace("'", "''") + "', '"
                + _now() + "');")
            script = "BEGIN IMMEDIATE;" + sql + bookkeeping + "COMMIT;"
            with self._write_lock:
                try:
                    conn.executescript(script)
                except Exception:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    raise
            applied += 1
        return applied

    def schema_version(self) -> int:
        row = self.connect().execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
        return int(row[0])

    # -- alerts -------------------------------------------------------------

    def insert_alert(self, alert: Dict[str, Any]) -> str:
        conn = self.connect()
        alert_id = alert["id"]
        with self._write_lock:
            conn.execute(
                "INSERT OR IGNORE INTO alerts(id, entity, window_start, score, "
                "probability, threshold, model_version, suspected, truth, payload, "
                "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (alert_id, alert["entity"], int(alert["window_start"]),
                 float(alert.get("score", 0.0)), float(alert["probability"]),
                 float(alert["threshold"]), alert.get("model_version", "unknown"),
                 alert.get("suspected"), alert.get("truth"),
                 canonical(alert), alert.get("created_at") or _now()))
        return alert_id

    def get_alert(self, alert_id: str) -> Dict[str, Any]:
        row = self.connect().execute(
            "SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
        if row is None:
            raise NotFound("no alert " + alert_id)
        return dict(row)

    def list_alerts(self, limit: int = 50, cursor: Optional[str] = None,
                    entity: Optional[str] = None, suspected: Optional[str] = None,
                    min_probability: Optional[float] = None) -> Dict[str, Any]:
        """Keyset pagination ordered by (probability DESC, id DESC).

        Offset pagination over a table being written to skips and duplicates
        rows as offsets shift, which for an alert queue means an analyst
        silently never sees some alerts.
        """
        limit = max(1, min(int(limit), 200))
        where = []
        params: List[Any] = []
        if entity:
            where.append("entity = ?")
            params.append(entity)
        if suspected:
            where.append("suspected = ?")
            params.append(suspected)
        if min_probability is not None:
            where.append("probability >= ?")
            params.append(float(min_probability))
        if cursor:
            last_p, last_id = decode_cursor(cursor)
            where.append("(probability < ? OR (probability = ? AND id < ?))")
            params.extend([float(last_p), float(last_p), last_id])
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        sql = ("SELECT * FROM alerts" + clause
               + " ORDER BY probability DESC, id DESC LIMIT ?")
        params.append(limit + 1)
        rows = _rows(self.connect().execute(sql, params))
        next_cursor = None
        if len(rows) > limit:
            rows = rows[:limit]
            next_cursor = encode_cursor(rows[-1]["probability"], rows[-1]["id"])
        return {"items": rows, "next_cursor": next_cursor}

    def count_alerts(self) -> int:
        return int(self.connect().execute("SELECT COUNT(*) FROM alerts").fetchone()[0])

    # -- cases --------------------------------------------------------------

    def open_case(self, alert_id: str, actor: str, priority: str = "medium",
                  assignee: Optional[str] = None) -> Dict[str, Any]:
        conn = self.connect()
        now = _now()
        case_id = "CASE-" + hashlib.sha256(
            (alert_id + now + actor).encode("utf-8")).hexdigest()[:12]
        with self._write_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if conn.execute("SELECT 1 FROM alerts WHERE id = ?",
                                (alert_id,)).fetchone() is None:
                    raise NotFound("no alert " + alert_id)
                conn.execute(
                    "INSERT INTO cases(id, alert_id, status, priority, assignee, "
                    "version, opened_by, opened_at, updated_at) "
                    "VALUES (?,?,'new',?,?,1,?,?,?)",
                    (case_id, alert_id, priority, assignee, actor, now, now))
                conn.execute(
                    "INSERT INTO case_events(case_id, kind, actor, body, from_state, "
                    "to_state, created_at) VALUES (?,'opened',?,NULL,NULL,'new',?)",
                    (case_id, actor, now))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return self.get_case(case_id)

    def get_case(self, case_id: str, with_events: bool = False) -> Dict[str, Any]:
        row = self.connect().execute(
            "SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            raise NotFound("no case " + case_id)
        case = dict(row)
        if with_events:
            case["events"] = _rows(self.connect().execute(
                "SELECT * FROM case_events WHERE case_id = ? ORDER BY id", (case_id,)))
        return case

    def transition_case(self, case_id: str, to_state: str, actor: str,
                        expected_version: int, note: Optional[str] = None,
                        verdict: Optional[str] = None) -> Dict[str, Any]:
        """Move a case, guarded by an optimistic version check.

        Two analysts opening the same case and both clicking resolve is the
        normal case, not the exotic one. The loser gets a 409 rather than
        silently overwriting the winner.
        """
        if verdict is not None and verdict not in VERDICTS:
            raise StoreError("unknown verdict " + str(verdict))
        conn = self.connect()
        now = _now()
        with self._write_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT * FROM cases WHERE id = ?",
                                   (case_id,)).fetchone()
                if row is None:
                    raise NotFound("no case " + case_id)
                current = row["status"]
                if int(row["version"]) != int(expected_version):
                    raise ConflictError(
                        "case " + case_id + " is at version " + str(row["version"])
                        + ", client sent " + str(expected_version))
                allowed = TRANSITIONS.get(current, ())
                if to_state not in allowed:
                    raise InvalidTransition(
                        "cannot move case from " + current + " to " + to_state)
                closed_at = now if to_state == "closed" else row["closed_at"]
                conn.execute(
                    "UPDATE cases SET status = ?, version = version + 1, "
                    "updated_at = ?, closed_at = ?, verdict = COALESCE(?, verdict) "
                    "WHERE id = ? AND version = ?",
                    (to_state, now, closed_at, verdict, case_id, expected_version))
                conn.execute(
                    "INSERT INTO case_events(case_id, kind, actor, body, from_state, "
                    "to_state, created_at) VALUES (?,'transition',?,?,?,?,?)",
                    (case_id, actor, note, current, to_state, now))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return self.get_case(case_id)

    def add_case_note(self, case_id: str, actor: str, body: str) -> Dict[str, Any]:
        conn = self.connect()
        now = _now()
        with self._write_lock:
            if conn.execute("SELECT 1 FROM cases WHERE id = ?",
                            (case_id,)).fetchone() is None:
                raise NotFound("no case " + case_id)
            conn.execute(
                "INSERT INTO case_events(case_id, kind, actor, body, created_at) "
                "VALUES (?,'note',?,?,?)", (case_id, actor, body, now))
            conn.execute("UPDATE cases SET updated_at = ? WHERE id = ?", (now, case_id))
        return self.get_case(case_id, with_events=True)

    def list_cases(self, limit: int = 50, cursor: Optional[str] = None,
                   status: Optional[str] = None,
                   assignee: Optional[str] = None) -> Dict[str, Any]:
        limit = max(1, min(int(limit), 200))
        where = []
        params: List[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        if assignee:
            where.append("assignee = ?")
            params.append(assignee)
        if cursor:
            last_ts, last_id = decode_cursor(cursor)
            where.append("(updated_at < ? OR (updated_at = ? AND id < ?))")
            params.extend([last_ts, last_ts, last_id])
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        sql = ("SELECT * FROM cases" + clause
               + " ORDER BY updated_at DESC, id DESC LIMIT ?")
        params.append(limit + 1)
        rows = _rows(self.connect().execute(sql, params))
        next_cursor = None
        if len(rows) > limit:
            rows = rows[:limit]
            next_cursor = encode_cursor(rows[-1]["updated_at"], rows[-1]["id"])
        return {"items": rows, "next_cursor": next_cursor}

    def open_case_counts(self) -> Dict[str, int]:
        rows = self.connect().execute(
            "SELECT status, COUNT(*) AS n FROM cases GROUP BY status").fetchall()
        return {r["status"]: int(r["n"]) for r in rows}

    # -- feedback -----------------------------------------------------------

    def add_feedback(self, alert_id: str, label: int, actor: str,
                     note: Optional[str] = None) -> int:
        if label not in (0, 1):
            raise StoreError("label must be 0 or 1")
        conn = self.connect()
        with self._write_lock:
            cur = conn.execute(
                "INSERT INTO feedback(alert_id, label, actor, note, created_at) "
                "VALUES (?,?,?,?,?) ON CONFLICT(alert_id, actor) DO UPDATE SET "
                "label = excluded.label, note = excluded.note, "
                "created_at = excluded.created_at",
                (alert_id, int(label), actor, note, _now()))
        return int(cur.rowcount)

    def feedback_for(self, alert_id: str) -> List[Dict[str, Any]]:
        return _rows(self.connect().execute(
            "SELECT * FROM feedback WHERE alert_id = ? ORDER BY id", (alert_id,)))

    def count_feedback(self) -> int:
        return int(self.connect().execute("SELECT COUNT(*) FROM feedback").fetchone()[0])

    # -- audit chain --------------------------------------------------------

    @staticmethod
    def _chain_hash(prev_hash: str, ts: str, actor: str, action: str,
                    subject: Optional[str], payload_json: str) -> str:
        material = "|".join([prev_hash, ts, actor, action, subject or "", payload_json])
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def append_audit(self, actor: str, action: str, payload: Any,
                     subject: Optional[str] = None) -> Dict[str, Any]:
        conn = self.connect()
        ts = _now()
        payload_json = canonical(payload)
        with self._write_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
                prev = row["hash"] if row is not None else GENESIS
                digest = self._chain_hash(prev, ts, actor, action, subject, payload_json)
                conn.execute(
                    "INSERT INTO audit_log(ts, actor, action, subject, payload, "
                    "prev_hash, hash) VALUES (?,?,?,?,?,?,?)",
                    (ts, actor, action, subject, payload_json, prev, digest))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return {"ts": ts, "actor": actor, "action": action, "subject": subject,
                "prev_hash": prev, "hash": digest}

    def verify_chain(self) -> Dict[str, Any]:
        """Recompute every link. Returns the first break rather than a bare bool,
        because 'the chain is broken' is not an actionable statement."""
        prev = GENESIS
        checked = 0
        for row in self.connect().execute("SELECT * FROM audit_log ORDER BY seq"):
            expected = self._chain_hash(prev, row["ts"], row["actor"], row["action"],
                                        row["subject"], row["payload"])
            if row["prev_hash"] != prev:
                return {"valid": False, "checked": checked, "broken_at": row["seq"],
                        "reason": "prev_hash mismatch"}
            if row["hash"] != expected:
                return {"valid": False, "checked": checked, "broken_at": row["seq"],
                        "reason": "hash mismatch"}
            prev = row["hash"]
            checked += 1
        return {"valid": True, "checked": checked, "broken_at": None,
                "head": prev, "reason": None}

    def audit_page(self, limit: int = 100, cursor: Optional[str] = None) -> Dict[str, Any]:
        limit = max(1, min(int(limit), 500))
        params: List[Any] = []
        clause = ""
        if cursor:
            _, last_seq = decode_cursor(cursor)
            clause = " WHERE seq < ?"
            params.append(int(last_seq))
        params.append(limit + 1)
        rows = _rows(self.connect().execute(
            "SELECT * FROM audit_log" + clause + " ORDER BY seq DESC LIMIT ?", params))
        next_cursor = None
        if len(rows) > limit:
            rows = rows[:limit]
            next_cursor = encode_cursor(rows[-1]["seq"], rows[-1]["seq"])
        return {"items": rows, "next_cursor": next_cursor}

    # -- outbox -------------------------------------------------------------

    def publish(self, topic: str, payload: Any) -> int:
        conn = self.connect()
        with self._write_lock:
            cur = conn.execute(
                "INSERT INTO outbox(topic, payload, created_at) VALUES (?,?,?)",
                (topic, canonical(payload), _now()))
        return int(cur.lastrowid)

    def fetch_events(self, consumer: str, topic: Optional[str] = None,
                     limit: int = 100) -> List[Dict[str, Any]]:
        conn = self.connect()
        row = conn.execute("SELECT last_id FROM consumer_offsets WHERE consumer = ?",
                           (consumer,)).fetchone()
        last_id = int(row["last_id"]) if row is not None else 0
        params: List[Any] = [last_id]
        clause = ""
        if topic:
            clause = " AND topic = ?"
            params.append(topic)
        params.append(int(limit))
        return _rows(conn.execute(
            "SELECT * FROM outbox WHERE id > ?" + clause + " ORDER BY id LIMIT ?",
            params))

    def commit_offset(self, consumer: str, last_id: int) -> None:
        """Advance the offset after the side effect has happened.

        Committing first would give at-most-once delivery and lose events on a
        crash; committing after gives at-least-once, so consumers must be
        idempotent. That is the trade we want for security events.
        """
        conn = self.connect()
        with self._write_lock:
            conn.execute(
                "INSERT INTO consumer_offsets(consumer, last_id, updated_at) "
                "VALUES (?,?,?) ON CONFLICT(consumer) DO UPDATE SET "
                "last_id = MAX(last_id, excluded.last_id), "
                "updated_at = excluded.updated_at",
                (consumer, int(last_id), _now()))

    def outbox_lag(self, consumer: str) -> int:
        conn = self.connect()
        row = conn.execute("SELECT last_id FROM consumer_offsets WHERE consumer = ?",
                           (consumer,)).fetchone()
        last_id = int(row["last_id"]) if row is not None else 0
        return int(conn.execute("SELECT COUNT(*) FROM outbox WHERE id > ?",
                                (last_id,)).fetchone()[0])

    # -- idempotency --------------------------------------------------------

    def begin_idempotent(self, key: str, route: str, body: bytes,
                         ttl_seconds: int = 86400) -> Optional[Dict[str, Any]]:
        """Claim a key. Returns None to proceed, or the stored response to replay.

        Replaying a stored 4xx or 5xx is correct: the client asked what happened
        to that request, and the answer is what happened.
        """
        conn = self.connect()
        digest = body_hash(body)
        now = time.time()
        now_iso = _now()
        expires = time.strftime("%Y-%m-%dT%H:%M:%S",
                                time.gmtime(now + ttl_seconds)) + "+00:00"
        with self._write_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("DELETE FROM idempotency_keys WHERE expires_at < ?",
                             (now_iso,))
                row = conn.execute("SELECT * FROM idempotency_keys WHERE key = ?",
                                   (key,)).fetchone()
                if row is None:
                    conn.execute(
                        "INSERT INTO idempotency_keys(key, route, body_hash, state, "
                        "created_at, expires_at) VALUES (?,?,?,'in_flight',?,?)",
                        (key, route, digest, now_iso, expires))
                    conn.execute("COMMIT")
                    return None
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        if row["body_hash"] != digest:
            raise IdempotencyConflict(
                "idempotency key reused with a different body")
        if row["state"] == "in_flight":
            raise IdempotencyInFlight("request with this key is still in flight")
        return {"status_code": int(row["status_code"] or 500),
                "response": row["response"]}

    def complete_idempotent(self, key: str, status_code: int, response: str) -> None:
        conn = self.connect()
        with self._write_lock:
            conn.execute(
                "UPDATE idempotency_keys SET state = 'done', status_code = ?, "
                "response = ? WHERE key = ?", (int(status_code), response, key))

    def release_idempotent(self, key: str) -> None:
        """Drop an in-flight claim so a crashed request can be retried."""
        conn = self.connect()
        with self._write_lock:
            conn.execute(
                "DELETE FROM idempotency_keys WHERE key = ? AND state = 'in_flight'",
                (key,))
