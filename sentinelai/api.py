"""Hardened scoring/alerting API on the Python standard library.

In the target deployment this is FastAPI behind mTLS with an OIDC provider; here
it is stdlib `http.server` so the repo runs with zero external dependencies while
keeping the *security controls* real and testable:

  * HS256 bearer tokens (compact JWT) signed with an env-provided secret; the
    signature is verified with `hmac.compare_digest` (constant time), plus `exp`
    and `nbf` checks and a required audience.
  * RBAC: viewer < analyst < responder < admin. Route-level scope enforcement.
  * Per-token token-bucket rate limiting (429 + Retry-After).
  * Request size cap, JSON schema validation, strict content type.
  * Structured JSON access log with automatic redaction of secrets/tokens.
  * Security headers, no server banner leakage, generic error bodies (no stack
    traces to clients).
  * Secrets are read from the environment only, never written to disk or logs.

Routes
  GET  /healthz                     public
  GET  /v1/alerts?limit=            viewer+
  POST /v1/score                    analyst+   (feature dict -> probability)
  POST /v1/feedback                 analyst+   (analyst verdict for retraining)
  POST /v1/respond                  responder+ (dry-run SOAR decision)
  GET  /v1/audit                    admin      (hash-chained decision log)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Sequence
from urllib.parse import parse_qs, urlparse

from . import obs
from .apiv2 import ApiError, V2Router

ROLE_ORDER = {"viewer": 0, "analyst": 1, "responder": 2, "admin": 3}
MAX_BODY = 64 * 1024
# Paths served by the v2 router rather than the frozen v1 table.
V2_PUBLIC = ("/readyz", "/metrics", "/openapi.json")


def _err(error: str, message: str, details: Dict[str, Any] | None = None) -> dict:
    """The v2 error shape. v1 keeps its own {"error": "..."} bodies untouched."""
    return {"error": error, "message": message,
            "request_id": obs.request_id_var.get(), "details": details or {}}
REDACT = re.compile(
    r"(?i)(\"?(?:authorization|token|secret|password|api[-_]?key)\"?\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|[^\s\",}]+)"
)


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(txt: str) -> bytes:
    return base64.urlsafe_b64decode(txt + "=" * (-len(txt) % 4))


def issue_token(secret: str, sub: str, role: str, ttl: int = 3600, aud: str = "sentinelai") -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {"sub": sub, "role": role, "aud": aud, "iat": now, "nbf": now, "exp": now + ttl}
    signing_input = f"{_b64e(json.dumps(header, separators=(',', ':')).encode())}.{_b64e(json.dumps(payload, separators=(',', ':')).encode())}"
    sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64e(sig)}"


class AuthError(Exception):
    pass


def verify_token(secret: str, token: str, aud: str = "sentinelai") -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("malformed token")
    signing_input = f"{parts[0]}.{parts[1]}"
    expected = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    # Everything below decodes attacker-controlled bytes. binascii.Error and
    # JSONDecodeError are both ValueError, and an unhandled one here escapes
    # the handler's except AuthError and kills the thread: the client sees a
    # dropped connection instead of a 401, which is both a worse error message
    # and a free denial of service for anyone who can send a malformed header.
    try:
        signature = _b64d(parts[2])
        header = json.loads(_b64d(parts[0]))
        claims = json.loads(_b64d(parts[1]))
    except (ValueError, UnicodeDecodeError) as exc:
        raise AuthError("malformed token") from exc
    if not hmac.compare_digest(expected, signature):
        raise AuthError("bad signature")
    if header.get("alg") != "HS256":  # block alg confusion / 'none'
        raise AuthError("unsupported alg")
    now = int(time.time())
    if claims.get("aud") != aud:
        raise AuthError("bad audience")
    if now < int(claims.get("nbf", 0)) - 5:
        raise AuthError("token not yet valid")
    if now >= int(claims.get("exp", 0)):
        raise AuthError("token expired")
    if claims.get("role") not in ROLE_ORDER:
        raise AuthError("unknown role")
    return claims


class TokenBucket:
    def __init__(self, capacity: int = 30, refill_per_sec: float = 0.5):
        self.capacity = capacity
        self.refill = refill_per_sec
        self.state: Dict[str, tuple[float, float]] = {}

    def allow(self, key: str, now: float | None = None) -> tuple[bool, float]:
        now = time.time() if now is None else now
        tokens, last = self.state.get(key, (float(self.capacity), now))
        tokens = min(self.capacity, tokens + (now - last) * self.refill)
        if tokens < 1.0:
            self.state[key] = (tokens, now)
            return False, (1.0 - tokens) / self.refill
        self.state[key] = (tokens - 1.0, now)
        return True, 0.0


def redact(text: str) -> str:
    """Strip credential values from anything about to be logged.

    Handles quoted values containing spaces (e.g. \"Bearer eyJ...\") as well as
    bare tokens, and keeps the key name so logs stay diagnosable.
    """
    return REDACT.sub(lambda m: m.group(1) + '"[REDACTED]"', text)


class ScoringService:
    """Adapter the HTTP layer talks to. Keeps transport and ML concerns apart."""

    def __init__(
        self,
        feature_names: Sequence[str],
        score_fn: Callable[[Dict[str, float]], float],
        alerts: List[dict] | None = None,
        responder=None,
    ):
        self.feature_names = list(feature_names)
        self.score_fn = score_fn
        self.alerts = alerts or []
        self.responder = responder
        self.feedback: List[dict] = []

    def score(self, features: Dict[str, Any]) -> dict:
        missing = [f for f in self.feature_names if f not in features]
        clean: Dict[str, float] = {}
        for f in self.feature_names:
            v = features.get(f, 0.0)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError(f"feature '{f}' must be numeric")
            clean[f] = float(v)
        p = float(self.score_fn(clean))
        return {"probability": p, "missing_features_defaulted": missing}

    def add_feedback(self, item: dict) -> dict:
        if item.get("verdict") not in ("true_positive", "false_positive"):
            raise ValueError("verdict must be true_positive or false_positive")
        rec = {
            "entity": str(item.get("entity", ""))[:64],
            "window": int(item.get("window", 0)),
            "verdict": item["verdict"],
            "analyst": str(item.get("analyst", ""))[:64],
            "ts": int(time.time()),
        }
        self.feedback.append(rec)
        return {"accepted": True, "queued_verdicts": len(self.feedback)}


def make_handler(service: ScoringService, secret: str, bucket: TokenBucket,
                 logger: logging.Logger, router: Optional[V2Router] = None):
    ROUTES = {
        ("GET", "/healthz"): None,
        ("GET", "/v1/alerts"): "viewer",
        ("POST", "/v1/score"): "analyst",
        ("POST", "/v1/feedback"): "analyst",
        ("POST", "/v1/respond"): "responder",
        ("GET", "/v1/audit"): "admin",
    }

    class Handler(BaseHTTPRequestHandler):
        server_version = "SentinelAI"
        sys_version = ""

        # ---------------------------------------------------------- plumbing
        def log_message(self, fmt: str, *args) -> None:  # structured + redacted
            logger.info(redact(json.dumps({"client": self.client_address[0], "msg": fmt % args})))

        def _send(self, code: int, body: Any, extra: Dict[str, str] | None = None,
                  content_type: str = "application/json") -> None:
            # A str body is sent verbatim; /metrics is Prometheus text, not JSON.
            raw = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)

        def _claims(self, required_role: str | None) -> dict | None:
            if required_role is None:
                return {}
            header = self.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                self._send(401, {"error": "missing bearer token"})
                return None
            try:
                claims = verify_token(secret, header[7:])
            except AuthError:
                self._send(401, {"error": "invalid token"})
                return None
            if ROLE_ORDER[claims["role"]] < ROLE_ORDER[required_role]:
                self._send(403, {"error": "insufficient role"})
                return None
            ok, wait = bucket.allow(claims.get("sub", "anon"))
            if not ok:
                self._send(429, {"error": "rate limited"}, {"Retry-After": str(int(wait) + 1)})
                return None
            return claims

        def _body(self) -> dict | None:
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                self._send(415, {"error": "content-type must be application/json"})
                return None
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send(400, {"error": "bad content-length"})
                return None
            if length <= 0 or length > MAX_BODY:
                self._send(413, {"error": "body missing or too large"})
                return None
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._send(400, {"error": "invalid json"})
                return None

        # ---------------------------------------------------------------- v2
        def _is_v2(self, path: str) -> bool:
            return path.startswith("/v2/") or path in V2_PUBLIC

        def _v2_claims(self, path: str):
            """Returns (claims, ok). Readiness and metrics carry no token.

            Scrape targets that require a bearer token get scraped by nobody,
            and a readiness probe that needs credentials cannot be used by a
            load balancer.
            """
            if path in V2_PUBLIC:
                return None, True
            header = self.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                self._send(401, _err("unauthenticated", "authentication required"))
                return None, False
            try:
                claims = verify_token(secret, header[7:])
            except AuthError:
                # The reason goes to the log and to the auth-failure metric, not
                # to the client. Telling an attacker which part of their forged
                # token was wrong is free help.
                obs.AUTH_FAILURES.inc({"reason": "invalid_token"})
                self._send(401, _err("unauthenticated", "authentication required"))
                return None, False
            ok, wait = bucket.allow(claims.get("sub", "anon"))
            if not ok:
                obs.RATE_LIMITED.inc({"route": path})
                self._send(429, _err("rate_limited", "too many requests"),
                           {"Retry-After": str(int(wait) + 1)})
                return None, False
            return claims, True

        def _raw_body(self):
            """Raw bytes, because the idempotency body hash must see exactly what
            the client sent. Re-serialising parsed JSON would let a whitespace
            change look like a different request."""
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                self._send(415, _err("unsupported_media_type",
                                     "content-type must be application/json"))
                return None
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send(400, _err("bad_content_length", "bad content-length"))
                return None
            if length < 0 or length > MAX_BODY:
                self._send(413, _err("body_too_large", "body missing or too large"))
                return None
            return self.rfile.read(length) if length else b""

        def _v2(self, method: str) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if router is None:
                self._send(503, _err("v2_unavailable",
                                     "no store is attached to this server"))
                return
            claims, ok = self._v2_claims(path)
            if not ok:
                return
            raw = b""
            if method in ("POST", "PATCH", "PUT"):
                raw = self._raw_body()
                if raw is None:
                    return
            try:
                status, payload, extra = router.dispatch(
                    method, path, parse_qs(parsed.query), raw, claims,
                    {k: v for k, v in self.headers.items()})
            except ApiError as exc:
                self._send(exc.status, exc.body(), exc.headers)
                return
            except Exception:
                logger.exception("unhandled v2 error")
                self._send(500, _err("internal_error", "internal error"))
                return
            if isinstance(payload, str):
                self._send(status, payload, extra,
                           content_type="text/plain; version=0.0.4; charset=utf-8")
            else:
                self._send(status, payload, extra)

        def do_PATCH(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if self._is_v2(path):
                self._v2("PATCH")
                return
            self._send(404, {"error": "not found"})

        # ------------------------------------------------------------ routes
        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if self._is_v2(path):
                self._v2("GET")
                return
            key = ("GET", path)
            if key not in ROUTES:
                self._send(404, {"error": "not found"})
                return
            claims = self._claims(ROUTES[key])
            if claims is None:
                return
            if path == "/healthz":
                self._send(200, {"status": "ok", "features": len(service.feature_names)})
            elif path == "/v1/alerts":
                q = parse_qs(urlparse(self.path).query)
                limit = min(int(q.get("limit", [50])[0]), 500)
                self._send(200, {"alerts": service.alerts[:limit], "total": len(service.alerts)})
            elif path == "/v1/audit":
                records = service.responder.audit.records if service.responder else []
                valid = service.responder.audit.verify() if service.responder else True
                self._send(200, {"records": records, "chain_valid": valid})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if self._is_v2(path):
                self._v2("POST")
                return
            key = ("POST", path)
            if key not in ROUTES:
                self._send(404, {"error": "not found"})
                return
            claims = self._claims(ROUTES[key])
            if claims is None:
                return
            body = self._body()
            if body is None:
                return
            try:
                if path == "/v1/score":
                    self._send(200, service.score(body.get("features", {})))
                elif path == "/v1/feedback":
                    self._send(202, service.add_feedback(body))
                elif path == "/v1/respond":
                    if service.responder is None:
                        self._send(503, {"error": "response engine unavailable"})
                        return
                    self._send(200, service.responder.decide(body))
            except ValueError as exc:
                self._send(422, {"error": str(exc)})
            except Exception:  # no stack traces to clients
                logger.exception("unhandled error")
                self._send(500, {"error": "internal error"})

    return Handler


def serve(service: ScoringService, host: str = "127.0.0.1", port: int = 8088,
          store: Any = None, execute: bool = False,
          registry: Any = None) -> ThreadingHTTPServer:
    """Build the server. Passing a Store mounts /v2; without one, /v2 answers 503.

    v1 behaves identically either way. That is the point of the version split:
    adding persistence must not change a single response the console already
    depends on.
    """
    secret = os.environ.get("SENTINELAI_JWT_SECRET")
    if not secret:
        raise RuntimeError("SENTINELAI_JWT_SECRET must be set in the environment")
    logger = logging.getLogger("sentinelai.api")
    router = None
    if store is not None:
        store.migrate()
        router = V2Router(store, scorer=service.score, execute=execute,
                          registry=registry)
    handler = make_handler(service, secret, TokenBucket(), logger, router)
    return ThreadingHTTPServer((host, port), handler)
