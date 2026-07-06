"""Telemetry generator for SentinelAI.

Why synthetic telemetry instead of raw CIC-IDS2017 CSVs?
-------------------------------------------------------
The popular public IDS corpora have *documented* labelling and flow-construction
defects (Engelen et al., WTMC 2021; Lanvin et al., 2022; Dube, JCVHT 2024), so a
model trained naively on them learns dataset artefacts. For engineering and
evaluating the *detection stack* we therefore need a corpus where:

  * ground truth is exact (we know every malicious window),
  * the deployment base rate is an explicit knob (base-rate fallacy analysis),
  * the attack mix can be shifted in time to test concept drift,
  * per-entity behavioural baselines actually exist (needed for UEBA features).

The loader contract is dataset-shaped: `Telemetry.flows` mirrors NetFlow/Zeek
conn.log and `Telemetry.auth` mirrors Windows 4624/4625-style auth events, so
swapping in real telemetry means producing the same two frames.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

WINDOW_S = 300  # 5-minute feature windows

ATTACKS = (
    "portscan",
    "dos",
    "brute_force",
    "exfil",
    "dns_tunnel",
    "lateral_movement",
)

DEFAULT_MIX: Dict[str, float] = {
    "portscan": 0.22,
    "dos": 0.14,
    "brute_force": 0.20,
    "exfil": 0.14,
    "dns_tunnel": 0.12,
    "lateral_movement": 0.18,
}

ADMIN_PORTS = (22, 445, 3389, 5985)

# Benign-but-anomalous activity. This is the part naive IDS demos leave out and
# the reason unsupervised-only detectors flood analysts: each of these looks
# structurally like an attack family but is legitimate, so it exercises the
# ensemble's job of separating 'rare' from 'malicious'.
BENIGN_ANOMALIES = (
    "nightly_backup",       # confusable with exfil
    "vuln_scan",            # confusable with portscan
    "patch_burst",          # confusable with dos
    "admin_maintenance",    # confusable with lateral movement
    "password_reset_storm",  # confusable with brute force
    "cdn_dns_churn",        # confusable with DNS tunnelling
)


@dataclass
class Telemetry:
    flows: pd.DataFrame
    auth: pd.DataFrame
    incidents: pd.DataFrame
    benign_anomalies: pd.DataFrame = field(default_factory=pd.DataFrame)
    config: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return {
            "flows": int(len(self.flows)),
            "auth_events": int(len(self.auth)),
            "incident_windows": int(len(self.incidents)),
            "attack_windows_by_type": self.incidents["attack"].value_counts().to_dict()
            if len(self.incidents)
            else {},
            "benign_anomaly_windows": int(len(self.benign_anomalies)),
            "benign_anomaly_by_type": self.benign_anomalies["kind"].value_counts().to_dict()
            if len(self.benign_anomalies)
            else {},
            **self.config,
        }


def _diurnal(hour: np.ndarray | float):
    """Office-hours activity envelope (peak ~14:00, floor at night)."""
    return 0.30 + 0.70 * np.exp(-0.5 * ((hour - 14.0) / 4.5) ** 2)


def _plan_incidents(
    rng: np.random.Generator,
    n_hosts: int,
    n_windows: int,
    base_rate: float,
    mix: Dict[str, float],
) -> Dict[Tuple[int, int], str]:
    """Pick (host, window) cells that are malicious.

    Incidents are *contiguous episodes* on a single host, not i.i.d. windows.
    That matters for evaluation: splits must be episode/time aware, otherwise
    neighbouring windows of the same episode leak between train and test.
    """
    target_cells = max(1, int(round(base_rate * n_hosts * n_windows)))
    names = list(mix)
    probs = np.array([mix[n] for n in names], dtype=float)
    probs = probs / probs.sum()

    plan: Dict[Tuple[int, int], str] = {}
    episode_id = 0
    while len(plan) < target_cells:
        attack = names[int(rng.choice(len(names), p=probs))]
        host = int(rng.integers(0, n_hosts))
        length = int(rng.integers(1, 5))
        start = int(rng.integers(0, max(1, n_windows - length)))
        for w in range(start, min(start + length, n_windows)):
            plan[(host, w)] = attack
        episode_id += 1
        if episode_id > 20 * target_cells:  # safety valve
            break
    return plan


def _plan_benign_anomalies(
    rng: np.random.Generator,
    n_hosts: int,
    n_windows: int,
    rate: float,
    taken: Dict[Tuple[int, int], str],
) -> Dict[Tuple[int, int], str]:
    """Legitimate rare events, never overlapping a real incident."""
    target = max(1, int(round(rate * n_hosts * n_windows)))
    plan: Dict[Tuple[int, int], str] = {}
    guard = 0
    while len(plan) < target and guard < 50 * target:
        guard += 1
        cell = (int(rng.integers(0, n_hosts)), int(rng.integers(0, n_windows)))
        if cell in taken or cell in plan:
            continue
        plan[cell] = BENIGN_ANOMALIES[int(rng.integers(0, len(BENIGN_ANOMALIES)))]
    return plan


def generate(
    seed: int = 7,
    n_hosts: int = 40,
    n_users: int = 30,
    days: float = 2.0,
    base_rate: float = 0.004,
    benign_anomaly_rate: float = 0.02,
    stealth_share: float = 0.4,
    mix: Dict[str, float] | None = None,
    start_ts: int = 1_767_225_600,  # 2026-01-01T00:00:00Z
) -> Telemetry:
    rng = np.random.default_rng(seed)
    mix = dict(mix or DEFAULT_MIX)
    n_windows = int(days * 24 * 3600 // WINDOW_S)
    hosts = [f"h{i:03d}" for i in range(n_hosts)]
    users = [f"u{i:03d}" for i in range(n_users)]

    # ---- per-host behavioural profile (this is what UEBA baselines learn) ----
    lam = rng.uniform(3.0, 22.0, n_hosts)          # flows per window
    bo_mu = rng.uniform(6.2, 8.6, n_hosts)         # log-normal mu, bytes out
    bi_mu = bo_mu + rng.uniform(0.3, 1.6, n_hosts)  # servers pull more in
    common_ports = [
        rng.choice([80, 443, 443, 53, 8080, 993, 5432, 3306], size=int(rng.integers(2, 5)), replace=False)
        for _ in range(n_hosts)
    ]
    peers = [
        rng.choice(n_hosts, size=int(rng.integers(2, 6)), replace=False) for _ in range(n_hosts)
    ]
    host_users = [
        rng.choice(n_users, size=int(rng.integers(1, 4)), replace=False) for _ in range(n_hosts)
    ]

    plan = _plan_incidents(rng, n_hosts, n_windows, base_rate, mix)
    benign_plan = _plan_benign_anomalies(rng, n_hosts, n_windows, benign_anomaly_rate, plan)

    f_cols: Dict[str, List[np.ndarray]] = {
        k: [] for k in (
            "ts", "src", "dst", "dport", "bytes_out", "bytes_in", "pkts",
            "syn", "dur", "dns_entropy", "ext",
        )
    }
    a_rows: List[tuple] = []

    for wi in range(n_windows):
        w_start = start_ts + wi * WINDOW_S
        hour = ((w_start % 86400) / 3600.0)
        env = _diurnal(hour)
        for hi in range(n_hosts):
            attack = plan.get((hi, wi))
            benign_kind = benign_plan.get((hi, wi))
            # ---------------- benign background traffic ----------------
            n = int(rng.poisson(max(0.4, lam[hi] * env)))
            if n:
                dport = np.where(
                    rng.random(n) < 0.90,
                    rng.choice(common_ports[hi], size=n),
                    rng.integers(1024, 65535, size=n),
                )
                bo = rng.lognormal(bo_mu[hi], 0.9, n)
                bi = rng.lognormal(bi_mu[hi], 1.0, n)
                pk = np.maximum(1, (bo + bi) / rng.uniform(400, 1400, n)).astype(int)
                dst = rng.choice(peers[hi], size=n)
                _add(
                    f_cols,
                    ts=w_start + rng.integers(0, WINDOW_S, n),
                    src=np.full(n, hi),
                    dst=dst,
                    dport=dport,
                    bytes_out=bo,
                    bytes_in=bi,
                    pkts=pk,
                    syn=(rng.random(n) < 0.06).astype(int),
                    dur=rng.exponential(4.0, n),
                    dns_entropy=np.where(dport == 53, rng.normal(2.8, 0.30, n), 0.0),
                    ext=(rng.random(n) < 0.35).astype(int),
                )
            # benign authentication
            na = int(rng.poisson(1.4 * env))
            for _ in range(na):
                u = int(rng.choice(host_users[hi]))
                d = int(rng.choice(peers[hi]))
                ok = int(rng.random() < 0.97)
                a_rows.append(
                    (w_start + int(rng.integers(0, WINDOW_S)), users[u], hosts[hi], hosts[d], ok, 3)
                )

            # ------------- benign-but-anomalous overlay (label = benign) -------
            if benign_kind == "nightly_backup":
                n = int(rng.integers(4, 10))
                _add(
                    f_cols,
                    ts=w_start + rng.integers(0, WINDOW_S, n),
                    src=np.full(n, hi),
                    dst=np.full(n, int(rng.choice(peers[hi]))),
                    dport=np.full(n, 445),
                    bytes_out=rng.lognormal(bo_mu[hi] + 3.1, 0.4, n),
                    bytes_in=rng.uniform(500, 4000, n),
                    pkts=rng.integers(400, 3500, n),
                    syn=np.zeros(n, dtype=int),
                    dur=rng.uniform(30, 280, n),
                    dns_entropy=np.zeros(n),
                    ext=np.zeros(n, dtype=int),
                )
            elif benign_kind == "vuln_scan":
                n = int(rng.integers(150, 500))
                _add(
                    f_cols,
                    ts=w_start + rng.integers(0, WINDOW_S, n),
                    src=np.full(n, hi),
                    dst=rng.integers(0, n_hosts, n),
                    dport=rng.integers(1, 10000, n),
                    bytes_out=rng.uniform(60, 200, n),
                    bytes_in=rng.uniform(0, 120, n),
                    pkts=rng.integers(1, 4, n),
                    syn=np.ones(n, dtype=int),
                    dur=rng.uniform(0.01, 0.5, n),
                    dns_entropy=np.zeros(n),
                    ext=np.zeros(n, dtype=int),
                )
            elif benign_kind == "patch_burst":
                n = int(rng.integers(250, 800))
                _add(
                    f_cols,
                    ts=w_start + rng.integers(0, WINDOW_S, n),
                    src=np.full(n, hi),
                    dst=np.full(n, int(rng.choice(peers[hi]))),
                    dport=np.full(n, 443),
                    bytes_out=rng.uniform(300, 1500, n),
                    bytes_in=rng.lognormal(bi_mu[hi] + 1.0, 0.6, n),
                    pkts=rng.integers(20, 200, n),
                    syn=(rng.random(n) < 0.3).astype(int),
                    dur=rng.uniform(0.5, 8.0, n),
                    dns_entropy=np.zeros(n),
                    ext=np.ones(n, dtype=int),
                )
            elif benign_kind == "cdn_dns_churn":
                n = int(rng.integers(80, 260))
                _add(
                    f_cols,
                    ts=w_start + rng.integers(0, WINDOW_S, n),
                    src=np.full(n, hi),
                    dst=rng.integers(0, n_hosts, n),
                    dport=np.full(n, 53),
                    bytes_out=rng.uniform(90, 260, n),
                    bytes_in=rng.uniform(80, 320, n),
                    pkts=rng.integers(1, 4, n),
                    syn=np.zeros(n, dtype=int),
                    dur=rng.uniform(0.01, 0.4, n),
                    dns_entropy=rng.normal(3.75, 0.25, n),  # high, but below tunnelling
                    ext=np.ones(n, dtype=int),
                )
            elif benign_kind == "admin_maintenance":
                strangers = np.setdiff1d(np.arange(n_hosts), peers[hi])
                k = int(min(len(strangers), rng.integers(3, 7)))
                if k > 0:
                    targets = rng.choice(strangers, size=k, replace=False)
                    n = k * int(rng.integers(1, 4))
                    _add(
                        f_cols,
                        ts=w_start + rng.integers(0, WINDOW_S, n),
                        src=np.full(n, hi),
                        dst=rng.choice(targets, size=n),
                        dport=rng.choice(ADMIN_PORTS, size=n),
                        bytes_out=rng.lognormal(bo_mu[hi] - 0.3, 0.7, n),
                        bytes_in=rng.lognormal(bi_mu[hi] - 0.5, 0.7, n),
                        pkts=rng.integers(10, 150, n),
                        syn=(rng.random(n) < 0.3).astype(int),
                        dur=rng.uniform(1, 30, n),
                        dns_entropy=np.zeros(n),
                        ext=np.zeros(n, dtype=int),
                    )
                    admin = users[int(rng.choice(host_users[hi]))]
                    for t in targets:
                        a_rows.append(
                            (w_start + int(rng.integers(0, WINDOW_S)), admin, hosts[hi], hosts[int(t)], 1, 10)
                        )
            elif benign_kind == "password_reset_storm":
                user = users[int(rng.choice(host_users[hi]))]
                target = hosts[int(rng.choice(peers[hi]))]
                for _ in range(int(rng.integers(15, 45))):
                    a_rows.append(
                        (w_start + int(rng.integers(0, WINDOW_S)), user, hosts[hi], target, 0, 3)
                    )
                a_rows.append((w_start + WINDOW_S - 2, user, hosts[hi], target, 1, 3))

            if attack is None:
                continue
            stealth = bool(rng.random() < stealth_share)
            # ---------------- attack overlay ----------------
            if attack == "portscan":
                # 'stealth' = slow scan spread thin enough to hide in the noise
                n = int(rng.integers(25, 70) if stealth else rng.integers(180, 600))
                _add(
                    f_cols,
                    ts=w_start + rng.integers(0, WINDOW_S, n),
                    src=np.full(n, hi),
                    dst=rng.integers(0, n_hosts, n),
                    dport=rng.integers(1, 10000, n),
                    bytes_out=rng.uniform(40, 160, n),
                    bytes_in=rng.uniform(0, 80, n),
                    pkts=rng.integers(1, 3, n),
                    syn=np.ones(n, dtype=int),
                    dur=rng.uniform(0.01, 0.4, n),
                    dns_entropy=np.zeros(n),
                    ext=np.zeros(n, dtype=int),
                )
            elif attack == "dos":
                n = int(rng.integers(400, 1200))
                _add(
                    f_cols,
                    ts=w_start + rng.integers(0, WINDOW_S, n),
                    src=np.full(n, hi),
                    dst=np.full(n, int(rng.choice(peers[hi]))),
                    dport=np.full(n, int(rng.choice([80, 443]))),
                    bytes_out=rng.uniform(60, 400, n),
                    bytes_in=rng.uniform(0, 60, n),
                    pkts=rng.integers(1, 6, n),
                    syn=(rng.random(n) < 0.85).astype(int),
                    dur=rng.uniform(0.01, 0.6, n),
                    dns_entropy=np.zeros(n),
                    ext=np.zeros(n, dtype=int),
                )
            elif attack == "brute_force":
                n = int(rng.integers(15, 45) if stealth else rng.integers(60, 220))
                port = int(rng.choice([22, 3389]))
                _add(
                    f_cols,
                    ts=w_start + rng.integers(0, WINDOW_S, n),
                    src=np.full(n, hi),
                    dst=np.full(n, int(rng.choice(peers[hi]))),
                    dport=np.full(n, port),
                    bytes_out=rng.uniform(200, 900, n),
                    bytes_in=rng.uniform(200, 900, n),
                    pkts=rng.integers(4, 14, n),
                    syn=(rng.random(n) < 0.5).astype(int),
                    dur=rng.uniform(0.2, 2.0, n),
                    dns_entropy=np.zeros(n),
                    ext=np.zeros(n, dtype=int),
                )
                target = hosts[int(rng.choice(peers[hi]))]
                user = users[int(rng.integers(0, n_users))]
                # stealth = low-and-slow password spraying, not a loud burst
                for _ in range(int(rng.integers(8, 22) if stealth else rng.integers(40, 140))):
                    a_rows.append(
                        (w_start + int(rng.integers(0, WINDOW_S)), user, hosts[hi], target, 0, 3)
                    )
                if rng.random() < 0.4:  # eventual success
                    a_rows.append((w_start + WINDOW_S - 1, user, hosts[hi], target, 1, 10))
            elif attack == "exfil":
                n = int(rng.integers(3, 12))
                bump = 1.5 if stealth else 3.4  # stealth = staged, low-volume egress
                _add(
                    f_cols,
                    ts=w_start + rng.integers(0, WINDOW_S, n),
                    src=np.full(n, hi),
                    dst=rng.integers(0, n_hosts, n),
                    dport=np.full(n, int(rng.choice([443, 8443, 22]))),
                    bytes_out=rng.lognormal(bo_mu[hi] + bump, 0.5, n),
                    bytes_in=rng.uniform(500, 5000, n),
                    pkts=rng.integers(200, 4000, n),
                    syn=np.zeros(n, dtype=int),
                    dur=rng.uniform(20, 260, n),
                    dns_entropy=np.zeros(n),
                    ext=np.ones(n, dtype=int),
                )
            elif attack == "dns_tunnel":
                n = int(rng.integers(40, 110) if stealth else rng.integers(120, 400))
                _add(
                    f_cols,
                    ts=w_start + rng.integers(0, WINDOW_S, n),
                    src=np.full(n, hi),
                    dst=rng.integers(0, n_hosts, n),
                    dport=np.full(n, 53),
                    bytes_out=rng.uniform(120, 420, n),
                    bytes_in=rng.uniform(80, 300, n),
                    pkts=rng.integers(1, 4, n),
                    syn=np.zeros(n, dtype=int),
                    dur=rng.uniform(0.01, 0.5, n),
                    dns_entropy=rng.normal(4.05 if stealth else 4.35, 0.20, n),
                    ext=np.ones(n, dtype=int),
                )
            elif attack == "lateral_movement":
                # new admin-port edges to hosts this source never talks to
                strangers = np.setdiff1d(np.arange(n_hosts), peers[hi])
                k = int(min(len(strangers), rng.integers(2, 4) if stealth else rng.integers(4, 10)))
                targets = rng.choice(strangers, size=k, replace=False)
                n = k * int(rng.integers(2, 5))
                dst = rng.choice(targets, size=n)
                _add(
                    f_cols,
                    ts=w_start + rng.integers(0, WINDOW_S, n),
                    src=np.full(n, hi),
                    dst=dst,
                    dport=rng.choice(ADMIN_PORTS, size=n),
                    bytes_out=rng.lognormal(bo_mu[hi] - 0.2, 0.7, n),
                    bytes_in=rng.lognormal(bi_mu[hi] - 0.4, 0.7, n),
                    pkts=rng.integers(10, 200, n),
                    syn=(rng.random(n) < 0.3).astype(int),
                    dur=rng.uniform(1, 40, n),
                    dns_entropy=np.zeros(n),
                    ext=np.zeros(n, dtype=int),
                )
                user = users[int(rng.choice(host_users[hi]))]
                for t in targets:
                    a_rows.append(
                        (w_start + int(rng.integers(0, WINDOW_S)), user, hosts[hi], hosts[int(t)], 1, 10)
                    )

    flows = pd.DataFrame({k: np.concatenate(v) for k, v in f_cols.items()})
    flows["src"] = [hosts[i] for i in flows["src"].astype(int)]
    flows["dst"] = [hosts[i] for i in flows["dst"].astype(int)]
    flows["dport"] = flows["dport"].astype(int)
    flows["ts"] = flows["ts"].astype(np.int64)
    flows = flows.sort_values("ts", ignore_index=True)

    auth = pd.DataFrame(
        a_rows, columns=["ts", "user", "src_host", "dst_host", "success", "logon_type"]
    ).sort_values("ts", ignore_index=True)

    incidents = pd.DataFrame(
        [
            {"entity": hosts[hi], "win": start_ts + wi * WINDOW_S, "attack": a}
            for (hi, wi), a in sorted(plan.items())
        ]
    )

    benign_anomalies = pd.DataFrame(
        [
            {"entity": hosts[hi], "win": start_ts + wi * WINDOW_S, "kind": k}
            for (hi, wi), k in sorted(benign_plan.items())
        ],
        columns=["entity", "win", "kind"],
    )

    return Telemetry(
        flows=flows,
        auth=auth,
        incidents=incidents,
        benign_anomalies=benign_anomalies,
        config={
            "seed": seed,
            "n_hosts": n_hosts,
            "n_users": n_users,
            "days": days,
            "window_s": WINDOW_S,
            "planned_base_rate": base_rate,
            "planned_benign_anomaly_rate": benign_anomaly_rate,
            "stealth_share": stealth_share,
            "start_ts": start_ts,
        },
    )


def _add(store: Dict[str, List[np.ndarray]], **cols) -> None:
    for k, v in cols.items():
        store[k].append(np.asarray(v))


if __name__ == "__main__":  # pragma: no cover
    import json

    t = generate()
    print(json.dumps(t.summary(), indent=2, default=str))
