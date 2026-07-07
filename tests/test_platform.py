"""Security and platform tests: auth, RBAC, rate limiting, SOAR policy, audit."""

from __future__ import annotations

import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from sentinelai.api import (
    AuthError,
    ScoringService,
    TokenBucket,
    issue_token,
    make_handler,
    redact,
    verify_token,
)
from sentinelai.soar import Policy, ResponseEngine

SECRET = "unit-test-secret-not-a-real-key"
FEATURES = ["flow_count", "bytes_out_sum", "failed_logins"]


def _service() -> ScoringService:
    return ScoringService(
        feature_names=FEATURES,
        score_fn=lambda f: min(1.0, f["failed_logins"] / 100.0),
        alerts=[{"alert_id": "AL-1", "entity": "h001", "probability": 0.97}],
        responder=ResponseEngine(Policy(), execute=False),
    )


class TestTokens(unittest.TestCase):
    def test_roundtrip(self):
        claims = verify_token(SECRET, issue_token(SECRET, "alice", "analyst"))
        self.assertEqual(claims["role"], "analyst")
        self.assertEqual(claims["sub"], "alice")

    def test_tampered_payload_rejected(self):
        token = issue_token(SECRET, "bob", "viewer")
        head, payload, sig = token.split(".")
        forged = issue_token(SECRET, "bob", "admin").split(".")[1]
        with self.assertRaises(AuthError):
            verify_token(SECRET, f"{head}.{forged}.{sig}")

    def test_wrong_secret_rejected(self):
        with self.assertRaises(AuthError):
            verify_token("other-secret", issue_token(SECRET, "bob", "viewer"))

    def test_expired_rejected(self):
        with self.assertRaises(AuthError):
            verify_token(SECRET, issue_token(SECRET, "bob", "viewer", ttl=-1))

    def test_audience_enforced(self):
        with self.assertRaises(AuthError):
            verify_token(SECRET, issue_token(SECRET, "bob", "viewer", aud="other-app"))


class TestRateLimit(unittest.TestCase):
    def test_bucket_exhausts_then_refills(self):
        b = TokenBucket(capacity=3, refill_per_sec=1.0)
        t = 1000.0
        self.assertTrue(all(b.allow("k", t)[0] for _ in range(3)))
        ok, wait = b.allow("k", t)
        self.assertFalse(ok)
        self.assertGreater(wait, 0)
        self.assertTrue(b.allow("k", t + 2.0)[0])


class TestRedaction(unittest.TestCase):
    def test_secrets_never_logged(self):
        line = '{"authorization": "Bearer abc.def.ghi", "api_key": "sk-12345"}'
        out = redact(line)
        self.assertNotIn("abc.def.ghi", out)
        self.assertNotIn("sk-12345", out)
        self.assertIn("REDACTED", out)


class TestHttpApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        import logging

        cls.service = _service()
        handler = make_handler(
            cls.service, SECRET, TokenBucket(capacity=200, refill_per_sec=50.0),
            logging.getLogger("test"),
        )
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _req(self, method, path, token=None, body=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read() or b"{}"), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            # HTTPError is a file-like object holding an open socket. Closing it
            # explicitly keeps the suite silent under -W error and avoids the
            # ResourceWarning Python 3.14 emits when the GC cleans it up late.
            try:
                return exc.code, json.loads(exc.read() or b"{}"), dict(exc.headers)
            finally:
                exc.close()

    def test_healthz_public_and_headers_hardened(self):
        status, body, headers = self._req("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")

    def test_anonymous_denied(self):
        status, body, _ = self._req("GET", "/v1/alerts")
        self.assertEqual(status, 401)

    def test_role_enforced(self):
        viewer = issue_token(SECRET, "v", "viewer")
        status, _, _ = self._req("POST", "/v1/score", viewer, {"features": {"failed_logins": 90}})
        self.assertEqual(status, 403)
        status, _, _ = self._req("GET", "/v1/audit", viewer)
        self.assertEqual(status, 403)

    def test_scoring_and_validation(self):
        analyst = issue_token(SECRET, "a", "analyst")
        status, body, _ = self._req(
            "POST", "/v1/score", analyst, {"features": {"flow_count": 10, "bytes_out_sum": 1, "failed_logins": 80}}
        )
        self.assertEqual(status, 200)
        self.assertAlmostEqual(body["probability"], 0.8, places=6)
        status, body, _ = self._req(
            "POST", "/v1/score", analyst, {"features": {"failed_logins": "eighty"}}
        )
        self.assertEqual(status, 422)

    def test_feedback_and_unknown_route(self):
        analyst = issue_token(SECRET, "a", "analyst")
        status, body, _ = self._req(
            "POST", "/v1/feedback", analyst,
            {"entity": "h001", "window": 1767225600, "verdict": "false_positive", "analyst": "a"},
        )
        self.assertEqual(status, 202)
        self.assertTrue(body["accepted"])
        status, _, _ = self._req("GET", "/v1/nope", analyst)
        self.assertEqual(status, 404)

    def test_responder_route_requires_responder_role(self):
        analyst = issue_token(SECRET, "a", "analyst")
        alert = {"entity": "h009", "probability": 0.99, "suspected_family": "portscan",
                 "top": [{"feature": "distinct_dports", "contribution": 2.0}]}
        status, _, _ = self._req("POST", "/v1/respond", analyst, alert)
        self.assertEqual(status, 403)
        responder = issue_token(SECRET, "r", "responder")
        status, body, _ = self._req("POST", "/v1/respond", responder, alert)
        self.assertEqual(status, 200)
        self.assertEqual(body["mode"], "auto_contain")


class TestSoarPolicy(unittest.TestCase):
    def _alert(self, p, family="portscan", entity="h009", feats=(("distinct_dports", 2.0),)):
        return {
            "entity": entity,
            "window": 1767225600,
            "probability": p,
            "suspected_family": family,
            "top": [{"feature": f, "contribution": c} for f, c in feats],
        }

    def test_confidence_tiers(self):
        eng = ResponseEngine(Policy())
        self.assertEqual(eng.decide(self._alert(0.98))["mode"], "auto_contain")
        self.assertEqual(eng.decide(self._alert(0.60))["mode"], "human_review")
        self.assertEqual(eng.decide(self._alert(0.30))["mode"], "enrich")
        self.assertEqual(eng.decide(self._alert(0.05))["mode"], "suppress")

    def test_protected_asset_never_auto_contained(self):
        eng = ResponseEngine(Policy(protected_assets=("dc01",)))
        d = eng.decide(self._alert(0.99, entity="dc01"))
        self.assertEqual(d["mode"], "human_review")
        self.assertIn("protected asset", " ".join(d["rationale"]))

    def test_unsupported_explanation_blocks_auto_action(self):
        eng = ResponseEngine(Policy())
        d = eng.decide(self._alert(0.99, feats=(("night_flag", 2.0), ("mean_pkt_size", 1.0))))
        self.assertEqual(d["mode"], "human_review")

    def test_auto_action_rate_limit(self):
        eng = ResponseEngine(Policy(max_auto_actions_per_hour=2))
        modes = [eng.decide(self._alert(0.99), now=1000 + i)["mode"] for i in range(4)]
        self.assertEqual(modes.count("auto_contain"), 2)
        self.assertEqual(modes.count("human_review"), 2)

    def test_audit_chain_detects_tampering(self):
        eng = ResponseEngine(Policy())
        for p in (0.99, 0.7, 0.3):
            eng.decide(self._alert(p))
        self.assertTrue(eng.audit.verify())
        eng.audit.records[1]["probability"] = 0.01  # retro-active edit
        self.assertFalse(eng.audit.verify())

    def test_dry_run_default(self):
        eng = ResponseEngine(Policy())
        self.assertFalse(eng.decide(self._alert(0.99))["executed"])
        eng2 = ResponseEngine(Policy(), execute=True)
        self.assertTrue(eng2.decide(self._alert(0.99))["executed"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
