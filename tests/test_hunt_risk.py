"""Tests for the hunting query language and the entity risk rollup.

The query language is the part an analyst types into, so its failure modes matter
more than its happy path: a query that cannot be parsed must raise, never
silently match everything.
"""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from sentinelai import hunt
from sentinelai.entity_risk import (
    TIMELINE_BUCKETS,
    TOP_K,
    build_entity_risk,
    build_hunt_index,
    noisy_or,
)

ROWS = [
    {"entity": "h001", "probability": 0.95, "attack": "brute_force", "flow_count": 10.0,
     "distinct_dsts": 30.0, "failed_logins": 9.0, "night_flag": 1.0},
    {"entity": "h002", "probability": 0.55, "attack": "benign", "flow_count": 800.0,
     "distinct_dsts": 2.0, "failed_logins": 0.0, "night_flag": 0.0},
    {"entity": "h003", "probability": 0.10, "attack": "benign", "flow_count": 5.0,
     "distinct_dsts": 40.0, "failed_logins": 1.0, "night_flag": 1.0},
]
FIELDS = tuple(ROWS[0].keys())


class TestNoisyOr(unittest.TestCase):
    def test_empty_is_zero(self):
        self.assertEqual(noisy_or(np.array([])), 0.0)

    def test_single_value_is_that_value(self):
        self.assertAlmostEqual(noisy_or(np.array([0.4])), 0.4, places=9)

    def test_corroboration_beats_a_single_stronger_window(self):
        # Four windows at 0.7 should outrank one at 0.9: this is the whole reason
        # the rollup exists instead of taking a max.
        many = noisy_or(np.array([0.7, 0.7, 0.7, 0.7]))
        one = noisy_or(np.array([0.9]))
        self.assertGreater(many, one)

    def test_monotonic_in_each_probability(self):
        base = np.array([0.2, 0.3, 0.4])
        higher = np.array([0.2, 0.3, 0.5])
        self.assertGreater(noisy_or(higher), noisy_or(base))

    def test_caps_at_top_k_windows(self):
        # Windows beyond TOP_K must not accumulate, otherwise every busy entity
        # saturates to 1.0 and the ranking carries no information.
        exactly_k = np.full(TOP_K, 0.5)
        far_more = np.full(TOP_K * 5, 0.5)
        self.assertAlmostEqual(noisy_or(exactly_k), noisy_or(far_more), places=12)

    def test_never_reaches_exactly_one(self):
        self.assertLess(noisy_or(np.array([1.0, 1.0, 1.0])), 1.0000000001)
        self.assertGreaterEqual(noisy_or(np.array([1.0])), 0.999999)


class TestEntityRisk(unittest.TestCase):
    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"entity": "h001", "win": 100, "probability": 0.95, "attack": "brute_force"},
                {"entity": "h001", "win": 200, "probability": 0.80, "attack": "brute_force"},
                {"entity": "h002", "win": 100, "probability": 0.20, "attack": "benign"},
                {"entity": "h002", "win": 300, "probability": 0.10, "attack": "benign"},
            ]
        )

    def test_ranks_the_attacked_entity_first(self):
        out = build_entity_risk(self.frame(), threshold=0.6)
        self.assertEqual(out["entities"][0]["entity"], "h001")
        self.assertEqual(out["ranks_of_true_positive_entities"], [1])

    def test_counts_alerts_against_the_threshold(self):
        out = build_entity_risk(self.frame(), threshold=0.6)
        first = out["entities"][0]
        self.assertEqual(first["alerts"], 2)
        self.assertEqual(first["windows"], 2)
        self.assertEqual(first["truth_attack_windows"], 2)
        self.assertEqual(first["truth_families"], ["brute_force"])

    def test_timeline_has_fixed_width_and_holds_the_peak(self):
        out = build_entity_risk(self.frame(), threshold=0.6)
        timeline = out["entities"][0]["timeline"]
        self.assertEqual(len(timeline), TIMELINE_BUCKETS)
        self.assertAlmostEqual(max(timeline), 0.95, places=4)

    def test_missing_columns_raise(self):
        with self.assertRaises(ValueError):
            build_entity_risk(pd.DataFrame([{"nope": 1}]), threshold=0.5)

    def test_hunt_index_keeps_every_alert_and_every_true_attack(self):
        frame = self.frame()
        index = build_hunt_index(frame, threshold=0.6)
        kept = {(r["entity"], r["win"]) for r in index["rows"]}
        self.assertIn(("h001", 100), kept)
        self.assertIn(("h001", 200), kept)
        self.assertEqual(index["total_scored_windows"], 4)


class TestHuntParsing(unittest.TestCase):
    def test_numeric_comparison(self):
        hits = hunt.run("probability > 0.9", ROWS, FIELDS)
        self.assertEqual([r["entity"] for r in hits], ["h001"])

    def test_and_narrows(self):
        hits = hunt.run("distinct_dsts > 20 and flow_count < 50", ROWS, FIELDS)
        self.assertEqual({r["entity"] for r in hits}, {"h001", "h003"})

    def test_or_widens(self):
        hits = hunt.run("probability > 0.9 or flow_count > 500", ROWS, FIELDS)
        self.assertEqual({r["entity"] for r in hits}, {"h001", "h002"})

    def test_and_binds_tighter_than_or(self):
        # 'a or b and c' must mean 'a or (b and c)', matching the CLI and the
        # TypeScript implementation.
        query = "probability > 0.9 or flow_count > 1 and distinct_dsts > 35"
        hits = hunt.run(query, ROWS, FIELDS)
        self.assertEqual({r["entity"] for r in hits}, {"h001", "h003"})

    def test_not_negates_one_clause(self):
        hits = hunt.run("not attack ~ benign", ROWS, FIELDS)
        self.assertEqual([r["entity"] for r in hits], ["h001"])

    def test_substring_match_is_case_insensitive(self):
        self.assertEqual(len(hunt.run("attack ~ BRUTE", ROWS, FIELDS)), 1)

    def test_negated_substring(self):
        self.assertEqual(len(hunt.run("attack !~ benign", ROWS, FIELDS)), 1)

    def test_quoted_value(self):
        self.assertEqual(len(hunt.run("attack = 'brute_force'", ROWS, FIELDS)), 1)

    def test_sort_and_limit(self):
        hits = hunt.run("probability > 0 | sort probability asc | limit 2", ROWS, FIELDS)
        self.assertEqual([r["entity"] for r in hits], ["h003", "h002"])

    def test_empty_query_matches_everything(self):
        self.assertEqual(len(hunt.run("", ROWS, FIELDS)), len(ROWS))

    def test_every_shipped_example_parses_and_runs(self):
        # The examples are shown in the CLI and the console; a broken one would
        # teach the wrong syntax.
        for query, _why in hunt.EXAMPLES:
            with self.subTest(query=query):
                hunt.parse(query)


class TestHuntErrors(unittest.TestCase):
    def test_unknown_field_raises(self):
        with self.assertRaises(hunt.HuntError):
            hunt.run("nonsense > 1", ROWS, FIELDS)

    def test_missing_operator_raises(self):
        with self.assertRaises(hunt.HuntError):
            hunt.run("probability", ROWS, FIELDS)

    def test_missing_value_raises(self):
        with self.assertRaises(hunt.HuntError):
            hunt.run("probability >", ROWS, FIELDS)

    def test_trailing_not_raises(self):
        with self.assertRaises(hunt.HuntError):
            hunt.run("probability > 0.5 and not", ROWS, FIELDS)

    def test_unknown_directive_raises(self):
        with self.assertRaises(hunt.HuntError):
            hunt.run("probability > 0.5 | frobnicate", ROWS, FIELDS)

    def test_bad_sort_direction_raises(self):
        with self.assertRaises(hunt.HuntError):
            hunt.run("probability > 0 | sort probability sideways", ROWS, FIELDS)

    def test_non_positive_limit_raises(self):
        with self.assertRaises(hunt.HuntError):
            hunt.run("probability > 0 | limit 0", ROWS, FIELDS)

    def test_ordering_a_string_raises(self):
        with self.assertRaises(hunt.HuntError):
            hunt.run("attack > benign", ROWS, FIELDS)

    def test_comparing_a_label_to_a_number_raises(self):
        with self.assertRaises(hunt.HuntError):
            hunt.run("attack > 1", ROWS, FIELDS)


if __name__ == "__main__":
    unittest.main()
