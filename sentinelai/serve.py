"""Runnable entrypoint for the scoring service.

Boot loads a registered model from the registry. It does not fit one. Fitting
takes roughly two minutes and belongs offline, in the CLI:

    python3 -m sentinelai.registry fit --days 4 --stage staging
    python3 -m sentinelai.registry promote <version> --to production

Then:

    export SENTINELAI_JWT_SECRET="$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
    python3 -m sentinelai.serve --port 8088

The old behaviour is still available behind --fit-if-empty for a one-command
demo, but it is opt-in. A service that silently spends two minutes training on
startup will be killed by every orchestrator's readiness probe, and the fix is
to stop doing it rather than to lengthen the timeout.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np

from . import api
from .pipeline import ALL_FEATURES
from .soar import Policy, ResponseEngine


def _score_fn(model: Any):
    def score_fn(features: Dict[str, float]) -> float:
        row = np.array([[float(features.get(f, 0.0)) for f in ALL_FEATURES]])
        return float(model.proba(row, prior_shift=True)[0])
    return score_fn


def build_service(model: Any, artifacts: str) -> api.ScoringService:
    alerts_path = Path(artifacts) / "alerts.json"
    alerts = json.loads(alerts_path.read_text(encoding="utf-8")) if alerts_path.exists() else []
    # Dry-run responder: decisions are recorded and audited, never executed.
    responder = ResponseEngine(Policy(protected_assets=("h000", "h001")), execute=False)
    return api.ScoringService(ALL_FEATURES, _score_fn(model), alerts=alerts,
                              responder=responder)


def load_model(models_dir: str, store: Any, fit_if_empty: bool,
               days: float, seed: int) -> Tuple[Any, Optional[dict]]:
    """Return (model, manifest). Fits only when explicitly permitted."""
    from .registry import Registry

    registry = Registry(models_dir, store=store)
    found = registry.load_production()
    if found is not None:
        model, manifest = found
        return model, manifest

    if not fit_if_empty:
        raise SystemExit(
            "no production model in " + models_dir + "\n"
            "  fit one:     python3 -m sentinelai.registry fit --days 4\n"
            "  promote it:  python3 -m sentinelai.registry promote <version> --to production\n"
            "  or re-run with --fit-if-empty to fit on startup (slow)")

    from .pipeline import fit_pipeline

    print("no production model; fitting on %.1f day(s) (this is the slow part)..."
          % days, flush=True)
    t0 = time.time()
    fit = fit_pipeline(seed=seed, days=days)
    print("fitted in %.1fs" % (time.time() - t0), flush=True)
    return fit["model"], None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8088)
    ap.add_argument("--artifacts", default="artifacts")
    ap.add_argument("--models", default="models")
    ap.add_argument("--db", default="data/sentinelai.db")
    ap.add_argument("--fit-if-empty", action="store_true",
                    help="fit on startup when the registry has no production model")
    ap.add_argument("--days", type=float, default=1.5,
                    help="telemetry days, only used with --fit-if-empty")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-worker", action="store_true",
                    help="do not run the background drain/drift/retention loop")
    ap.add_argument("--execute", action="store_true",
                    help="allow SOAR actions to actually execute (default dry-run)")
    args = ap.parse_args()

    secret = os.environ.get("SENTINELAI_JWT_SECRET")
    if not secret:
        raise SystemExit(
            "SENTINELAI_JWT_SECRET is not set. There is deliberately no default secret.\n"
            "  export SENTINELAI_JWT_SECRET=\"$(python3 -c 'import secrets;print(secrets.token_hex(32))')\""
        )

    boot0 = time.time()

    from .registry import Registry
    from .store import Store

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    store = Store(args.db)
    store.migrate()

    model, manifest = load_model(args.models, store, args.fit_if_empty,
                                 args.days, args.seed)
    registry = Registry(args.models, store=store)
    service = build_service(model, args.artifacts)

    worker = None
    if not args.no_worker:
        from .worker import Worker, default_bus

        worker = Worker(store, default_bus(store)).start()

    server = api.serve(service, host=args.host, port=args.port, store=store,
                       execute=args.execute, registry=registry)

    boot = time.time() - boot0
    version = (manifest or {}).get("version", "unregistered")
    print("\nready on http://%s:%d in %.2fs" % (args.host, args.port, boot))
    print("model %s | responder %s | worker %s"
          % (version, "live" if args.execute else "dry-run",
             "off" if args.no_worker else "on"))
    print("tokens (1h, aud=sentinelai):")
    for role in ("viewer", "analyst", "responder", "admin"):
        print("  %-9s %s" % (role, api.issue_token(secret, "demo-" + role, role)))
    print("\nv1 (frozen): GET /healthz | GET /v1/alerts | POST /v1/score")
    print("             POST /v1/feedback | POST /v1/respond | GET /v1/audit")
    print("v2:          GET /v2/alerts | GET /v2/cases | PATCH /v2/cases/{id}")
    print("             GET /v2/models | POST /v2/models/{v}/promote | GET /v2/drift")
    print("ops:         GET /readyz | GET /metrics | GET /openapi.json")
    print("Ctrl-C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        server.shutdown()
        if worker is not None:
            worker.stop()
        store.close_all()


if __name__ == "__main__":
    main()
