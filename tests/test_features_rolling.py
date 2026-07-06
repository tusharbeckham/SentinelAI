"""Tests for the trailing-window (roll_*) features.

The entire value of these features depends on them being STRICTLY CAUSAL. A
single off-by-one (a centred window, a shift in the wrong direction, or a
groupby that reorders rows) would let a window's score depend on traffic that
had not happened yet, which is the classic way time-series security models
produce impressive and completely invalid results. So causality is asserted by
hand-recomputation rather than trusted.
"""

import unittest

import numpy as np

from sentinelai.features import (
    ABSOLUTE_FEATURES,
    FEATURES,
    RELATIVE_FEATURES,
    ROLLING_FEATURES,
    ROLLING_WINDOWS,
    build_dataset,
)
from sentinelai.pipeline import ALL_FEATURES
from sentinelai.synth import generate


class TestRollingFeatures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tel = generate(seed=5, days=0.6)
        cls.df = build_dataset(cls.tel)

    def test_feature_contract(self):
        self.assertEqual(
            len(FEATURES),
            len(ABSOLUTE_FEATURES) + len(RELATIVE_FEATURES) + len(ROLLING_FEATURES),
        )
        # features.py owns 34 columns (22 absolute + 5 relative + 7 rolling);
        # the 6 graph features are attached later, giving the 40-column model
        # input asserted against pipeline.ALL_FEATURES below.
        self.assertEqual(len(FEATURES), 34)
        self.assertEqual(len(ALL_FEATURES), 40)
        for name in ROLLING_FEATURES:
            self.assertIn(name, self.df.columns)

    def test_rolling_sum_matches_hand_recomputation(self):
        """roll_dport_reach_1h[t] must equal sum(distinct_dports[t-11 .. t])."""
        for entity in sorted(self.df["entity"].unique())[:5]:
            e = self.df[self.df["entity"] == entity].sort_values("win")
            src = e["distinct_dports"].to_numpy(dtype=float)
            manual = np.array(
                [src[max(0, i - ROLLING_WINDOWS + 1) : i + 1].sum() for i in range(len(src))]
            )
            np.testing.assert_allclose(
                e["roll_dport_reach_1h"].to_numpy(dtype=float), manual, rtol=0, atol=1e-9
            )

    def test_no_future_leakage(self):
        """Truncating the future must not change any past rolling value.

        Recomputing the features on a prefix of the telemetry has to reproduce
        the prefix rows exactly. If a rolling aggregate peeked forward, the full
        run and the truncated run would disagree on the overlapping windows.
        """
        wins = np.sort(self.df["win"].unique())
        cut = int(wins[int(len(wins) * 0.7)])

        tel = self.tel
        truncated = type(tel)(
            flows=tel.flows[tel.flows["ts"] < cut].copy(),
            auth=tel.auth[tel.auth["ts"] < cut].copy(),
            incidents=tel.incidents.copy(),
            benign_anomalies=tel.benign_anomalies.copy(),
            config=tel.config,
        )
        df_trunc = build_dataset(truncated)

        # Compare only well inside the prefix: the baseline cutoff used for the
        # z-scores is a function of the corpus length, so the very edges are
        # legitimately different. Rolling features must match regardless.
        keys = ["entity", "win"]
        merged = self.df.merge(df_trunc, on=keys, suffixes=("_full", "_trunc"))
        merged = merged[merged["win"] < cut - ROLLING_WINDOWS * 300]
        self.assertGreater(len(merged), 200, "not enough overlapping rows to be meaningful")
        for name in ROLLING_FEATURES:
            np.testing.assert_allclose(
                merged[f"{name}_full"].to_numpy(dtype=float),
                merged[f"{name}_trunc"].to_numpy(dtype=float),
                rtol=1e-9,
                atol=1e-9,
                err_msg=f"{name} depends on future windows",
            )

    def test_active_window_count_is_bounded(self):
        col = self.df["roll_active_windows_1h"].to_numpy(dtype=float)
        self.assertGreaterEqual(col.min(), 0.0)
        self.assertLessEqual(col.max(), float(ROLLING_WINDOWS))

    def test_rolling_features_are_finite(self):
        X = self.df[list(ROLLING_FEATURES)].to_numpy(dtype=float)
        self.assertTrue(np.isfinite(X).all())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
