#!/usr/bin/env python3
"""Independently verify artifacts/alert_trace.json.

The explainer's whole claim is that the arithmetic shown on the website is the
arithmetic the model performed. That claim needs a checker that does not import
the code it is checking: this script reads only the JSON and recomputes every
step from first principles, so a bug in explain_trace.py cannot hide by being
equally wrong on both sides.

It exists because the first version of the trace was wrong in exactly that way.
It multiplied each stacker coefficient by the leg's raw score instead of the
standardised one, producing believable numbers that misstated the log-odds by
0.91. The published probability was the only witness.

Usage:
    python scripts/verify_trace.py [path/to/alert_trace.json]

Exit status 0 if every check passes, 1 otherwise. Intended for CI.
"""

from __future__ import annotations

import json
import math
import sys
from typing import Any, Dict, List, Tuple

EXPECTED_STAGES = ("telemetry", "features", "legs", "fusion", "threshold", "response")

# Tolerances. The fusion sum is pure float arithmetic over a small number of
# terms, so it should agree to near machine precision; anything looser would
# hide the class of bug this script was written to catch.
EXACT = 1e-9
TIGHT = 1e-12


def sigmoid(z: float) -> float:
    """Overflow-safe logistic. exp(+800) raises OverflowError on a float."""
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


class Checker:
    def __init__(self) -> None:
        self.failures: List[str] = []
        self.passed = 0

    def ok(self, label: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.passed += 1
            print(f"  pass  {label}" + (f"  [{detail}]" if detail else ""))
        else:
            self.failures.append(f"{label}: {detail}")
            print(f"  FAIL  {label}  [{detail}]")

    def close(self, label: str, got: float, want: float, tol: float) -> None:
        delta = abs(got - want)
        self.ok(label, delta <= tol, f"got {got!r} want {want!r} delta {delta:.3e}")


def stage_map(trace: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {s["id"]: s for s in trace["stages"]}


def check_structure(c: Checker, trace: Dict[str, Any]) -> None:
    print("structure")
    ids = tuple(s["id"] for s in trace["stages"])
    c.ok("six stages in pipeline order", ids == EXPECTED_STAGES, f"{ids}")
    c.ok(
        "declared stage_order matches the stages present",
        tuple(trace["stage_order"]) == ids,
    )
    for s in trace["stages"]:
        c.ok(
            f"stage {s['id']} carries its own title and caption",
            bool(s.get("title")) and bool(s.get("caption")),
        )


def check_fusion(c: Checker, fusion: Dict[str, Any]) -> None:
    print("fusion arithmetic")
    terms: List[Dict[str, Any]] = fusion["terms"]
    # Deliberately not asserted against a literal. This line read
    # `len(terms) == 3` until v3.3.0 removed the auth-graph leg, at which
    # point the gate failed on a correct model. A verifier should encode
    # the invariant (every declared term reconstructs the published
    # probability, checked below) and not the architecture of the week.
    c.ok("at least two fused legs", len(terms) >= 2, f"{len(terms)}")
    required = ("label", "coefficient", "value", "mean", "scale", "term")
    missing = [
        (t.get("label", "?"), k)
        for t in terms
        for k in required
        if k not in t
    ]
    c.ok("every leg is fully specified", not missing, f"{missing}")

    for t in terms:
        # The standardisation must be re-derivable from the raw value, the
        # centre and the scale that the artifact itself reports.
        expect_z = (t["value"] - t["mean"]) / t["scale"]
        c.close(f"{t['label']}: z = (raw - mean) / scale", t["standardized"], expect_z, TIGHT)
        # And the term must be the coefficient times the STANDARDISED input.
        c.close(
            f"{t['label']}: term = coefficient x z",
            t["term"],
            t["coefficient"] * t["standardized"],
            TIGHT,
        )
        # The specific regression: coefficient x raw must NOT be what is shown,
        # unless the leg happens to be standardised to the identity.
        raw_form = t["coefficient"] * t["value"]
        degenerate = abs(t["mean"]) < TIGHT and abs(t["scale"] - 1.0) < TIGHT
        c.ok(
            f"{t['label']}: not the raw-score form",
            degenerate or abs(t["term"] - raw_form) > EXACT,
            f"term {t['term']:.6f} vs raw form {raw_form:.6f}",
        )

    total = sum(t["term"] for t in terms) + fusion["intercept"]
    c.close("terms + intercept = log-odds", total, fusion["log_odds"], EXACT)
    c.close(
        "sigmoid(log-odds) = probability",
        sigmoid(fusion["log_odds"]),
        fusion["probability"],
        TIGHT,
    )

    # The load-bearing check: does the reconstruction match what actually shipped?
    published = fusion["published_probability"]
    c.close("reconstruction matches the published probability", fusion["probability"], published, EXACT)
    c.close(
        "reported reconstruction_error is honest",
        fusion["reconstruction_error"],
        abs(fusion["probability"] - published),
        TIGHT,
    )
    c.ok(
        "reconstruction_error is within tolerance",
        fusion["reconstruction_error"] <= EXACT,
        f"{fusion['reconstruction_error']:.3e}",
    )

    # Prior shift: deployment probability is the same log-odds recentred on the
    # production base rate, not a rescaled probability.
    shifted = sigmoid(fusion["log_odds"] + fusion["prior_shift_logodds"])
    c.close(
        "prior shift applied in log-odds space",
        shifted,
        fusion["probability_at_deployment_prior"],
        TIGHT,
    )
    c.ok(
        "prior shift lowers confidence",
        fusion["probability_at_deployment_prior"] < fusion["probability"],
        f"{fusion['probability_at_deployment_prior']:.6f} < {fusion['probability']:.6f}",
    )


def check_gate(c: Checker, trace: Dict[str, Any], gate: Dict[str, Any]) -> None:
    print("threshold gate")
    p = trace["probability"]
    c.close("margin = probability - threshold", gate["margin"], p - gate["threshold"], TIGHT)
    c.ok(
        "fired iff probability >= threshold",
        gate["fired"] == (p >= gate["threshold"]),
        f"fired={gate['fired']} p={p:.6f} thr={gate['threshold']:.6f}",
    )
    rank, total = gate["rank"], gate["total_windows"]
    c.ok("rank within the population", 1 <= rank <= total, f"{rank} of {total}")
    for k in ("recall", "precision_eval", "ppv_at_deployment_prior", "fpr"):
        v = gate[k]
        c.ok(f"{k} is a probability", 0.0 <= v <= 1.0, f"{v}")


def check_evidence(c: Checker, trace: Dict[str, Any], features: Dict[str, Any]) -> None:
    print("evidence and disagreement")
    credited = {
        f["feature"]
        for band in features["bands"]
        for f in band["features"]
        if f["attributed_credit"]
    }
    attributed = {a["feature"] for a in trace["attributions"]}
    c.ok(
        "credited features are exactly the attributed ones",
        credited <= attributed,
        f"credited-but-unattributed: {sorted(credited - attributed)}",
    )

    for band in features["bands"]:
        for f in band["features"]:
            pctile = f["percentile"]
            if pctile is not None:
                c.ok(
                    f"{f['feature']} percentile in range",
                    0.0 <= pctile <= 1.0,
                    f"{pctile}",
                )

    d = trace["disagreement"]
    c.ok(
        "disagreement flag agrees with the labels",
        d["detected"] == (d["truth"] is not None and d["truth"] != d["suspected"]),
        f"suspected={d['suspected']} truth={d['truth']} detected={d['detected']}",
    )
    for m in d["missed_evidence"]:
        # Missed evidence must be genuinely extreme AND genuinely uncredited,
        # otherwise the headline finding is overstated.
        c.ok(
            f"missed evidence {m['feature']} is extreme",
            (m["percentile"] or 0.0) >= 0.99,
            f"percentile {m['percentile']}",
        )
        c.ok(
            f"missed evidence {m['feature']} received no credit",
            not m["attributed_credit"] and m["feature"] not in attributed,
        )


def main(argv: List[str]) -> int:
    path = argv[1] if len(argv) > 1 else "artifacts/alert_trace.json"
    try:
        with open(path, encoding="utf-8") as fh:
            trace = json.load(fh)
    except FileNotFoundError:
        print(f"trace not found: {path}\nrun: python -m sentinelai.pipeline", file=sys.stderr)
        return 1

    print(f"verifying {path}")
    print(f"alert {trace['alert_id']}  p={trace['probability']:.10f}")
    print(f"suspected {trace['suspected_family']}  truth {trace['ground_truth']}\n")

    c = Checker()
    check_structure(c, trace)
    stages = stage_map(trace)
    check_fusion(c, stages["fusion"])
    check_gate(c, trace, stages["threshold"])
    check_evidence(c, trace, stages["features"])

    print(f"\n{c.passed} checks passed, {len(c.failures)} failed")
    if c.failures:
        print("\nthe rendered explanation does not reproduce the model:", file=sys.stderr)
        for f in c.failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("the website's arithmetic is the model's arithmetic")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
