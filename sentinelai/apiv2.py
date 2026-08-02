"""The /v2 router: the same API, but backed by the store.

/v1 is frozen. Every route, status code and body shape documented in README
section 2.7 keeps working and its tests keep passing unmodified. This module is
additive: it owns /v2, plus the three unversioned operational endpoints
(/readyz, /metrics, /openapi.json is left to the caller).

The duplication between v1 and v2 is deliberate. Quietly changing a response
shape to add a field is how integrations break at 3am, and the console and the
README both document v1.

What v2 adds over v1:

  * Persistence. Feedback and audit entries go to SQLite and survive a restart.
    In v1 they lived in a Python list and a process-local chain.
  * Cursor pagination. Offset pagination over a table that is being written to
    skips and duplicates rows as the offsets shift underneath the reader, which
    for an alert queue means an analyst silently never sees some alerts.
  * Idempotency on every mutating route, required on /v2/respond. A containment
    action must never fire twice because a client retried.
  * Case lifecycle with optimistic concurrency, spelled in HTTP as ETag and
    If-Match.
  * One error shape, always, carrying a request_id.

Routes
  GET    /readyz                    public     model loaded and DB writable
  GET    /metrics                   public     Prometheus exposition
  GET    /v2/alerts                 viewer     cursor pagination, filters, ETag
  GET    /v2/alerts/{id}            viewer
  POST   /v2/score                  analyst    idempotent
  GET    /v2/cases                  viewer
  POST   /v2/cases                  analyst    idempotent
  GET    /v2/cases/{id}             viewer     ETag carries the version
  PATCH  /v2/cases/{id}             analyst    requires If-Match
  POST   /v2/cases/{id}/notes       analyst    append-only
  POST   /v2/feedback               analyst    idempotent
  POST   /v2/respond                responder  idempotent, dry-run by default
  GET    /v2/audit                  admin      paginated
  GET    /v2/audit/verify           admin      recomputes the whole chain
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import obs
from .store import (
    ConflictError,
    IdempotencyConflict,
    IdempotencyInFlight,
    InvalidTransition,
    NotFound,
    Store,
    StoreError,
    VERDICTS,
)

ROLE_ORDER = {"viewer": 0, "analyst": 1, "responder": 2, "admin": 3}
MAX_LIMIT = 200
DEFAULT_LIMIT = 50
PRIORITIES = ("low", "medium", "high", "critical")

# status, payload, extra headers. A str payload is served as text/plain.
Result = Tuple[int, Any, Dict[str, str]]


class ApiError(Exception):
    """An error with a stable machine token, not just a status code."""

    def __init__(self, status: int, error: str, message: str,
                 details: Optional[Dict[str, Any]] = None,
                 headers: Optional[Dict[str, str]] = None) -> None:
        super().__init__(message)
        self.status = int(status)
        self.error = error
        self.message = message
        self.details = details or {}
        self.headers = headers or {}

    def body(self) -> Dict[str, Any]:
        return {
            "error": self.error,
            "message": self.message,
            "request_id": obs.request_id_var.get(),
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# request helpers
# ---------------------------------------------------------------------------

def require_role(claims: Optional[Dict[str, Any]], need: str) -> str:
    """Authenticate then authorise, and never say which one failed in the body."""
    if not claims:
        raise ApiError(401, "unauthenticated", "authentication required")
    role = claims.get("role")
    if ROLE_ORDER.get(role, -1) < ROLE_ORDER[need]:
        obs.AUTH_FAILURES.inc({"reason": "insufficient_role"})
        raise ApiError(403, "forbidden", "this role cannot access this route")
    return str(claims.get("sub") or "unknown")


def parse_json(body: bytes) -> Dict[str, Any]:
    if not body:
        raise ApiError(400, "empty_body", "a JSON object body is required")
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError(400, "malformed_json", "body is not valid JSON",
                       {"reason": str(exc)})
    if not isinstance(parsed, dict):
        raise ApiError(400, "malformed_json", "body must be a JSON object")
    return parsed


def _one(query: Dict[str, List[str]], key: str) -> Optional[str]:
    values = query.get(key)
    if not values:
        return None
    return values[0]


def _limit_of(query: Dict[str, List[str]]) -> int:
    raw = _one(query, "limit")
    if raw is None:
        return DEFAULT_LIMIT
    try:
        value = int(raw)
    except ValueError:
        raise ApiError(400, "invalid_limit", "limit must be an integer",
                       {"got": raw})
    return max(1, min(value, MAX_LIMIT))


def _float_of(query: Dict[str, List[str]], key: str) -> Optional[float]:
    raw = _one(query, key)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        raise ApiError(400, "invalid_" + key, key + " must be a number",
                       {"got": raw})


def etag_of(payload: Any) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    return chr(34) + digest + chr(34)


def _unquote_etag(raw: str) -> str:
    value = raw.strip()
    if value.startswith("W/"):
        value = value[2:]
    return value.strip(chr(34))


# ---------------------------------------------------------------------------
# router
# ---------------------------------------------------------------------------

class V2Router:
    """Dispatch for /v2. Deliberately transport-agnostic.

    It takes already-parsed pieces of a request and returns a status, a payload
    and headers. That keeps it testable without binding a socket, and means the
    stdlib handler in api.py stays a thin adapter.
    """

    def __init__(self, store: Store, scorer: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
                 idempotency_ttl: int = 86400, execute: bool = False) -> None:
        self.store = store
        self.scorer = scorer
        self.idempotency_ttl = int(idempotency_ttl)
        self.execute = bool(execute)

    # -- entry point --------------------------------------------------------

    def dispatch(self, method: str, path: str,
                 query: Optional[Dict[str, List[str]]] = None,
                 body: bytes = b"",
                 claims: Optional[Dict[str, Any]] = None,
                 headers: Optional[Dict[str, str]] = None) -> Result:
        query = query or {}
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        parts = [p for p in path.split("/") if p]

        if method == "GET" and path == "/readyz":
            return self.readyz()
        if method == "GET" and path == "/metrics":
            return (200, obs.REGISTRY.render(), {})

        if not parts or parts[0] != "v2":
            raise ApiError(404, "not_found", "no such route", {"path": path})
        rest = parts[1:]

        if rest[:1] == ["alerts"]:
            if method == "GET" and len(rest) == 1:
                return self.list_alerts(query, claims)
            if method == "GET" and len(rest) == 2:
                return self.get_alert(rest[1], claims)
        elif rest == ["score"] and method == "POST":
            return self.score(body, claims, headers)
        elif rest[:1] == ["cases"]:
            if method == "GET" and len(rest) == 1:
                return self.list_cases(query, claims)
            if method == "POST" and len(rest) == 1:
                return self.open_case(body, claims, headers)
            if method == "GET" and len(rest) == 2:
                return self.get_case(rest[1], claims)
            if method == "PATCH" and len(rest) == 2:
                return self.patch_case(rest[1], body, claims, headers)
            if method == "POST" and len(rest) == 3 and rest[2] == "notes":
                return self.add_note(rest[1], body, claims)
        elif rest == ["feedback"] and method == "POST":
            return self.feedback(body, claims, headers)
        elif rest == ["respond"] and method == "POST":
            return self.respond(body, claims, headers)
        elif rest[:1] == ["audit"]:
            if method == "GET" and len(rest) == 1:
                return self.audit(query, claims)
            if method == "GET" and rest[1:] == ["verify"]:
                return self.audit_verify(claims)

        raise ApiError(404, "not_found", "no such route",
                       {"path": path, "method": method})

    # -- operational --------------------------------------------------------

    def readyz(self) -> Result:
        """Readiness is a real check, not an echo.

        Liveness says the process is up; readiness must say the process can do
        its job. A /readyz that returns 200 unconditionally is worse than none,
        because it will keep a broken replica in the load balancer.
        """
        problems: List[str] = []
        try:
            version = self.store.schema_version()
            if version <= 0:
                problems.append("database not migrated")
        except Exception as exc:
            problems.append("database unreachable: " + type(exc).__name__)
            version = 0
        if self.scorer is None:
            problems.append("model not loaded")
        if problems:
            return (503, {"ready": False, "problems": problems,
                          "schema_version": version}, {})
        return (200, {"ready": True, "problems": [],
                      "schema_version": version}, {})

    # -- idempotency --------------------------------------------------------

    def _idempotent(self, key: Optional[str], route: str, body: bytes,
                    run: Callable[[], Result], required: bool = False) -> Result:
        if not key:
            if required:
                raise ApiError(
                    400, "idempotency_key_required",
                    "this route requires an Idempotency-Key header",
                    {"route": route})
            return run()

        try:
            replay = self.store.begin_idempotent(key, route, body,
                                                 self.idempotency_ttl)
        except IdempotencyConflict:
            raise ApiError(422, "idempotency_key_reuse",
                           "this key was already used with a different body",
                           {"route": route})
        except IdempotencyInFlight:
            raise ApiError(409, "request_in_flight",
                           "a request with this key is still in flight",
                           {"route": route}, {"Retry-After": "1"})

        if replay is not None:
            # Replaying a stored 4xx is correct: the client asked what happened
            # to that request, and the answer is what happened.
            try:
                payload = json.loads(replay["response"])
            except (TypeError, json.JSONDecodeError):
                payload = {"error": "replay_unavailable",
                           "message": "stored response could not be decoded",
                           "request_id": obs.request_id_var.get(),
                           "details": {}}
            return (replay["status_code"], payload, {"Idempotent-Replay": "true"})

        try:
            status, payload, extra = run()
        except ApiError as exc:
            self.store.complete_idempotent(key, exc.status,
                                           json.dumps(exc.body()))
            raise
        except Exception:
            # An unexpected failure is not a decided outcome, so the key is
            # released rather than pinned to a 500 the client can never retry.
            self.store.release_idempotent(key)
            raise
        self.store.complete_idempotent(key, status, json.dumps(payload))
        return (status, payload, extra)

    # -- alerts -------------------------------------------------------------

    def list_alerts(self, query: Dict[str, List[str]],
                    claims: Optional[Dict[str, Any]]) -> Result:
        require_role(claims, "viewer")
        page = self.store.list_alerts(
            limit=_limit_of(query),
            cursor=_one(query, "cursor"),
            entity=_one(query, "entity"),
            suspected=_one(query, "suspected"),
            min_probability=_float_of(query, "min_probability"),
        )
        return (200, page, {"ETag": etag_of(page)})

    def get_alert(self, alert_id: str, claims: Optional[Dict[str, Any]]) -> Result:
        require_role(claims, "viewer")
        try:
            alert = self.store.get_alert(alert_id)
        except NotFound:
            raise ApiError(404, "alert_not_found", "no such alert",
                           {"alert_id": alert_id})
        return (200, alert, {"ETag": etag_of(alert)})

    # -- scoring ------------------------------------------------------------

    def score(self, body: bytes, claims: Optional[Dict[str, Any]],
              headers: Dict[str, str]) -> Result:
        require_role(claims, "analyst")
        if self.scorer is None:
            raise ApiError(503, "model_not_loaded",
                           "no scorer is attached to this router")
        payload = parse_json(body)
        features = payload.get("features")
        if not isinstance(features, dict):
            raise ApiError(422, "invalid_features",
                           "features must be an object of name to number")

        def run() -> Result:
            started = time.perf_counter()
            result = self.scorer(features)
            obs.SCORE_LATENCY.observe(time.perf_counter() - started)
            return (200, result, {})

        return self._idempotent(headers.get("idempotency-key"), "/v2/score",
                                body, run)

    # -- cases --------------------------------------------------------------

    def list_cases(self, query: Dict[str, List[str]],
                   claims: Optional[Dict[str, Any]]) -> Result:
        require_role(claims, "viewer")
        page = self.store.list_cases(
            limit=_limit_of(query),
            cursor=_one(query, "cursor"),
            status=_one(query, "status"),
            assignee=_one(query, "assignee"),
        )
        return (200, page, {"ETag": etag_of(page)})

    def open_case(self, body: bytes, claims: Optional[Dict[str, Any]],
                  headers: Dict[str, str]) -> Result:
        actor = require_role(claims, "analyst")
        payload = parse_json(body)
        alert_id = payload.get("alert_id")
        if not isinstance(alert_id, str) or not alert_id:
            raise ApiError(422, "invalid_alert_id", "alert_id is required")
        priority = payload.get("priority", "medium")
        if priority not in PRIORITIES:
            raise ApiError(422, "invalid_priority",
                           "priority is not a known value",
                           {"got": priority, "allowed": list(PRIORITIES)})

        def run() -> Result:
            try:
                case = self.store.open_case(
                    alert_id, actor, priority=priority,
                    assignee=payload.get("assignee"))
            except NotFound:
                raise ApiError(422, "alert_not_found",
                               "cannot open a case on an alert that does not exist",
                               {"alert_id": alert_id})
            self._refresh_case_gauge()
            return (201, case, {"Location": "/v2/cases/" + str(case.get("id")),
                                "ETag": chr(34) + str(case.get("version", 1)) + chr(34)})

        return self._idempotent(headers.get("idempotency-key"), "/v2/cases",
                                body, run)

    def get_case(self, case_id: str, claims: Optional[Dict[str, Any]]) -> Result:
        require_role(claims, "viewer")
        try:
            case = self.store.get_case(case_id, with_events=True)
        except NotFound:
            raise ApiError(404, "case_not_found", "no such case",
                           {"case_id": case_id})
        return (200, case, {"ETag": chr(34) + str(case.get("version", 1)) + chr(34)})

    def patch_case(self, case_id: str, body: bytes,
                   claims: Optional[Dict[str, Any]],
                   headers: Dict[str, str]) -> Result:
        actor = require_role(claims, "analyst")
        payload = parse_json(body)
        to_state = payload.get("status")
        if not isinstance(to_state, str) or not to_state:
            raise ApiError(422, "invalid_status", "status is required")
        verdict = payload.get("verdict")
        if verdict is not None and verdict not in VERDICTS:
            raise ApiError(422, "invalid_verdict", "verdict is not a known value",
                           {"got": verdict, "allowed": list(VERDICTS)})

        raw_match = headers.get("if-match")
        if not raw_match:
            # 428 rather than a silent last-write-wins. Two analysts resolving
            # the same case is the normal situation, not the exotic one.
            raise ApiError(428, "precondition_required",
                           "If-Match with the case version is required")
        try:
            expected = int(_unquote_etag(raw_match))
        except ValueError:
            raise ApiError(400, "invalid_if_match",
                           "If-Match must carry an integer version",
                           {"got": raw_match})

        try:
            case = self.store.transition_case(
                case_id, to_state, actor, expected_version=expected,
                note=payload.get("note"), verdict=verdict)
        except NotFound:
            raise ApiError(404, "case_not_found", "no such case",
                           {"case_id": case_id})
        except ConflictError:
            raise ApiError(412, "version_conflict",
                           "the case changed since you read it; re-read and retry",
                           {"case_id": case_id, "expected_version": expected})
        except InvalidTransition as exc:
            raise ApiError(422, "invalid_transition", str(exc),
                           {"case_id": case_id, "to": to_state})
        except StoreError as exc:
            raise ApiError(422, "invalid_case_update", str(exc))

        self._refresh_case_gauge()
        return (200, case,
                {"ETag": chr(34) + str(case.get("version", 1)) + chr(34)})

    def add_note(self, case_id: str, body: bytes,
                 claims: Optional[Dict[str, Any]]) -> Result:
        actor = require_role(claims, "analyst")
        payload = parse_json(body)
        text = payload.get("body")
        if not isinstance(text, str) or not text.strip():
            raise ApiError(422, "invalid_note", "body must be a non-empty string")
        try:
            event = self.store.add_case_note(case_id, actor, text)
        except NotFound:
            raise ApiError(404, "case_not_found", "no such case",
                           {"case_id": case_id})
        return (201, event, {})

    def _refresh_case_gauge(self) -> None:
        for status, count in self.store.open_case_counts().items():
            obs.CASES_OPEN.set(float(count), {"status": status})

    # -- feedback -----------------------------------------------------------

    def feedback(self, body: bytes, claims: Optional[Dict[str, Any]],
                 headers: Dict[str, str]) -> Result:
        actor = require_role(claims, "analyst")
        payload = parse_json(body)
        alert_id = payload.get("alert_id")
        label = payload.get("label")
        if not isinstance(alert_id, str) or not alert_id:
            raise ApiError(422, "invalid_alert_id", "alert_id is required")
        if label not in (0, 1):
            raise ApiError(422, "invalid_label", "label must be 0 or 1",
                           {"got": label})

        def run() -> Result:
            # The feedback table does not constrain alert_id, so the store will
            # happily accept a verdict on an alert that does not exist. That is
            # a label the retraining loop can never join back to features, so
            # it is refused here rather than stored as quiet garbage.
            try:
                self.store.get_alert(alert_id)
            except NotFound:
                raise ApiError(422, "alert_not_found",
                               "cannot record feedback on an unknown alert",
                               {"alert_id": alert_id})
            try:
                count = self.store.add_feedback(alert_id, int(label), actor,
                                                note=payload.get("note"))
            except StoreError as exc:
                raise ApiError(422, "invalid_feedback", str(exc),
                               {"alert_id": alert_id})
            self.store.append_audit(actor, "feedback",
                                    {"alert_id": alert_id, "label": int(label)},
                                    subject=alert_id)
            return (201, {"alert_id": alert_id, "label": int(label),
                          "actor": actor, "total_feedback": count}, {})

        return self._idempotent(headers.get("idempotency-key"), "/v2/feedback",
                                body, run)

    # -- response -----------------------------------------------------------

    def respond(self, body: bytes, claims: Optional[Dict[str, Any]],
                headers: Dict[str, str]) -> Result:
        actor = require_role(claims, "responder")
        payload = parse_json(body)
        entity = payload.get("entity")
        action = payload.get("action")
        if not isinstance(entity, str) or not entity:
            raise ApiError(422, "invalid_entity", "entity is required")
        if action not in ("auto_contain", "human_review", "no_action"):
            raise ApiError(422, "invalid_action", "action is not a known value",
                           {"got": action})

        def run() -> Result:
            entry = self.store.append_audit(
                actor, action,
                {"entity": entity, "executed": self.execute,
                 "reason": payload.get("reason")},
                subject=entity)
            self.store.publish("soar.decision",
                               {"entity": entity, "action": action,
                                "actor": actor})
            obs.SOAR_ACTIONS.inc({"action": action})
            return (202, {"entity": entity, "action": action,
                          "executed": self.execute, "audit": entry}, {})

        # Required, not optional. A containment action must never fire twice
        # because a client retried a request whose response it never saw.
        return self._idempotent(headers.get("idempotency-key"), "/v2/respond",
                                body, run, required=True)

    # -- audit --------------------------------------------------------------

    def audit(self, query: Dict[str, List[str]],
              claims: Optional[Dict[str, Any]]) -> Result:
        require_role(claims, "admin")
        page = self.store.audit_page(limit=_limit_of(query),
                                     cursor=_one(query, "cursor"))
        return (200, page, {"ETag": etag_of(page)})

    def audit_verify(self, claims: Optional[Dict[str, Any]]) -> Result:
        require_role(claims, "admin")
        result = self.store.verify_chain()
        obs.AUDIT_CHAIN_VALID.set(1.0 if result["valid"] else 0.0)
        # A broken chain is not a server error; the server is working correctly
        # and is reporting a true fact about its data. 200 with valid=false.
        return (200, result, {})
