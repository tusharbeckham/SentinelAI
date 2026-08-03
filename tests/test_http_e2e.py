"""End-to-end HTTP: a real socket, real headers, real status codes.

Every other API test calls the router directly, which is fast and keeps the
router honest but proves nothing about the transport. This one binds a port.
It exists to catch the class of bug that only appears once a real client is
talking to a real handler: a route mounted in the router but not reachable, an
auth check that passes claims but not headers, a body that is not valid JSON on
the wire.

Port 0 asks the OS for a free port. Hard-coding one makes the suite fail on any
machine already running the service, which is every developer machine.
"""

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from sentinelai import api
from sentinelai.pipeline import ALL_FEATURES
from sentinelai.registry import Registry
from sentinelai.soar import Policy, ResponseEngine
from sentinelai.store import Store

SECRET = "t" * 64


class HttpTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prior_secret = os.environ.get("SENTINELAI_JWT_SECRET")
        os.environ["SENTINELAI_JWT_SECRET"] = SECRET

        cls.tmp = tempfile.TemporaryDirectory()
        cls.store = Store(os.path.join(cls.tmp.name, "e2e.db"))
        cls.store.migrate()
        cls.registry = Registry(os.path.join(cls.tmp.name, "models"),
                                store=cls.store)

        # A stub scorer. This test is about the transport, and fitting a real
        # ensemble here would add ten seconds to prove nothing extra.
        service = api.ScoringService(
            ALL_FEATURES, lambda features: 0.5, alerts=[],
            responder=ResponseEngine(Policy(protected_assets=("h000",)),
                                     execute=False))

        cls.server = api.serve(service, host="127.0.0.1", port=0,
                               store=cls.store, registry=cls.registry)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.store.close_all()
        cls.tmp.cleanup()
        if cls.prior_secret is None:
            os.environ.pop("SENTINELAI_JWT_SECRET", None)
        else:
            os.environ["SENTINELAI_JWT_SECRET"] = cls.prior_secret

    # -- helpers ------------------------------------------------------------
    def token(self, role):
        return api.issue_token(SECRET, "e2e-" + role, role)

    def call(self, method, path, role=None, body=None, headers=None):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        payload = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=payload, method=method)
        if payload is not None:
            request.add_header("Content-Type", "application/json")
        if role is not None:
            request.add_header("Authorization", "Bearer " + self.token(role))
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    def json_call(self, *args, **kwargs):
        status, raw, headers = self.call(*args, **kwargs)
        return status, json.loads(raw.decode("utf-8")), headers


class TestOperationalRoutes(HttpTestCase):
    def test_healthz_needs_no_credentials(self):
        status, _body, _headers = self.call("GET", "/healthz")
        self.assertEqual(status, 200)

    def test_readyz_reports_ready(self):
        status, body, _headers = self.json_call("GET", "/readyz")
        self.assertIn(status, (200, 503))
        self.assertIn("problems", body)

    def test_metrics_is_prometheus_text_not_json(self):
        status, raw, headers = self.call("GET", "/metrics")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/plain"))
        self.assertIn(b"sentinelai_", raw)

    def test_openapi_is_served_and_parses(self):
        status, body, _headers = self.json_call("GET", "/openapi.json")
        self.assertEqual(status, 200)
        self.assertEqual(body["openapi"], "3.1.0")
        self.assertIn("/v2/alerts", body["paths"])
        self.assertIn("/v2/models", body["paths"])


class TestVersionsCoexist(HttpTestCase):
    def test_v1_alerts_still_answers(self):
        status, body, _headers = self.json_call("GET", "/v1/alerts", role="viewer")
        self.assertEqual(status, 200)

    def test_v2_alerts_answers_with_a_cursor_envelope(self):
        status, body, _headers = self.json_call("GET", "/v2/alerts", role="viewer")
        self.assertEqual(status, 200)
        self.assertIn("items", body)

    def test_the_two_versions_are_served_by_one_process(self):
        v1, _b1, _h1 = self.call("GET", "/v1/alerts", role="viewer")
        v2, _b2, _h2 = self.call("GET", "/v2/alerts", role="viewer")
        self.assertEqual((v1, v2), (200, 200))


class TestAuthOverTheWire(HttpTestCase):
    def test_a_missing_token_is_401(self):
        status, body, _headers = self.json_call("GET", "/v2/alerts")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "unauthenticated")

    def test_a_garbage_token_is_401(self):
        status, body, _headers = self.json_call(
            "GET", "/v2/alerts", headers={"Authorization": "Bearer not.a.token"})
        self.assertEqual(status, 401)

    def test_an_insufficient_role_is_403(self):
        status, body, _headers = self.json_call("GET", "/v2/audit", role="viewer")
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "forbidden")

    def test_admin_reaches_the_audit_log(self):
        status, _body, _headers = self.json_call("GET", "/v2/audit", role="admin")
        self.assertEqual(status, 200)


class TestModelRoutesOverTheWire(HttpTestCase):
    def test_models_lists_an_empty_registry_rather_than_failing(self):
        status, body, _headers = self.json_call("GET", "/v2/models", role="viewer")
        self.assertEqual(status, 200)
        self.assertEqual(body["versions"], [])
        self.assertIsNone(body["production"])

    def test_promotion_requires_admin(self):
        status, body, _headers = self.json_call(
            "POST", "/v2/models/whatever/promote", role="analyst",
            body={"to": "production"})
        self.assertEqual(status, 403)

    def test_drift_answers_honestly_before_any_sweep(self):
        status, body, _headers = self.json_call("GET", "/v2/drift", role="viewer")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "no_recommendation")


class TestErrorShape(HttpTestCase):
    def test_an_unknown_path_uses_the_common_error_shape(self):
        status, body, _headers = self.json_call("GET", "/v2/nonsense", role="admin")
        self.assertEqual(status, 404)
        for key in ("error", "message", "request_id"):
            self.assertIn(key, body)

    def test_responses_carry_the_hardening_headers(self):
        """These are cheap and they only work if they are on every response."""
        _status, _body, headers = self.call("GET", "/v2/alerts", role="viewer")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
