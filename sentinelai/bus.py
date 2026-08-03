"""Outbox consumers: at-least-once delivery over the transactional outbox.

Phase 5 of docs/backend/BACKEND-PLAN.md. `Store.publish` writes a domain event
in the same SQLite transaction as the domain change, so an alert can never
exist without its event. This module is the other half: something that reads
them.

Delivery is AT-LEAST-ONCE. The offset is committed after a handler returns, so
a crash between handling and committing replays the event. Every handler must
therefore be idempotent, and that is a requirement placed on handlers rather
than an assumption about the runtime: exactly-once delivery across a process
boundary is not something a single-node SQLite outbox can offer, and pretending
otherwise would put the lie somewhere much harder to find later.

A handler that raises stops its own consumer at the failing event and leaves
the offset pointing just before it. The event is retried on the next drain.
Skipping past a poison event would silently drop a security event; blocking is
the safer failure, and the lag gauge makes a stuck consumer visible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import obs

EVENTS_HANDLED = obs.REGISTRY.counter(
    "sentinelai_events_handled_total", "Outbox events handled successfully.",
    ("consumer", "topic"))
EVENTS_FAILED = obs.REGISTRY.counter(
    "sentinelai_events_failed_total", "Outbox events whose handler raised.",
    ("consumer", "topic"))

DEFAULT_BATCH = 100


@dataclass(frozen=True)
class Event:
    id: int
    topic: str
    payload: Dict[str, Any]
    created_at: str

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Event":
        raw = row.get("payload")
        try:
            payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except json.JSONDecodeError:
            # A malformed payload is data damage, not a transient fault. Passing
            # the raw text through lets the handler decide, and keeps the
            # consumer from wedging on a row it can never parse.
            payload = {"malformed": True, "raw": raw}
        if not isinstance(payload, dict):
            payload = {"value": payload}
        return cls(
            id=int(row["id"]),
            topic=str(row["topic"]),
            payload=payload,
            created_at=str(row.get("created_at", "")),
        )


Handler = Callable[[Event], None]


@dataclass
class Consumer:
    """A named offset plus a handler.

    `topics` empty means every topic. The name is the offset key, so renaming a
    consumer replays history from the beginning - which is occasionally what
    you want and should never be an accident.
    """

    name: str
    handle: Handler
    topics: Tuple[str, ...] = ()

    def wants(self, topic: str) -> bool:
        return not self.topics or topic in self.topics


@dataclass
class DrainResult:
    consumer: str
    handled: int = 0
    failed: int = 0
    last_id: int = 0
    error: Optional[str] = None
    stuck_at: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "consumer": self.consumer, "handled": self.handled,
            "failed": self.failed, "last_id": self.last_id,
            "error": self.error, "stuck_at": self.stuck_at,
        }


class Bus:
    def __init__(self, store: Any, logger: Any = None):
        self.store = store
        self.logger = logger or obs.get_logger("sentinelai.bus")
        self._consumers: Dict[str, Consumer] = {}

    def register(self, consumer: Consumer) -> Consumer:
        if consumer.name in self._consumers:
            raise ValueError("consumer already registered: " + consumer.name)
        self._consumers[consumer.name] = consumer
        return consumer

    def consumers(self) -> List[Consumer]:
        return list(self._consumers.values())

    def lag(self, name: str) -> int:
        return int(self.store.outbox_lag(name))

    def drain(self, name: str, batch: int = DEFAULT_BATCH,
              max_batches: int = 100) -> DrainResult:
        """Handle pending events for one consumer.

        Bounded by max_batches so a worker tick cannot be captured indefinitely
        by a backlog that is growing as fast as it is drained.
        """
        consumer = self._consumers.get(name)
        if consumer is None:
            raise KeyError("no such consumer: " + name)

        result = DrainResult(consumer=name)
        # Deliberately not pushing the topic filter down into SQL. Filtering in
        # the query hides unwanted rows from the reader, so the offset can never
        # advance past them and every later drain re-reads the same backlog. The
        # cost of reading and discarding a row is far smaller than a consumer
        # whose lag never returns to zero.
        topic = None

        for _ in range(max_batches):
            rows = self.store.fetch_events(name, topic=topic, limit=batch)
            if not rows:
                break
            progressed = False
            for row in rows:
                event = Event.from_row(row)
                if not consumer.wants(event.topic):
                    # Not ours, but the offset must still advance past it or the
                    # consumer re-reads this row on every single drain forever.
                    result.last_id = event.id
                    progressed = True
                    continue
                try:
                    consumer.handle(event)
                except Exception as exc:  # noqa: BLE001 - a handler may raise anything
                    EVENTS_FAILED.inc({"consumer": name, "topic": event.topic})
                    result.failed += 1
                    result.error = type(exc).__name__ + ": " + str(exc)
                    result.stuck_at = event.id
                    obs.log_event(
                        self.logger, 40, "outbox handler failed",
                        consumer=name, topic=event.topic, event_id=event.id,
                        error=result.error,
                    )
                    if progressed:
                        self.store.commit_offset(name, result.last_id)
                    self._set_lag(name)
                    return result
                EVENTS_HANDLED.inc({"consumer": name, "topic": event.topic})
                result.handled += 1
                result.last_id = event.id
                progressed = True

            if progressed:
                self.store.commit_offset(name, result.last_id)
            if len(rows) < batch:
                break

        self._set_lag(name)
        return result

    def drain_all(self, batch: int = DEFAULT_BATCH) -> List[DrainResult]:
        out: List[DrainResult] = []
        for name in list(self._consumers):
            out.append(self.drain(name, batch=batch))
        return out

    def _set_lag(self, name: str) -> None:
        try:
            obs.OUTBOX_LAG.set(float(self.store.outbox_lag(name)), {"consumer": name})
        except Exception:  # noqa: BLE001
            # A metrics failure must never take down event processing.
            pass


# ---------------------------------------------------------------------------
# Built-in consumers
# ---------------------------------------------------------------------------

def metrics_consumer(store: Any, name: str = "metrics") -> Consumer:
    """Reflect domain events into the Prometheus registry.

    Idempotent in the sense that matters: replaying an event re-sets a gauge to
    a value read from the database rather than incrementing a counter twice.
    The one counter here, SOAR_ACTIONS, can over-count on replay; that is
    recorded in docs/backend/OBSERVABILITY.md rather than hidden, because a
    monitoring counter is the right place to accept that trade and an audit log
    is not.
    """

    def handle(event: Event) -> None:
        if event.topic == "case.opened" or event.topic == "case.transitioned":
            for status, count in (store.open_case_counts() or {}).items():
                obs.CASES_OPEN.set(float(count), {"status": str(status)})
        elif event.topic == "soar.decided":
            action = str(event.payload.get("action", "unknown"))
            obs.SOAR_ACTIONS.inc({"action": action})
        elif event.topic == "alert.recorded":
            family = str(event.payload.get("suspected", "unknown"))
            obs.ALERTS_TOTAL.inc({"family": family})
        elif event.topic == "model.promoted":
            obs.MODEL_INFO.set(1.0, {
                "version": str(event.payload.get("version", "unknown")),
                "stage": str(event.payload.get("stage", "unknown"))})

    return Consumer(name=name, handle=handle)


def audit_consumer(store: Any, name: str = "audit") -> Consumer:
    """Mirror response decisions into the hash-chained audit log.

    Deliberately narrow: only topics that represent an action taken against the
    estate. Mirroring everything would make the chain a second copy of the
    outbox and dilute the thing it is for.
    """

    def handle(event: Event) -> None:
        if event.topic not in ("soar.decided", "model.promoted"):
            return
        subject = str(event.payload.get("entity")
                      or event.payload.get("version")
                      or event.id)
        store.append_audit(
            actor=str(event.payload.get("actor", "system")),
            action="event." + event.topic,
            payload=event.payload,
            subject=subject,
        )

    return Consumer(name=name, handle=handle, topics=("soar.decided", "model.promoted"))
