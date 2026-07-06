"""Active-learning feedback loop and drift monitoring.

Two production realities:

1. CONCEPT DRIFT. Attacker behaviour and network behaviour both change, so a
   static model decays. We monitor drift with the Population Stability Index
   (PSI) per feature between the reference (training) window and the live window:
       PSI = sum_b (p_b - q_b) * ln(p_b / q_b)
   Conventional reading: <0.10 stable, 0.10-0.25 moderate shift, >0.25 material
   shift -> retrain. PSI is used because it needs no labels, which is the whole
   point: labels arrive late in security.

2. LABEL SCARCITY. Analyst verdicts are the scarcest resource in a SOC, so we
   spend them where they are worth most. `select_for_review` combines
   uncertainty sampling (entropy near the decision boundary) with a diversity
   term (spread across entities) and always keeps the top-risk items an analyst
   would look at anyway. The fusion layer is then refit on
   calibration-plus-verdict data with verdict rows up-weighted, which is a cheap,
   bounded retrain (the base detectors stay frozen, so a poisoned batch of
   verdicts cannot silently rewrite the whole model).
"""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

import numpy as np


def psi(reference: np.ndarray, current: np.ndarray, bins: int = 10) -> float:
    ref = np.asarray(reference, dtype=float)
    cur = np.asarray(current, dtype=float)
    if len(ref) < 2 or len(cur) < 2:
        return 0.0
    qs = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    if len(qs) < 3:
        return 0.0
    edges = qs[1:-1]
    p = np.bincount(np.digitize(ref, edges), minlength=len(edges) + 1).astype(float)
    q = np.bincount(np.digitize(cur, edges), minlength=len(edges) + 1).astype(float)
    p = np.clip(p / p.sum(), 1e-6, None)
    q = np.clip(q / q.sum(), 1e-6, None)
    return float(np.sum((q - p) * np.log(q / p)))


def drift_report(
    reference: np.ndarray, current: np.ndarray, names: Sequence[str], warn: float = 0.10, alarm: float = 0.25
) -> dict:
    scores = {n: psi(reference[:, i], current[:, i]) for i, n in enumerate(names)}
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    material = [n for n, v in ranked if v > alarm]
    return {
        "psi": {n: round(v, 4) for n, v in ranked},
        "features_with_material_shift": material,
        "features_with_moderate_shift": [n for n, v in ranked if warn < v <= alarm],
        "retrain_recommended": bool(material),
        "thresholds": {"warn": warn, "alarm": alarm},
    }


def select_for_review(
    probs: np.ndarray,
    entities: Sequence[str],
    budget: int,
    boundary: float = 0.5,
    top_risk_share: float = 0.4,
) -> List[int]:
    """Pick indices for analyst labelling: top-risk + most-uncertain, diversified."""
    probs = np.asarray(probs, dtype=float)
    budget = int(min(budget, len(probs)))
    n_top = int(round(budget * top_risk_share))
    chosen: List[int] = [int(i) for i in np.argsort(-probs)[:n_top]]
    picked = set(chosen)
    uncertainty = -np.abs(probs - boundary)

    # Diversity cap scales with how many entities actually exist, so a small
    # estate cannot starve the review budget (a fixed cap silently returned
    # fewer labels than requested).
    n_entities = max(1, len(set(entities)))
    cap = max(3, int(np.ceil(2.0 * budget / n_entities)))
    seen: Dict[str, int] = {}
    for i in chosen:
        seen[entities[i]] = seen.get(entities[i], 0) + 1

    for pass_cap in (cap, None):  # second pass ignores the cap to fill the budget
        for j in np.argsort(-uncertainty):
            if len(chosen) >= budget:
                break
            i = int(j)
            if i in picked:
                continue
            if pass_cap is not None and seen.get(entities[i], 0) >= pass_cap:
                continue
            chosen.append(i)
            picked.add(i)
            seen[entities[i]] = seen.get(entities[i], 0) + 1
        if len(chosen) >= budget:
            break
    return chosen[:budget]


def merge_feedback(
    X_calib: np.ndarray,
    y_calib: np.ndarray,
    X_fb: np.ndarray,
    y_fb: np.ndarray,
    feedback_weight: float = 3.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """Duplicate-weight analyst verdicts (keeps the stacker's IRLS solver simple)."""
    reps = max(1, int(round(feedback_weight)))
    X = np.vstack([X_calib] + [X_fb] * reps)
    y = np.concatenate([y_calib] + [y_fb] * reps)
    return X, y
