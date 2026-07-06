"""Windowed feature extraction (the 'stream processing' stage).

In production this is Spark Structured Streaming / Flink over Kafka topics.
Here the exact same aggregation semantics are expressed with pandas so the
feature contract is testable offline: one row per (entity, 5-minute window),
labelled by ground truth for evaluation only.

Two feature families, deliberately kept separate:
  * absolute features  - what the traffic looked like
  * relative features  - how far the entity deviated from ITS OWN baseline
    (robust z-score using median/MAD from the training period only, so there is
    no leakage of test-period statistics into the features)
"""

from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from .synth import ADMIN_PORTS, WINDOW_S, Telemetry

ABSOLUTE_FEATURES: Tuple[str, ...] = (
    "flow_count",
    "bytes_out_sum",
    "bytes_in_sum",
    "bytes_out_p95",
    "out_in_ratio",
    "pkts_sum",
    "mean_pkt_size",
    "distinct_dports",
    "distinct_dsts",
    "port_entropy",
    "syn_ratio",
    "short_flow_ratio",
    "admin_port_ratio",
    "dns_flow_ratio",
    "dns_entropy_max",
    "ext_dst_ratio",
    "night_flag",
    "auth_events",
    "failed_logins",
    "failed_login_ratio",
    "distinct_auth_targets",
    "interactive_logons",
)

RELATIVE_FEATURES: Tuple[str, ...] = (
    "z_flow_count",
    "z_bytes_out_sum",
    "z_distinct_dports",
    "z_distinct_dsts",
    "z_failed_logins",
)

# Trailing-window features (1 hour = 12 windows of 300 s).
#
# Motivation, measured: stealth portscan and DNS-tunnel variants attenuate their
# per-window magnitude below the single-window decision surface, so a detector
# that only ever sees one 5-minute window structurally cannot separate them from
# rare-but-legitimate activity. Low-and-slow attacks are only anomalous in
# ACCUMULATION. These features give the models a memory without leaking the
# future: every aggregate is strictly backward-looking (closed on the right),
# so the value at window t uses windows [t-11, t] and nothing after t.
ROLLING_WINDOWS = 12

ROLLING_FEATURES: Tuple[str, ...] = (
    "roll_dport_reach_1h",
    "roll_dst_reach_1h",
    "roll_dns_flows_1h",
    "roll_dns_entropy_mean_1h",
    "roll_bytes_out_1h",
    "roll_admin_flows_1h",
    "roll_active_windows_1h",
)

FEATURES: Tuple[str, ...] = ABSOLUTE_FEATURES + RELATIVE_FEATURES + ROLLING_FEATURES


def _entropy(counts: np.ndarray) -> float:
    total = counts.sum()
    if total <= 0:
        return 0.0
    p = counts / total
    p = p[p > 0]
    return float(-(p * np.log2(p)).sum())


def _flow_window_features(flows: pd.DataFrame) -> pd.DataFrame:
    f = flows.copy()
    f["win"] = (f["ts"] // WINDOW_S) * WINDOW_S
    f["is_admin"] = f["dport"].isin(ADMIN_PORTS).astype(int)
    f["is_dns"] = (f["dport"] == 53).astype(int)
    f["is_short"] = (f["dur"] < 0.5).astype(int)

    g = f.groupby(["src", "win"], sort=False)
    out = g.agg(
        flow_count=("dport", "size"),
        bytes_out_sum=("bytes_out", "sum"),
        bytes_in_sum=("bytes_in", "sum"),
        bytes_out_p95=("bytes_out", lambda s: float(np.percentile(s, 95))),
        pkts_sum=("pkts", "sum"),
        distinct_dports=("dport", "nunique"),
        distinct_dsts=("dst", "nunique"),
        syn_ratio=("syn", "mean"),
        short_flow_ratio=("is_short", "mean"),
        admin_port_ratio=("is_admin", "mean"),
        dns_flow_ratio=("is_dns", "mean"),
        dns_entropy_max=("dns_entropy", "max"),
        ext_dst_ratio=("ext", "mean"),
        port_entropy=("dport", lambda s: _entropy(s.value_counts().to_numpy())),
    ).reset_index()

    out["out_in_ratio"] = out["bytes_out_sum"] / (out["bytes_in_sum"] + 1.0)
    out["mean_pkt_size"] = (out["bytes_out_sum"] + out["bytes_in_sum"]) / (out["pkts_sum"] + 1.0)
    hour = ((out["win"] % 86400) / 3600.0)
    out["night_flag"] = ((hour < 7) | (hour >= 21)).astype(int)
    return out.rename(columns={"src": "entity"})


def _auth_window_features(auth: pd.DataFrame) -> pd.DataFrame:
    a = auth.copy()
    a["win"] = (a["ts"] // WINDOW_S) * WINDOW_S
    a["failed"] = (a["success"] == 0).astype(int)
    a["interactive"] = (a["logon_type"] == 10).astype(int)
    g = a.groupby(["src_host", "win"], sort=False)
    out = g.agg(
        auth_events=("success", "size"),
        failed_logins=("failed", "sum"),
        distinct_auth_targets=("dst_host", "nunique"),
        interactive_logons=("interactive", "sum"),
    ).reset_index()
    out["failed_login_ratio"] = out["failed_logins"] / out["auth_events"].clip(lower=1)
    return out.rename(columns={"src_host": "entity"})


def _add_relative(df: pd.DataFrame, baseline_cutoff: int) -> pd.DataFrame:
    """Robust per-entity z-scores computed ONLY from pre-cutoff windows."""
    base = df[df["win"] < baseline_cutoff]
    pairs = {
        "z_flow_count": "flow_count",
        "z_bytes_out_sum": "bytes_out_sum",
        "z_distinct_dports": "distinct_dports",
        "z_distinct_dsts": "distinct_dsts",
        "z_failed_logins": "failed_logins",
    }
    stats: Dict[str, pd.DataFrame] = {}
    for zname, col in pairs.items():
        med = base.groupby("entity")[col].median()
        mad = base.groupby("entity")[col].apply(
            lambda s: float(np.median(np.abs(s - np.median(s))))
        )
        stats[zname] = pd.DataFrame({"med": med, "mad": mad})
    for zname, col in pairs.items():
        st = stats[zname]
        med = df["entity"].map(st["med"]).fillna(float(base[col].median() if len(base) else 0.0))
        mad = df["entity"].map(st["mad"]).fillna(0.0)
        scale = 1.4826 * mad  # MAD -> sigma for a normal distribution
        scale = scale.where(scale > 1e-6, other=np.maximum(1.0, 0.1 * med.abs()))
        df[zname] = (df[col] - med) / scale
    return df


def _add_rolling(df: pd.DataFrame) -> pd.DataFrame:
    """Backward-looking per-entity accumulation over the trailing hour.

    Strictly causal: rows are sorted by time within each entity and every
    aggregate includes the current window and earlier ones only. No centred
    windows, no shift(-1), nothing that could see the future.
    """
    df = df.sort_values(["entity", "win"], ignore_index=True)
    df["_dns_flows"] = df["dns_flow_ratio"] * df["flow_count"]
    df["_admin_flows"] = df["admin_port_ratio"] * df["flow_count"]
    df["_active"] = (df["flow_count"] > 0).astype(float)

    spec = [
        ("roll_dport_reach_1h", "distinct_dports", "sum"),
        ("roll_dst_reach_1h", "distinct_dsts", "sum"),
        ("roll_dns_flows_1h", "_dns_flows", "sum"),
        ("roll_dns_entropy_mean_1h", "dns_entropy_max", "mean"),
        ("roll_bytes_out_1h", "bytes_out_sum", "sum"),
        ("roll_admin_flows_1h", "_admin_flows", "sum"),
        ("roll_active_windows_1h", "_active", "sum"),
    ]
    grouped = df.groupby("entity", sort=False)
    for name, col, how in spec:
        roll = grouped[col].rolling(ROLLING_WINDOWS, min_periods=1)
        agg = roll.sum() if how == "sum" else roll.mean()
        df[name] = agg.reset_index(level=0, drop=True).to_numpy(dtype=float)

    return df.drop(columns=["_dns_flows", "_admin_flows", "_active"])


def build_dataset(tel: Telemetry, baseline_frac: float = 0.35) -> pd.DataFrame:
    """Join flow + auth windows, attach ground-truth labels, add baselines."""
    flow_f = _flow_window_features(tel.flows)
    auth_f = _auth_window_features(tel.auth)
    df = flow_f.merge(auth_f, on=["entity", "win"], how="outer")

    for col in ABSOLUTE_FEATURES:
        if col not in df:
            df[col] = 0.0
    df = df.fillna(0.0)

    wins = np.sort(df["win"].unique())
    cutoff = int(wins[int(len(wins) * baseline_frac)])
    df = _add_relative(df, baseline_cutoff=cutoff)
    df = _add_rolling(df)

    truth = tel.incidents.set_index(["entity", "win"])["attack"] if len(tel.incidents) else None
    if truth is not None:
        idx = pd.MultiIndex.from_arrays([df["entity"], df["win"]])
        df["attack"] = truth.reindex(idx).to_numpy()
    else:
        df["attack"] = np.nan
    df["attack"] = df["attack"].fillna("benign")
    df["label"] = (df["attack"] != "benign").astype(int)
    df = df.sort_values(["win", "entity"], ignore_index=True)
    df["baseline_cutoff"] = cutoff
    return df


def time_split(df: pd.DataFrame, train_frac: float = 0.6, calib_frac: float = 0.15):
    """Strictly chronological train / calibration / test split.

    Random splits are the single most common way IDS papers overstate results:
    windows of the same attack episode land on both sides of the split. We split
    on time only, which is also how the system behaves in production.
    """
    wins = np.sort(df["win"].unique())
    n = len(wins)
    t_end = wins[int(n * train_frac)]
    c_end = wins[int(n * (train_frac + calib_frac))]
    train = df[df["win"] < t_end].reset_index(drop=True)
    calib = df[(df["win"] >= t_end) & (df["win"] < c_end)].reset_index(drop=True)
    test = df[df["win"] >= c_end].reset_index(drop=True)
    return train, calib, test


def matrix(df: pd.DataFrame, columns: List[str] | Tuple[str, ...] = FEATURES) -> np.ndarray:
    X = df.loc[:, list(columns)].to_numpy(dtype=float)
    return np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
