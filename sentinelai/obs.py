"""Structured logging, correlation ids and Prometheus metrics.

Standard library only. Adding prometheus_client and structlog would be two more
packages for roughly 250 lines of behaviour, and the two-dependency claim in the
README is load bearing.

Two ideas do the work:

* A ContextVar holds the current request id and a logging.Filter copies it onto
  every record, so a line emitted deep inside the store carries the id of the
  request that caused it, with no parameter threaded through the call stack.
* Redaction happens in the formatter, not at call sites. A redaction helper that
  every caller must remember to invoke is one that will eventually be forgotten.

Regex patterns below deliberately avoid backslash escapes (character classes are
used instead) so the source survives transport through JSON tooling unharmed.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
import uuid
from contextvars import ContextVar
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "request_id_var", "actor_var", "new_request_id", "adopt_request_id",
    "redact", "configure_logging", "get_logger", "log_event",
    "Counter", "Gauge", "Histogram", "Registry", "REGISTRY",
    "DEFAULT_BUCKETS", "CardinalityError",
]

BACKSLASH = chr(92)
QUOTE = chr(34)
NEWLINE = chr(10)

# --------------------------------------------------------------------------
# Correlation ids
# --------------------------------------------------------------------------

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
actor_var: ContextVar[str] = ContextVar("actor", default="-")

# Strict on purpose. An inbound header is attacker controlled and lands in every
# log line for that request; a newline in it would let a client forge entries.
_ID_RE = re.compile("^[A-Za-z0-9_-]{8,64}$")


def new_request_id() -> str:
    return uuid.uuid4().hex[:24]


def adopt_request_id(header_value: Optional[str]) -> str:
    """Honour a client supplied id when it is safe, otherwise generate one."""
    if header_value and _ID_RE.match(header_value):
        return header_value
    return new_request_id()


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------

_REDACTIONS: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile("(?i)(bearer)[ ]+[A-Za-z0-9._~+/=-]{8,}"), "BEARER [REDACTED]"),
    (re.compile("(?i)(api[_-]?key|secret|password|token|authorization)([ ]*[:=][ ]*)[^ ,;]+"),
     "REDACTED_CREDENTIAL"),
    # A bare JWT anywhere in the text: three base64url segments.
    (re.compile("eyJ[A-Za-z0-9_-]{4,}[.][A-Za-z0-9_-]{4,}[.][A-Za-z0-9_-]{4,}"),
     "[REDACTED_JWT]"),
)


def redact(text: str) -> str:
    """Strip credentials from a string destined for a log."""
    if not text:
        return text
    out = text
    for pattern, replacement in _REDACTIONS:
        out = pattern.sub(replacement, out)
    return out


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

_RESERVED = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()
) | {"message", "asctime", "taskName"}


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.actor = actor_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with redaction applied centrally."""

    def __init__(self, env: str = "dev") -> None:
        super().__init__()
        self.env = env

    def format(self, record: logging.LogRecord) -> str:
        created = time.gmtime(record.created)
        millis = int((record.created - int(record.created)) * 1000)
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", created) + (".%03d+00:00" % millis)

        payload: Dict[str, object] = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
            "request_id": getattr(record, "request_id", "-"),
            "actor": getattr(record, "actor", "-"),
            "env": self.env,
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key in payload or key.startswith("_"):
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                payload[key] = value
            else:
                payload[key] = str(value)
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, separators=(",", ":"), default=str)


class TextFormatter(logging.Formatter):
    """Human readable form for local work. Redaction still applies."""

    def format(self, record: logging.LogRecord) -> str:
        base = "%s %-7s %-16s [%s] %s" % (
            time.strftime("%H:%M:%S", time.localtime(record.created)),
            record.levelname,
            record.name,
            getattr(record, "request_id", "-"),
            redact(record.getMessage()),
        )
        if record.exc_info:
            base = base + NEWLINE + redact(self.formatException(record.exc_info))
        return base


def configure_logging(level: str = "INFO", fmt: str = "json", env: str = "dev",
                      stream=None) -> logging.Logger:
    """Install the root handler. Idempotent: safe to call twice."""
    root = logging.getLogger("sentinelai")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)
    target = stream if stream is not None else sys.stderr
    handler = logging.StreamHandler(target)
    handler.setFormatter(JsonFormatter(env) if fmt == "json" else TextFormatter())
    handler.addFilter(_ContextFilter())
    root.addHandler(handler)
    root.propagate = False
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger("sentinelai." + name)


def log_event(logger: logging.Logger, level: int, msg: str, **fields: object) -> None:
    """Log with arbitrary structured fields merged into the JSON object."""
    logger.log(level, msg, extra=fields)


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

DEFAULT_BUCKETS: Tuple[float, ...] = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
)

_NAME_RE = re.compile("^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_LABEL_RE = re.compile("^[a-zA-Z_][a-zA-Z0-9_]*$")
# Five or more consecutive digits in a label value almost always means an id has
# been used as a label, which is the classic way to melt a Prometheus.
_HIGH_CARD_RE = re.compile("[0-9]{5,}")


class CardinalityError(ValueError):
    pass


def _escape(value: str) -> str:
    out = value.replace(BACKSLASH, BACKSLASH + BACKSLASH)
    out = out.replace(QUOTE, BACKSLASH + QUOTE)
    out = out.replace(NEWLINE, BACKSLASH + "n")
    return out


def _fmt(value: float) -> str:
    if value == float("inf"):
        return "+Inf"
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


class _Metric:
    kind = "untyped"

    def __init__(self, name: str, help_text: str, labelnames: Sequence[str] = ()) -> None:
        if not _NAME_RE.match(name):
            raise ValueError("invalid metric name: " + name)
        for label in labelnames:
            if not _LABEL_RE.match(label):
                raise ValueError("invalid label name: " + label)
        self.name = name
        self.help_text = help_text
        self.labelnames: Tuple[str, ...] = tuple(labelnames)
        self._lock = threading.Lock()

    def _key(self, labels: Optional[Mapping[str, str]]) -> Tuple[str, ...]:
        given = labels or {}
        if set(given) != set(self.labelnames):
            raise ValueError(self.name + " expects labels " + repr(self.labelnames)
                             + ", got " + repr(tuple(given)))
        key: List[str] = []
        for label in self.labelnames:
            value = str(given[label])
            if _HIGH_CARD_RE.search(value):
                raise CardinalityError(
                    "label " + label + "=" + repr(value) + " on " + self.name
                    + " looks like an identifier; use a route pattern, not a resolved path")
            key.append(value)
        return tuple(key)

    def _label_str(self, key: Tuple[str, ...],
                   extra: Optional[Tuple[str, str]] = None) -> str:
        parts = []
        for name, value in zip(self.labelnames, key):
            parts.append(name + "=" + QUOTE + _escape(value) + QUOTE)
        if extra is not None:
            parts.append(extra[0] + "=" + QUOTE + _escape(extra[1]) + QUOTE)
        if not parts:
            return ""
        return "{" + ",".join(parts) + "}"

    def _header(self) -> List[str]:
        return ["# HELP " + self.name + " " + self.help_text,
                "# TYPE " + self.name + " " + self.kind]

    def render(self) -> List[str]:
        raise NotImplementedError


class Counter(_Metric):
    kind = "counter"

    def __init__(self, name: str, help_text: str, labelnames: Sequence[str] = ()) -> None:
        super().__init__(name, help_text, labelnames)
        self._values: Dict[Tuple[str, ...], float] = {}

    def inc(self, labels: Optional[Mapping[str, str]] = None, amount: float = 1.0) -> None:
        if amount < 0:
            raise ValueError("counters cannot decrease")
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def value(self, labels: Optional[Mapping[str, str]] = None) -> float:
        with self._lock:
            return self._values.get(self._key(labels), 0.0)

    def render(self) -> List[str]:
        lines = self._header()
        with self._lock:
            items = sorted(self._values.items())
        for key, value in items:
            lines.append(self.name + self._label_str(key) + " " + _fmt(value))
        return lines


class Gauge(_Metric):
    kind = "gauge"

    def __init__(self, name: str, help_text: str, labelnames: Sequence[str] = ()) -> None:
        super().__init__(name, help_text, labelnames)
        self._values: Dict[Tuple[str, ...], float] = {}

    def set(self, value: float, labels: Optional[Mapping[str, str]] = None) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = float(value)

    def inc(self, labels: Optional[Mapping[str, str]] = None, amount: float = 1.0) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def value(self, labels: Optional[Mapping[str, str]] = None) -> float:
        with self._lock:
            return self._values.get(self._key(labels), 0.0)

    def render(self) -> List[str]:
        lines = self._header()
        with self._lock:
            items = sorted(self._values.items())
        for key, value in items:
            lines.append(self.name + self._label_str(key) + " " + _fmt(value))
        return lines


class Histogram(_Metric):
    kind = "histogram"

    def __init__(self, name: str, help_text: str, labelnames: Sequence[str] = (),
                 buckets: Sequence[float] = DEFAULT_BUCKETS) -> None:
        super().__init__(name, help_text, labelnames)
        bounds = sorted(float(b) for b in buckets)
        if not bounds:
            raise ValueError("histogram needs at least one bucket")
        self.bounds: Tuple[float, ...] = tuple(bounds)
        self._counts: Dict[Tuple[str, ...], List[int]] = {}
        self._sums: Dict[Tuple[str, ...], float] = {}
        self._totals: Dict[Tuple[str, ...], int] = {}

    def observe(self, value: float, labels: Optional[Mapping[str, str]] = None) -> None:
        key = self._key(labels)
        with self._lock:
            counts = self._counts.get(key)
            if counts is None:
                counts = [0] * len(self.bounds)
                self._counts[key] = counts
                self._sums[key] = 0.0
                self._totals[key] = 0
            for i, bound in enumerate(self.bounds):
                if value <= bound:
                    counts[i] += 1
            self._sums[key] = self._sums[key] + float(value)
            self._totals[key] = self._totals[key] + 1

    def count(self, labels: Optional[Mapping[str, str]] = None) -> int:
        with self._lock:
            return self._totals.get(self._key(labels), 0)

    def render(self) -> List[str]:
        lines = self._header()
        with self._lock:
            keys = sorted(self._counts)
            snap = {}
            for k in keys:
                snap[k] = (list(self._counts[k]), self._sums[k], self._totals[k])
        for key in keys:
            counts, total_sum, total = snap[key]
            for bound, cumulative in zip(self.bounds, counts):
                lines.append(self.name + "_bucket"
                             + self._label_str(key, ("le", _fmt(bound)))
                             + " " + str(cumulative))
            lines.append(self.name + "_bucket"
                         + self._label_str(key, ("le", "+Inf")) + " " + str(total))
            lines.append(self.name + "_sum" + self._label_str(key) + " " + _fmt(total_sum))
            lines.append(self.name + "_count" + self._label_str(key) + " " + str(total))
        return lines


class Registry:
    def __init__(self) -> None:
        self._metrics: Dict[str, _Metric] = {}
        self._lock = threading.Lock()

    def register(self, metric: _Metric) -> _Metric:
        with self._lock:
            existing = self._metrics.get(metric.name)
            if existing is not None:
                # Re-registering happens when a module is imported twice under
                # test. Returning the original keeps accumulated values rather
                # than silently resetting them.
                return existing
            self._metrics[metric.name] = metric
            return metric

    def counter(self, name: str, help_text: str, labelnames: Sequence[str] = ()) -> Counter:
        metric = self.register(Counter(name, help_text, labelnames))
        return metric  # type: ignore[return-value]

    def gauge(self, name: str, help_text: str, labelnames: Sequence[str] = ()) -> Gauge:
        metric = self.register(Gauge(name, help_text, labelnames))
        return metric  # type: ignore[return-value]

    def histogram(self, name: str, help_text: str, labelnames: Sequence[str] = (),
                  buckets: Sequence[float] = DEFAULT_BUCKETS) -> Histogram:
        metric = self.register(Histogram(name, help_text, labelnames, buckets))
        return metric  # type: ignore[return-value]

    def names(self) -> List[str]:
        with self._lock:
            return sorted(self._metrics)

    def render(self) -> str:
        with self._lock:
            metrics = [self._metrics[n] for n in sorted(self._metrics)]
        lines: List[str] = []
        for metric in metrics:
            lines.extend(metric.render())
        return NEWLINE.join(lines) + NEWLINE


REGISTRY = Registry()

# Registered once, here, so the full set is greppable in one place rather than
# scattered across handlers.
HTTP_REQUESTS = REGISTRY.counter(
    "sentinelai_http_requests_total", "HTTP requests by route pattern.",
    ("route", "method", "status"))
HTTP_LATENCY = REGISTRY.histogram(
    "sentinelai_http_request_duration_seconds", "HTTP request latency.",
    ("route", "method"))
AUTH_FAILURES = REGISTRY.counter(
    "sentinelai_auth_failures_total", "Rejected credentials by reason.", ("reason",))
RATE_LIMITED = REGISTRY.counter(
    "sentinelai_rate_limited_total", "Requests rejected by the token bucket.", ("route",))
SCORE_LATENCY = REGISTRY.histogram(
    "sentinelai_score_duration_seconds", "Model scoring latency.", (),
    (0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0))
ALERTS_TOTAL = REGISTRY.counter(
    "sentinelai_alerts_total", "Alerts recorded by suspected family.", ("family",))
CASES_OPEN = REGISTRY.gauge(
    "sentinelai_cases_open", "Open cases by status.", ("status",))
SOAR_ACTIONS = REGISTRY.counter(
    "sentinelai_soar_actions_total", "SOAR decisions by action.", ("action",))
AUDIT_CHAIN_VALID = REGISTRY.gauge(
    "sentinelai_audit_chain_valid", "1 if the audit hash chain verifies, else 0.")
MODEL_INFO = REGISTRY.gauge(
    "sentinelai_model_info", "Loaded model version and stage.", ("version", "stage"))
DRIFT_PSI_MAX = REGISTRY.gauge(
    "sentinelai_drift_psi_max", "Largest per-feature PSI in the last drift sweep.")
OUTBOX_LAG = REGISTRY.gauge(
    "sentinelai_outbox_lag", "Unconsumed outbox events per consumer.", ("consumer",))
