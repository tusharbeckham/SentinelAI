"""The committed OpenAPI document must equal the generated one.

The plan called for hand-writing docs/openapi.json and testing that the code
matched it. That detects drift after the fact. Generating the document from
ROUTE_SPECS makes drift impossible, and reduces this file to two jobs:

  1. the committed artefact is current, so a reader of the repository is not
     looking at yesterday's API;
  2. every declared route actually dispatches, and every dispatchable route is
     declared -- a specification that documents a route which 404s is worse
     than no specification.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from sentinelai.apiv2 import ApiError, ROUTE_SPECS, V2Router, openapi_document
from sentinelai.store import Store

SPEC_PATH = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"


class TestCommittedSpec(unittest.TestCase):
    def test_the_committed_file_is_current(self):
        committed = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
        self.assertEqual(committed, openapi_document(),
                         "docs/openapi.json is stale; regenerate it")

    def test_the_document_declares_bearer_auth(self):
        doc = openapi_document()
        self.assertIn("bearerAuth", doc["components"]["securitySchemes"])

    def test_protected_routes_declare_a_minimum_role(self):
        doc = openapi_document()
        for method, path, role, _summary in ROUTE_SPECS:
            operation = doc["paths"][path][method.lower()]
            if role == "public":
                self.assertNotIn("security", operation)
            else:
                self.assertEqual(operation["x-minimum-role"], role)

    def test_path_parameters_are_declared(self):
        doc = openapi_document()
        operation = doc["paths"]["/v2/cases/{case_id}"]["get"]
        names = [p["name"] for p in operation["parameters"]]
        self.assertEqual(names, ["case_id"])


class TestRouteParity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "parity.db"))
        self.store.migrate()
        self.router = V2Router(self.store)

    def tearDown(self):
        self.store.close_all()
        self.tmp.cleanup()

    def test_every_declared_route_is_reachable(self):
        admin = {"sub": "root", "role": "admin"}
        unreachable = []
        for method, path, _role, _summary in ROUTE_SPECS:
            probe = (path.replace("{alert_id}", "A")
                         .replace("{case_id}", "1")
                         .replace("{version}", "v1"))
            try:
                self.router.dispatch(method, probe, claims=admin, body=b"{}",
                                     headers={})
            except ApiError as exc:
                # Any other error means the route was found and then objected to
                # something, which is all this test needs to know.
                if exc.status == 404 and exc.error == "not_found":
                    unreachable.append(method + " " + path)
            except Exception:
                pass
        self.assertEqual(unreachable, [])


if __name__ == "__main__":
    unittest.main()
