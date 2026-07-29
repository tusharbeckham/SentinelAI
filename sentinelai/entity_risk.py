"""Per-entity risk rollup and a compact hunting index.

The alert list answers "which windows fired". An analyst also needs "which host
should I look at first", which is a different question: an entity with eight
windows at p=0.7 is a better use of the next hour than one window at p=0.95.

Aggregation is noisy-or over an entity's strongest windows:

    risk = 1 - prod(1 - p_i)   for the top K windows

Noisy-or is used instead of max (which throws away corroboration) and instead of
mean (which punishes an entity for being observed often - a host with 300 quiet
windows and one screaming one would score near zero). It is capped at K windows
because an unbounded product drives every busy entity to 1.0, which would make
the ranking useless.

Independence between windows is assumed, and that assumption is wrong: windows of
the same attack episode are strongly correlated, so the risk is optimistic for a
sustained attack. It is a triage ranking, not a probability.

Both outputs are derived from artifacts/scored_test_windows.csv, so this runs
without retraining anything.

    python -m sentinelai.entity_risk --artifacts artifacts
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# Windows the noisy-or is allowed to accumulate over.
TOP_K = 8

# Buckets in the per-entity sparkline. 48 keeps the payload small while still
# showing the shape of an episode across the test period.
TIMELINE_BUCKETS = 48

# Fields exposed to the hunting query language. Deliberately a subset: these are
# the ones an analyst reasons about, and shipping all 45 columns to the browser
# would quadruple the payload for columns nobody queries.
HUNT_FIELDS: tuple[str, ...] = (
    "entity",
    "win",
    "probability",
    "attack",
    "flow_count",
    "bytes_out_sum",
    "distinct_dsts",
    "distinct_dports",
    "mean_pkt_size",
    "syn_ratio",
    "failed_logins",
    "failed_login_ratio",
    "dns_entropy_max",
    "ext_dst_ratio",
    "admin_port_ratio",
    "night_flag",
    "graph_score",
    "roll_admin_flows_1h",
    "roll_dst_reach_1h",
)

# Cap on rows shipped to the browser for client-side hunting.
HUNT_ROW_CAP = 3000
HUNT_SEED = 20260729


def noisy_or(probabilities: np.ndarray, top_k: int = TOP_K) -> float:
    """Combine window probabilities into one entity risk in [0, 1)."""
    if probabilities.size == 0:
        return 0.0
    top = np.sort(np.asarray(probabilities, dtype=float))[::-1][:top_k]
    # Clip strictly below 1 so a single saturated window cannot make every
    # downstream comparison degenerate to exactly 1.0.
    top = np.clip(top, 0.0, 1.0 - 1e-12)
    return float(1.0 - np.prod(1.0 - top))


def _timeline(windows: np.ndarray, probs: np.ndarray, lo: int, hi: int) -> list[float]:
    """Max probability per time bucket, for a sparkline."""
    out = [0.0] * TIMELINE_BUCKETS
    if windows.size == 0 or hi <= lo:
        return out
    span = hi - lo
    for w, p in zip(windows, probs):
        idx = int((float(w) - lo) / span * (TIMELINE_BUCKETS - 1))
        idx = max(0, min(TIMELINE_BUCKETS - 1, idx))
        if p > out[idx]:
            out[idx] = float(p)
    return out


def build_entity_risk(scored: pd.DataFrame, threshold: float) -> dict[str, Any]:
    """Rank entities by noisy-or risk over their strongest windows."""
    if "entity" not in scored.columns or "probability" not in scored.columns:
        raise ValueError("scored windows must carry 'entity' and 'probability' columns")

    lo = int(scored["win"].min())
    hi = int(scored["win"].max())

    entities: list[dict[str, Any]] = []
    for entity, group in scored.groupby("entity", sort=False):
        probs = group["probability"].to_numpy(dtype=float)
        wins = group["win"].to_numpy()
        alerts = int((probs >= threshold).sum())

        families: list[str] = []
        truth_windows = 0
        if "attack" in group.columns:
            attacks = group.loc[group["attack"] != "benign", "attack"]
            truth_windows = int(attacks.shape[0])
            families = sorted(set(attacks.astype(str)))

        entities.append(
            {
                "entity": str(entity),
                "windows": int(probs.size),
                "alerts": alerts,
                "risk": round(noisy_or(probs), 6),
                "max_probability": round(float(probs.max()), 6),
                "mean_probability": round(float(probs.mean()), 6),
                # Ground truth is included so the console can show where the
                # ranking is wrong, not just where it is right.
                "truth_attack_windows": truth_windows,
                "truth_families": families,
                "timeline": [round(v, 4) for v in _timeline(wins, probs, lo, hi)],
            }
        )

    entities.sort(key=lambda e: (-e["risk"], -e["max_probability"], e["entity"]))
    ranked_true = [i for i, e in enumerate(entities, start=1) if e["truth_attack_windows"] > 0]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": f"noisy-or over each entity's top {TOP_K} windows",
        "assumption_violated": (
            "windows within one attack episode are correlated, so risk is "
            "optimistic for sustained attacks; treat it as a ranking, not a probability"
        ),
        "threshold": threshold,
        "top_k": TOP_K,
        "window_span": {"first": lo, "last": hi, "buckets": TIMELINE_BUCKETS},
        "entity_count": len(entities),
        # Where the truly-compromised entities land in the ranking. If this is
        # not near the top, the rollup is not doing its job.
        "ranks_of_true_positive_entities": ranked_true,
        "entities": entities,
    }


def build_hunt_index(scored: pd.DataFrame, threshold: float) -> dict[str, Any]:
    """A sampled, narrow projection of scored windows for in-browser hunting."""
    fields = [f for f in HUNT_FIELDS if f in scored.columns]

    interesting = scored["probability"] >= threshold
    if "attack" in scored.columns:
        interesting = interesting | (scored["attack"] != "benign")

    kept = scored.loc[interesting]
    remaining = HUNT_ROW_CAP - len(kept)
    if remaining > 0:
        rest = scored.loc[~interesting]
        if len(rest) > remaining:
            # Seeded so the shipped index is reproducible byte for byte.
            rest = rest.sample(n=remaining, random_state=HUNT_SEED)
        kept = pd.concat([kept, rest])

    kept = kept.sort_values("probability", ascending=False)
    rows = json.loads(kept[fields].to_json(orient="records"))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fields": fields,
        "threshold": threshold,
        "total_scored_windows": int(len(scored)),
        "rows_in_index": len(rows),
        "sampling": (
            "every window at or above threshold and every true-attack window is "
            f"included; benign windows are a seeded random sample up to {HUNT_ROW_CAP} rows"
        ),
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build entity risk rollup and hunting index")
    ap.add_argument("--artifacts", default="artifacts", help="artifacts directory")
    args = ap.parse_args(argv)

    root = Path(args.artifacts)
    csv_path = root / "scored_test_windows.csv"
    report_path = root / "report.json"
    if not csv_path.exists():
        raise SystemExit(
            f"missing {csv_path}\nRun: python -m sentinelai.pipeline --out {root} --budget 50"
        )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    threshold = float(report["operating_point"]["threshold"])
    scored = pd.read_csv(csv_path)

    risk = build_entity_risk(scored, threshold)
    index = build_hunt_index(scored, threshold)

    (root / "entity_risk.json").write_text(json.dumps(risk, indent=2), encoding="utf-8")
    (root / "hunt_index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")

    top = risk["entities"][:8]
    print(f"entities ranked: {risk['entity_count']}")
    print(f"true-positive entity ranks: {risk['ranks_of_true_positive_entities']}")
    print(f"hunt index rows: {index['rows_in_index']} of {index['total_scored_windows']}")
    print()
    print(f"{'rank':>4}  {'entity':<8} {'risk':>8} {'alerts':>7} {'truth':>6}  families")
    for i, e in enumerate(top, start=1):
        fams = ",".join(e["truth_families"]) or "-"
        print(
            f"{i:>4}  {e['entity']:<8} {e['risk']:>8.4f} {e['alerts']:>7} "
            f"{e['truth_attack_windows']:>6}  {fams}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
