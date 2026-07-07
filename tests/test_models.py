"""Unit tests for the detection maths. Run: python3 -m pytest -q  (or unittest)."""

from __future__ import annotations

import unittest

import numpy as np

from sentinelai import active_learning as al
from sentinelai import ensemble as ens
from sentinelai.explain import ShapleyExplainer
from sentinelai.features import FEATURES, build_dataset, matrix, time_split
from sentinelai.graphlm import AuthGraphDetector, attach_graph_features
from sentinelai.models.gbdt import GBDT
from sentinelai.models.iforest import IsolationForest
from sentinelai.synth import generate


class TestIsolationForest(unittest.TestCase):
    def test_outliers_score_higher(self):
        rng = np.random.default_rng(0)
        normal = rng.normal(0, 1, size=(800, 6))
        outliers = rng.normal(9, 1, size=(40, 6))
        f = IsolationForest(n_trees=100, sample_size=128, seed=1).fit(normal)
        s_n, s_o = f.score(normal), f.score(outliers)
        self.assertGreater(s_o.mean(), s_n.mean() + 0.1)
        self.assertTrue(((s_n > 0) & (s_n < 1)).all())
        auc = ens.roc_auc(
            np.r_[np.zeros(len(s_n)), np.ones(len(s_o))], np.r_[s_n, s_o]
        )
        self.assertGreater(auc, 0.95)


class TestGBDT(unittest.TestCase):
    def test_learns_nonlinear_boundary(self):
        rng = np.random.default_rng(2)
        X = rng.normal(size=(1500, 4))
        y = ((X[:, 0] * X[:, 1] > 0.35) | (X[:, 2] > 1.6)).astype(int)
        m = GBDT(n_trees=80, max_depth=3, seed=3).fit(X[:1000], y[:1000])
        auc = ens.roc_auc(y[1000:], m.predict_proba(X[1000:]))
        self.assertGreater(auc, 0.92)

    def test_imbalance_weighting_moves_scores(self):
        rng = np.random.default_rng(4)
        X = rng.normal(size=(1200, 3))
        y = (X[:, 0] > 2.4).astype(int)
        plain = GBDT(n_trees=40, seed=5).fit(X, y).predict_proba(X)
        weighted = GBDT(n_trees=40, seed=5, scale_pos_weight=25.0).fit(X, y).predict_proba(X)
        self.assertGreater(weighted[y == 1].mean(), plain[y == 1].mean())


class TestMetrics(unittest.TestCase):
    def test_roc_auc_matches_closed_form(self):
        y = np.array([0, 0, 1, 1])
        s = np.array([0.1, 0.4, 0.35, 0.8])
        self.assertAlmostEqual(ens.roc_auc(y, s), 0.75, places=6)

    def test_average_precision_perfect_ranking(self):
        y = np.array([1, 1, 0, 0])
        s = np.array([0.9, 0.8, 0.2, 0.1])
        self.assertAlmostEqual(ens.average_precision(y, s), 1.0, places=6)

    def test_bayesian_ppv_base_rate_fallacy(self):
        # Axelsson: with a 1e-4 prior, 99% TPR and 1e-3 FPR still yields <10% PPV
        ppv = ens.bayesian_ppv(tpr=0.99, fpr=1e-3, prior=1e-4)
        self.assertLess(ppv, 0.10)
        self.assertAlmostEqual(ppv, 0.99e-4 / (0.99e-4 + 1e-3 * (1 - 1e-4)), places=9)

    def test_prior_shift_lowers_probability(self):
        rng = np.random.default_rng(6)
        Z = rng.normal(size=(600, 3))
        y = (Z[:, 0] + 0.5 * Z[:, 1] > 1.2).astype(int)
        st = ens.LogisticStacker().fit(Z, y, names=("a", "b", "c"))
        st.set_deployment_prior(1e-4)
        p_eval = st.predict_proba(Z)
        p_dep = st.predict_proba(Z, apply_prior_shift=True)
        self.assertTrue((p_dep <= p_eval + 1e-12).all())
        self.assertLess(st.prior_shift, 0.0)


class TestExplainability(unittest.TestCase):
    def test_local_accuracy(self):
        rng = np.random.default_rng(7)
        bg = rng.normal(size=(60, 5))
        w = np.array([1.5, -2.0, 0.7, 0.0, 3.0])

        def f(X):
            X = np.atleast_2d(X)
            return X @ w

        ex = ShapleyExplainer(f, bg, [f"f{i}" for i in range(5)], n_permutations=64, seed=1)
        x = rng.normal(size=5)
        out = ex.explain_row(x)
        # additive model: Shapley values are exact, residual must vanish
        self.assertLess(abs(out["attribution_residual"]), 1e-8)
        self.assertAlmostEqual(
            out["base_value"] + sum(out["contributions"].values()), out["score"], places=8
        )
        # a zero-weight feature must get zero credit
        self.assertAlmostEqual(out["contributions"]["f3"], 0.0, places=8)


class TestGraphDetector(unittest.TestCase):
    def test_new_edge_chain_scores_above_routine(self):
        import pandas as pd

        rows = []
        for t in range(0, 3600, 60):  # routine baseline: u1 h1->h2
            rows.append((t, "u1", "h1", "h2", 1, 3))
        base = pd.DataFrame(rows, columns=["ts", "user", "src_host", "dst_host", "success", "logon_type"])
        det = AuthGraphDetector().fit(base)

        routine = pd.DataFrame(
            [(3700, "u1", "h1", "h2", 1, 3)],
            columns=["ts", "user", "src_host", "dst_host", "success", "logon_type"],
        )
        lateral = pd.DataFrame(
            [
                (3700, "u1", "h1", "h5", 1, 10),
                (3705, "u1", "h5", "h9", 1, 10),
                (3710, "u1", "h1", "h7", 1, 10),
            ],
            columns=["ts", "user", "src_host", "dst_host", "success", "logon_type"],
        )
        s_routine = det.score_windows(routine)["graph_score"].max()
        s_lateral = det.score_windows(lateral)["graph_score"].max()
        self.assertGreater(s_lateral, s_routine + 0.2)
        self.assertGreaterEqual(det.score_windows(lateral)["g_chain_depth"].max(), 2)


class TestDriftAndActiveLearning(unittest.TestCase):
    def test_psi_zero_when_identical_and_large_when_shifted(self):
        rng = np.random.default_rng(8)
        a = rng.normal(size=4000)
        b = rng.normal(size=4000)
        c = rng.normal(loc=2.5, size=4000)
        self.assertLess(al.psi(a, b), 0.10)
        self.assertGreater(al.psi(a, c), 0.25)

    def test_review_selection_respects_budget_and_diversity(self):
        rng = np.random.default_rng(9)
        p = rng.random(500)
        entities = [f"h{i % 5:02d}" for i in range(500)]
        idx = al.select_for_review(p, entities, budget=40)
        self.assertEqual(len(idx), 40)
        self.assertEqual(len(set(idx)), 40)


class TestDatasetIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tel = generate(seed=11, n_hosts=12, days=0.5)
        cls.df = build_dataset(cls.tel)

    def test_labels_align_with_ground_truth(self):
        truth = {(r.entity, r.win) for r in self.tel.incidents.itertuples()}
        labelled = {
            (r.entity, r.win) for r in self.df[self.df["label"] == 1].itertuples()
        }
        self.assertTrue(labelled.issubset(truth))
        self.assertGreater(len(labelled), 0)

    def test_time_split_is_chronological_and_disjoint(self):
        train, calib, test = time_split(self.df)
        self.assertLess(train["win"].max(), calib["win"].min())
        self.assertLess(calib["win"].max(), test["win"].min())
        self.assertEqual(len(train) + len(calib) + len(test), len(self.df))

    def test_feature_matrix_is_finite(self):
        X = matrix(self.df, FEATURES)
        self.assertTrue(np.isfinite(X).all())

    def test_graph_features_merge_without_nans(self):
        out, _ = attach_graph_features(
            self.df, self.tel.auth, baseline_cutoff=int(self.df["baseline_cutoff"].iloc[0])
        )
        self.assertFalse(out[["graph_score", "g_new_edges"]].isna().any().any())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
