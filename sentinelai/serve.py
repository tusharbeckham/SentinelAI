"""Runnable entrypoint for the scoring service.

Fits the ensemble, wires it behind the authenticated HTTP layer, prints ready-to-use
tokens for each role, and serves until interrupted.

    export SENTINELAI_JWT_SECRET="$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
    python3 -m sentinelai.serve --days 1.5 --port 8088
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Dict

import numpy as np

from . import api
from .pipeline import ALL_FEATURES, Ensemble, fit_pipeline
from .soar import Policy, ResponseEngine


def build_service(days: float, seed: int, artifacts: str) -> api.ScoringService:
    print(f"fitting ensemble on {days} day(s) of telemetry (this is the slow part)...", flush=True)
    t0 = time.time()
    fit = fit_pipeline(seed=seed, days=days)
    model: Ensemble = fit["model"]
    prior = fit["deployment_prior"]
    print(f"fitted in {time.time() - t0:.1f}s", flush=True)

    def score_fn(features: Dict[str, float]) -> float:
        row = np.array([[float(features.get(f, 0.0)) for f in ALL_FEATURES]])
        return float(model.proba(row, prior_shift=True)[0])

    alerts_path = Path(artifacts) / "alerts.json"
    alerts = json.loads(alerts_path.read_text(encoding="utf-8")) if alerts_path.exists() else []

    # Dry-run responder: decisions are recorded and audited, never executed.
    responder = ResponseEngine(Policy(protected_assets=("h000", "h001")), execute=False)
    print(f"deployment prior {prior:.1e} · {len(alerts)} alerts loaded · responder in dry-run", flush=True)
    return api.ScoringService(ALL_FEATURES, score_fn, alerts=alerts, responder=responder)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8088)
    ap.add_argument("--days", type=float, default=1.5, help="telemetry days to fit on; smaller is faster")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--artifacts", default="artifacts")
    args = ap.parse_args()

    secret = os.environ.get("SENTINELAI_JWT_SECRET")
    if not secret:
        raise SystemExit(
            "SENTINELAI_JWT_SECRET is not set. There is deliberately no default secret.\n"
            "  export SENTINELAI_JWT_SECRET=\"$(python3 -c 'import secrets;print(secrets.token_hex(32))')\""
        )

    service = build_service(args.days, args.seed, args.artifacts)
    server = api.serve(service, host=args.host, port=args.port)

    print("\nready on http://%s:%d" % (args.host, args.port))
    print("tokens (1h, aud=sentinelai):")
    for role in ("viewer", "analyst", "responder", "admin"):
        print(f"  {role:<9} {api.issue_token(secret, f'demo-{role}', role)}")
    print("\nroutes: GET /healthz | GET /v1/alerts (viewer) | POST /v1/score (analyst)")
    print("        POST /v1/feedback (analyst) | POST /v1/respond (responder) | GET /v1/audit (admin)")
    print("Ctrl-C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
