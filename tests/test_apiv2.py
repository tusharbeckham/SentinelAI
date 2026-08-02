"""Tests for the /v2 router.

These exercise the router directly rather than over a socket. The router is
transport-agnostic on purpose, so binding a port here would test the stdlib
HTTP server rather than the routing, authorisation and concurrency rules that
are actually ours.
"""
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from sentinelai.apiv2 import ApiError, V2Router, etag_of
from sentinelai.store import Store

VIEWER = {"sub": "vic", "role": "viewer"}
ANALYST = {"sub": "ana", "role": "analyst"}
RESPONDER = {"sub": "rae", "role": "responder"}
ADMIN = {"sub": "adi", "role": "admin"}


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
        "created_at": "2026-01-04T01:55:%02d+00:00" % (i % 60),
    }


def jb(obj):
    return json.dumps(obj).encode("utf-8")


class RouterTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = Path(self.tmp.name) / "test.db"
        self.store = Store(self.path)
        self.store.migrate()
        self.router = V2Router(self.store, scorer=self.fake_scorer)

    def tearDown(self):
        self.store.close_all()
        self.tmp.cleanup()

    @staticmethod
    def fake_scorer(features):
        return {"probability": 0.9866, "features_seen": len(features)}

    def call(self, method, path, **kw):
        return self.router.dispatch(method, path, **kw)

    def assertApi(self, status, error, fn, *a, **kw):
        with self.assertRaises(ApiError) as ctx:
            fn(*a, **kw)
        self.assertEqual(ctx.exception.status, status)
        self.assertEqual(ctx.exception.error, error)
        return ctx.exception


class TestRouting(RouterTestCase):
    def test_unknown_path_is_404(self):
        self.assertApi(404, "not_found", self.call, "GET", "/v2/nope",
                       claims=ADMIN)

    def test_v1_paths_are_not_claimed_by_this_router(self):
        # /v1 is frozen and belongs to api.py. If the v2 router ever answers a
        # /v1 path, the freeze has been broken.
        self.assertApi(404, "not_found", self.call, "GET", "/v1/alerts",
                       claims=ADMIN)

    def test_wrong_method_on_a_real_path_is_404(self):
        self.assertApi(404, "not_found", self.call, "DELETE", "/v2/alerts",
                       claims=ADMIN)

    def test_metrics_is_public_and_text(self):
        status, payload, _ = self.call("GET", "/metrics")
        self.assertEqual(status, 200)
        self.assertIsInstance(payload, str)
        self.assertIn("sentinelai_", payload)


class TestReadiness(RouterTestCase):
    def test_ready_when_migrated_and_scorer_present(self):
        status, payload, _ = self.call("GET", "/readyz")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ready"])
        self.assertGreater(payload["schema_version"], 0)

    def test_not_ready_without_a_model(self):
        # A readiness probe that returns 200 unconditionally is worse than none,
        # because it keeps a broken replica in the load balancer.
        router = V2Router(self.store, scorer=None)
        status, payload, _ = router.dispatch("GET", "/readyz")
        self.assertEqual(status, 503)
        self.assertFalse(payload["ready"])
        self.assertIn("model not loaded", payload["problems"])

    def test_not_ready_when_the_database_is_gone(self):
        broken = Store(Path(self.tmp.name) / "never-migrated.db")
        router = V2Router(broken, scorer=self.fake_scorer)
        status, payload, _ = router.dispatch("GET", "/readyz")
        broken.close_all()
        self.assertEqual(status, 503)
        self.assertFalse(payload["ready"])


class TestAuthorisation(RouterTestCase):
    def test_no_claims_is_401(self):
        self.assertApi(401, "unauthenticated", self.call, "GET", "/v2/alerts")

    def test_viewer_cannot_write_feedback(self):
        self.assertApi(403, "forbidden", self.call, "POST", "/v2/feedback",
                       body=jb({"alert_id": "AL-000001", "label": 1}),
                       claims=VIEWER)

    def test_analyst_cannot_respond(self):
        self.assertApi(403, "forbidden", self.call, "POST", "/v2/respond",
                       body=jb({"entity": "h002", "action": "auto_contain"}),
                       claims=ANALYST)

    def test_responder_cannot_read_the_audit_log(self):
        # Role order is a ladder, not a set of tags: responder outranks analyst
        # but is still below admin.
        self.assertApi(403, "forbidden", self.call, "GET", "/v2/audit",
                       claims=RESPONDER)

    def test_admin_inherits_every_lower_role(self):
        status, _, _ = self.call("GET", "/v2/alerts", claims=ADMIN)
        self.assertEqual(status, 200)

    def test_the_error_body_never_says_which_check_failed(self):
        exc = self.assertApi(403, "forbidden", self.call, "GET", "/v2/audit",
                             claims=VIEWER)
        body = json.dumps(exc.body())
        self.assertNotIn("viewer", body)
        self.assertNotIn("admin", body)


class TestAlerts(RouterTestCase):
    def setUp(self):
        super().setUp()
        for i in range(1, 8):
            self.store.insert_alert(make_alert(i, prob=0.5 + i / 100.0))

    def test_list_is_ordered_by_probability(self):
        _, page, _ = self.call("GET", "/v2/alerts", claims=VIEWER)
        probs = [row["probability"] for row in page["items"]]
        self.assertEqual(probs, sorted(probs, reverse=True))

    def test_cursor_walks_the_whole_list_without_repeats(self):
        seen = []
        cursor = None
        for _ in range(10):
            query = {"limit": ["3"]}
            if cursor:
                query["cursor"] = [cursor]
            _, page, _ = self.call("GET", "/v2/alerts", query=query,
                                   claims=VIEWER)
            seen.extend(row["id"] for row in page["items"])
            cursor = page["next_cursor"]
            if not cursor:
                break
        self.assertEqual(len(seen), 7)
        self.assertEqual(len(set(seen)), 7)

    def test_limit_is_clamped_not_rejected(self):
        _, page, _ = self.call("GET", "/v2/alerts", query={"limit": ["9999"]},
                               claims=VIEWER)
        self.assertLessEqual(len(page["items"]), 200)

    def test_a_non_numeric_limit_is_400(self):
        self.assertApi(400, "invalid_limit", self.call, "GET", "/v2/alerts",
                       query={"limit": ["lots"]}, claims=VIEWER)

    def test_filter_by_entity(self):
        self.store.insert_alert(make_alert(99, entity="h404"))
        _, page, _ = self.call("GET", "/v2/alerts",
                               query={"entity": ["h404"]}, claims=VIEWER)
        self.assertEqual(len(page["items"]), 1)
        self.assertEqual(page["items"][0]["entity"], "h404")

    def test_missing_alert_is_404(self):
        self.assertApi(404, "alert_not_found", self.call, "GET",
                       "/v2/alerts/AL-999999", claims=VIEWER)

    def test_etag_changes_when_the_page_changes(self):
        _, _, h1 = self.call("GET", "/v2/alerts", claims=VIEWER)
        self.store.insert_alert(make_alert(50, prob=0.999))
        _, _, h2 = self.call("GET", "/v2/alerts", claims=VIEWER)
        self.assertNotEqual(h1["ETag"], h2["ETag"])


class TestCases(RouterTestCase):
    def setUp(self):
        super().setUp()
        self.store.insert_alert(make_alert(1))
        _, self.case, self.headers = self.call(
            "POST", "/v2/cases", body=jb({"alert_id": "AL-000001"}),
            claims=ANALYST)
        self.case_id = self.case["id"]

    def test_opening_a_case_returns_201_with_location_and_etag(self):
        self.assertIn("/v2/cases/", self.headers["Location"])
        self.assertEqual(self.headers["ETag"], chr(34) + "1" + chr(34))

    def test_unknown_priority_is_422(self):
        self.assertApi(422, "invalid_priority", self.call, "POST", "/v2/cases",
                       body=jb({"alert_id": "AL-000001", "priority": "urgent"}),
                       claims=ANALYST)

    def test_patch_without_if_match_is_428(self):
        self.assertApi(428, "precondition_required", self.call, "PATCH",
                       "/v2/cases/" + self.case_id,
                       body=jb({"status": "triaging"}), claims=ANALYST)

    def test_patch_with_a_stale_if_match_is_412(self):
        self.call("PATCH", "/v2/cases/" + self.case_id,
                  body=jb({"status": "triaging"}), claims=ANALYST,
                  headers={"If-Match": chr(34) + "1" + chr(34)})
        self.assertApi(412, "version_conflict", self.call, "PATCH",
                       "/v2/cases/" + self.case_id,
                       body=jb({"status": "escalated"}), claims=ANALYST,
                       headers={"If-Match": chr(34) + "1" + chr(34)})

    def test_a_successful_patch_bumps_the_etag(self):
        _, case, headers = self.call(
            "PATCH", "/v2/cases/" + self.case_id,
            body=jb({"status": "triaging"}), claims=ANALYST,
            headers={"If-Match": chr(34) + "1" + chr(34)})
        self.assertEqual(case["status"], "triaging")
        self.assertEqual(headers["ETag"], chr(34) + "2" + chr(34))

    def test_an_illegal_transition_is_422_not_412(self):
        # The version is correct; the move is not. Conflating the two would tell
        # the client to re-read and retry, which would fail forever.
        self.assertApi(422, "invalid_transition", self.call, "PATCH",
                       "/v2/cases/" + self.case_id,
                       body=jb({"status": "resolved"}), claims=ANALYST,
                       headers={"If-Match": chr(34) + "1" + chr(34)})

    def test_if_match_accepts_a_weak_etag(self):
        _, _, headers = self.call(
            "PATCH", "/v2/cases/" + self.case_id,
            body=jb({"status": "triaging"}), claims=ANALYST,
            headers={"If-Match": "W/" + chr(34) + "1" + chr(34)})
        self.assertEqual(headers["ETag"], chr(34) + "2" + chr(34))

    def test_get_case_includes_events(self):
        self.call("POST", "/v2/cases/" + self.case_id + "/notes",
                  body=jb({"body": "checked the auth logs"}), claims=ANALYST)
        _, case, _ = self.call("GET", "/v2/cases/" + self.case_id,
                               claims=VIEWER)
        self.assertTrue(any("auth logs" in json.dumps(e)
                            for e in case["events"]))

    def test_an_empty_note_is_422(self):
        self.assertApi(422, "invalid_note", self.call, "POST",
                       "/v2/cases/" + self.case_id + "/notes",
                       body=jb({"body": "   "}), claims=ANALYST)

    def test_note_on_a_missing_case_is_404(self):
        self.assertApi(404, "case_not_found", self.call, "POST",
                       "/v2/cases/CASE-000000000000/notes",
                       body=jb({"body": "hello"}), claims=ANALYST)


class TestIdempotency(RouterTestCase):
    def setUp(self):
        super().setUp()
        self.store.insert_alert(make_alert(1))

    def test_respond_without_a_key_is_refused(self):
        self.assertApi(400, "idempotency_key_required", self.call, "POST",
                       "/v2/respond",
                       body=jb({"entity": "h002", "action": "auto_contain"}),
                       claims=RESPONDER)

    def test_replay_returns_the_stored_response_and_acts_once(self):
        body = jb({"entity": "h002", "action": "auto_contain"})
        headers = {"Idempotency-Key": "contain-h002"}
        s1, p1, _ = self.call("POST", "/v2/respond", body=body,
                              claims=RESPONDER, headers=headers)
        s2, p2, h2 = self.call("POST", "/v2/respond", body=body,
                               claims=RESPONDER, headers=headers)
        self.assertEqual((s1, s2), (202, 202))
        self.assertEqual(p1, p2)
        self.assertEqual(h2["Idempotent-Replay"], "true")
        # One audit entry, not two. This is the whole point.
        page = self.store.audit_page()
        contains = [r for r in page["items"] if r["action"] == "auto_contain"]
        self.assertEqual(len(contains), 1)

    def test_same_key_different_body_is_422(self):
        headers = {"Idempotency-Key": "k1"}
        self.call("POST", "/v2/respond",
                  body=jb({"entity": "h002", "action": "auto_contain"}),
                  claims=RESPONDER, headers=headers)
        self.assertApi(422, "idempotency_key_reuse", self.call, "POST",
                       "/v2/respond",
                       body=jb({"entity": "h999", "action": "auto_contain"}),
                       claims=RESPONDER, headers=headers)

    def test_a_stored_error_is_replayed_as_the_error(self):
        # Replaying a 4xx is correct: the client asked what happened to that
        # request, and the answer is that it failed.
        headers = {"Idempotency-Key": "bad-feedback"}
        body = jb({"alert_id": "AL-999999", "label": 1})
        self.assertApi(422, "alert_not_found", self.call, "POST",
                       "/v2/feedback", body=body, claims=ANALYST,
                       headers=headers)
        status, payload, hdrs = self.call("POST", "/v2/feedback", body=body,
                                          claims=ANALYST, headers=headers)
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"], "alert_not_found")
        self.assertEqual(hdrs["Idempotent-Replay"], "true")

    def test_a_stampede_of_retries_contains_once(self):
        body = jb({"entity": "h002", "action": "auto_contain"})
        headers = {"Idempotency-Key": "stampede"}
        barrier = threading.Barrier(8)
        outcomes = []
        lock = threading.Lock()

        def attempt():
            store = Store(self.path)
            router = V2Router(store, scorer=self.fake_scorer)
            try:
                barrier.wait()
                status, _, _ = router.dispatch(
                    "POST", "/v2/respond", body=body, claims=RESPONDER,
                    headers=headers)
                with lock:
                    outcomes.append(status)
            except (ApiError, sqlite3.OperationalError):
                pass
            finally:
                store.close_all()

        threads = [threading.Thread(target=attempt) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        page = self.store.audit_page()
        contains = [r for r in page["items"] if r["action"] == "auto_contain"]
        self.assertEqual(len(contains), 1)


class TestFeedback(RouterTestCase):
    def setUp(self):
        super().setUp()
        self.store.insert_alert(make_alert(1))

    def test_feedback_persists_across_a_restart(self):
        # The v1 service appended to a Python list. This is the difference.
        self.call("POST", "/v2/feedback",
                  body=jb({"alert_id": "AL-000001", "label": 1,
                           "note": "real brute force"}),
                  claims=ANALYST)
        self.store.close_all()
        reopened = Store(self.path)
        try:
            self.assertEqual(reopened.count_feedback(), 1)
            self.assertEqual(
                reopened.feedback_for("AL-000001")[0]["label"], 1)
        finally:
            reopened.close_all()
        self.store = Store(self.path)

    def test_feedback_writes_an_audit_entry(self):
        self.call("POST", "/v2/feedback",
                  body=jb({"alert_id": "AL-000001", "label": 0}),
                  claims=ANALYST)
        page = self.store.audit_page()
        self.assertTrue(any(r["action"] == "feedback" for r in page["items"]))

    def test_label_two_is_422(self):
        self.assertApi(422, "invalid_label", self.call, "POST", "/v2/feedback",
                       body=jb({"alert_id": "AL-000001", "label": 2}),
                       claims=ANALYST)

    def test_malformed_json_is_400_not_500(self):
        self.assertApi(400, "malformed_json", self.call, "POST",
                       "/v2/feedback", body=b"{not json", claims=ANALYST)

    def test_an_empty_body_is_400(self):
        self.assertApi(400, "empty_body", self.call, "POST", "/v2/feedback",
                       body=b"", claims=ANALYST)


class TestAudit(RouterTestCase):
    def test_verify_reports_a_valid_chain(self):
        self.store.append_audit("ana", "contain", {"host": "h001"})
        _, result, _ = self.call("GET", "/v2/audit/verify", claims=ADMIN)
        self.assertTrue(result["valid"])
        self.assertEqual(result["checked"], 1)

    def test_a_broken_chain_is_200_with_valid_false(self):
        # The server is working correctly and reporting a true fact about its
        # data. A 500 would say the server failed, which is the wrong story.
        self.store.append_audit("ana", "contain", {"host": "h001"})
        self.store.append_audit("ana", "contain", {"host": "h002"})
        raw = sqlite3.connect(str(self.path))
        raw.execute("DROP TRIGGER audit_no_update")
        raw.execute("UPDATE audit_log SET payload = ? WHERE seq = 2",
                    (json.dumps({"host": "attacker-owned"}),))
        raw.commit()
        raw.close()
        status, result, _ = self.call("GET", "/v2/audit/verify", claims=ADMIN)
        self.assertEqual(status, 200)
        self.assertFalse(result["valid"])
        self.assertEqual(result["broken_at"], 2)

    def test_audit_page_is_admin_only(self):
        self.assertApi(403, "forbidden", self.call, "GET", "/v2/audit",
                       claims=ANALYST)


class TestErrorShape(RouterTestCase):
    def test_every_error_body_has_the_same_four_keys(self):
        exc = self.assertApi(404, "not_found", self.call, "GET", "/v2/nope",
                             claims=ADMIN)
        self.assertEqual(sorted(exc.body().keys()),
                         ["details", "error", "message", "request_id"])

    def test_in_flight_conflict_carries_retry_after(self):
        self.store.insert_alert(make_alert(1))
        body = jb({"entity": "h002", "action": "auto_contain"})
        self.store.begin_idempotent("held", "/v2/respond", body)
        exc = self.assertApi(409, "request_in_flight", self.call, "POST",
                             "/v2/respond", body=body, claims=RESPONDER,
                             headers={"Idempotency-Key": "held"})
        self.assertEqual(exc.headers["Retry-After"], "1")


class TestEtagHelper(unittest.TestCase):
    def test_etag_is_stable_under_key_order(self):
        self.assertEqual(etag_of({"a": 1, "b": 2}), etag_of({"b": 2, "a": 1}))

    def test_etag_is_quoted(self):
        tag = etag_of({"a": 1})
        self.assertTrue(tag.startswith(chr(34)) and tag.endswith(chr(34)))


if __name__ == "__main__":
    unittest.main()
