"""Tests for the SQLite persistence layer.

The interesting cases here are the ones that only appear under concurrency or
across a restart, which is exactly where the previous in-memory implementation
was silently wrong.
"""

import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from sentinelai.store import (
    Store, StoreError, ConflictError, NotFound, InvalidTransition,
    IdempotencyConflict, IdempotencyInFlight, MIGRATIONS,
    encode_cursor, decode_cursor,
)


def make_alert(i, prob=0.9, entity="h002", suspected="dos"):
    return {
        "id": "AL-%06d" % i,
        "entity": entity,
        "window_start": 1767491700 + i,
        "score": 4.3,
        "probability": prob,
        "threshold": 0.6303419959358633,
        "model_version": "v20260802-abc123",
        "suspected": suspected,
        "truth": "brute_force",
    }


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        # ignore_cleanup_errors is a backstop, not the fix. On Windows an open
        # sqlite handle blocks deletion of the file, so a leaked connection
        # surfaces as PermissionError [WinError 32] in tearDown instead of
        # leaking silently as it does on POSIX. The real fix is close_all().
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = Path(self.tmp.name) / "test.db"
        self.store = Store(self.path)
        self.store.migrate()

    def tearDown(self):
        # close() would release only the main thread's connection and leave
        # every worker thread's handle open.
        self.store.close_all()
        self.tmp.cleanup()


class TestMigrations(StoreTestCase):
    def test_migrate_is_idempotent(self):
        self.assertEqual(self.store.migrate(), 0)
        self.assertEqual(self.store.migrate(), 0)
        self.assertEqual(self.store.schema_version(), MIGRATIONS[-1][0])

    def test_wal_mode_persists_on_the_file(self):
        mode = self.store.connect().execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")
        fresh = Store(self.path)
        self.assertEqual(
            fresh.connect().execute("PRAGMA journal_mode").fetchone()[0].lower(),
            "wal")
        fresh.close_all()

    def test_foreign_keys_are_on(self):
        self.assertEqual(
            self.store.connect().execute("PRAGMA foreign_keys").fetchone()[0], 1)


class TestAlerts(StoreTestCase):
    def test_insert_is_idempotent_on_id(self):
        self.store.insert_alert(make_alert(1))
        self.store.insert_alert(make_alert(1))
        self.assertEqual(self.store.count_alerts(), 1)

    def test_keyset_pagination_covers_every_row_exactly_once(self):
        for i in range(25):
            self.store.insert_alert(make_alert(i, prob=0.5 + i / 100.0))
        seen, cursor = [], None
        while True:
            page = self.store.list_alerts(limit=7, cursor=cursor)
            seen.extend(r["id"] for r in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(len(seen), 25)
        self.assertEqual(len(set(seen)), 25, "pagination duplicated a row")

    def test_pagination_is_stable_under_concurrent_insert(self):
        for i in range(10):
            self.store.insert_alert(make_alert(i, prob=0.10 + i / 100.0))
        first = self.store.list_alerts(limit=5)
        # A new highest-probability alert arrives between pages. With offset
        # pagination this would shift every row and hide one.
        self.store.insert_alert(make_alert(999, prob=0.99))
        second = self.store.list_alerts(limit=5, cursor=first["next_cursor"])
        ids = [r["id"] for r in first["items"]] + [r["id"] for r in second["items"]]
        self.assertEqual(len(set(ids)), len(ids))
        self.assertNotIn("AL-000999", ids)

    def test_filters(self):
        self.store.insert_alert(make_alert(1, entity="h001", suspected="dos"))
        self.store.insert_alert(make_alert(2, entity="h002", suspected="exfil"))
        self.assertEqual(len(self.store.list_alerts(entity="h001")["items"]), 1)
        self.assertEqual(len(self.store.list_alerts(suspected="exfil")["items"]), 1)
        self.assertEqual(len(self.store.list_alerts(min_probability=0.95)["items"]), 0)

    def test_malformed_cursor_is_rejected(self):
        with self.assertRaises(StoreError):
            self.store.list_alerts(cursor="not-a-cursor!!")


class TestCases(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store.insert_alert(make_alert(1))
        self.case = self.store.open_case("AL-000001", actor="ana")

    def test_open_case_requires_a_real_alert(self):
        with self.assertRaises(NotFound):
            self.store.open_case("AL-nope", actor="ana")

    def test_legal_transition_bumps_version_and_logs_an_event(self):
        moved = self.store.transition_case(self.case["id"], "triaging", "ana",
                                           expected_version=1)
        self.assertEqual(moved["status"], "triaging")
        self.assertEqual(moved["version"], 2)
        events = self.store.get_case(self.case["id"], with_events=True)["events"]
        self.assertEqual([e["kind"] for e in events], ["opened", "transition"])

    def test_illegal_transition_is_refused(self):
        with self.assertRaises(InvalidTransition):
            self.store.transition_case(self.case["id"], "resolved", "ana",
                                       expected_version=1)

    def test_closed_is_terminal(self):
        cid = self.case["id"]
        self.store.transition_case(cid, "closed", "ana", expected_version=1)
        with self.assertRaises(InvalidTransition):
            self.store.transition_case(cid, "triaging", "ana", expected_version=2)

    def test_stale_version_loses(self):
        cid = self.case["id"]
        self.store.transition_case(cid, "triaging", "ana", expected_version=1)
        with self.assertRaises(ConflictError):
            self.store.transition_case(cid, "escalated", "bob", expected_version=1)

    def test_only_one_of_two_racing_analysts_wins(self):
        cid = self.case["id"]
        results = []
        barrier = threading.Barrier(2)

        def attempt(actor):
            barrier.wait()
            try:
                self.store.transition_case(cid, "triaging", actor, expected_version=1)
                results.append(("ok", actor))
            except ConflictError:
                results.append(("conflict", actor))

        threads = [threading.Thread(target=attempt, args=(a,)) for a in ("ana", "bob")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(r[0] for r in results), ["conflict", "ok"])
        self.assertEqual(self.store.get_case(cid)["version"], 2)

    def test_bad_verdict_is_refused(self):
        with self.assertRaises(StoreError):
            self.store.transition_case(self.case["id"], "triaging", "ana",
                                       expected_version=1, verdict="probably_bad")


class TestAuditChain(StoreTestCase):
    def test_chain_verifies_and_survives_a_restart(self):
        for i in range(5):
            self.store.append_audit("ana", "contain", {"host": "h%03d" % i})
        self.assertTrue(self.store.verify_chain()["valid"])
        self.store.close()

        # This is the property the in-memory implementation could not have:
        # tamper evidence that outlives the process.
        reopened = Store(self.path)
        result = reopened.verify_chain()
        self.assertTrue(result["valid"])
        self.assertEqual(result["checked"], 5)
        reopened.append_audit("bob", "contain", {"host": "h999"})
        self.assertTrue(reopened.verify_chain()["valid"])
        reopened.close_all()

    def test_update_and_delete_are_refused_by_triggers(self):
        self.store.append_audit("ana", "contain", {"host": "h001"})
        conn = self.store.connect()
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE audit_log SET actor = 'mallory' WHERE seq = 1")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM audit_log WHERE seq = 1")

    def test_tampering_at_the_file_level_is_detected(self):
        for i in range(4):
            self.store.append_audit("ana", "contain", {"host": "h%03d" % i})
        self.store.close()

        # Drop the triggers and rewrite a payload, i.e. an attacker with write
        # access to the file rather than to the API.
        raw = sqlite3.connect(str(self.path))
        raw.execute("DROP TRIGGER audit_no_update")
        raw.execute("UPDATE audit_log SET payload = ? WHERE seq = 2",
                    (json.dumps({"host": "attacker-owned"}),))
        raw.commit()
        raw.close()

        verifier = Store(self.path)
        try:
            result = verifier.verify_chain()
        finally:
            verifier.close_all()
        self.assertFalse(result["valid"])
        self.assertEqual(result["broken_at"], 2)
        self.assertEqual(result["reason"], "hash mismatch")

    def test_concurrent_appends_produce_one_unbroken_chain(self):
        def worker(n):
            store = Store(self.path)
            try:
                for i in range(20):
                    store.append_audit("w%d" % n, "scan", {"i": i})
            finally:
                store.close_all()

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        result = self.store.verify_chain()
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["checked"], 80)


class TestOutbox(StoreTestCase):
    def test_at_least_once_delivery_with_offsets(self):
        for i in range(5):
            self.store.publish("alerts", {"i": i})
        events = self.store.fetch_events("soar")
        self.assertEqual(len(events), 5)
        # Crash before committing: the same events come back.
        self.assertEqual(len(self.store.fetch_events("soar")), 5)
        self.store.commit_offset("soar", events[-1]["id"])
        self.assertEqual(self.store.fetch_events("soar"), [])
        self.assertEqual(self.store.outbox_lag("soar"), 0)

    def test_offsets_are_per_consumer(self):
        self.store.publish("alerts", {"i": 1})
        self.store.commit_offset("soar", 1)
        self.assertEqual(len(self.store.fetch_events("notifier")), 1)

    def test_offset_never_goes_backwards(self):
        for i in range(3):
            self.store.publish("alerts", {"i": i})
        self.store.commit_offset("soar", 3)
        self.store.commit_offset("soar", 1)
        self.assertEqual(self.store.outbox_lag("soar"), 0)


class TestIdempotency(StoreTestCase):
    def test_replay_returns_the_stored_response(self):
        body = b'{"alert":"AL-1"}'
        self.assertIsNone(self.store.begin_idempotent("k1", "/v2/respond", body))
        self.store.complete_idempotent("k1", 201, '{"ok":true}')
        replay = self.store.begin_idempotent("k1", "/v2/respond", body)
        self.assertEqual(replay["status_code"], 201)
        self.assertEqual(replay["response"], '{"ok":true}')

    def test_errors_are_replayed_too(self):
        body = b'{"a":1}'
        self.store.begin_idempotent("k2", "/v2/score", body)
        self.store.complete_idempotent("k2", 500, '{"error":"boom"}')
        replay = self.store.begin_idempotent("k2", "/v2/score", body)
        self.assertEqual(replay["status_code"], 500)

    def test_same_key_different_body_is_a_conflict(self):
        self.store.begin_idempotent("k3", "/v2/score", b'{"a":1}')
        self.store.complete_idempotent("k3", 200, "{}")
        with self.assertRaises(IdempotencyConflict):
            self.store.begin_idempotent("k3", "/v2/score", b'{"a":2}')

    def test_in_flight_replay_is_refused(self):
        self.store.begin_idempotent("k4", "/v2/respond", b'{}')
        with self.assertRaises(IdempotencyInFlight):
            self.store.begin_idempotent("k4", "/v2/respond", b'{}')

    def test_containment_fires_once_under_a_stampede(self):
        """Ten concurrent retries of the same containment must execute once."""
        fired = []
        lock = threading.Lock()
        barrier = threading.Barrier(10)

        def attempt():
            store = Store(self.path)
            barrier.wait()
            try:
                if store.begin_idempotent("contain-h002", "/v2/respond", b'{}') is None:
                    with lock:
                        fired.append(1)
                    store.complete_idempotent("contain-h002", 201, '{"ok":true}')
            except (IdempotencyInFlight, IdempotencyConflict, sqlite3.OperationalError):
                pass
            finally:
                store.close_all()

        threads = [threading.Thread(target=attempt) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(fired), 1, "containment fired more than once")

    def test_released_key_can_be_retried(self):
        self.store.begin_idempotent("k5", "/v2/score", b'{}')
        self.store.release_idempotent("k5")
        self.assertIsNone(self.store.begin_idempotent("k5", "/v2/score", b'{}'))


class TestCursorCodec(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(decode_cursor(encode_cursor(0.98, "AL-1")), (0.98, "AL-1"))

    def test_cursor_has_no_padding(self):
        self.assertNotIn("=", encode_cursor(0.5, "AL-2"))


class TestDurability(StoreTestCase):
    def test_feedback_survives_a_restart(self):
        self.store.insert_alert(make_alert(1))
        self.store.add_feedback("AL-000001", 1, "ana", note="real brute force")
        self.store.close_all()
        reopened = Store(self.path)
        self.assertEqual(reopened.count_feedback(), 1)
        self.assertEqual(reopened.feedback_for("AL-000001")[0]["label"], 1)
        reopened.close_all()

    def test_one_verdict_per_actor_per_alert(self):
        self.store.insert_alert(make_alert(1))
        self.store.add_feedback("AL-000001", 1, "ana")
        self.store.add_feedback("AL-000001", 0, "ana")
        rows = self.store.feedback_for("AL-000001")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["label"], 0)

    def test_many_writers_do_not_lose_writes(self):
        def worker(n):
            store = Store(self.path)
            try:
                for i in range(25):
                    store.insert_alert(make_alert(n * 100 + i))
            finally:
                store.close_all()

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(self.store.count_alerts(), 100)


if __name__ == "__main__":
    unittest.main()
