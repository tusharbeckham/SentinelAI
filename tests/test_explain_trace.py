"""Tests for the per-alert forensic trace.

The point of these tests is that the trace is *arithmetic*, not narration. If
the fusion terms stop summing to the log-odds, or the family comparison stops
reproducing the pipeline's own labelling rule, the explainer starts lying to
the reader in a way no screenshot review would catch.
"""

from __future__ import annotations

import math
import unittest

import numpy as np

from sentinelai.explain_trace import (
    EXTREME_PERCENTILE,
    FEATURE_GROUPS,
    STAGE_ORDER,
    build_trace,
    family_evidence,
    feature_bands,
    leg_breakdown,
    missed_evidence,
    percentile_of,
    select_alert,
    sigmoid,
)

COEFFICIENTS = {
    "iforest_logit": 0.34254645234181474,
    "gbdt_logit": 1.1008832230073278,
    "graph_score": 0.11347992873914635,
}
INTERCEPT = -8.433968927814007

ABLATION = {
    "unsupervised_isolation_forest": {
        "average_precision": 0.2984837011150931,
        "roc_auc": 0.9920361397391212,
        "operating_point_at_budget": {"recall": 0.2033898305084746, "precision_eval": 0.24489795918367346},
    },
    "supervised_gbdt": {
        "average_precision": 0.858,
        "roc_auc": 0.9984054227660499,
        "operating_point_at_budget": {"recall": 0.66, "precision_eval": 0.79},
    },
    "graph_lateral_movement": {
        "average_precision": 0.039,
        "roc_auc": 0.71,
        "operating_point_at_budget": {"recall": 0.0, "precision_eval": 0.0},
    },
}

FAMILY_SIGNATURES = {
    "dos": ("pkts_sum", "flow_count", "z_flow_count", "syn_ratio"),
    "brute_force": ("failed_logins", "failed_login_ratio", "z_failed_logins"),
    "exfil": ("bytes_out_sum", "z_bytes_out_sum", "out_in_ratio", "bytes_out_p95"),
}

# The real h002 window, trimmed to the columns these tests touch.
ROW = {
    "flow_count": 89.0,
    "pkts_sum": 823.0,
    "mean_pkt_size": 201.9778367194372,
    "syn_ratio": 0.48314606741573035,
    "z_flow_count": 17.986420252709205,
    "auth_events": 47.0,
    "failed_logins": 45.0,
    "failed_login_ratio": 0.9574468085106383,
    "z_failed_logins": 45.0,
    "bytes_out_sum": 67054.38273852244,
    "z_bytes_out_sum": 3.0561979037443407,
    "out_in_ratio": 0.6747518856835133,
    "bytes_out_p95": 1257.2198583901225,
    "distinct_dsts": 3.0,
    "night_flag": 1.0,
    "graph_score": 0.31613859078764417,
}

ALERT = {
    "alert_id": "AL-1767491700-h002",
    "entity": "h002",
    "window": 1767491700,
    "window_iso": "2026-01-04T01:55:00+00:00",
    "probability": 0.9866137013264895,
    "probability_at_deployment_prior": 0.5933014617059006,
    "suspected_family": "dos",
    "ground_truth": "brute_force",
    "narrative": "Likely dos: mean_pkt_size elevated; flow count spike",
    "top": [
        {"feature": "mean_pkt_size", "contribution": 3.3269938144079663, "value": 201.98},
        {"feature": "flow_count", "contribution": 1.6205228315750537, "value": 89.0},
        {"feature": "syn_ratio", "contribution": 1.3859831986983355, "value": 0.483},
    ],
    "base_value_logodds": -8.362396334841463,
}

OPERATING_POINT = {
    "threshold": 0.6303419959358633,
    "alerts_per_day": 49.83930778739184,
    "recall": 0.6610169491525424,
    "precision_eval": 0.7959183673469388,
    "budget_per_day": 50.0,
    "ppv_at_deployment_prior": 0.06932091336199164,
}

DATASET = {
    "flows": 563619,
    "auth_events": 49535,
    "n_hosts": 40,
    "n_users": 30,
    "days": 4.0,
    "window_s": 300,
    "planned_base_rate": 0.005,
}


def fake_percentile(feature: str, value: float) -> float:
    """Deterministic stand-in: the brute-force signature is extreme, the rest mid."""
    extreme = {"failed_logins", "failed_login_ratio", "z_failed_logins", "flow_count"}
    return 0.999 if feature in extreme else 0.5


class TestSigmoid(unittest.TestCase):
    def test_matches_reference(self) -> None:
        for z in (-40.0, -3.5, 0.0, 2.25, 18.0):
            self.assertAlmostEqual(sigmoid(z), 1.0 / (1.0 + math.exp(-z)), places=12)

    def test_no_overflow_at_extremes(self) -> None:
        # exp(800) overflows; the branch on the sign is what avoids it.
        self.assertEqual(sigmoid(-800.0), 0.0)
        self.assertEqual(sigmoid(800.0), 1.0)

    def test_monotone(self) -> None:
        xs = [sigmoid(z) for z in np.linspace(-20, 20, 50)]
        self.assertEqual(xs, sorted(xs))


class TestPercentile(unittest.TestCase):
    def test_ties_count_as_at_or_below(self) -> None:
        self.assertEqual(percentile_of([0, 0, 1, 1], 1.0), 1.0)

    def test_median(self) -> None:
        self.assertAlmostEqual(percentile_of(range(100), 49), 0.5, places=6)

    def test_ignores_nan_and_empty(self) -> None:
        self.assertAlmostEqual(percentile_of([1.0, float("nan"), 3.0], 3.0), 1.0)
        self.assertTrue(math.isnan(percentile_of([], 1.0)))


class TestLegBreakdown(unittest.TestCase):
    def setUp(self) -> None:
        self.fusion = leg_breakdown([2.5, 6.0, 0.31613859078764417], COEFFICIENTS, INTERCEPT, ABLATION)

    def test_terms_sum_to_log_odds(self) -> None:
        # The whole promise of the fusion stage: it is a visible sum.
        total = sum(leg["term"] for leg in self.fusion["legs"]) + self.fusion["intercept"]
        self.assertAlmostEqual(total, self.fusion["log_odds"], places=12)

    def test_each_term_is_coefficient_times_value(self) -> None:
        for leg in self.fusion["legs"]:
            self.assertAlmostEqual(leg["term"], leg["coefficient"] * leg["value"], places=12)

    def test_probability_is_sigmoid_of_log_odds(self) -> None:
        self.assertAlmostEqual(self.fusion["probability"], sigmoid(self.fusion["log_odds"]), places=12)

    def test_leg_order_is_stable(self) -> None:
        self.assertEqual(
            [leg["label"] for leg in self.fusion["legs"]],
            ["isolation forest", "gradient boosting", "graph leg"],
        )

    def test_carries_standalone_quality(self) -> None:
        # Average precision, because ROC-AUC at a 0.5% base rate flatters all three.
        by_label = {leg["label"]: leg for leg in self.fusion["legs"]}
        self.assertAlmostEqual(by_label["graph leg"]["average_precision"], 0.039)
        self.assertAlmostEqual(by_label["gradient boosting"]["average_precision"], 0.858)

    def test_gbdt_dominates_the_sum(self) -> None:
        by_label = {leg["label"]: leg for leg in self.fusion["legs"]}
        self.assertGreater(by_label["gradient boosting"]["term"], by_label["graph leg"]["term"])


# The fitted stacker standardises its inputs. These numbers stand in for
# LogisticStacker.mu / .sd.
STANDARDIZER = {"mean": [0.5, 4.0, 0.1], "scale": [0.25, 3.0, 0.2]}


class TestStandardisedFusion(unittest.TestCase):
    """Regression tests for a real defect.

    The first version of the trace multiplied each stacker coefficient by the
    *raw* leg score. That reproduces a believable number and it is wrong: the
    stacker weights standardised inputs. On the h002 alert it misstated the
    log-odds by ~0.9 and the probability by ~0.02, and it survived because
    nothing compared the reconstruction to the published score.
    """

    def setUp(self) -> None:
        self.row = [1.0, 10.0, 0.5]
        self.std = leg_breakdown(
            self.row,
            COEFFICIENTS,
            INTERCEPT,
            ABLATION,
            mean=STANDARDIZER["mean"],
            scale=STANDARDIZER["scale"],
        )

    def test_standardised_value_is_centred_and_scaled(self) -> None:
        self.assertAlmostEqual(self.std["legs"][0]["standardized"], (1.0 - 0.5) / 0.25, places=12)
        self.assertAlmostEqual(self.std["legs"][1]["standardized"], (10.0 - 4.0) / 3.0, places=12)

    def test_term_is_coefficient_times_standardised_input(self) -> None:
        for leg in self.std["legs"]:
            self.assertAlmostEqual(leg["term"], leg["coefficient"] * leg["standardized"], places=12)

    def test_terms_still_sum_to_log_odds(self) -> None:
        total = sum(leg["term"] for leg in self.std["legs"]) + self.std["intercept"]
        self.assertAlmostEqual(total, self.std["log_odds"], places=12)

    def test_raw_multiplication_gives_a_different_answer(self) -> None:
        # If these ever agree, this test is no longer guarding anything.
        raw = leg_breakdown(self.row, COEFFICIENTS, INTERCEPT, ABLATION)
        self.assertGreater(abs(self.std["log_odds"] - raw["log_odds"]), 1.0)

    def test_identity_transform_is_the_default(self) -> None:
        raw = leg_breakdown(self.row, COEFFICIENTS, INTERCEPT, ABLATION)
        ident = leg_breakdown(
            self.row, COEFFICIENTS, INTERCEPT, ABLATION, mean=[0, 0, 0], scale=[1, 1, 1]
        )
        self.assertAlmostEqual(raw["log_odds"], ident["log_odds"], places=12)

    def test_degenerate_scale_is_clamped_not_infinite(self) -> None:
        f = leg_breakdown(
            self.row, COEFFICIENTS, INTERCEPT, ABLATION, mean=[0, 0, 0], scale=[0.0, 0.0, 0.0]
        )
        for leg in f["legs"]:
            self.assertTrue(math.isfinite(leg["term"]))

    def test_build_trace_reconstructs_through_standardisation(self) -> None:
        # Solve for the gbdt raw score that lands on the published probability
        # once centring and scaling are applied, then require the trace to
        # rediscover it.
        mean, scale = STANDARDIZER["mean"], STANDARDIZER["scale"]
        target = math.log(ALERT["probability"] / (1 - ALERT["probability"]))
        graph = ROW["graph_score"]
        fixed = (
            COEFFICIENTS["iforest_logit"] * ((0.0 - mean[0]) / scale[0])
            + COEFFICIENTS["graph_score"] * ((graph - mean[2]) / scale[2])
            + INTERCEPT
        )
        z_gbdt = (target - fixed) / COEFFICIENTS["gbdt_logit"]
        gbdt_raw = z_gbdt * scale[1] + mean[1]
        trace = build_trace(
            alert=ALERT,
            row=ROW,
            fusion_row=[0.0, gbdt_raw, graph],
            coefficients=COEFFICIENTS,
            intercept=INTERCEPT,
            prior_shift=-3.9224164612054793,
            family_signatures=FAMILY_SIGNATURES,
            ablation=ABLATION,
            standardizer=STANDARDIZER,
            operating_point=OPERATING_POINT,
            dataset=DATASET,
            percentile=fake_percentile,
        )
        fusion = {s["id"]: s for s in trace["stages"]}["fusion"]
        self.assertLess(fusion["reconstruction_error"], 1e-9)
        self.assertAlmostEqual(fusion["terms"][1]["value"], gbdt_raw, places=9)


class TestFamilyEvidence(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = family_evidence(ROW, ALERT["top"], FAMILY_SIGNATURES, fake_percentile)
        self.by_family = {f["family"]: f for f in self.evidence}

    def test_reproduces_the_pipeline_label(self) -> None:
        # pipeline.suspected_family sums positive contributions over signatures;
        # the trace must agree or it is describing a different decision.
        self.assertEqual(self.evidence[0]["family"], ALERT["suspected_family"])

    def test_dos_attribution_is_the_two_signature_features(self) -> None:
        self.assertAlmostEqual(
            self.by_family["dos"]["attributed"],
            1.6205228315750537 + 1.3859831986983355,
            places=12,
        )

    def test_true_family_got_no_attribution(self) -> None:
        # This is the defect the explainer exists to show.
        self.assertEqual(self.by_family["brute_force"]["attributed"], 0.0)

    def test_true_family_still_corroborates_strongest(self) -> None:
        self.assertGreater(
            self.by_family["brute_force"]["corroboration"],
            self.by_family["exfil"]["corroboration"],
        )

    def test_negative_contributions_never_credit_a_family(self) -> None:
        attrs = [{"feature": "failed_logins", "contribution": -5.0}]
        ev = {f["family"]: f for f in family_evidence(ROW, attrs, FAMILY_SIGNATURES, fake_percentile)}
        self.assertEqual(ev["brute_force"]["attributed"], 0.0)

    def test_missing_columns_are_skipped_not_zero_filled(self) -> None:
        ev = family_evidence({"flow_count": 89.0}, ALERT["top"], FAMILY_SIGNATURES, fake_percentile)
        dos = {f["family"]: f for f in ev}["dos"]
        self.assertEqual([f["feature"] for f in dos["features"]], ["flow_count"])


class TestMissedEvidence(unittest.TestCase):
    def test_flags_uncredited_extremes(self) -> None:
        evidence = family_evidence(ROW, ALERT["top"], FAMILY_SIGNATURES, fake_percentile)
        missed = missed_evidence(evidence, "brute_force")
        self.assertEqual(
            sorted(m["feature"] for m in missed),
            ["failed_login_ratio", "failed_logins", "z_failed_logins"],
        )
        for m in missed:
            self.assertGreaterEqual(m["percentile"], EXTREME_PERCENTILE)

    def test_credited_features_are_not_missed(self) -> None:
        evidence = family_evidence(ROW, ALERT["top"], FAMILY_SIGNATURES, fake_percentile)
        self.assertEqual(missed_evidence(evidence, "dos"), [])

    def test_no_truth_means_nothing_missed(self) -> None:
        evidence = family_evidence(ROW, ALERT["top"], FAMILY_SIGNATURES, fake_percentile)
        self.assertEqual(missed_evidence(evidence, None), [])


class TestFeatureBands(unittest.TestCase):
    def test_every_group_present_and_sorted(self) -> None:
        bands = feature_bands(ROW, fake_percentile, ALERT["top"])
        self.assertEqual([b["group"] for b in bands], list(FEATURE_GROUPS))
        for band in bands:
            pcts = [f["percentile"] or 0.0 for f in band["features"]]
            self.assertEqual(pcts, sorted(pcts, reverse=True))

    def test_marks_attributed_features(self) -> None:
        bands = {b["group"]: b for b in feature_bands(ROW, fake_percentile, ALERT["top"])}
        volume = {f["feature"]: f for f in bands["volume"]["features"]}
        self.assertTrue(volume["mean_pkt_size"]["attributed_credit"])
        identity = {f["feature"]: f for f in bands["identity"]["features"]}
        self.assertFalse(identity["failed_logins"]["attributed_credit"])


class TestSelectAlert(unittest.TestCase):
    def test_prefers_a_misnamed_true_positive(self) -> None:
        agree = dict(ALERT, alert_id="AL-agree", suspected_family="dos", ground_truth="dos")
        self.assertEqual(select_alert([agree, ALERT])["alert_id"], ALERT["alert_id"])

    def test_falls_back_to_top_ranked(self) -> None:
        agree = dict(ALERT, alert_id="AL-agree", ground_truth="dos")
        self.assertEqual(select_alert([agree])["alert_id"], "AL-agree")

    def test_ignores_benign_ground_truth(self) -> None:
        fp = dict(ALERT, alert_id="AL-fp", suspected_family="dos", ground_truth="benign")
        self.assertEqual(select_alert([fp])["alert_id"], "AL-fp")

    def test_empty_is_an_error(self) -> None:
        with self.assertRaises(ValueError):
            select_alert([])


class TestBuildTrace(unittest.TestCase):
    def setUp(self) -> None:
        # Fusion inputs chosen to land on the published probability, the way the
        # real pipeline's fusion_inputs() does.
        target_logodds = math.log(ALERT["probability"] / (1 - ALERT["probability"]))
        graph = ROW["graph_score"]
        rest = target_logodds - INTERCEPT - COEFFICIENTS["graph_score"] * graph
        gbdt = rest / COEFFICIENTS["gbdt_logit"]
        self.trace = build_trace(
            alert=ALERT,
            row=ROW,
            fusion_row=[0.0, gbdt, graph],
            coefficients=COEFFICIENTS,
            intercept=INTERCEPT,
            prior_shift=-3.9224164612054793,
            family_signatures=FAMILY_SIGNATURES,
            ablation=ABLATION,
            operating_point=OPERATING_POINT,
            dataset=DATASET,
            soar_decision={"mode": "human_review", "playbook": "triage", "actions": ["enrich"]},
            percentile=fake_percentile,
            top_importance={"mean_pkt_size": 0.8086114683376238},
            rank=1,
            total_windows=11326,
            audit_chain_valid=True,
        )

    def test_stages_match_declared_order(self) -> None:
        self.assertEqual([s["id"] for s in self.trace["stages"]], list(STAGE_ORDER))

    def test_every_stage_is_self_describing(self) -> None:
        # The front end is a renderer; it must not have to supply prose.
        for stage in self.trace["stages"]:
            self.assertTrue(stage["title"])
            self.assertTrue(stage["caption"])

    def test_fusion_reconstructs_the_published_probability(self) -> None:
        fusion = {s["id"]: s for s in self.trace["stages"]}["fusion"]
        self.assertLess(fusion["reconstruction_error"], 1e-9)

    def test_threshold_stage_agrees_the_alert_fired(self) -> None:
        gate = {s["id"]: s for s in self.trace["stages"]}["threshold"]
        self.assertTrue(gate["fired"])
        self.assertAlmostEqual(gate["margin"], ALERT["probability"] - OPERATING_POINT["threshold"], places=12)

    def test_deployment_probability_is_lower_than_bench(self) -> None:
        # Negative prior shift: the enriched bench must not be quoted as production.
        fusion = {s["id"]: s for s in self.trace["stages"]}["fusion"]
        self.assertLess(fusion["probability_at_deployment_prior"], fusion["probability"])

    def test_disagreement_is_surfaced_with_missed_evidence(self) -> None:
        dis = self.trace["disagreement"]
        self.assertTrue(dis["detected"])
        self.assertEqual(dis["suspected"], "dos")
        self.assertEqual(dis["truth"], "brute_force")
        self.assertTrue(dis["missed_evidence"])

    def test_json_safe(self) -> None:
        import json

        json.dumps(self.trace)  # must not raise on numpy scalars


if __name__ == "__main__":
    unittest.main()
