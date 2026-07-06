"""Isolation Forest (Liu, Ting & Zhou, ICDM 2008) in pure NumPy.

Why this model for the unsupervised leg:
  * it needs no labels, so it detects novel/zero-day behaviour;
  * its complexity is O(t * psi * log psi) for training regardless of dataset
    size (sub-sampling), which is what makes it viable on streaming telemetry;
  * anomaly score is bounded in (0, 1), which makes fusion with a supervised
    probability well behaved.

Implementation notes:
  * trees are stored as flat arrays and traversal is vectorised (batch descent),
    so scoring 100k+ windows across 150 trees stays in the seconds range;
  * training uses ONLY windows that the operator believes are clean (we pass the
    training-period majority-benign data), matching the semi-supervised
    'baseline of normal' assumption the model actually requires.
"""

from __future__ import annotations

import numpy as np


def _c(n: float) -> float:
    """Average path length of an unsuccessful BST search over n points."""
    if n <= 1:
        return 1.0
    return 2.0 * (np.log(n - 1.0) + 0.5772156649) - 2.0 * (n - 1.0) / n


class _Tree:
    __slots__ = ("feature", "threshold", "left", "right", "size", "depth_lim")

    def __init__(self, depth_lim: int):
        self.feature: list[int] = []
        self.threshold: list[float] = []
        self.left: list[int] = []
        self.right: list[int] = []
        self.size: list[int] = []
        self.depth_lim = depth_lim

    def _new_node(self) -> int:
        self.feature.append(-1)
        self.threshold.append(0.0)
        self.left.append(-1)
        self.right.append(-1)
        self.size.append(0)
        return len(self.feature) - 1

    def build(self, X: np.ndarray, idx: np.ndarray, depth: int, rng: np.random.Generator) -> int:
        node = self._new_node()
        n = idx.size
        self.size[node] = n
        if depth >= self.depth_lim or n <= 1:
            return node
        # pick a feature that actually varies in this partition
        cand = rng.permutation(X.shape[1])
        for f in cand:
            col = X[idx, f]
            lo, hi = col.min(), col.max()
            if hi > lo:
                thr = float(rng.uniform(lo, hi))
                mask = col < thr
                if mask.any() and (~mask).any():
                    self.feature[node] = int(f)
                    self.threshold[node] = thr
                    self.left[node] = self.build(X, idx[mask], depth + 1, rng)
                    self.right[node] = self.build(X, idx[~mask], depth + 1, rng)
                    return node
        return node  # all points identical -> leaf

    def freeze(self) -> dict:
        return {
            "feature": np.array(self.feature, dtype=np.int32),
            "threshold": np.array(self.threshold, dtype=np.float64),
            "left": np.array(self.left, dtype=np.int32),
            "right": np.array(self.right, dtype=np.int32),
            "size": np.array(self.size, dtype=np.float64),
        }


class IsolationForest:
    def __init__(self, n_trees: int = 150, sample_size: int = 256, seed: int = 0):
        self.n_trees = n_trees
        self.sample_size = sample_size
        self.seed = seed
        self.trees: list[dict] = []
        self._c_psi = 1.0

    def fit(self, X: np.ndarray) -> "IsolationForest":
        X = np.asarray(X, dtype=float)
        rng = np.random.default_rng(self.seed)
        psi = int(min(self.sample_size, X.shape[0]))
        depth_lim = max(1, int(np.ceil(np.log2(max(2, psi)))))
        self._c_psi = _c(psi)
        self.trees = []
        for _ in range(self.n_trees):
            rows = rng.choice(X.shape[0], size=psi, replace=False)
            sub = X[rows]
            t = _Tree(depth_lim)
            t.build(sub, np.arange(psi), 0, rng)
            self.trees.append(t.freeze())
        return self

    def path_length(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, dtype=float)
        n = X.shape[0]
        total = np.zeros(n)
        for t in self.trees:
            feat, thr, left, right, size = (
                t["feature"], t["threshold"], t["left"], t["right"], t["size"]
            )
            node = np.zeros(n, dtype=np.int32)
            depth = np.zeros(n)
            active = np.ones(n, dtype=bool)
            while active.any():
                cur = node[active]
                f = feat[cur]
                internal = f >= 0
                if not internal.any():
                    break
                idx_active = np.flatnonzero(active)
                go = idx_active[internal]
                cur_go = cur[internal]
                go_left = X[go, f[internal]] < thr[cur_go]
                node[go] = np.where(go_left, left[cur_go], right[cur_go])
                depth[go] += 1.0
                # samples that reached a leaf become inactive
                stop = idx_active[~internal]
                active[stop] = False
            # add the expected extra path length inside truncated leaves
            total += depth + np.vectorize(_c)(size[node])
        return total / len(self.trees)

    def score(self, X: np.ndarray) -> np.ndarray:
        """Anomaly score in (0, 1); higher = more anomalous."""
        return 2.0 ** (-self.path_length(X) / self._c_psi)
