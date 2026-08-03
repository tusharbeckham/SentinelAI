"""Outbox delivery and the background worker.

These tests are about delivery semantics rather than happy-path plumbing: what
happens on a crash, on a poison event, and when retention meets a lagging
consumer. Those are the cases that lose security events in production.
"""

import os
import tempfile
import unittest

from sentinelai.bus import Bus, Consumer, Event, audit_consumer, metrics_consumer
from sentinelai.store import Store
from sentinelai.worker import Worker, default_bus


class BusTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "test.db"))
        self.store.migrate()
        self.bus = Bus(self.store)

    def tearDown(self):
        self.store.close_all()
        self.tmp.cleanup()

    def publish(self, n, topic="soar.decided"):
        for i in range(n):
            self.store.publish(topic, {"action": "auto_contain",
                                       "entity": "h%03d" % i, "actor": "sys"})


class TestDelivery(BusTestCase):
    def test_every_published_event_reaches_the_handler(self):
        seen = []
        self.bus.register(Consumer("probe", lambda e: seen.append(e.id)))
        self.publish(5)
        result = self.bus.drain("probe")
        self.assertEqual(result.handled, 5)
        self.assertEqual(len(seen), 5)

    def test_a_second_drain_redelivers_nothing(self):
        seen = []
        self.bus.register(Consumer("probe", lambda e: seen.append(e.id)))
        self.publish(5)
        self.bus.drain("probe")
        self.assertEqual(self.bus.drain("probe").handled, 0)
        self.assertEqual(len(seen), 5)
        self.assertEqual(self.bus.lag("probe"), 0)

    def test_two_consumers_keep_independent_offsets(self):
        a, b = [], []
        self.bus.register(Consumer("a", lambda e: a.append(e.id)))
        self.bus.register(Consumer("b", lambda e: b.append(e.id)))
        self.publish(3)
        self.bus.drain("a")
        self.assertEqual((len(a), len(b)), (3, 0))
        self.bus.drain("b")
        self.assertEqual(len(b), 3)

    def test_a_batch_larger_than_the_page_size_still_drains_fully(self):
        seen = []
        self.bus.register(Consumer("probe", lambda e: seen.append(e.id)))
        self.publish(25)
        result = self.bus.drain("probe", batch=10)
        self.assertEqual(result.handled, 25)

    def test_an_unregistered_consumer_is_an_error_not_a_silent_no_op(self):
        with self.assertRaises(KeyError):
            self.bus.drain("nobody")

    def test_registering_the_same_name_twice_is_refused(self):
        self.bus.register(Consumer("probe", lambda e: None))
        with self.assertRaises(ValueError):
            self.bus.register(Consumer("probe", lambda e: None))


class TestFailureSemantics(BusTestCase):
    def test_a_poison_event_blocks_rather_than_being_skipped(self):
        """Skipping past a failing event silently drops a security event.

        Blocking is the safer failure: the lag gauge makes a stuck consumer
        visible, whereas a dropped event is invisible forever.
        """
        calls = []

        def explode(event):
            calls.append(event.id)
            raise RuntimeError("handler is broken")

        self.bus.register(Consumer("poison", explode))
        self.publish(3)
        first = self.bus.drain("poison")
        second = self.bus.drain("poison")
        self.assertEqual(first.failed, 1)
        self.assertEqual(first.stuck_at, second.stuck_at)
        self.assertEqual(calls, [first.stuck_at, first.stuck_at])

    def test_events_before_the_poison_one_are_not_replayed(self):
        handled = []

        def fail_on_third(event):
            if len(handled) == 2:
                raise RuntimeError("boom")
            handled.append(event.id)

        self.bus.register(Consumer("partial", fail_on_third))
        self.publish(5)
        self.bus.drain("partial")
        self.assertEqual(len(handled), 2)
        self.bus.drain("partial")
        # The two successes were committed, so they are not handled again.
        self.assertEqual(len(handled), 2)

    def test_a_malformed_payload_does_not_wedge_the_consumer(self):
        conn = self.store.connect()
        conn.execute("INSERT INTO outbox(topic, payload, created_at)"
                     " VALUES (?,?,?)", ("junk", "{not json", "2026-08-03T00:00:00"))
        seen = []
        self.bus.register(Consumer("probe", lambda e: seen.append(e.payload)))
        result = self.bus.drain("probe")
        self.assertEqual(result.handled, 1)
        self.assertTrue(seen[0].get("malformed"))


class TestTopicFilter(BusTestCase):
    def test_a_filtered_consumer_only_sees_its_topics(self):
        seen = []
        self.bus.register(Consumer("only_soar", lambda e: seen.append(e.topic),
                                   topics=("soar.decided",)))
        self.store.publish("alert.recorded", {"suspected": "dos"})
        self.publish(2)
        self.bus.drain("only_soar")
        self.assertEqual(seen, ["soar.decided", "soar.decided"])

    def test_the_offset_advances_past_unwanted_topics(self):
        """Otherwise the consumer re-reads skipped rows on every drain forever."""
        seen = []
        self.bus.register(Consumer("picky", lambda e: seen.append(e.id),
                                   topics=("never.published",)))
        self.publish(4)
        self.bus.drain("picky")
        self.assertEqual(self.bus.lag("picky"), 0)


class TestBuiltinConsumers(BusTestCase):
    def test_the_audit_consumer_mirrors_response_decisions(self):
        self.bus.register(audit_consumer(self.store))
        self.publish(3)
        self.bus.drain("audit")
        page = self.store.audit_page(limit=100)
        self.assertEqual(len(page["items"]), 3)
        self.assertTrue(self.store.verify_chain()["valid"])

    def test_the_metrics_consumer_tolerates_unknown_topics(self):
        self.bus.register(metrics_consumer(self.store))
        self.store.publish("something.new", {"x": 1})
        self.assertEqual(self.bus.drain("metrics").failed, 0)


class TestWorker(BusTestCase):
    def setUp(self):
        super().setUp()
        self.worker = Worker(self.store, default_bus(self.store), interval=0.05)

    def test_one_tick_runs_every_job(self):
        report = self.worker.run_once()
        for job in ("outbox", "gauges", "drift", "retention"):
            self.assertIn(job, report)
            self.assertNotIn("error", report[job])

    def test_a_failing_job_does_not_stop_the_others(self):
        def broken():
            raise RuntimeError("drift is broken")

        self.worker.job_drift = broken
        report = self.worker.run_once()
        self.assertIn("error", report["drift"])
        self.assertNotIn("error", report["outbox"])

    def test_drift_reports_insufficient_data_rather_than_zero(self):
        """'No drift' and 'no evidence' are different claims.

        Returning 0.0 with two data points would read as a healthy model on
        every dashboard that plots it.
        """
        result = self.worker.job_drift()
        self.assertEqual(result["status"], "insufficient_data")

    def test_retention_will_not_purge_past_a_lagging_consumer(self):
        self.publish(5)
        # Nothing has been drained, so no offset is committed and nothing is
        # eligible for deletion no matter how old it is.
        self.worker.retention_days = -1
        result = self.worker.job_retention()
        self.assertEqual(result["outbox_purged"], 0)
        conn = self.store.connect()
        remaining = conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
        self.assertEqual(remaining, 5)

    def test_the_thread_starts_and_stops_promptly(self):
        self.worker.start()
        self.worker.stop(timeout=2.0)
        self.assertGreaterEqual(self.worker.ticks, 1)


class TestEvent(unittest.TestCase):
    def test_a_non_dict_payload_is_wrapped_not_dropped(self):
        event = Event.from_row({"id": 1, "topic": "t", "payload": "42",
                                "created_at": "now"})
        self.assertEqual(event.payload, {"value": 42})


if __name__ == "__main__":
    unittest.main()
