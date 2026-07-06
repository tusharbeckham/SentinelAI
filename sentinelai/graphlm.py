"""Graph-based lateral-movement detection over authentication logs.

Flow-level features are blind to lateral movement: each individual RDP/SMB
logon looks perfectly normal. What is abnormal is the *shape of the traversal*
in the authentication graph. So we model auth events as a temporal bipartite-ish
graph  user -> (src_host -> dst_host)  and score each (entity, window) on:

  1. new_edges      - host->host edges never observed during the baseline
  2. rarity         - surprisal  -log2 p(edge)  under the baseline edge
                      distribution with Laplace smoothing (an unseen edge on a
                      chatty host is far less surprising than on a quiet one)
  3. breadth        - fan-out of distinct destinations vs the entity's own
                      historical fan-out (robust z)
  4. chain_depth    - longest path of *new* edges traversed by one user inside
                      the window (u: A->B then B->C is the canonical
                      pass-the-hash / pivot signature)
  5. new_user_host  - user authenticating from a host they never used before

The composite `graph_score` is squashed to (0,1) so it fuses cleanly with the
other detectors, and each component is exported as its own feature so the
supervised leg and the SHAP explanations can use them directly.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Dict, Set, Tuple

import numpy as np
import pandas as pd

from .synth import WINDOW_S

GRAPH_FEATURES: Tuple[str, ...] = (
    "g_new_edges",
    "g_rarity",
    "g_breadth_z",
    "g_chain_depth",
    "g_new_user_host",
    "graph_score",
)


class AuthGraphDetector:
    """Baseline-then-score detector. `fit` sees only the training period."""

    def __init__(self, alpha: float = 0.5):
        self.alpha = alpha
        self.edge_counts: Dict[Tuple[str, str], int] = defaultdict(int)
        self.src_counts: Dict[str, int] = defaultdict(int)
        self.user_hosts: Dict[str, Set[str]] = defaultdict(set)
        self.breadth_med: Dict[str, float] = {}
        self.breadth_mad: Dict[str, float] = {}
        self.n_hosts = 1
        self.fitted = False

    def fit(self, auth: pd.DataFrame) -> "AuthGraphDetector":
        for src, dst in zip(auth["src_host"], auth["dst_host"]):
            self.edge_counts[(src, dst)] += 1
            self.src_counts[src] += 1
        for user, src in zip(auth["user"], auth["src_host"]):
            self.user_hosts[user].add(src)
        self.n_hosts = max(1, len(set(auth["src_host"]) | set(auth["dst_host"])))

        a = auth.copy()
        a["win"] = (a["ts"] // WINDOW_S) * WINDOW_S
        breadth = a.groupby(["src_host", "win"])["dst_host"].nunique()
        for host, s in breadth.groupby(level=0):
            v = s.to_numpy(dtype=float)
            self.breadth_med[host] = float(np.median(v))
            self.breadth_mad[host] = float(np.median(np.abs(v - np.median(v))))
        self.fitted = True
        return self

    # ------------------------------------------------------------------ score
    def _surprisal(self, src: str, dst: str) -> float:
        n_src = self.src_counts.get(src, 0)
        c = self.edge_counts.get((src, dst), 0)
        p = (c + self.alpha) / (n_src + self.alpha * self.n_hosts)
        return float(-np.log2(max(p, 1e-12)))

    @staticmethod
    def _longest_new_chain(edges: list[Tuple[str, str]]) -> int:
        """Longest simple path length in the sub-graph of new edges (DAG-ish BFS)."""
        if not edges:
            return 0
        adj: Dict[str, list[str]] = defaultdict(list)
        nodes: Set[str] = set()
        for s, d in set(edges):
            adj[s].append(d)
            nodes.update((s, d))
        best = 0
        for start in nodes:
            q = deque([(start, 1, {start})])
            while q:
                node, depth, seen = q.popleft()
                best = max(best, depth - 1)
                if depth > 6:
                    continue
                for nxt in adj.get(node, ()):
                    if nxt not in seen:
                        q.append((nxt, depth + 1, seen | {nxt}))
        return int(best)

    def score_windows(self, auth: pd.DataFrame) -> pd.DataFrame:
        if not self.fitted:
            raise RuntimeError("AuthGraphDetector.fit must be called first")
        a = auth.copy()
        a["win"] = (a["ts"] // WINDOW_S) * WINDOW_S

        # Chains are a property of a USER's traversal, not of a single host:
        # u: A->B then B->C is two different src_host groups. So compute the
        # longest new-edge chain per (window, user) first, then attribute it to
        # every host that participated in that traversal.
        chain_by_user: Dict[Tuple[int, str], int] = {}
        for (win, user), grp in a.groupby(["win", "user"], sort=False):
            edges = [
                (s, d)
                for s, d, ok in zip(grp["src_host"], grp["dst_host"], grp["success"])
                if ok and self.edge_counts.get((s, d), 0) == 0
            ]
            chain_by_user[(int(win), user)] = self._longest_new_chain(edges)

        rows = []
        for (host, win), grp in a.groupby(["src_host", "win"], sort=False):
            new_edges, rarities, chain_edges = 0, [], []
            new_user_host = 0
            for user, src, dst, ok in zip(
                grp["user"], grp["src_host"], grp["dst_host"], grp["success"]
            ):
                seen = self.edge_counts.get((src, dst), 0) > 0
                if not seen:
                    new_edges += 1
                    if ok:
                        chain_edges.append((src, dst))
                rarities.append(self._surprisal(src, dst))
                if src not in self.user_hosts.get(user, set()):
                    new_user_host = 1
            chain_users = {chain_by_user.get((int(win), u), 0) for u in set(grp["user"])}
            breadth = grp["dst_host"].nunique()
            med = self.breadth_med.get(host, 1.0)
            mad = self.breadth_mad.get(host, 0.0)
            scale = max(1.4826 * mad, 1.0)
            breadth_z = (breadth - med) / scale
            chain = max([self._longest_new_chain(chain_edges), *chain_users])
            rarity = float(np.mean(rarities)) if rarities else 0.0
            raw = (
                0.55 * np.log1p(new_edges)
                + 0.20 * max(0.0, rarity - 3.0)
                + 0.35 * max(0.0, breadth_z)
                + 0.90 * chain
                + 0.60 * new_user_host
            )
            rows.append(
                {
                    "entity": host,
                    "win": int(win),
                    "g_new_edges": float(new_edges),
                    "g_rarity": rarity,
                    "g_breadth_z": float(breadth_z),
                    "g_chain_depth": float(chain),
                    "g_new_user_host": float(new_user_host),
                    "graph_score": float(1.0 - np.exp(-raw / 2.5)),
                }
            )
        return pd.DataFrame(rows, columns=["entity", "win", *GRAPH_FEATURES])


def attach_graph_features(
    df: pd.DataFrame, auth: pd.DataFrame, baseline_cutoff: int
) -> Tuple[pd.DataFrame, AuthGraphDetector]:
    """Fit the auth-graph baseline on pre-cutoff data, score every window."""
    det = AuthGraphDetector().fit(auth[auth["ts"] < baseline_cutoff])
    scored = det.score_windows(auth)
    out = df.merge(scored, on=["entity", "win"], how="left")
    for c in GRAPH_FEATURES:
        out[c] = out[c].fillna(0.0)
    return out, det
