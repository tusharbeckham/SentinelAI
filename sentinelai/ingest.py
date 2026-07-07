"""Real-dataset loaders: CICIDS2017 and UNSW-NB15 -> the internal flow contract.

The whole point of keeping `features.py` a pure function over a documented flow
schema is that swapping the data source should touch exactly one module. This is
that module. It maps two public IDS corpora onto the same eleven flow columns the
synthetic generator emits, so the identical pipeline, ensemble, explainer, SOAR
policy and dashboard run unchanged on real capture data.

WHAT IS HONESTLY LOST WHEN YOU DO THIS
--------------------------------------
Neither corpus is a drop-in replacement, and pretending otherwise is how papers
end up with results that cannot be reproduced. The degradations are explicit in
`LOADER_CAVEATS` and are attached to every dataset this module returns, so they
travel with the data instead of living in someone's memory:

* **No authentication logs.** Both corpora are flow-only. The 6 auth features and
  all 6 graph features collapse to zero, so the auth-graph leg is inert and the
  ensemble degrades to two legs. Lateral-movement detection is simply not
  measurable on these datasets.
* **No DNS payloads.** `dns_entropy_max` cannot be computed from flow records, so
  the DNS-tunnelling signal is weaker than on the synthetic corpus.
* **Entity semantics differ.** Here an "entity" is a source IP, not a managed
  host, so per-entity baselines are noisier and short-lived scanner IPs get very
  little history.
* **CICIDS2017 is known-defective.** See Engelen et al. (WTMC 2021), Lanvin et al.
  (2022) and Dube (JCVHT 2024): duplicated rows, mislabelled windows, and a
  payload artefact that lets trivial models reach ~0.99. The loader is provided
  for comparability with published work, with `strict_dedup=True` by default to
  remove the exact-duplicate rows, and results from it should be reported as
  "on a dataset with documented labelling errors", never as ground truth.
* **UNSW-NB15 has no TCP-flag column**, so `syn` is approximated from the
  connection `state` field. It is a proxy, and it is labelled as one.

Usage:
    from sentinelai.ingest import load_cicids2017
    tel = load_cicids2017(["Friday-WorkingHours-Morning.pcap_ISCX.csv"])
    df = build_dataset(tel)          # identical downstream contract
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

import numpy as np
import pandas as pd

from .synth import WINDOW_S, Telemetry

FLOW_COLUMNS: Sequence[str] = (
    "ts",
    "src",
    "dst",
    "dport",
    "bytes_out",
    "bytes_in",
    "pkts",
    "syn",
    "dur",
    "dns_entropy",
    "ext",
)

AUTH_COLUMNS: Sequence[str] = ("ts", "user", "src_host", "dst_host", "success", "logon_type")

LOADER_CAVEATS: Dict[str, List[str]] = {
    "cicids2017": [
        "flow-only: all auth and graph features are zero; the auth-graph leg is inert",
        "no DNS payloads: dns_entropy is zero, weakening the DNS-tunnel signal",
        "documented labelling and feature-extraction errors (Engelen 2021, Lanvin 2022, Dube 2024)",
        "entity = source IP, not a managed host, so per-entity baselines are noisier",
    ],
    "unsw-nb15": [
        "flow-only: all auth and graph features are zero; the auth-graph leg is inert",
        "no DNS payloads: dns_entropy is zero",
        "no TCP flag counts: 'syn' is approximated from the connection state field",
        "entity = source IP, not a managed host",
    ],
}

# Attack-family names are normalised onto the internal vocabulary so per-family
# recall tables are comparable across corpora. Anything unmapped is kept under
# its own lowercase name rather than being silently dropped into 'benign'.
FAMILY_ALIASES: Dict[str, str] = {
    "benign": "benign",
    "normal": "benign",
    "portscan": "portscan",
    "port scan": "portscan",
    "reconnaissance": "portscan",
    "dos": "dos",
    "ddos": "dos",
    "dos hulk": "dos",
    "dos goldeneye": "dos",
    "dos slowloris": "dos",
    "dos slowhttptest": "dos",
    "ftp-patator": "brute_force",
    "ssh-patator": "brute_force",
    "web attack \ufffd brute force": "brute_force",
    "web attack - brute force": "brute_force",
    "bot": "exfil",
    "infiltration": "lateral_movement",
    "backdoor": "lateral_movement",
    "backdoors": "lateral_movement",
    "exploits": "lateral_movement",
    "worms": "lateral_movement",
    "generic": "dos",
    "fuzzers": "portscan",
    "analysis": "portscan",
    "shellcode": "lateral_movement",
}


def _normalise_family(label: object) -> str:
    s = str(label).strip().lower()
    if not s or s in {"nan", "0"}:
        return "benign"
    return FAMILY_ALIASES.get(s, s.replace(" ", "_"))


def _is_external(ip: str) -> int:
    """1 when the destination is outside RFC1918/loopback space."""
    try:
        addr = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return 0
    return int(not (addr.is_private or addr.is_loopback or addr.is_link_local))


def _read_concat(paths: Iterable[str | Path], **kwargs) -> pd.DataFrame:
    frames = []
    for p in paths:
        path = Path(p)
        if not path.exists():
            raise FileNotFoundError(f"dataset file not found: {path}")
        frame = pd.read_csv(path, **kwargs)
        # CICIDS2017 ships column names with leading/trailing spaces.
        frame.columns = [str(c).strip() for c in frame.columns]
        frames.append(frame)
    if not frames:
        raise ValueError("no input files given")
    return pd.concat(frames, ignore_index=True)


def _require(frame: pd.DataFrame, columns: Sequence[str], dataset: str) -> None\
        :
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise KeyError(
            f"{dataset}: missing expected column(s) {missing}. "
            f"Found columns: {sorted(frame.columns)[:12]}..."
        )


def _assemble(
    flows: pd.DataFrame, families: pd.Series, dataset: str, config_extra: Dict[str, object]
) -> Telemetry:
    """Window the flows, derive entity-window ground truth, build a Telemetry.

    A window is labelled with an attack family if ANY flow inside it carries that
    family. Where two families overlap in one window the more specific (non-DoS)
    one wins, because DoS labels are by far the most numerous in both corpora and
    would otherwise mask everything else.
    """
    flows = flows.copy()
    flows["ts"] = flows["ts"].astype("int64")
    flows = flows.sort_values("ts", ignore_index=True)

    win = (flows["ts"] // WINDOW_S) * WINDOW_S
    labelled = pd.DataFrame(
        {"entity": flows["src"].astype(str), "win": win.astype("int64"), "attack": families.values}
    )
    attacks = labelled[labelled["attack"] != "benign"]

    priority = {"dos": 0}  # everything else outranks dos
    if len(attacks):
        attacks = attacks.assign(_rank=attacks["attack"].map(lambda f: priority.get(f, 1)))
        incidents = (
            attacks.sort_values("_rank", ascending=False)
            .drop_duplicates(subset=["entity", "win"], keep="first")
            .loc[:, ["entity", "win", "attack"]]
            .reset_index(drop=True)
        )
    else:
        incidents = pd.DataFrame(columns=["entity", "win", "attack"])

    auth = pd.DataFrame({c: pd.Series(dtype="float64") for c in AUTH_COLUMNS})
    benign_anomalies = pd.DataFrame(columns=["entity", "win", "kind"])

    config = {
        "source": dataset,
        "synthetic": False,
        "window_seconds": WINDOW_S,
        "days": round(float(flows["ts"].max() - flows["ts"].min()) / 86400.0, 3),
        "flows": int(len(flows)),
        "auth_events": 0,
        "incident_windows": int(len(incidents)),
        "benign_anomaly_windows": 0,
        "entities": int(flows["src"].nunique()),
        "attack_families": sorted(incidents["attack"].unique().tolist()) if len(incidents) else [],
        "caveats": LOADER_CAVEATS.get(dataset, []),
        **config_extra,
    }

    return Telemetry(
        flows=flows.loc[:, list(FLOW_COLUMNS)],
        auth=auth,
        incidents=incidents,
        benign_anomalies=benign_anomalies,
        config=config,
    )


def load_cicids2017(
    paths: Sequence[str | Path], strict_dedup: bool = True, chunk_rows: int | None = None
) -> Telemetry:
    """Load one or more CICIDS2017 `*_ISCX.csv` flow files.

    `strict_dedup` removes exact duplicate rows, which are a documented defect of
    this corpus and inflate every metric if left in.
    """
    frame = _read_concat(paths, low_memory=False, nrows=chunk_rows)
    _require(
        frame,
        [
            "Source IP",
            "Destination IP",
            "Destination Port",
            "Timestamp",
            "Flow Duration",
            "Total Fwd Packets",
            "Total Backward Packets",
            "Label",
        ],
        "cicids2017",
    )

    n_before = len(frame)
    if strict_dedup:
        frame = frame.drop_duplicates().reset_index(drop=True)

    fwd_bytes = _first_present(frame, ["Total Length of Fwd Packets", "Total Length of Fwd Packet"])
    bwd_bytes = _first_present(frame, ["Total Length of Bwd Packets", "Total Length of Bwd Packet"])
    syn = _first_present(frame, ["SYN Flag Count"], default=0.0)

    ts = pd.to_datetime(frame["Timestamp"], errors="coerce", dayfirst=True)
    ts = ts.ffill().bfill()

    flows = pd.DataFrame(
        {
            "ts": (ts.astype("int64") // 10**9),
            "src": frame["Source IP"].astype(str),
            "dst": frame["Destination IP"].astype(str),
            "dport": pd.to_numeric(frame["Destination Port"], errors="coerce").fillna(0).astype(int),
            "bytes_out": pd.to_numeric(fwd_bytes, errors="coerce").fillna(0.0),
            "bytes_in": pd.to_numeric(bwd_bytes, errors="coerce").fillna(0.0),
            "pkts": (
                pd.to_numeric(frame["Total Fwd Packets"], errors="coerce").fillna(0)
                + pd.to_numeric(frame["Total Backward Packets"], errors="coerce").fillna(0)
            ),
            "syn": (pd.to_numeric(syn, errors="coerce").fillna(0) > 0).astype(int),
            # Flow Duration is microseconds in this corpus.
            "dur": pd.to_numeric(frame["Flow Duration"], errors="coerce").fillna(0.0) / 1e6,
            "dns_entropy": 0.0,
            "ext": frame["Destination IP"].map(_is_external),
        }
    )
    families = frame["Label"].map(_normalise_family)
    return _assemble(
        flows,
        families,
        "cicids2017",
        {"rows_read": int(n_before), "rows_after_dedup": int(len(frame))},
    )


def load_unsw_nb15(paths: Sequence[str | Path], header: bool = True) -> Telemetry:
    """Load UNSW-NB15 CSV parts (the 49-feature flow records)."""
    frame = _read_concat(paths, low_memory=False) if header else _read_concat(paths, header=None)
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    _require(frame, ["srcip", "dstip", "dsport", "sbytes", "dbytes", "dur", "stime"], "unsw-nb15")

    spkts = _first_present(frame, ["spkts", "spkts "], default=0.0)
    dpkts = _first_present(frame, ["dpkts"], default=0.0)
    state = _first_present(frame, ["state"], default="")

    flows = pd.DataFrame(
        {
            "ts": pd.to_numeric(frame["stime"], errors="coerce").fillna(0).astype("int64"),
            "src": frame["srcip"].astype(str),
            "dst": frame["dstip"].astype(str),
            "dport": pd.to_numeric(frame["dsport"], errors="coerce").fillna(0).astype(int),
            "bytes_out": pd.to_numeric(frame["sbytes"], errors="coerce").fillna(0.0),
            "bytes_in": pd.to_numeric(frame["dbytes"], errors="coerce").fillna(0.0),
            "pkts": (
                pd.to_numeric(spkts, errors="coerce").fillna(0)
                + pd.to_numeric(dpkts, errors="coerce").fillna(0)
            ),
            # Proxy, not ground truth: REQ/INT are half-open or request-only
            # states, which is the closest available stand-in for a SYN count.
            "syn": pd.Series(state).astype(str).str.upper().isin({"REQ", "INT"}).astype(int),
            "dur": pd.to_numeric(frame["dur"], errors="coerce").fillna(0.0),
            "dns_entropy": 0.0,
            "ext": frame["dstip"].map(_is_external),
        }
    )

    if "attack_cat" in frame.columns:
        families = frame["attack_cat"].map(_normalise_family)
    else:
        label = _first_present(frame, ["label"], default=0)
        families = pd.Series(
            np.where(pd.to_numeric(label, errors="coerce").fillna(0) > 0, "unknown_attack", "benign")
        )

    return _assemble(flows, families, "unsw-nb15", {"rows_read": int(len(frame))})


def _first_present(frame: pd.DataFrame, names: Sequence[str], default=None):
    for n in names:
        if n in frame.columns:
            return frame[n]
    if default is None:
        raise KeyError(f"none of the expected columns {names} are present")
    return pd.Series([default] * len(frame), index=frame.index)
