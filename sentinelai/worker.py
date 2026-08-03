"""Periodic background jobs: drain the outbox, sweep drift, apply retention.

Phase 5 of docs/backend/BACKEND-PLAN.md.

The unit of work is `run_once()`, which is a plain synchronous method returning
a report. The thread in `start()` does nothing but call it on an interval. That
split is deliberate: every job is then testable without spawning a thread or
sleeping, and the tests that exercise them are ordinary assertions rather than
timing races.

One failing job must not stop the others. Each is wrapped, and its error is
reported in the result under its own key rather than raised, because a drift
sweep that fails is not a reason to stop draining security events.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from . import obs
from .bus import Bus

DEFAULT_INTERVAL = 30.0
DEFAULT_RETENTION_DAYS = 30
MIN_DRIFT_SAMPLES = 200
PSI_RETRAIN_THRESHOLD = 0.2


class Worker:
    def __init__(self, store: Any, bus: Bus, interval: float = DEFAULT_INTERVAL,
                 retention_days: int = DEFAULT_RETENTION_DAYS,
                 logger: Any = None):
        self.store = store
        self.bus = bus
        self.interval = float(interval)
        self.retention_days = int(retention_days)
        self.logger = logger or obs.get_logger("sentinelai.worker")
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.ticks = 0

    # -- lifecycle --------------------------------------------------------
    def start(self) -> "Worker":
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="sentinelai-worker",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 5.0) -> None:
        """Signal and join.

        The loop waits on an Event rather than sleeping, so shutdown is prompt
        instead of taking up to a full interval. A daemon thread blocked in
        time.sleep is the standard reason a service takes 30 seconds to die.
        """
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001
                obs.log_event(self.logger, 40, "worker tick failed",
                              error=type(exc).__name__ + ": " + str(exc))
            self._stop.wait(self.interval)

    # -- the work ---------------------------------------------------------
    def run_once(self) -> Dict[str, Any]:
        self.ticks += 1
        report: Dict[str, Any] = {"tick": self.ticks}
        for job_name, job in (
            ("outbox", self.job_drain),
            ("gauges", self.job_refresh_gauges),
            ("drift", self.job_drift),
            ("retention", self.job_retention),
        ):
            started = time.time()
            try:
                report[job_name] = job()
            except Exception as exc:  # noqa: BLE001
                report[job_name] = {"error": type(exc).__name__ + ": " + str(exc)}
                obs.log_event(self.logger, 40, "worker job failed",
                              job=job_name, error=str(exc))
            report[job_name + "_seconds"] = round(time.time() - started, 4)
        return report

    def job_drain(self) -> Dict[str, Any]:
        results = self.bus.drain_all()
        return {
            "consumers": [r.to_dict() for r in results],
            "handled": sum(r.handled for r in results),
            "failed": sum(r.failed for r in results),
        }

    def job_refresh_gauges(self) -> Dict[str, Any]:
        counts = self.store.open_case_counts() or {}
        for status, count in counts.items():
            obs.CASES_OPEN.set(float(count), {"status": str(status)})

        chain = self.store.verify_chain()
        obs.AUDIT_CHAIN_VALID.set(1.0 if chain.get("valid") else 0.0)
        if not chain.get("valid"):
            # This is the alarm the chain exists for. It goes to the log at
            # error level as well as the gauge, because a gauge nobody is
            # scraping yet is not a notification.
            obs.log_event(self.logger, 40, "audit chain does not verify",
                          broken_at=chain.get("broken_at"),
                          reason=chain.get("reason"))

        lags = {}
        for consumer in self.bus.consumers():
            lag = int(self.store.outbox_lag(consumer.name))
            lags[consumer.name] = lag
            obs.OUTBOX_LAG.set(float(lag), {"consumer": consumer.name})
        return {"cases": counts, "chain_valid": bool(chain.get("valid")), "lag": lags}

    def job_drift(self) -> Dict[str, Any]:
        """PSI between the older and newer halves of the recorded alert scores.

        This watches the score distribution, not the input features, because
        scores are what the store actually retains. It is a genuinely weaker
        signal than per-feature PSI over the live feature matrix: a change that
        moves two features in compensating directions can leave the score
        distribution untouched. Stated here rather than in a commit message,
        since anyone reading a drift number needs to know what it can miss.
        """
        import numpy as np

        from .active_learning import psi

        conn = self.store.connect()
        rows = conn.execute(
            "SELECT probability FROM alerts ORDER BY id"
        ).fetchall()
        values = [float(r[0]) for r in rows if r[0] is not None]
        if len(values) < MIN_DRIFT_SAMPLES:
            # Reporting 0.0 here would look like 'no drift' when it means 'no
            # evidence'. Those are different claims and only one of them is true.
            return {"status": "insufficient_data", "n": len(values),
                    "need": MIN_DRIFT_SAMPLES}

        half = len(values) // 2
        reference = np.asarray(values[:half], dtype=float)
        current = np.asarray(values[half:], dtype=float)
        score = float(psi(reference, current))
        obs.DRIFT_PSI_MAX.set(score)

        recommended = score >= PSI_RETRAIN_THRESHOLD
        if recommended:
            self.store.publish("retrain_recommended", {
                "psi": score, "threshold": PSI_RETRAIN_THRESHOLD,
                "n_reference": int(reference.size), "n_current": int(current.size),
                "basis": "alert_probability",
            })
        return {"status": "ok", "psi": score, "n": len(values),
                "retrain_recommended": recommended}

    def job_retention(self) -> Dict[str, Any]:
        """Delete expired idempotency keys and fully-consumed outbox rows.

        Outbox rows are only removed below the MINIMUM committed offset across
        registered consumers. Deleting by age alone would silently discard
        events a lagging consumer had not read yet, which is the one thing an
        outbox must never do. With no consumers registered, nothing is deleted.
        """
        conn = self.store.connect()
        now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

        # Single statements against a connection opened with isolation_level=None,
        # so each is its own transaction.
        expired = conn.execute(
            "DELETE FROM idempotency_keys WHERE expires_at IS NOT NULL"
            " AND expires_at < ?", (now,)).rowcount

        names = [c.name for c in self.bus.consumers()]
        purged = 0
        safe_below = 0
        if names:
            placeholders = ",".join("?" for _ in names)
            row = conn.execute(
                "SELECT MIN(last_id) FROM consumer_offsets WHERE consumer IN ("
                + placeholders + ")", names).fetchone()
            committed = row[0] if row is not None else None
            # A registered consumer with no offset row has committed nothing.
            if committed is not None and len(names) == int(conn.execute(
                    "SELECT COUNT(*) FROM consumer_offsets WHERE consumer IN ("
                    + placeholders + ")", names).fetchone()[0]):
                safe_below = int(committed)
                cutoff = time.strftime(
                    "%Y-%m-%dT%H:%M:%S",
                    time.gmtime(time.time() - self.retention_days * 86400))
                purged = conn.execute(
                    "DELETE FROM outbox WHERE id <= ? AND created_at < ?",
                    (safe_below, cutoff)).rowcount

        return {"idempotency_keys_expired": int(expired),
                "outbox_purged": int(purged),
                "safe_below": safe_below}


def default_bus(store: Any) -> Bus:
    """A Bus with the built-in consumers registered."""
    from .bus import audit_consumer, metrics_consumer

    bus = Bus(store)
    bus.register(metrics_consumer(store))
    bus.register(audit_consumer(store))
    return bus
