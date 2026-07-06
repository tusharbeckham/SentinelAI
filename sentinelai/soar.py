"""Policy-driven response engine (SOAR) with a tamper-evident audit log.

Design rules that make automated containment defensible:

  * Actions are chosen from CALIBRATED probability + asset criticality + blast
    radius, never from a raw anomaly score.
  * Auto-containment requires (a) probability above the auto threshold, (b) the
    explanation to be dominated by features on an allow-list for that playbook
    (so we never auto-block on a single opaque feature), and (c) the target not
    to be on the protected-asset list (domain controllers, prod DBs) - those
    always escalate to a human.
  * Every decision is appended to a hash-chained audit log
    (h_n = SHA256(h_{n-1} || record)), so any retro-active edit of response
    history is detectable. This mirrors the integrity requirement any real
    IR team will have for automated actions.
  * Dry-run is the default; `execute=True` is required to emit real actions.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Dict, List, Sequence

AUTO_ALLOWED_FEATURES = {
    "portscan": {"distinct_dports", "z_distinct_dports", "syn_ratio", "port_entropy", "flow_count", "z_flow_count", "short_flow_ratio"},
    "dos": {"pkts_sum", "flow_count", "z_flow_count", "syn_ratio", "mean_pkt_size"},
    "brute_force": {"failed_logins", "failed_login_ratio", "z_failed_logins", "admin_port_ratio"},
    "dns_tunnel": {"dns_entropy_max", "dns_flow_ratio", "ext_dst_ratio"},
    "exfil": {"bytes_out_sum", "z_bytes_out_sum", "out_in_ratio", "bytes_out_p95", "ext_dst_ratio"},
    "lateral_movement": {"g_new_edges", "g_chain_depth", "graph_score", "g_breadth_z", "admin_port_ratio"},
}


@dataclass
class Policy:
    auto_threshold: float = 0.90
    review_threshold: float = 0.50
    enrich_threshold: float = 0.20
    protected_assets: Sequence[str] = ()
    max_auto_actions_per_hour: int = 5
    require_explanation_support: float = 0.55  # share of positive attribution


@dataclass
class AuditLog:
    records: List[dict] = field(default_factory=list)
    head: str = "0" * 64

    def append(self, record: dict) -> dict:
        payload = json.dumps(record, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256((self.head + payload).encode()).hexdigest()
        entry = {**record, "prev_hash": self.head, "hash": digest}
        self.records.append(entry)
        self.head = digest
        return entry

    def verify(self) -> bool:
        head = "0" * 64
        for entry in self.records:
            body = {k: v for k, v in entry.items() if k not in ("prev_hash", "hash")}
            payload = json.dumps(body, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256((head + payload).encode()).hexdigest()
            if entry["prev_hash"] != head or entry["hash"] != digest:
                return False
            head = digest
        return True


PLAYBOOKS: Dict[str, dict] = {
    "portscan": {"auto": "block_src_ip_edge_firewall", "manual": "open_case_scan_triage"},
    "dos": {"auto": "rate_limit_src_at_edge", "manual": "open_case_volumetric"},
    "brute_force": {"auto": "lock_account_and_force_reset", "manual": "open_case_credential_attack"},
    "dns_tunnel": {"auto": "sinkhole_domain", "manual": "open_case_c2_suspect"},
    "exfil": {"auto": "quarantine_host_egress", "manual": "open_case_data_loss"},
    "lateral_movement": {"auto": "isolate_host_edr", "manual": "open_case_lateral_movement"},
    "unknown": {"auto": "quarantine_host_egress", "manual": "open_case_generic_anomaly"},
}


class ResponseEngine:
    def __init__(self, policy: Policy | None = None, execute: bool = False):
        self.policy = policy or Policy()
        self.execute = execute
        self.audit = AuditLog()
        self._auto_times: List[float] = []

    def _rate_limited(self, now: float) -> bool:
        self._auto_times = [t for t in self._auto_times if now - t < 3600]
        return len(self._auto_times) >= self.policy.max_auto_actions_per_hour

    def decide(self, alert: dict, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        p = float(alert["probability"])
        entity = alert.get("entity", "unknown")
        family = alert.get("suspected_family", "unknown")
        top = alert.get("top", [])
        pos = [t for t in top if t.get("contribution", 0) > 0]
        total_pos = sum(t["contribution"] for t in pos) or 1e-9
        allow = AUTO_ALLOWED_FEATURES.get(family, set())
        supported = sum(t["contribution"] for t in pos if t["feature"] in allow) / total_pos

        reasons: List[str] = []
        if p >= self.policy.auto_threshold:
            action, mode = PLAYBOOKS.get(family, PLAYBOOKS["unknown"])["auto"], "auto_contain"
            if entity in self.policy.protected_assets:
                action, mode = PLAYBOOKS.get(family, PLAYBOOKS["unknown"])["manual"], "human_review"
                reasons.append("protected asset: containment requires human approval")
            elif supported < self.policy.require_explanation_support:
                action, mode = PLAYBOOKS.get(family, PLAYBOOKS["unknown"])["manual"], "human_review"
                reasons.append(
                    f"explanation support {supported:.2f} below {self.policy.require_explanation_support:.2f}"
                )
            elif self._rate_limited(now):
                action, mode = PLAYBOOKS.get(family, PLAYBOOKS["unknown"])["manual"], "human_review"
                reasons.append("auto-action rate limit reached")
            else:
                self._auto_times.append(now)
                reasons.append(f"probability {p:.3f} >= auto threshold, explanation consistent")
        elif p >= self.policy.review_threshold:
            action, mode = PLAYBOOKS.get(family, PLAYBOOKS["unknown"])["manual"], "human_review"
            reasons.append("medium confidence: analyst triage queue")
        elif p >= self.policy.enrich_threshold:
            action, mode = "enrich_and_watchlist", "enrich"
            reasons.append("low confidence: enrich context, no disruption")
        else:
            action, mode = "suppress", "suppress"
            reasons.append("below enrichment threshold")

        decision = {
            "ts": int(now),
            "entity": entity,
            "window": alert.get("window"),
            "probability": p,
            "suspected_family": family,
            "mode": mode,
            "action": action,
            "executed": bool(self.execute and mode == "auto_contain"),
            "explanation_support": round(float(supported), 4),
            "rationale": reasons,
        }
        return self.audit.append(decision)

    def run(self, alerts: Sequence[dict], now: float | None = None) -> List[dict]:
        base = time.time() if now is None else now
        return [self.decide(a, now=base + i) for i, a in enumerate(alerts)]

    def stats(self) -> dict:
        modes: Dict[str, int] = {}
        for r in self.audit.records:
            modes[r["mode"]] = modes.get(r["mode"], 0) + 1
        return {"decisions": len(self.audit.records), "by_mode": modes, "audit_chain_valid": self.audit.verify()}
