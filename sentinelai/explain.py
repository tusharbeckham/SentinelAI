"""Per-alert feature attribution (model-agnostic Shapley sampling).

A SOC analyst will not action a bare score. Every alert therefore carries an
additive explanation of *why* it fired.

Method: Shapley value estimation by permutation sampling (Strumbelj & Kononenko,
2014) - the same game-theoretic quantity SHAP estimates, applied to the FULL
ensemble (isolation forest + GBDT + graph detector + logistic stacker), not just
the tree model. Guarantees we care about:

  * local accuracy: sum(phi_i) + f(baseline) = f(x) (checked in tests to a
    tolerance; the residual is reported per alert as `attribution_residual`),
  * missingness and consistency follow from the Shapley axioms,
  * background distribution = benign windows from the calibration period, so
    'normal' means normal for this network, not a synthetic zero vector.

Attributions are computed on the log-odds scale, which is additive and therefore
the correct scale for an additive explanation of a probability model.
"""

from __future__ import annotations

from typing import Callable, Sequence

import numpy as np


class ShapleyExplainer:
    def __init__(
        self,
        score_fn: Callable[[np.ndarray], np.ndarray],
        background: np.ndarray,
        feature_names: Sequence[str],
        n_permutations: int = 48,
        n_background: int = 24,
        seed: int = 0,
    ):
        self.score_fn = score_fn
        self.names = list(feature_names)
        rng = np.random.default_rng(seed)
        bg = np.asarray(background, dtype=float)
        take = rng.choice(len(bg), size=min(n_background, len(bg)), replace=False)
        self.background = bg[take]
        self.n_permutations = n_permutations
        self.rng = rng
        # Reference value of the game: E[f(background)].
        self.base_value = float(np.mean(self.score_fn(self.background)))

    def explain_row(self, x: np.ndarray) -> dict:
        x = np.asarray(x, dtype=float).ravel()
        d = x.size
        phi = np.zeros(d)
        rows: list[np.ndarray] = []
        plan: list[tuple[int, int]] = []  # (permutation step feature, row index)
        base_row_idx: list[int] = []
        for _ in range(self.n_permutations):
            base = self.background[self.rng.integers(0, len(self.background))].copy()
            perm = self.rng.permutation(d)
            cur = base.copy()
            rows.append(cur.copy())
            base_row_idx.append(len(rows) - 1)
            for f in perm:
                cur[f] = x[f]
                rows.append(cur.copy())
                plan.append((int(f), len(rows) - 1))
        vals = self.score_fn(np.vstack(rows))
        # marginal contribution = f(with feature) - f(previous state)
        for f, ridx in plan:
            phi[f] += vals[ridx] - vals[ridx - 1]
        phi /= self.n_permutations
        fx = float(self.score_fn(x.reshape(1, -1))[0])
        # Local accuracy must hold against the SAMPLED reference, i.e. the mean of
        # the background rows actually drawn, otherwise the reported residual
        # silently absorbs Monte-Carlo error in the reference value itself.
        sampled_base = float(np.mean(vals[base_row_idx]))
        residual = fx - (sampled_base + phi.sum())
        return {
            "base_value": sampled_base,
            "base_value_population": self.base_value,
            "score": fx,
            "attribution_residual": float(residual),
            "contributions": {n: float(v) for n, v in zip(self.names, phi)},
        }

    def top_reasons(self, x: np.ndarray, k: int = 6) -> dict:
        exp = self.explain_row(x)
        ranked = sorted(exp["contributions"].items(), key=lambda kv: -abs(kv[1]))[:k]
        exp["top"] = [
            {"feature": n, "contribution": v, "value": float(np.asarray(x).ravel()[self.names.index(n)])}
            for n, v in ranked
        ]
        return exp


def narrate(top: list[dict], attack_hint: str | None = None) -> str:
    """Turn attributions into an analyst-readable one-liner."""
    phrases = {
        "distinct_dports": "scanned an unusual number of destination ports",
        "z_distinct_dports": "port fan-out far above this host's own baseline",
        "syn_ratio": "high proportion of SYN-only flows",
        "bytes_out_sum": "large outbound volume",
        "z_bytes_out_sum": "outbound volume far above this host's own baseline",
        "out_in_ratio": "outbound/inbound byte ratio skewed towards egress",
        "failed_logins": "burst of failed authentications",
        "failed_login_ratio": "authentication failure rate spike",
        "dns_entropy_max": "high-entropy DNS query names (tunnelling signature)",
        "dns_flow_ratio": "traffic dominated by DNS",
        "g_new_edges": "authenticated to hosts it has never contacted before",
        "g_chain_depth": "multi-hop authentication chain of new edges",
        "g_breadth_z": "authentication fan-out above baseline",
        "g_new_user_host": "user authenticated from an unfamiliar host",
        "graph_score": "anomalous authentication-graph traversal",
        "admin_port_ratio": "unusual share of admin-protocol traffic (SSH/SMB/RDP)",
        "night_flag": "activity outside business hours",
        "pkts_sum": "very high packet rate",
        "flow_count": "flow count spike",
        "z_flow_count": "flow count far above this host's own baseline",
        "ext_dst_ratio": "traffic skewed to external destinations",
    }
    parts = []
    for item in top:
        if item["contribution"] <= 0:
            continue
        parts.append(phrases.get(item["feature"], f"{item['feature']} elevated"))
        if len(parts) == 3:
            break
    body = "; ".join(parts) if parts else "composite deviation from baseline"
    prefix = f"Likely {attack_hint.replace('_', ' ')}: " if attack_hint else ""
    return prefix + body
