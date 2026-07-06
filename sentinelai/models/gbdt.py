"""Histogram gradient-boosted decision trees (logistic loss) in pure NumPy.

This is the supervised leg: an XGBoost/LightGBM-style learner implemented from
first principles so every modelling choice is inspectable and the repo has no
heavy binary dependencies.

Math (Chen & Guestrin, KDD 2016, section 2.2):
  loss   L = sum_i logloss(y_i, sigma(F_i)) + 0.5*lambda*||w||^2
  grad   g_i = p_i - y_i,  hess h_i = p_i (1 - p_i)
  leaf   w = -sum(g) / (sum(h) + lambda)
  gain   = 0.5 * [ G_L^2/(H_L+l) + G_R^2/(H_R+l) - G^2/(H+l) ] - gamma

Extras needed by a security product:
  * `scale_pos_weight` for extreme class imbalance,
  * per-feature gain importance,
  * deterministic seeding for reproducible model artefacts.
"""

from __future__ import annotations

import numpy as np


def sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


class GBDT:
    def __init__(
        self,
        n_trees: int = 120,
        max_depth: int = 3,
        learning_rate: float = 0.15,
        n_bins: int = 32,
        reg_lambda: float = 1.0,
        gamma: float = 0.0,
        min_child_weight: float = 5.0,
        scale_pos_weight: float = 1.0,
        subsample: float = 0.8,
        seed: int = 0,
    ):
        self.p = dict(
            n_trees=n_trees,
            max_depth=max_depth,
            learning_rate=learning_rate,
            n_bins=n_bins,
            reg_lambda=reg_lambda,
            gamma=gamma,
            min_child_weight=min_child_weight,
            scale_pos_weight=scale_pos_weight,
            subsample=subsample,
            seed=seed,
        )
        self.bin_edges: np.ndarray | None = None
        self.trees: list[dict] = []
        self.base_score = 0.0
        self.importance_: np.ndarray | None = None

    # ------------------------------------------------------------------ bins
    def _fit_bins(self, X: np.ndarray) -> None:
        nb = self.p["n_bins"]
        qs = np.linspace(0, 100, nb + 1)[1:-1]
        self.bin_edges = np.stack(
            [np.unique(np.percentile(X[:, j], qs)) for j in range(X.shape[1])]
            if False
            else [np.percentile(X[:, j], qs) for j in range(X.shape[1])]
        )

    def _bin(self, X: np.ndarray) -> np.ndarray:
        assert self.bin_edges is not None
        out = np.empty(X.shape, dtype=np.int16)
        for j in range(X.shape[1]):
            out[:, j] = np.searchsorted(self.bin_edges[j], X[:, j], side="left")
        return out

    # ----------------------------------------------------------------- train
    def fit(self, X: np.ndarray, y: np.ndarray) -> "GBDT":
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)
        rng = np.random.default_rng(self.p["seed"])
        self._fit_bins(X)
        B = self._bin(X)
        nb = self.p["n_bins"]
        n, d = X.shape

        w = np.where(y > 0, self.p["scale_pos_weight"], 1.0)
        prior = float(np.clip((w * y).sum() / w.sum(), 1e-6, 1 - 1e-6))
        self.base_score = float(np.log(prior / (1 - prior)))
        F = np.full(n, self.base_score)
        self.trees = []
        self.importance_ = np.zeros(d)

        for _ in range(self.p["n_trees"]):
            pr = sigmoid(F)
            g = w * (pr - y)
            h = w * np.maximum(pr * (1 - pr), 1e-6)
            if self.p["subsample"] < 1.0:
                take = rng.random(n) < self.p["subsample"]
            else:
                take = np.ones(n, dtype=bool)
            tree = self._grow(B, g, h, np.flatnonzero(take), nb, d)
            self.trees.append(tree)
            F += self.p["learning_rate"] * self._apply(tree, B)
        return self

    def _grow(self, B, g, h, idx, nb, d) -> dict:
        feature, threshold_bin, left, right, value = [], [], [], [], []

        def new_node() -> int:
            feature.append(-1)
            threshold_bin.append(-1)
            left.append(-1)
            right.append(-1)
            value.append(0.0)
            return len(feature) - 1

        lam, gam, mcw = self.p["reg_lambda"], self.p["gamma"], self.p["min_child_weight"]

        def leaf_value(rows) -> float:
            return float(-g[rows].sum() / (h[rows].sum() + lam))

        def build(rows, depth) -> int:
            node = new_node()
            if depth >= self.p["max_depth"] or rows.size < 2 * mcw:
                value[node] = leaf_value(rows)
                return node
            G, H = g[rows].sum(), h[rows].sum()
            parent = G * G / (H + lam)
            best = (0.0, -1, -1)
            for j in range(d):
                bins_j = B[rows, j]
                Gh = np.bincount(bins_j, weights=g[rows], minlength=nb)
                Hh = np.bincount(bins_j, weights=h[rows], minlength=nb)
                GL, HL = np.cumsum(Gh)[:-1], np.cumsum(Hh)[:-1]
                GR, HR = G - GL, H - HL
                ok = (HL >= mcw) & (HR >= mcw)
                if not ok.any():
                    continue
                gain = 0.5 * (GL**2 / (HL + lam) + GR**2 / (HR + lam) - parent) - gam
                gain = np.where(ok, gain, -np.inf)
                b = int(np.argmax(gain))
                if gain[b] > best[0]:
                    best = (float(gain[b]), j, b)
            if best[1] < 0:
                value[node] = leaf_value(rows)
                return node
            _, j, b = best
            self.importance_[j] += best[0]
            mask = B[rows, j] <= b
            feature[node] = j
            threshold_bin[node] = b
            left[node] = build(rows[mask], depth + 1)
            right[node] = build(rows[~mask], depth + 1)
            return node

        build(idx, 0)
        return {
            "feature": np.array(feature, dtype=np.int32),
            "threshold_bin": np.array(threshold_bin, dtype=np.int32),
            "left": np.array(left, dtype=np.int32),
            "right": np.array(right, dtype=np.int32),
            "value": np.array(value, dtype=np.float64),
        }

    # ---------------------------------------------------------------- predict
    @staticmethod
    def _apply(tree: dict, B: np.ndarray) -> np.ndarray:
        feat, thr, left, right, val = (
            tree["feature"], tree["threshold_bin"], tree["left"], tree["right"], tree["value"]
        )
        node = np.zeros(B.shape[0], dtype=np.int32)
        while True:
            f = feat[node]
            internal = f >= 0
            if not internal.any():
                break
            rows = np.flatnonzero(internal)
            cur = node[rows]
            go_left = B[rows, f[rows]] <= thr[cur]
            node[rows] = np.where(go_left, left[cur], right[cur])
        return val[node]

    def decision_function(self, X: np.ndarray) -> np.ndarray:
        B = self._bin(np.asarray(X, dtype=float))
        F = np.full(B.shape[0], self.base_score)
        for t in self.trees:
            F += self.p["learning_rate"] * self._apply(t, B)
        return F

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return sigmoid(self.decision_function(X))

    def importance(self, names: list[str] | tuple[str, ...]) -> dict:
        imp = self.importance_ if self.importance_ is not None else np.zeros(len(names))
        total = imp.sum() or 1.0
        return {n: float(v / total) for n, v in sorted(zip(names, imp), key=lambda kv: -kv[1])}
