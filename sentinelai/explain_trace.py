"""Forensic trace of a single alert: every stage from packets to playbook.

The console's explainer renders this file and nothing else. That constraint is
deliberate. A hand-written walkthrough of "how our detector works" drifts away
from the model the moment the model changes, and the reader has no way to tell.
Everything here is pulled from the same fitted objects that produced the alert,
so the explanation cannot silently disagree with the system it describes.

The trace also has to be able to say something unflattering. The alert this
module is pointed at by default is a case where the ensemble fired on the right
window for the wrong reason: it named the family `dos` from an inflated
`mean_pkt_size` attribution while 45 failed logins -- the actual brute-force
signature -- sat in the same feature vector with almost no attributed credit.
Hiding that would make a nicer demo and a dishonest one, so `disagreement` and
`missed_evidence` are first-class fields rather than a footnote.

The module deliberately takes plain arrays and callables instead of importing
the pipeline's `Ensemble`. That keeps `pipeline -> explain_trace` a one-way
dependency (no import cycle) and lets the tests exercise the arithmetic with a
three-line fake fusion function instead of a fitted forest.
"""

from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Mapping, Sequence, Tuple

import numpy as np

# The narrative spine. The console walks these in order; each stage in the
# returned trace carries the same `id` so the UI never hard-codes an order.
STAGE_ORDER: Tuple[str, ...] = (
    "telemetry",
    "features",
    "legs",
    "fusion",
    "threshold",
    "response",
)

# Feature groups, used only for presentation: the explainer shows the vector as
# six labelled bands instead of 40 anonymous numbers.
FEATURE_GROUPS: Dict[str, Tuple[str, ...]] = {
    "volume": (
        "flow_count",
        "bytes_out_sum",
        "bytes_in_sum",
        "bytes_out_p95",
        "pkts_sum",
        "mean_pkt_size",
        "out_in_ratio",
    ),
    "reach": (
        "distinct_dports",
        "distinct_dsts",
        "port_entropy",
        "ext_dst_ratio",
        "admin_port_ratio",
    ),
    "shape": ("syn_ratio", "short_flow_ratio", "night_flag"),
    "identity": (
        "auth_events",
        "failed_logins",
        "failed_login_ratio",
        "distinct_auth_targets",
        "interactive_logons",
    ),
    "naming": ("dns_flow_ratio", "dns_entropy_max"),
    "graph": (
        "g_new_edges",
        "g_rarity",
        "g_breadth_z",
        "g_chain_depth",
        "g_new_user_host",
        "graph_score",
    ),
}

# A raw value only means something next to the population it came from, so the
# explainer needs a percentile for anything it wants to call "extreme".
EXTREME_PERCENTILE = 0.99


def sigmoid(z: float) -> float:
    """Stable logistic. Guards the overflow that `exp` hits past |z| ~ 700."""
    if z >= 0.0:
        return float(1.0 / (1.0 + np.exp(-z)))
    e = float(np.exp(z))
    return e / (1.0 + e)


def percentile_of(column: Iterable[float], value: float) -> float:
    """Fraction of the population at or below `value`.

    Ties count as "at or below" on purpose: a window sitting on a hard ceiling
    (`night_flag == 1`, a saturated ratio) should read as high, not as median.
    """
    arr = np.asarray(list(column), dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.count_nonzero(arr <= value) / arr.size)


def select_alert(alerts: Sequence[Mapping], prefer_disagreement: bool = True) -> Mapping:
    """Pick the alert worth explaining.

    Not the highest-scoring one. The most instructive alert is a true positive
    whose *named family* is wrong: it is simultaneously proof the detector works
    and proof its reasoning is shakier than its ranking. Falls back to the
    top-ranked alert when every family label happens to agree.
    """
    if not alerts:
        raise ValueError("no alerts to explain")
    if prefer_disagreement:
        for a in alerts:
            truth = a.get("ground_truth")
            if truth and truth != "benign" and a.get("suspected_family") != truth:
                return a
    return alerts[0]


def leg_breakdown(
    fusion_row: Sequence[float],
    coefficients: Mapping[str, float],
    intercept: float,
    ablation: Mapping[str, Mapping],
    mean: Sequence[float] | None = None,
    scale: Sequence[float] | None = None,
) -> Dict:
    """Turn the stacker's three inputs into an auditable sum.

    The fusion layer is a 3-parameter logistic regression precisely so this
    step can be shown as arithmetic rather than asserted. The terms plus the
    intercept are the log-odds, and the caller can check the reconstruction
    against the published probability instead of trusting this function.

    The subtlety that makes or breaks the arithmetic: the stacker standardises
    its inputs before applying weights (`Z = (X - mu) / sd`, then `Z @ w + b`).
    So a term is `coefficient x standardised input`, NOT `coefficient x raw
    score`. Multiplying by the raw score reproduces a plausible-looking number
    that is simply wrong -- it misstated this alert's log-odds by 0.9 and its
    probability by ~0.02. `mean` and `scale` default to the identity transform
    so callers with an unstandardised stacker still get exact arithmetic.
    """
    # Mirrors pipeline.FUSION_NAMES. The graph leg was removed from the
    # fusion after it was measured to cost average precision; it survives as
    # an input feature to the GBDT, so it shows up in the feature
    # attributions rather than as a leg of its own.
    order = ("iforest_logit", "gbdt_logit")
    labels = {
        "iforest_logit": "isolation forest",
        "gbdt_logit": "gradient boosting",
    }
    ablation_key = {
        "iforest_logit": "unsupervised_isolation_forest",
        "gbdt_logit": "supervised_gbdt",
    }
    n_legs = len(order)
    row = [float(v) for v in np.asarray(fusion_row, dtype=float).ravel()[:n_legs]]
    mu = (
        np.zeros(n_legs)
        if mean is None
        else np.asarray(mean, dtype=float).ravel()[:n_legs]
    )
    sd = (
        np.ones(n_legs)
        if scale is None
        else np.asarray(scale, dtype=float).ravel()[:n_legs]
    )
    # A zero scale would divide by zero; the stacker itself clamps degenerate
    # columns to 1.0 during fit, so mirror that rather than emitting infinities.
    sd = np.where(np.abs(sd) < 1e-9, 1.0, sd)

    legs: List[Dict] = []
    for name, value, m, s in zip(order, row, mu, sd):
        coef = float(coefficients[name])
        standardized = (value - float(m)) / float(s)
        abl = ablation.get(ablation_key[name], {}) or {}
        op = abl.get("operating_point_at_budget", {}) or {}
        legs.append(
            {
                "input": name,
                "label": labels[name],
                "value": value,
                "standardized": standardized,
                "mean": float(m),
                "scale": float(s),
                "coefficient": coef,
                "term": coef * standardized,
                # How good is this leg *alone*? Average precision, not AUC:
                # at a 0.5% base rate, ROC-AUC flatters everything.
                "average_precision": abl.get("average_precision"),
                "roc_auc": abl.get("roc_auc"),
                "recall_at_budget": op.get("recall"),
                "precision_at_budget": op.get("precision_eval"),
            }
        )

    total_terms = float(sum(leg["term"] for leg in legs))
    log_odds = total_terms + float(intercept)
    return {
        "legs": legs,
        "intercept": float(intercept),
        "terms_sum": total_terms,
        "log_odds": log_odds,
        "probability": sigmoid(log_odds),
    }


def family_evidence(
    row: Mapping[str, float],
    attributions: Sequence[Mapping],
    family_signatures: Mapping[str, Sequence[str]],
    percentile: Callable[[str, float], float] | None = None,
) -> List[Dict]:
    """Score every attack family two independent ways and compare them.

    `attributed` is what the model actually leaned on: the sum of positive
    Shapley contributions landing on that family's signature features. This is
    the same rule `pipeline.suspected_family` uses, so the trace reproduces the
    published label instead of second-guessing it.

    `corroboration` ignores the model entirely and asks how extreme this
    window's signature features are within the test population. When the two
    disagree, the gap is the interesting part -- a family can be screaming in
    the raw percentiles while receiving no attributed credit at all.
    """
    attr_by_feature: Dict[str, float] = {}
    for item in attributions:
        feat = item.get("feature")
        if feat is None:
            continue
        attr_by_feature[str(feat)] = float(item.get("contribution", 0.0))

    out: List[Dict] = []
    for family, signature in family_signatures.items():
        attributed = 0.0
        features: List[Dict] = []
        pcts: List[float] = []
        for feat in signature:
            if feat not in row:
                continue
            value = float(row[feat])
            contribution = attr_by_feature.get(feat, 0.0)
            if contribution > 0:
                attributed += contribution
            pct = percentile(feat, value) if percentile is not None else float("nan")
            if np.isfinite(pct):
                pcts.append(pct)
            features.append(
                {
                    "feature": feat,
                    "value": value,
                    "percentile": None if not np.isfinite(pct) else pct,
                    "attributed": contribution,
                    "attributed_credit": contribution > 0,
                }
            )
        out.append(
            {
                "family": family,
                "attributed": attributed,
                "corroboration": float(np.mean(pcts)) if pcts else None,
                "features": features,
            }
        )

    out.sort(key=lambda f: (-f["attributed"], -(f["corroboration"] or 0.0)))
    return out


def missed_evidence(evidence: Sequence[Mapping], family: str | None) -> List[Dict]:
    """Signature features of the true family that the model ignored.

    "Ignored" means: sitting in the top percentile of the population while
    receiving no positive attribution. These are the features a human analyst
    would have led with, and they are exactly what a shortcut-learning model
    leaves on the table.
    """
    if not family:
        return []
    for fam in evidence:
        if fam["family"] != family:
            continue
        return [
            dict(f)
            for f in fam["features"]
            if not f["attributed_credit"]
            and f["percentile"] is not None
            and f["percentile"] >= EXTREME_PERCENTILE
        ]
    return []


def feature_bands(
    row: Mapping[str, float],
    percentile: Callable[[str, float], float] | None = None,
    attributions: Sequence[Mapping] = (),
) -> List[Dict]:
    """The 40-dim vector as six labelled bands, each sorted by extremeness."""
    attributed = {str(i.get("feature")) for i in attributions if float(i.get("contribution", 0)) > 0}
    bands: List[Dict] = []
    for group, feats in FEATURE_GROUPS.items():
        members: List[Dict] = []
        for feat in feats:
            if feat not in row:
                continue
            value = float(row[feat])
            pct = percentile(feat, value) if percentile is not None else float("nan")
            members.append(
                {
                    "feature": feat,
                    "value": value,
                    "percentile": None if not np.isfinite(pct) else pct,
                    "attributed_credit": feat in attributed,
                }
            )
        members.sort(key=lambda m: -(m["percentile"] or 0.0))
        bands.append({"group": group, "features": members})
    return bands


def build_trace(
    *,
    alert: Mapping,
    row: Mapping[str, float],
    fusion_row: Sequence[float],
    coefficients: Mapping[str, float],
    intercept: float,
    prior_shift: float,
    family_signatures: Mapping[str, Sequence[str]],
    ablation: Mapping[str, Mapping],
    standardizer: Mapping[str, Sequence[float]] | None = None,
    operating_point: Mapping,
    dataset: Mapping,
    soar_decision: Mapping | None = None,
    percentile: Callable[[str, float], float] | None = None,
    top_importance: Mapping[str, float] | None = None,
    rank: int | None = None,
    total_windows: int | None = None,
    audit_chain_valid: bool | None = None,
) -> Dict:
    """Assemble the full six-stage trace for one alert.

    Returns a JSON-safe dict. Every stage is self-describing (`id`, `title`,
    `caption`) so the front end is a renderer, not a second source of truth
    about what the pipeline does.
    """
    truth = alert.get("ground_truth")
    suspected = alert.get("suspected_family")
    attributions = list(alert.get("top", []) or [])

    fusion = leg_breakdown(
        fusion_row,
        coefficients,
        intercept,
        ablation,
        mean=(standardizer or {}).get("mean"),
        scale=(standardizer or {}).get("scale"),
    )
    evidence = family_evidence(row, attributions, family_signatures, percentile)
    missed = missed_evidence(evidence, truth if truth and truth != "benign" else None)

    threshold = float(operating_point.get("threshold", float("nan")))
    probability = float(alert.get("probability", fusion["probability"]))
    deployment_p = alert.get("probability_at_deployment_prior")
    if deployment_p is None:
        deployment_p = sigmoid(fusion["log_odds"] + float(prior_shift))

    # The fusion arithmetic must reproduce the published probability. If it
    # does not, the trace is describing a different model than the one that
    # fired, and that is worth surfacing rather than smoothing over.
    reconstruction_error = abs(fusion["probability"] - probability)

    stages: List[Dict] = [
        {
            "id": "telemetry",
            "title": "Raw telemetry",
            "caption": (
                "One entity, one 5-minute window. Flow records and authentication "
                "events only -- no payload, nothing that needs to be decrypted."
            ),
            "entity": alert.get("entity"),
            "window": alert.get("window"),
            "window_iso": alert.get("window_iso"),
            "corpus": {
                "flows": dataset.get("flows"),
                "auth_events": dataset.get("auth_events"),
                "hosts": dataset.get("n_hosts"),
                "users": dataset.get("n_users"),
                "days": dataset.get("days"),
                "window_s": dataset.get("window_s"),
                "base_rate": dataset.get("planned_base_rate"),
            },
            "observed": [
                {"label": "flows in window", "value": row.get("flow_count")},
                {"label": "packets", "value": row.get("pkts_sum")},
                {"label": "auth events", "value": row.get("auth_events")},
                {"label": "failed logins", "value": row.get("failed_logins")},
                {"label": "distinct destinations", "value": row.get("distinct_dsts")},
                {"label": "night window", "value": row.get("night_flag")},
            ],
        },
        {
            "id": "features",
            "title": "Feature vector",
            "caption": (
                "40 features: absolute counts, per-entity z-scores against that "
                "entity's own baseline, 1-hour rolling reach, and five graph "
                "features. Per-entity standardisation is what stops a busy "
                "server from looking permanently anomalous."
            ),
            "count": len([f for f in row if not str(f).startswith("_")]),
            "bands": feature_bands(row, percentile, attributions),
            "model_importance": dict(top_importance or {}),
        },
        {
            "id": "legs",
            "title": "Two detectors score it",
            "caption": (
                "An isolation forest that never saw a label and a "
                "gradient-boosted tree that saw every label. They fail "
                "differently, which is the only reason ensembling them "
                "helps. A third auth-graph leg voted here until v3.3.0; it "
                "was measured to lower average precision and now feeds the "
                "booster as features instead of casting its own vote."
            ),
            "legs": fusion["legs"],
        },
        {
            "id": "fusion",
            "title": "Calibrated fusion",
            "caption": (
                "A 3-parameter logistic stacker. Small on purpose: analyst "
                "feedback retrains it in milliseconds while the expensive base "
                "learners stay pinned to a versioned artifact."
            ),
            "terms": [
                {
                    "label": leg["label"],
                    "coefficient": leg["coefficient"],
                    "value": leg["value"],
                    "standardized": leg["standardized"],
                    "mean": leg["mean"],
                    "scale": leg["scale"],
                    "term": leg["term"],
                }
                for leg in fusion["legs"]
            ],
            "caption_standardisation": (
                "Each term is the coefficient times the *standardised* input: "
                "the stacker centres and scales its three inputs against the "
                "fusion-training distribution before weighting them. Reading "
                "the weights against raw scores is the usual way to misread a "
                "logistic model."
            ),
            "intercept": fusion["intercept"],
            "log_odds": fusion["log_odds"],
            "probability": fusion["probability"],
            "published_probability": probability,
            "reconstruction_error": reconstruction_error,
            "prior_shift_logodds": float(prior_shift),
            "probability_at_deployment_prior": float(deployment_p),
            "caption_prior": (
                "The evaluation set is enriched to a 0.5% base rate; production "
                "runs nearer 0.01%. Shifting the intercept by the log-odds "
                "difference is what keeps this number honest off the bench."
            ),
        },
        {
            "id": "threshold",
            "title": "The budget gate",
            "caption": (
                "The threshold is not tuned for F1. It is whatever value spends "
                "exactly the analyst's daily alert budget, because a detector "
                "nobody has time to read has a recall of zero."
            ),
            "threshold": threshold,
            "probability": probability,
            "margin": probability - threshold,
            "fired": probability >= threshold,
            "alerts_per_day": operating_point.get("alerts_per_day"),
            "budget_per_day": operating_point.get("budget_per_day"),
            "recall": operating_point.get("recall"),
            "precision_eval": operating_point.get("precision_eval"),
            "fpr": operating_point.get("fpr"),
            "ppv_at_deployment_prior": operating_point.get("ppv_at_deployment_prior"),
            "rank": rank,
            "total_windows": total_windows,
        },
        {
            "id": "response",
            "title": "Policy, not autonomy",
            "caption": (
                "Containment is gated on calibrated confidence and asset "
                "criticality, and every decision is hash-chained. The model "
                "recommends; the policy decides; the chain proves what happened."
            ),
            "mode": (soar_decision or {}).get("mode"),
            "playbook": (soar_decision or {}).get("playbook"),
            "actions": (soar_decision or {}).get("actions"),
            "rationale": (soar_decision or {}).get("rationale"),
            "audit_chain_valid": audit_chain_valid,
        },
    ]

    return {
        "alert_id": alert.get("alert_id"),
        "entity": alert.get("entity"),
        "window": alert.get("window"),
        "window_iso": alert.get("window_iso"),
        "probability": probability,
        "narrative": alert.get("narrative"),
        "suspected_family": suspected,
        "ground_truth": truth,
        "stage_order": list(STAGE_ORDER),
        "stages": stages,
        "attributions": attributions,
        "base_value_logodds": alert.get("base_value_logodds"),
        "attribution_residual": alert.get("attribution_residual"),
        "family_evidence": evidence,
        "disagreement": {
            "detected": bool(truth and truth != "benign" and suspected != truth),
            "suspected": suspected,
            "truth": truth,
            "missed_evidence": missed,
            "explanation": (
                "The window is a true positive: it was ranked into the alert "
                "budget and it is genuinely malicious. The family label is "
                "still wrong, because attribution concentrated on a feature "
                "the booster over-relies on while the true family's signature "
                "features drew almost no credit. Ranking and reasoning are "
                "separate failures and this alert only fixes one of them."
            )
            if (truth and truth != "benign" and suspected != truth)
            else "Attributed family agrees with ground truth for this alert.",
        },
    }
