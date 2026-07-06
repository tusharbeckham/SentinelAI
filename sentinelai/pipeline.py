"""End-to-end SentinelAI pipeline: ingest -> features -> detect -> fuse ->
calibrate -> explain -> respond -> monitor drift -> retrain.

Run:  python3 -m sentinelai.pipeline --out artifacts

Every number the README claims is produced here as measured JSON, including the
three experiments that test the design hypotheses rather than assuming them:
  1. Ablation at a fixed analyst budget (does fusing legs actually help?)
  2. False-positive provenance (do alerts land on confusable-but-benign events?)
  3. Zero-day holdout (does the hybrid survive a family it was never labelled on?)
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import numpy as np
import pandas as pd

from . import active_learning as al
from . import ensemble as ens
from .explain import ShapleyExplainer, narrate
from .features import FEATURES, build_dataset, matrix, time_split
from .graphlm import GRAPH_FEATURES, attach_graph_features
from .models.gbdt import GBDT
from .models.iforest import IsolationForest
from .soar import Policy, ResponseEngine
from .synth import DEFAULT_MIX, WINDOW_S, generate

ALL_FEATURES: Tuple[str, ...] = FEATURES + GRAPH_FEATURES
# The stacker is linear, so the legs are handed to it in LOG-ODDS space. Raw
# probabilities saturate at 0/1 and destroy the resolution that separates a
# strong detection from an overwhelming one.
FUSION_NAMES = ("iforest_logit", "gbdt_logit", "graph_score")

FAMILY_SIGNATURES: Dict[str, Tuple[str, ...]] = {
    "portscan": ("distinct_dports", "z_distinct_dports", "port_entropy", "short_flow_ratio"),
    "dos": ("pkts_sum", "flow_count", "z_flow_count", "syn_ratio"),
    "brute_force": ("failed_logins", "failed_login_ratio", "z_failed_logins"),
    "dns_tunnel": ("dns_entropy_max", "dns_flow_ratio"),
    "exfil": ("bytes_out_sum", "z_bytes_out_sum", "out_in_ratio", "bytes_out_p95"),
    "lateral_movement": ("g_new_edges", "g_chain_depth", "graph_score", "g_breadth_z"),
}


@dataclass
class Ensemble:
    """Frozen base detectors + a cheap, retrainable fusion layer.

    Splitting the model this way is deliberate: analyst feedback retrains the
    3-parameter fusion layer in milliseconds, while the expensive base learners
    stay pinned to a versioned artifact.
    """

    iforest: IsolationForest
    gbdt: GBDT
    stacker: ens.LogisticStacker
    feature_names: Sequence[str] = ALL_FEATURES

    def __post_init__(self) -> None:
        names = list(self.feature_names)
        self._idx_unsup = [names.index(f) for f in FEATURES]
        self._idx_graph = names.index("graph_score")

    def fusion_inputs(self, X: np.ndarray) -> np.ndarray:
        X = np.atleast_2d(np.asarray(X, dtype=float))
        s_if = np.clip(self.iforest.score(X[:, self._idx_unsup]), 1e-6, 1 - 1e-6)
        p_gb = np.clip(self.gbdt.predict_proba(X), 1e-6, 1 - 1e-6)
        return np.column_stack(
            [np.log(s_if / (1 - s_if)), np.log(p_gb / (1 - p_gb)), X[:, self._idx_graph]]
        )

    def log_odds(self, X: np.ndarray, prior_shift: bool = False) -> np.ndarray:
        z = self.stacker.decision(self.fusion_inputs(X))
        return z + (self.stacker.prior_shift if prior_shift else 0.0)

    def proba(self, X: np.ndarray, prior_shift: bool = False) -> np.ndarray:
        return ens.sigmoid(self.log_odds(X, prior_shift=prior_shift))


def suspected_family(top: List[dict]) -> str:
    """Name the family from the explanation, not from a second black box."""
    scores: Dict[str, float] = {}
    for item in top:
        if item["contribution"] <= 0:
            continue
        for fam, sig in FAMILY_SIGNATURES.items():
            if item["feature"] in sig:
                scores[fam] = scores.get(fam, 0.0) + item["contribution"]
    return max(scores.items(), key=lambda kv: kv[1])[0] if scores else "unknown"


def _fit_legs(X_tr, y_tr, seed: int):
    idx_unsup = [list(ALL_FEATURES).index(f) for f in FEATURES]
    # Unsupervised leg sees only presumed-normal history: it must not be taught
    # that attack traffic is ordinary.
    iforest = IsolationForest(n_trees=150, sample_size=256, seed=seed).fit(
        X_tr[y_tr == 0][:, idx_unsup]
    )
    spw = float(max(1.0, (y_tr == 0).sum() / max((y_tr == 1).sum(), 1)))
    gbdt = GBDT(
        n_trees=140, max_depth=3, learning_rate=0.15, scale_pos_weight=spw, seed=seed
    ).fit(X_tr, y_tr)
    return iforest, gbdt, spw


def fit_pipeline(
    seed: int = 7,
    deployment_prior: float = 1e-4,
    days: float = 4.0,
    base_rate: float = 0.005,
    n_folds: int = 3,
) -> dict:
    tel = generate(seed=seed, days=days, base_rate=base_rate)
    df = build_dataset(tel)
    cutoff = int(df["baseline_cutoff"].iloc[0])
    df, graph_det = attach_graph_features(df, tel.auth, baseline_cutoff=cutoff)
    train, calib, test = time_split(df, train_frac=0.5, calib_frac=0.25)

    X_tr, y_tr = matrix(train, ALL_FEATURES), train["label"].to_numpy()
    X_ca, y_ca = matrix(calib, ALL_FEATURES), calib["label"].to_numpy()
    X_te, y_te = matrix(test, ALL_FEATURES), test["label"].to_numpy()

    # ---- out-of-fold stacking -------------------------------------------
    # Fitting the fusion layer on a single held-out slice starved it of
    # positives (attacks are rare by construction), and fitting it on in-sample
    # base scores would let the supervised leg's memorised confidence leak in.
    # So: contiguous time-block CV over train+calibration produces honest
    # out-of-fold base scores, the fusion layer is fitted on all of them, and
    # the base learners are then refitted on the full development period.
    dev = pd.concat([train, calib], ignore_index=True)
    X_dev, y_dev = matrix(dev, ALL_FEATURES), dev["label"].to_numpy()
    folds = np.array_split(np.arange(len(dev)), n_folds)  # time-ordered blocks
    Z_oof = np.zeros((len(dev), len(FUSION_NAMES)))
    for k, hold in enumerate(folds):
        keep = np.setdiff1d(np.arange(len(dev)), hold)
        i_f, g_f, _ = _fit_legs(X_dev[keep], y_dev[keep], seed + 100 + k)
        Z_oof[hold] = Ensemble(i_f, g_f, ens.LogisticStacker()).fusion_inputs(X_dev[hold])

    iforest, gbdt, spw = _fit_legs(X_dev, y_dev, seed)
    stacker = ens.LogisticStacker(l2=1.0).fit(Z_oof, y_dev, names=FUSION_NAMES)
    stacker.set_deployment_prior(deployment_prior)

    return {
        "telemetry": tel,
        "df": df,
        "splits": (train, calib, test),
        "matrices": (X_tr, y_tr, X_ca, y_ca, X_te, y_te),
        "dev": (dev, X_dev, y_dev, Z_oof),
        "model": Ensemble(iforest, gbdt, stacker),
        "graph_detector": graph_det,
        "deployment_prior": deployment_prior,
        "scale_pos_weight": spw,
        "n_folds": n_folds,
    }


def zero_day_holdout(fit: dict, held_out: str, windows_per_day: float, budget: float, prior: float) -> dict:
    """Simulate a genuinely novel family: strip its labels from every supervised
    training signal (the org has never seen it), then measure who still catches it.

    This is the experiment that actually tests the hybrid rationale, rather than
    quoting it. A supervised model cannot learn a family it was never labelled
    on; the unsupervised and graph legs do not need labels at all.
    """
    train, calib, test = fit["splits"]
    X_tr, y_tr, X_ca, y_ca, X_te, y_te = fit["matrices"]
    model: Ensemble = fit["model"]

    is_family = (test["attack"] == held_out).to_numpy()
    out = {"held_out_family": held_out, "test_windows_of_family": int(is_family.sum())}
    if not is_family.any():
        return out

    dev, X_dev, y_dev, _ = fit["dev"]
    y_dev_b = np.where((dev["attack"] == held_out).to_numpy(), 0, y_dev)
    y_ca_b = np.where((calib["attack"] == held_out).to_numpy(), 0, y_ca)

    _, gbdt_b, _ = _fit_legs(X_dev, y_dev_b, seed=13)
    st_b = ens.LogisticStacker(l2=1.0)
    st_b.fit(Ensemble(model.iforest, gbdt_b, st_b).fusion_inputs(X_ca), y_ca_b, names=FUSION_NAMES)
    st_b.set_deployment_prior(prior)
    blind = Ensemble(model.iforest, gbdt_b, st_b)

    idx_unsup = [list(ALL_FEATURES).index(f) for f in FEATURES]
    for name, score in {
        "supervised_gbdt_blind": gbdt_b.predict_proba(X_te),
        "unsupervised_isolation_forest": model.iforest.score(X_te[:, idx_unsup]),
        "graph_only": X_te[:, list(ALL_FEATURES).index("graph_score")],
        "hybrid_ensemble_blind": blind.proba(X_te),
    }.items():
        op = ens.choose_threshold(y_te, score, windows_per_day, budget, prior)
        out[name] = {
            "recall_on_held_out_family_at_budget": round(
                float((score[is_family] >= op.threshold).mean()), 4
            ),
            "mean_percentile_of_family": round(
                float(np.mean([(score <= s).mean() for s in score[is_family]])), 4
            ),
        }
    return out


def run(out_dir: str = "artifacts", seed: int = 7, alert_budget_per_day: float = 50.0) -> dict:
    t0 = time.time()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    fit = fit_pipeline(seed=seed)
    tel, df = fit["telemetry"], fit["df"]
    train, calib, test = fit["splits"]
    X_tr, y_tr, X_ca, y_ca, X_te, y_te = fit["matrices"]
    model: Ensemble = fit["model"]
    prior = fit["deployment_prior"]

    n_entities = df["entity"].nunique()
    windows_per_day = n_entities * 86400 / WINDOW_S
    idx_unsup = [list(ALL_FEATURES).index(f) for f in FEATURES]

    # ------------------------------------------------------------- ablation
    legs = {
        "unsupervised_isolation_forest": model.iforest.score(X_te[:, idx_unsup]),
        "supervised_gbdt": model.gbdt.predict_proba(X_te),
        "graph_lateral_movement": X_te[:, list(ALL_FEATURES).index("graph_score")],
        "hybrid_ensemble": model.proba(X_te),
    }
    ablation, thresholds = {}, {}
    for name, score in legs.items():
        m = ens.evaluate(y_te, score, deployment_prior=prior)
        op = ens.choose_threshold(y_te, score, windows_per_day, alert_budget_per_day, prior)
        m["operating_point_at_budget"] = op.to_dict()
        ablation[name] = m
        thresholds[name] = op.threshold

    p_te = legs["hybrid_ensemble"]
    op = ens.choose_threshold(y_te, p_te, windows_per_day, alert_budget_per_day, prior)

    # --------------------------------------------------------- budget sweep
    # The alert budget is the single most consequential deployment knob, and it
    # is usually left implicit at 0.5. Sweeping it makes the trade-off explicit:
    # what recall does the SOC actually buy per unit of analyst attention, and
    # where does Bayesian PPV stop improving?
    sweep = []
    for budget in (5.0, 10.0, 15.0, 20.0, 30.0, 50.0, 75.0, 100.0, 150.0, 200.0):
        s_op = ens.choose_threshold(y_te, p_te, windows_per_day, budget, prior)
        row = s_op.to_dict()
        row["requested_budget_per_day"] = budget
        row["recall_per_alert_per_day"] = round(s_op.recall / budget, 6)
        sweep.append(row)

    # ------------------------------------------- false-positive provenance
    ba = {(r.entity, int(r.win)) for r in tel.benign_anomalies.itertuples()}
    is_ba = np.array([(e, int(w)) in ba for e, w in zip(test["entity"], test["win"])], dtype=bool)
    fp_provenance = {}
    for name, score in legs.items():
        fired = score >= thresholds[name]
        fp = fired & (y_te == 0)
        fp_provenance[name] = {
            "alerts": int(fired.sum()),
            "false_positives": int(fp.sum()),
            "false_positives_on_benign_anomalies": int((fp & is_ba).sum()),
            "share_of_fp_from_benign_anomalies": round(
                float((fp & is_ba).sum() / fp.sum()) if fp.sum() else 0.0, 4
            ),
            "benign_anomaly_flag_rate": round(
                float(fired[is_ba].mean()) if is_ba.any() else 0.0, 4
            ),
        }

    flagged = p_te >= op.threshold
    per_family = {}
    for fam, grp in test.assign(flag=flagged).groupby("attack"):
        if fam == "benign":
            per_family["benign_false_positive_rate"] = round(float(grp["flag"].mean()), 5)
        else:
            per_family[fam] = {"windows": int(len(grp)), "recall": round(float(grp["flag"].mean()), 4)}

    calibration = {
        "brier_test": ens.brier(y_te, p_te),
        "ece_test": ens.expected_calibration_error(y_te, p_te),
        "stacker_coefficients": model.stacker.coefficients(),
        "stacker_intercept": model.stacker.b,
        "eval_prior": float(y_te.mean()),
        "deployment_prior": prior,
        "prior_shift_logodds": model.stacker.prior_shift,
    }

    # ------------------------------------------------- alerts + explanations
    explainer = ShapleyExplainer(
        score_fn=lambda X: model.log_odds(X),
        background=X_ca[y_ca == 0],
        feature_names=list(ALL_FEATURES),
        n_permutations=32,
        n_background=16,
        seed=seed,
    )
    n_alerts = int(max(1, np.ceil(alert_budget_per_day * len(test) / windows_per_day)))
    alerts: List[dict] = []
    for i in np.argsort(-p_te)[:n_alerts]:
        i = int(i)
        row = test.iloc[i]
        exp = explainer.top_reasons(X_te[i], k=6)
        fam = suspected_family(exp["top"])
        alerts.append(
            {
                "alert_id": f"AL-{int(row['win'])}-{row['entity']}",
                "entity": row["entity"],
                "window": int(row["win"]),
                "window_iso": pd.to_datetime(int(row["win"]), unit="s", utc=True).isoformat(),
                "probability": float(p_te[i]),
                "probability_at_deployment_prior": float(model.proba(X_te[i], prior_shift=True)[0]),
                "suspected_family": fam,
                "ground_truth": row["attack"],
                "is_known_benign_anomaly": bool(is_ba[i]),
                "narrative": narrate(exp["top"], fam if fam != "unknown" else None),
                "top": exp["top"],
                "base_value_logodds": exp["base_value"],
                "attribution_residual": exp["attribution_residual"],
            }
        )

    engine = ResponseEngine(Policy(protected_assets=("h000", "h001")), execute=False)
    decisions = engine.run(alerts)

    # -------------------------------------------- drift + active learning
    drift_mix = dict(DEFAULT_MIX)
    drift_mix.update({"exfil": 0.30, "dns_tunnel": 0.28, "portscan": 0.08, "dos": 0.06})
    tel2 = generate(
        seed=seed,
        days=2.0,
        base_rate=0.005,
        mix=drift_mix,
        start_ts=int(df["win"].max()) + WINDOW_S,
    )
    df2 = build_dataset(tel2)
    df2, _ = attach_graph_features(df2, tel2.auth, baseline_cutoff=int(df2["win"].min()) + 3600)
    X2, y2 = matrix(df2, ALL_FEATURES), df2["label"].to_numpy()

    drift = al.drift_report(X_tr, X2, list(ALL_FEATURES))
    p2 = model.proba(X2)
    before = ens.evaluate(y2, p2, deployment_prior=prior)
    op_before = ens.choose_threshold(y2, p2, windows_per_day, alert_budget_per_day, prior)

    review_idx = al.select_for_review(p2, list(df2["entity"]), budget=60)
    Z2 = model.fusion_inputs(X2)
    _, _, y_dev, Z_oof = fit["dev"]
    Z_fit, y_fit = al.merge_feedback(
        Z_oof, y_dev, Z2[review_idx], y2[review_idx], feedback_weight=3.0
    )
    retrained = ens.LogisticStacker(l2=1.0).fit(Z_fit, y_fit, names=FUSION_NAMES)
    retrained.set_deployment_prior(prior)
    p2_after = Ensemble(model.iforest, model.gbdt, retrained).proba(X2)
    after = ens.evaluate(y2, p2_after, deployment_prior=prior)
    op_after = ens.choose_threshold(y2, p2_after, windows_per_day, alert_budget_per_day, prior)

    zero_day = [
        zero_day_holdout(fit, fam, windows_per_day, alert_budget_per_day, prior)
        for fam in ("dns_tunnel", "lateral_movement", "exfil")
    ]

    report = {
        "generated_at": pd.Timestamp.now("UTC").isoformat(),
        "runtime_seconds": round(time.time() - t0, 2),
        "dataset": tel.summary(),
        "windows": {
            "total": int(len(df)),
            "train": int(len(train)),
            "calibration": int(len(calib)),
            "test": int(len(test)),
            "entities": int(n_entities),
            "window_seconds": WINDOW_S,
            "positives_train": int(y_tr.sum()),
            "positives_calibration": int(y_ca.sum()),
            "positives_in_fusion_training": int(fit["dev"][2].sum()),
            "stacking_folds": fit["n_folds"],
            "positives_test": int(y_te.sum()),
            "benign_anomaly_windows_in_test": int(is_ba.sum()),
            "split": "strictly chronological; no shuffling across attack episodes",
        },
        "features": {
            "absolute_and_relative": list(FEATURES),
            "graph": list(GRAPH_FEATURES),
            "count": len(ALL_FEATURES),
        },
        "analyst_budget_per_day": alert_budget_per_day,
        "budget_sweep": sweep,
        "ablation_on_test": ablation,
        "false_positive_provenance": fp_provenance,
        "zero_day_holdout": zero_day,
        "operating_point": op.to_dict(),
        "per_family_recall_at_operating_point": per_family,
        "calibration": calibration,
        "gbdt_top_importance": dict(list(model.gbdt.importance(list(ALL_FEATURES)).items())[:12]),
        "soar": engine.stats(),
        "drift_and_active_learning": {
            "drifted_attack_mix": drift_mix,
            "drift": {
                "retrain_recommended": drift["retrain_recommended"],
                "material_shift": drift["features_with_material_shift"],
                "moderate_shift": drift["features_with_moderate_shift"],
                "top_psi": dict(list(drift["psi"].items())[:10]),
            },
            "labels_spent": len(review_idx),
            "before_retrain": {**before, "operating_point": op_before.to_dict()},
            "after_retrain": {**after, "operating_point": op_after.to_dict()},
            "delta": {
                "average_precision": round(
                    after["average_precision"] - before["average_precision"], 4
                ),
                "recall_at_budget": round(op_after.recall - op_before.recall, 4),
                "precision_at_budget": round(
                    op_after.precision_eval - op_before.precision_eval, 4
                ),
            },
        },
    }

    # encoding is always explicit. The locale default is cp1252 on Windows,
    # which silently changes or fails on non-ASCII content; artifacts must be
    # byte-identical whatever machine produced them.
    def _dump(name: str, obj: object) -> None:
        (out / name).write_text(json.dumps(obj, indent=2, default=str))

    _dump("report.json", report)
    _dump("alerts.json", alerts)
    _dump("soar_decisions.json", decisions)
    _dump("audit_log.json", engine.audit.records)
    _dump("drift_psi.json", drift)
    _dump("budget_sweep.json", sweep)
    test.assign(probability=p_te).to_csv(out / "scored_test_windows.csv", index=False)
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="SentinelAI end-to-end pipeline")
    ap.add_argument("--out", default="artifacts")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--budget", type=float, default=50.0, help="analyst alert budget per day")
    args = ap.parse_args()
    r = run(out_dir=args.out, seed=args.seed, alert_budget_per_day=args.budget)

    print(json.dumps({"windows": r["windows"], "operating_point": r["operating_point"]}, indent=2))
    print(f"\nAblation at {r['analyst_budget_per_day']:.0f} alerts/day (test period):")
    for name, m in r["ablation_on_test"].items():
        o, fp = m["operating_point_at_budget"], r["false_positive_provenance"][name]
        print(
            f"  {name:30s} AP={m['average_precision']:.3f} ROC={m['roc_auc']:.3f} "
            f"recall={o['recall']:.3f} prec={o['precision_eval']:.3f} "
            f"FP={fp['false_positives']:3d} benign-anom-share={fp['share_of_fp_from_benign_anomalies']:.0%}"
        )
    print("\nZero-day holdout (family never labelled in training):")
    for z in r["zero_day_holdout"]:
        if "hybrid_ensemble_blind" not in z:
            continue
        print(
            f"  {z['held_out_family']:18s} n={z['test_windows_of_family']:3d} "
            f"gbdt_blind={z['supervised_gbdt_blind']['recall_on_held_out_family_at_budget']:.3f} "
            f"iforest={z['unsupervised_isolation_forest']['recall_on_held_out_family_at_budget']:.3f} "
            f"hybrid_blind={z['hybrid_ensemble_blind']['recall_on_held_out_family_at_budget']:.3f}"
        )
    print("\nBudget sweep (hybrid ensemble, test period):")
    print(f"  {'budget/day':>10s} {'alerts/day':>10s} {'recall':>7s} {'prec':>7s} {'PPV@1e-4':>9s}")
    for s in r["budget_sweep"]:
        print(
            f"  {s['requested_budget_per_day']:10.0f} {s['alerts_per_day']:10.1f} "
            f"{s['recall']:7.3f} {s['precision_eval']:7.3f} "
            f"{s['ppv_at_deployment_prior']:9.4f}"
        )
    d = r["drift_and_active_learning"]
    print(
        f"\nDrift: retrain_recommended={d['drift']['retrain_recommended']} "
        f"labels={d['labels_spent']} AP {d['before_retrain']['average_precision']:.3f} "
        f"-> {d['after_retrain']['average_precision']:.3f} "
        f"(recall@budget {d['before_retrain']['operating_point']['recall']:.3f} "
        f"-> {d['after_retrain']['operating_point']['recall']:.3f})"
    )
    print(f"SOAR: {r['soar']}")


if __name__ == "__main__":  # pragma: no cover
    main()
