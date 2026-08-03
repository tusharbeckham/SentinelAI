"""Score fusion, probability calibration and base-rate-aware thresholding.

This module is the scientific core of SentinelAI.

1. FUSION. Two heterogeneous detectors (unsupervised isolation score,
   supervised GBDT probability) are combined by a small logistic stacker fitted
   on out-of-fold base scores from time-blocked folds. Stacking on the training
   window would fit the base models' in-sample optimism.

   There was a third leg -- the auth-graph score -- until v3.3.0. It was removed
   because it was measured to make the ensemble WORSE than its own best member:
   with it, average precision was 0.811 against 0.858 for the booster alone.
   Adding models to an ensemble is not free, and a detector that is nearly
   uninformative globally (AP 0.039) spends its weight on noise. The graph
   features remain inputs to the booster.

2. CALIBRATION. The stacker output is a calibrated probability; we report
   reliability (Brier score + expected calibration error) because an
   uncalibrated 'risk score' cannot drive automated response safely.

3. BASE-RATE CORRECTION (Axelsson, ACM TISSEC 2000). Evaluation data is far
   more attack-dense than production. The Bayesian detection rate is
       PPV = P(I|A) = TPR * p / (TPR * p + FPR * (1 - p))
   With p ~ 1e-4 even FPR = 1e-3 yields PPV ~ 9%. We therefore
     (a) shift the stacker intercept from the evaluation prior to the declared
         deployment prior (prior-shift correction), and
     (b) select the operating threshold from an ANALYST BUDGET (alerts/day)
         rather than from an arbitrary 0.5 cut.

4. OPERATING POINT. `choose_threshold` returns the smallest threshold that keeps
   the expected alert volume within budget, plus the resulting recall, so the
   trade-off is explicit instead of hidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Sequence

import numpy as np


def sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


def logit(p: float) -> float:
    p = float(np.clip(p, 1e-12, 1 - 1e-12))
    return float(np.log(p / (1 - p)))


# --------------------------------------------------------------------- metrics
def roc_auc(y: np.ndarray, s: np.ndarray) -> float:
    y = np.asarray(y).astype(int)
    if y.sum() == 0 or y.sum() == len(y):
        return float("nan")
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s), dtype=float)
    sorted_s = s[order]
    i = 0
    r = np.arange(1, len(s) + 1, dtype=float)
    while i < len(s):  # average ranks over ties
        j = i
        while j + 1 < len(s) and sorted_s[j + 1] == sorted_s[i]:
            j += 1
        ranks[order[i : j + 1]] = r[i : j + 1].mean()
        i = j + 1
    n_pos, n_neg = int(y.sum()), int((1 - y).sum())
    return float((ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def pr_curve(y: np.ndarray, s: np.ndarray):
    order = np.argsort(-s, kind="mergesort")
    y = np.asarray(y).astype(int)[order]
    tp = np.cumsum(y)
    fp = np.cumsum(1 - y)
    precision = tp / np.maximum(tp + fp, 1)
    recall = tp / max(int(y.sum()), 1)
    return precision, recall, s[order]


def average_precision(y: np.ndarray, s: np.ndarray) -> float:
    precision, recall, _ = pr_curve(y, s)
    return float(np.sum(np.diff(np.concatenate([[0.0], recall])) * precision))


def tpr_at_fpr(y: np.ndarray, s: np.ndarray, target_fpr: float) -> float:
    y = np.asarray(y).astype(int)
    neg = np.sort(s[y == 0])[::-1]
    if len(neg) == 0:
        return float("nan")
    k = max(1, int(np.floor(target_fpr * len(neg))))
    thr = neg[k - 1]
    return float((s[y == 1] >= thr).mean())


def brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((np.asarray(y, dtype=float) - p) ** 2))


def expected_calibration_error(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    y = np.asarray(y, dtype=float)
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    ece = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            ece += m.mean() * abs(y[m].mean() - p[m].mean())
    return float(ece)


def bayesian_ppv(tpr: float, fpr: float, prior: float) -> float:
    num = tpr * prior
    den = num + fpr * (1 - prior)
    return float(num / den) if den > 0 else 0.0


# -------------------------------------------------------------------- stacking
@dataclass
class LogisticStacker:
    """Tiny L2-regularised logistic regression trained with Newton-IRLS."""

    l2: float = 1.0
    max_iter: int = 100
    w: np.ndarray | None = None
    b: float = 0.0
    mu: np.ndarray | None = None
    sd: np.ndarray | None = None
    train_prior: float = 0.5
    prior_shift: float = 0.0
    names: Sequence[str] = field(default_factory=tuple)

    def fit(self, X: np.ndarray, y: np.ndarray, names: Sequence[str] = ()) -> "LogisticStacker":
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)
        self.names = tuple(names) or tuple(f"f{i}" for i in range(X.shape[1]))
        self.mu = X.mean(0)
        self.sd = X.std(0)
        self.sd[self.sd < 1e-9] = 1.0
        Z = (X - self.mu) / self.sd
        Z = np.hstack([Z, np.ones((len(Z), 1))])
        theta = np.zeros(Z.shape[1])
        reg = np.eye(Z.shape[1]) * self.l2
        reg[-1, -1] = 0.0
        for _ in range(self.max_iter):
            p = sigmoid(Z @ theta)
            W = np.maximum(p * (1 - p), 1e-8)
            grad = Z.T @ (p - y) + reg @ theta
            H = (Z * W[:, None]).T @ Z + reg
            step = np.linalg.solve(H, grad)
            theta -= step
            if np.max(np.abs(step)) < 1e-8:
                break
        self.w, self.b = theta[:-1], float(theta[-1])
        self.train_prior = float(np.clip(y.mean(), 1e-9, 1 - 1e-9))
        return self

    def set_deployment_prior(self, deployment_prior: float) -> None:
        """Prior-shift correction: recentre log-odds on the production base rate."""
        self.prior_shift = logit(deployment_prior) - logit(self.train_prior)

    def decision(self, X: np.ndarray) -> np.ndarray:
        assert self.w is not None and self.mu is not None and self.sd is not None
        Z = (np.asarray(X, dtype=float) - self.mu) / self.sd
        return Z @ self.w + self.b

    def predict_proba(self, X: np.ndarray, apply_prior_shift: bool = False) -> np.ndarray:
        z = self.decision(X)
        if apply_prior_shift:
            z = z + self.prior_shift
        return sigmoid(z)

    def coefficients(self) -> Dict[str, float]:
        assert self.w is not None
        return {n: float(v) for n, v in zip(self.names, self.w)}


# ------------------------------------------------------------- operating point
@dataclass
class OperatingPoint:
    threshold: float
    alerts_per_day: float
    recall: float
    precision_eval: float
    fpr: float
    tpr: float
    ppv_at_deployment_prior: float
    deployment_prior: float
    budget_per_day: float

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def choose_threshold(
    y: np.ndarray,
    p: np.ndarray,
    windows_per_day: float,
    budget_per_day: float,
    deployment_prior: float,
) -> OperatingPoint:
    """Smallest threshold whose alert volume fits the analyst budget."""
    y = np.asarray(y).astype(int)
    n = len(p)
    days = max(n / windows_per_day, 1e-9)
    max_alerts = max(1, int(round(budget_per_day * days)))
    order = np.argsort(-p, kind="mergesort")
    k = min(max_alerts, n)
    thr = float(p[order][k - 1])
    flag = p >= thr
    tp = int((flag & (y == 1)).sum())
    fp = int((flag & (y == 0)).sum())
    n_pos, n_neg = max(int((y == 1).sum()), 1), max(int((y == 0).sum()), 1)
    tpr, fpr = tp / n_pos, fp / n_neg
    return OperatingPoint(
        threshold=thr,
        alerts_per_day=float(flag.sum() / days),
        recall=float(tpr),
        precision_eval=float(tp / max(tp + fp, 1)),
        fpr=float(fpr),
        tpr=float(tpr),
        ppv_at_deployment_prior=bayesian_ppv(tpr, fpr, deployment_prior),
        deployment_prior=float(deployment_prior),
        budget_per_day=float(budget_per_day),
    )


def evaluate(y: np.ndarray, s: np.ndarray, deployment_prior: float = 1e-4) -> dict:
    y = np.asarray(y).astype(int)
    out = {
        "n": int(len(y)),
        "positives": int(y.sum()),
        "eval_base_rate": float(y.mean()),
        "roc_auc": roc_auc(y, s),
        "average_precision": average_precision(y, s),
        "tpr_at_fpr_1e-2": tpr_at_fpr(y, s, 1e-2),
        "tpr_at_fpr_1e-3": tpr_at_fpr(y, s, 1e-3),
    }
    for fpr in (1e-2, 1e-3):
        tpr = tpr_at_fpr(y, s, fpr)
        out[f"ppv_at_prior_{deployment_prior:g}_fpr_{fpr:g}"] = bayesian_ppv(
            tpr, fpr, deployment_prior
        )
    return out
