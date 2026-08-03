"""Registry tests: round-trip fidelity, the promotion gate, and rollback.

The model is fitted once for the whole module. Fitting is the slow part and
nothing here mutates it, so paying for it per-test would buy nothing but a
slower suite.
"""

import json
import os
import tempfile
import unittest

import numpy as np

from sentinelai.features import matrix
from sentinelai.pipeline import ALL_FEATURES, fit_pipeline
from sentinelai.registry import (PromotionRefused, Registry, RegistryError,
                                 feature_hash)
from sentinelai.store import Store

FIT = None
X_TEST = None


def setUpModule():
    global FIT, X_TEST
    FIT = fit_pipeline(seed=7, days=0.6)
    _train, _calib, test = FIT["splits"]
    X_TEST = matrix(test, ALL_FEATURES)


GOOD = {"pr_auc": 0.81, "ece": 0.002, "roc_auc": 0.99, "brier": 0.0024}


class RegistryTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "test.db"))
        self.store.migrate()
        self.registry = Registry(os.path.join(self.tmp.name, "models"),
                                 store=self.store)
        self.model = FIT["model"]

    def tearDown(self):
        self.store.close_all()
        self.tmp.cleanup()

    def save(self, metrics=None, **kw):
        return self.registry.save(
            self.model,
            metrics=dict(metrics or GOOD),
            training={"seed": 7, "days": 0.6},
            operating_point={"threshold": 0.63, "budget_per_day": 50},
            deployment_prior=1e-4,
            **kw)


class TestRoundTrip(RegistryTestCase):
    def test_a_reloaded_model_scores_bit_identically(self):
        """Not allclose. A tolerance would hide exactly the bug worth catching.

        If serialisation loses a bit somewhere, the reloaded model is a
        different model, and a threshold tuned on the original no longer means
        what it says.
        """
        before = self.model.proba(X_TEST, prior_shift=True)
        version = self.save()
        after = self.registry.load(version).proba(X_TEST, prior_shift=True)
        self.assertTrue(np.array_equal(before, after))

    def test_the_archive_loads_without_pickle(self):
        """Loading a pickle executes arbitrary code in the archive.

        A model file is an artefact that moves between machines, so it must not
        be a code-execution vector. allow_pickle=False is the assertion.
        """
        version = self.save()
        path = os.path.join(self.registry.root, version, "ensemble.npz")
        with np.load(path, allow_pickle=False) as bundle:
            self.assertIn("if_offsets", bundle.files)
            self.assertIn("gb_offsets", bundle.files)

    def test_saving_twice_produces_the_same_content_hash(self):
        a = self.registry.show(self.save())
        b = self.registry.show(self.save())
        self.assertEqual(a["content_hash"], b["content_hash"])

    def test_verify_detects_a_tampered_archive(self):
        """The hash is over array contents, so that is what must be tampered.

        Appending junk bytes to the zip container would not change what numpy
        reads back, and a check that passed on a modified file would be worse
        than no check at all.
        """
        version = self.save()
        self.assertTrue(self.registry.verify(version))
        path = os.path.join(self.registry.root, version, "ensemble.npz")
        with np.load(path, allow_pickle=False) as bundle:
            arrays = {k: bundle[k] for k in bundle.files}
        arrays["st_w"] = arrays["st_w"] + 1.0
        np.savez_compressed(path, **arrays)
        self.assertFalse(self.registry.verify(version))


class TestFeatureContract(RegistryTestCase):
    def test_the_hash_is_order_sensitive(self):
        """Sorting the names would let two incompatible models hash equal.

        The model indexes feature columns positionally, so the contract is the
        ordered list. Two models with the same names in a different order are
        not interchangeable and must not compare equal.
        """
        names = list(ALL_FEATURES)
        swapped = [names[1], names[0]] + names[2:]
        self.assertNotEqual(feature_hash(names), feature_hash(swapped))

    def test_the_manifest_records_the_ordered_contract(self):
        manifest = self.registry.show(self.save())
        self.assertEqual(manifest["feature_contract"]["names"], list(ALL_FEATURES))
        self.assertEqual(manifest["feature_contract"]["hash"],
                         feature_hash(ALL_FEATURES))


class TestPromotionGate(RegistryTestCase):
    def test_the_first_model_may_be_promoted(self):
        version = self.save()
        self.assertEqual(self.registry.gate(version, "production"), [])
        self.registry.promote(version, "production", actor="tester")
        self.assertEqual(self.registry.production_version(), version)

    def test_a_worse_model_is_refused(self):
        incumbent = self.save()
        self.registry.promote(incumbent, "production", actor="tester")
        challenger = self.save(metrics=dict(GOOD, pr_auc=0.50))
        with self.assertRaises(PromotionRefused) as caught:
            self.registry.promote(challenger, "production", actor="tester")
        self.assertTrue(any("pr_auc" in p for p in caught.exception.problems))
        self.assertEqual(self.registry.production_version(), incumbent)

    def test_a_miscalibrated_model_is_refused(self):
        version = self.save(metrics=dict(GOOD, ece=0.5))
        with self.assertRaises(PromotionRefused) as caught:
            self.registry.promote(version, "production", actor="tester")
        self.assertTrue(any("ece" in p for p in caught.exception.problems))

    def test_force_overrides_but_is_recorded(self):
        """Forcing must be possible and must leave a trace.

        An override nobody can find afterwards is indistinguishable from the
        gate never having existed.
        """
        incumbent = self.save()
        self.registry.promote(incumbent, "production", actor="tester")
        challenger = self.save(metrics=dict(GOOD, pr_auc=0.10))
        self.registry.promote(challenger, "production", actor="cowboy", force=True)
        self.assertEqual(self.registry.production_version(), challenger)

        page = self.store.audit_page(limit=100)
        forced = [e for e in page["items"] if "promote" in e["action"]]
        self.assertTrue(forced)
        self.assertTrue(any(json.loads(e["payload"]).get("forced")
                            for e in forced))

    def test_promoting_a_version_to_the_stage_it_already_holds_is_refused(self):
        version = self.save()
        self.registry.promote(version, "production", actor="tester")
        with self.assertRaises(PromotionRefused) as caught:
            self.registry.promote(version, "production", actor="tester")
        self.assertTrue(any("already in stage" in p
                            for p in caught.exception.problems))

    def test_archived_may_return_to_production(self):
        """This transition is legal on purpose: it is what rollback is.

        Forbidding it would make the registry tidy and the 3am recovery path
        impossible.
        """
        version = self.save()
        self.registry.promote(version, "production", actor="tester")
        self.registry.promote(version, "archived", actor="tester")
        self.registry.promote(version, "production", actor="tester")
        self.assertEqual(self.registry.production_version(), version)

    def test_an_unknown_version_is_an_error(self):
        with self.assertRaises(RegistryError):
            self.registry.show("v-does-not-exist")


class TestRollback(RegistryTestCase):
    def test_rollback_restores_the_previous_production_model(self):
        first = self.save()
        self.registry.promote(first, "production", actor="tester")
        second = self.save(metrics=dict(GOOD, pr_auc=0.82))
        self.registry.promote(second, "production", actor="tester")
        self.assertEqual(self.registry.production_version(), second)

        self.registry.rollback(actor="tester")
        self.assertEqual(self.registry.production_version(), first)

    def test_rollback_with_no_history_refuses(self):
        with self.assertRaises(RegistryError):
            self.registry.rollback(actor="tester")


class TestStagePointer(RegistryTestCase):
    def test_the_pointer_file_is_json_and_survives_reopen(self):
        version = self.save()
        self.registry.promote(version, "production", actor="tester")
        reopened = Registry(self.registry.root, store=self.store)
        self.assertEqual(reopened.production_version(), version)

    def test_load_production_returns_a_working_model(self):
        version = self.save()
        self.registry.promote(version, "production", actor="tester")
        found = self.registry.load_production()
        self.assertIsNotNone(found)
        model, manifest = found
        self.assertEqual(manifest["version"], version)
        self.assertTrue(np.array_equal(
            model.proba(X_TEST, prior_shift=True),
            self.model.proba(X_TEST, prior_shift=True)))


if __name__ == "__main__":
    unittest.main()
