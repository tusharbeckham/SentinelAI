"""Model registry: versioned artifacts, atomic stage pointers, a promotion gate.

This is the module that removes the 136-second boot. Fitting becomes an offline
command; startup becomes a file read. See docs/backend/MODEL-REGISTRY.md.

No pickle anywhere. Loading a pickle executes arbitrary code, and a security
product that deserialises untrusted pickles is an embarrassment. Every fitted
structure here reduces to NumPy arrays, packed ragged with an offsets array and
stored with np.savez_compressed.

Two deliberate deviations from MODEL-REGISTRY.md, both documented at the point
of deviation below:

  1. content_hash is computed over the canonical array contents, not over the
     .npz file bytes. A .npz is a zip, and zip entries carry modification
     timestamps, so two byte-identical models written a second apart produce
     different file hashes. Hashing the file would have made the determinism
     check in CI decorative, which is the exact failure the hash exists to
     prevent.
  2. feature_contract.hash is computed over the ORDERED feature names, not the
     sorted ones. The models index columns positionally. Two models with the
     same features in a different order are not interchangeable, and sorting
     before hashing would let them compare equal - which would let the
     promotion gate wave through a model that scores confident garbage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

STAGES: Tuple[str, ...] = ("staging", "production", "archived")

# A version cannot jump straight to production. Something has to have been in
# staging, because staging is where the gate inputs are recorded.
LEGAL_TRANSITIONS = {
    ("staging", "production"),
    ("staging", "archived"),
    ("production", "archived"),
    ("archived", "production"),  # rollback
}

DEFAULT_PR_AUC_TOLERANCE = 0.02
MAX_ECE = 0.05

IF_KEYS: Tuple[str, ...] = ("feature", "threshold", "left", "right", "size")
GB_KEYS: Tuple[str, ...] = ("feature", "threshold_bin", "left", "right", "value")


class RegistryError(Exception):
    pass


class PromotionRefused(RegistryError):
    """The gate said no. `problems` lists every reason, not just the first.

    Reporting only the first failure turns one deploy attempt into four.
    """

    def __init__(self, problems: Sequence[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def _digest_arrays(arrays: Dict[str, Any]) -> str:
    """Deterministic hash over array contents.

    Keys are sorted so insertion order cannot change the digest. dtype and
    shape are included because the same bytes reinterpreted as a different
    dtype is a different model.
    """
    h = hashlib.sha256()
    for key in sorted(arrays):
        arr = np.asarray(arrays[key])
        h.update(key.encode("utf-8"))
        h.update(str(arr.dtype).encode("utf-8"))
        h.update(str(arr.shape).encode("utf-8"))
        h.update(np.ascontiguousarray(arr).tobytes())
    return h.hexdigest()


def feature_hash(names: Sequence[str]) -> str:
    """Hash of the ordered feature contract. Order is part of the contract."""
    joined = chr(10).join(str(n) for n in names)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _version_id(content_hash: str, when: Optional[datetime] = None) -> str:
    when = when or datetime.now(timezone.utc)
    return "v" + when.strftime("%Y%m%dT%H%M%SZ") + "-" + content_hash[:6]


# ---------------------------------------------------------------------------
# Ragged tree packing
# ---------------------------------------------------------------------------

def _pack_trees(trees: List[dict], keys: Sequence[str], prefix: str) -> Dict[str, Any]:
    """Concatenate per-tree arrays and record where each tree starts.

    A list of variable-length trees does not fit an .npz, which holds
    rectangular arrays. One flat array per field plus an offsets array is the
    standard CSR-style answer and keeps the load path a pure slice.
    """
    if not trees:
        raise RegistryError("refusing to serialise a model with no trees")
    offsets = np.zeros(len(trees) + 1, dtype=np.int64)
    for i, tree in enumerate(trees):
        offsets[i + 1] = offsets[i] + int(np.asarray(tree[keys[0]]).size)
    out: Dict[str, Any] = {prefix + "offsets": offsets}
    for key in keys:
        out[prefix + key] = np.concatenate([np.asarray(t[key]) for t in trees])
    return out


def _unpack_trees(bundle: Any, keys: Sequence[str], prefix: str) -> List[dict]:
    offsets = np.asarray(bundle[prefix + "offsets"])
    cols = {key: np.asarray(bundle[prefix + key]) for key in keys}
    trees: List[dict] = []
    for i in range(len(offsets) - 1):
        lo, hi = int(offsets[i]), int(offsets[i + 1])
        trees.append({key: cols[key][lo:hi] for key in keys})
    return trees


# ---------------------------------------------------------------------------
# Ensemble <-> arrays
# ---------------------------------------------------------------------------

def ensemble_arrays(model: Any) -> Dict[str, Any]:
    """Flatten a fitted Ensemble into the exact arrays that get written."""
    arrays: Dict[str, Any] = {}

    forest = model.iforest
    arrays.update(_pack_trees(forest.trees, IF_KEYS, "if_"))
    arrays["if_meta"] = np.array(
        [float(forest.n_trees), float(forest.sample_size), float(forest.seed),
         float(forest._c_psi)],
        dtype=np.float64,
    )

    gbdt = model.gbdt
    arrays.update(_pack_trees(gbdt.trees, GB_KEYS, "gb_"))
    arrays["gb_bin_edges"] = np.asarray(gbdt.bin_edges, dtype=np.float64)
    arrays["gb_base_score"] = np.array([float(gbdt.base_score)], dtype=np.float64)
    imp = gbdt.importance_
    arrays["gb_importance"] = (
        np.asarray(imp, dtype=np.float64) if imp is not None else np.zeros(0)
    )
    arrays["gb_params"] = np.array(json.dumps(gbdt.p, sort_keys=True))

    stacker = model.stacker
    arrays["st_w"] = np.asarray(stacker.w, dtype=np.float64)
    arrays["st_mu"] = np.asarray(stacker.mu, dtype=np.float64)
    arrays["st_sd"] = np.asarray(stacker.sd, dtype=np.float64)
    # b, train_prior and prior_shift travel together in one array so a partial
    # write cannot land an intercept without its prior shift. Omitting the
    # standardisation parameters is the documented reconstruction_error bug in
    # README section 9.2; mu and sd above are not optional.
    arrays["st_scalars"] = np.array(
        [float(stacker.b), float(stacker.train_prior), float(stacker.prior_shift)],
        dtype=np.float64,
    )
    arrays["st_names"] = np.array([str(n) for n in stacker.names])
    arrays["feature_names"] = np.array([str(n) for n in model.feature_names])
    return arrays


def ensemble_from_arrays(bundle: Any) -> Any:
    """Rebuild an Ensemble from arrays without refitting anything.

    Imports from .pipeline are deferred to call time. Importing pipeline at
    module scope pulls in pandas and the synthetic generator, which would put
    roughly a second back into the boot this module exists to shorten.
    """
    from . import ensemble as ens
    from .models.gbdt import GBDT
    from .models.iforest import IsolationForest
    from .pipeline import Ensemble

    if_meta = np.asarray(bundle["if_meta"], dtype=np.float64)
    forest = IsolationForest(
        n_trees=int(if_meta[0]), sample_size=int(if_meta[1]), seed=int(if_meta[2])
    )
    forest.trees = _unpack_trees(bundle, IF_KEYS, "if_")
    forest._c_psi = float(if_meta[3])

    params = json.loads(str(bundle["gb_params"]))
    gbdt = GBDT()
    gbdt.p = params
    gbdt.trees = _unpack_trees(bundle, GB_KEYS, "gb_")
    gbdt.bin_edges = np.asarray(bundle["gb_bin_edges"], dtype=np.float64)
    gbdt.base_score = float(np.asarray(bundle["gb_base_score"])[0])
    imp = np.asarray(bundle["gb_importance"], dtype=np.float64)
    gbdt.importance_ = imp if imp.size else None

    scalars = np.asarray(bundle["st_scalars"], dtype=np.float64)
    stacker = ens.LogisticStacker()
    stacker.w = np.asarray(bundle["st_w"], dtype=np.float64)
    stacker.mu = np.asarray(bundle["st_mu"], dtype=np.float64)
    stacker.sd = np.asarray(bundle["st_sd"], dtype=np.float64)
    stacker.b = float(scalars[0])
    stacker.train_prior = float(scalars[1])
    stacker.prior_shift = float(scalars[2])
    stacker.names = tuple(str(n) for n in np.asarray(bundle["st_names"]).tolist())

    names = tuple(str(n) for n in np.asarray(bundle["feature_names"]).tolist())
    return Ensemble(forest, gbdt, stacker, feature_names=names)


def _read_npz(path: Path) -> Dict[str, Any]:
    with np.load(path, allow_pickle=False) as handle:
        return {key: handle[key] for key in handle.files}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class Registry:
    """Filesystem-backed registry with an optional Store mirror.

    The filesystem is the source of truth for artifacts because it is what a
    container image can ship. The Store rows are an index for the API, and the
    audit log is where promotions are recorded tamper-evidently.
    """

    def __init__(self, root: str = "models", store: Any = None):
        self.root = Path(root)
        self.store = store

    # -- paths ------------------------------------------------------------
    def _dir(self, version: str) -> Path:
        if "/" in version or "\\" in version or version.startswith("."):
            raise RegistryError("illegal version id: " + version)
        return self.root / version

    @property
    def _stages_path(self) -> Path:
        return self.root / "stages.json"

    # -- stage pointer ----------------------------------------------------
    def stages(self) -> Dict[str, str]:
        if not self._stages_path.exists():
            return {}
        try:
            return json.loads(self._stages_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise RegistryError("stages.json is not valid JSON")

    def _write_stages(self, mapping: Dict[str, str]) -> None:
        """Write the pointer atomically.

        Write-temp-then-replace. os.replace is atomic on POSIX and on Windows,
        so a crash mid-promotion leaves either the old pointer or the new one,
        never half of a filename.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.root), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(mapping, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self._stages_path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # -- write ------------------------------------------------------------
    def save(
        self,
        model: Any,
        metrics: Dict[str, float],
        training: Dict[str, Any],
        operating_point: Dict[str, Any],
        deployment_prior: float = 1e-4,
        lineage: Optional[Dict[str, Any]] = None,
        stage: str = "staging",
    ) -> str:
        if stage not in STAGES:
            raise RegistryError("unknown stage: " + str(stage))
        if stage == "production":
            # Saving straight to production would bypass the gate entirely.
            raise RegistryError("save to staging, then promote; the gate lives on promote")

        arrays = ensemble_arrays(model)
        names = [str(n) for n in model.feature_names]
        content_hash = _digest_arrays(arrays)
        version = _version_id(content_hash)
        # Identical arrays saved twice in the same second would collide on the
        # id, and the second write would overwrite the first manifest - which
        # may already be the promoted production manifest. Re-saving something
        # genuinely identical is a no-op; anything else gets its own id.
        base, suffix = version, 1
        while self._dir(version).exists():
            prior_path = self._dir(version) / "manifest.json"
            if prior_path.exists():
                prior = json.loads(prior_path.read_text(encoding="utf-8"))
                if (prior.get("metrics") == metrics
                        and prior.get("training") == training
                        and prior.get("operating_point") == operating_point):
                    return version
            suffix += 1
            version = base + "-" + str(suffix)
        target = self._dir(version)
        target.mkdir(parents=True, exist_ok=True)

        np.savez_compressed(target / "ensemble.npz", **arrays)

        contract = {"n": len(names), "hash": feature_hash(names), "names": names}
        (target / "features.json").write_text(
            json.dumps(contract, indent=2), encoding="utf-8"
        )
        (target / "metrics.json").write_text(
            json.dumps(metrics, indent=2, sort_keys=True), encoding="utf-8"
        )

        manifest = {
            "version": version,
            "created_at": _utc_now(),
            "content_hash": content_hash,
            "feature_contract": contract,
            "training": training,
            "metrics": metrics,
            "operating_point": operating_point,
            "deployment_prior": float(deployment_prior),
            "stacker": {
                "names": list(model.stacker.names),
                "coef": [float(v) for v in np.asarray(model.stacker.w).tolist()],
                "intercept": float(model.stacker.b),
                "prior_shift": float(model.stacker.prior_shift),
            },
            "lineage": lineage or {"git_sha": None, "parent": None},
            "stage": stage,
        }
        (target / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )

        self._record(manifest, stage=stage, promoted_at=None, notes="saved")
        return version

    # -- read -------------------------------------------------------------
    def list_versions(self) -> List[dict]:
        if not self.root.exists():
            return []
        out: List[dict] = []
        for child in sorted(self.root.iterdir()):
            manifest_path = child / "manifest.json"
            if child.is_dir() and manifest_path.exists():
                out.append(json.loads(manifest_path.read_text(encoding="utf-8")))
        pointers = self.stages()
        by_version = {v: s for s, v in pointers.items()}
        for manifest in out:
            manifest["stage"] = by_version.get(manifest["version"], "archived")
        return out

    def show(self, version: str) -> dict:
        path = self._dir(version) / "manifest.json"
        if not path.exists():
            raise RegistryError("no such version: " + version)
        manifest = json.loads(path.read_text(encoding="utf-8"))
        by_version = {v: s for s, v in self.stages().items()}
        manifest["stage"] = by_version.get(version, "archived")
        return manifest

    def verify(self, version: str) -> bool:
        """Recompute the content hash from the stored arrays."""
        manifest = self.show(version)
        arrays = _read_npz(self._dir(version) / "ensemble.npz")
        return _digest_arrays(arrays) == manifest["content_hash"]

    def load(self, version: str) -> Any:
        path = self._dir(version) / "ensemble.npz"
        if not path.exists():
            raise RegistryError("no artifact for version: " + version)
        return ensemble_from_arrays(_read_npz(path))

    def production_version(self) -> Optional[str]:
        return self.stages().get("production")

    def load_production(self) -> Optional[Tuple[Any, dict]]:
        """Return (model, manifest) or None if nothing is promoted.

        None rather than an exception: a service with no production model
        should still start, so that the operator can reach /metrics and the
        registry CLI to fix it. /readyz reports the 503.
        """
        version = self.production_version()
        if not version:
            return None
        return self.load(version), self.show(version)

    # -- promotion --------------------------------------------------------
    def gate(self, version: str, to: str,
             tolerance: float = DEFAULT_PR_AUC_TOLERANCE) -> List[str]:
        """Every reason this promotion should be refused. Empty list means go."""
        problems: List[str] = []
        if to not in STAGES:
            return ["unknown stage: " + str(to)]

        try:
            manifest = self.show(version)
        except RegistryError as exc:
            return [str(exc)]

        current = manifest.get("stage", "archived")
        if current == to:
            problems.append("already in stage " + to)
        elif (current, to) not in LEGAL_TRANSITIONS:
            problems.append("illegal transition " + current + " -> " + to)

        try:
            if not self.verify(version):
                problems.append("content_hash mismatch; the artifact is corrupt")
        except (RegistryError, OSError, ValueError) as exc:
            problems.append("cannot verify artifact: " + str(exc))

        if to != "production":
            return problems

        metrics = manifest.get("metrics") or {}
        ece = metrics.get("ece")
        if ece is None:
            problems.append("metrics.ece is missing")
        elif float(ece) > MAX_ECE:
            # An uncalibrated model breaks the base-rate argument the whole
            # project rests on: a probability that is not calibrated makes the
            # Bayesian PPV computation meaningless.
            problems.append(
                "ece " + repr(float(ece)) + " exceeds " + repr(MAX_ECE)
            )

        incumbent_version = self.production_version()
        if incumbent_version and incumbent_version != version:
            incumbent = self.show(incumbent_version)
            mine = manifest["feature_contract"]["hash"]
            theirs = incumbent["feature_contract"]["hash"]
            if mine != theirs:
                # This one does not fail loudly at runtime. A model handed the
                # wrong feature order scores garbage confidently.
                problems.append(
                    "feature contract differs from incumbent " + incumbent_version
                )
            mine_auc = (manifest.get("metrics") or {}).get("pr_auc")
            their_auc = (incumbent.get("metrics") or {}).get("pr_auc")
            if mine_auc is None:
                problems.append("metrics.pr_auc is missing")
            elif their_auc is not None and float(mine_auc) < float(their_auc) - tolerance:
                problems.append(
                    "pr_auc " + repr(float(mine_auc)) + " is below incumbent "
                    + repr(float(their_auc)) + " by more than " + repr(tolerance)
                )
        return problems

    def promote(self, version: str, to: str = "production", actor: str = "system",
                force: bool = False,
                tolerance: float = DEFAULT_PR_AUC_TOLERANCE) -> dict:
        problems = self.gate(version, to, tolerance=tolerance)
        if problems and not force:
            raise PromotionRefused(problems)

        mapping = self.stages()
        displaced = mapping.get(to)
        if to == "production" and displaced and displaced != version:
            mapping["previous_production"] = displaced
        mapping[to] = version
        for stage, held in list(mapping.items()):
            if held == version and stage != to and stage in STAGES:
                del mapping[stage]
        self._write_stages(mapping)

        manifest = self.show(version)
        event = {
            "version": version,
            "stage": to,
            "actor": actor,
            "forced": bool(force and problems),
            "problems": problems,
            "displaced": displaced,
            "promoted_at": _utc_now(),
            "content_hash": manifest["content_hash"],
        }
        # Forcing is sometimes correct. Forcing silently never is, so the
        # override and its reasons go into the hash-chained audit log.
        self._record(manifest, stage=to, promoted_at=event["promoted_at"],
                     notes=json.dumps(event, sort_keys=True))
        if self.store is not None:
            self.store.append_audit(
                actor=actor, action="model.promote", payload=event, subject=version
            )
            self.store.publish("model.promoted", event)
        return event

    def rollback(self, actor: str = "system") -> dict:
        """Promote the version production most recently displaced.

        One word long, because the command you want at 3am should be.
        """
        mapping = self.stages()
        previous = mapping.get("previous_production")
        if not previous:
            raise RegistryError("no previous production version to roll back to")
        return self.promote(previous, "production", actor=actor, force=True)

    # -- store mirror -----------------------------------------------------
    def _record(self, manifest: dict, stage: str, promoted_at: Optional[str],
                notes: str) -> None:
        """Mirror the manifest into model_versions if a Store is attached.

        The table is narrower than MODEL-REGISTRY.md section 5 describes: it has
        no content_hash, feature_hash, promoted_by or forced columns. Rather
        than add a migration for columns that would duplicate the manifest, the
        full manifest JSON goes in `manifest` and the promotion event in
        `notes`. The authoritative record of who promoted what is the audit
        chain, which is append-only and verifiable; a plain column is neither.
        """
        if self.store is None:
            return
        metrics = manifest.get("metrics") or {}
        conn = self.store.connect()
        # The store opens connections with isolation_level=None, so this single
        # statement is its own transaction. Wrapping it in an explicit BEGIN
        # would be the executescript mistake from v3.0.0 in a new costume.
        if stage == "production":
            # model_versions carries a partial unique index allowing exactly one
            # production row. The displaced version has to be stepped down in
            # the same breath, or the insert below trips the constraint and the
            # promotion fails after the stage pointer has already moved.
            conn.execute(
                "UPDATE model_versions SET stage = 'archived'"
                " WHERE stage = 'production' AND version <> ?",
                (manifest["version"],))
        conn.execute(
            "INSERT INTO model_versions(version, stage, created_at, promoted_at,"
            " manifest, pr_auc, ece, notes) VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(version) DO UPDATE SET stage=excluded.stage,"
            " promoted_at=excluded.promoted_at, manifest=excluded.manifest,"
            " pr_auc=excluded.pr_auc, ece=excluded.ece, notes=excluded.notes",
            (
                manifest["version"], stage, manifest["created_at"], promoted_at,
                json.dumps(manifest, sort_keys=True),
                _maybe_float(metrics.get("pr_auc")),
                _maybe_float(metrics.get("ece")),
                notes,
            ),
        )


def _maybe_float(value: Any) -> Optional[float]:
    return None if value is None else float(value)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_fit(args: argparse.Namespace) -> int:
    from . import ensemble as ens
    from .features import matrix
    from .pipeline import ALL_FEATURES, fit_pipeline

    started = time.time()
    print("fitting on " + repr(args.days) + " day(s); this is the slow part", flush=True)
    fit = fit_pipeline(seed=args.seed, days=args.days)
    model = fit["model"]
    _train, _calib, test = fit["splits"]
    X_te = matrix(test, ALL_FEATURES)
    y_te = test["label"].to_numpy()
    p_te = model.proba(X_te)
    report = ens.evaluate(y_te, p_te, deployment_prior=fit["deployment_prior"])

    # evaluate() returns average_precision, not pr_auc, and returns neither
    # brier nor ece. The gate reads metrics["pr_auc"] and metrics["ece"], so
    # they are computed here rather than defaulted to 0.0 - an ece silently
    # defaulted to zero would pass the calibration gate by accident, which is
    # precisely the check that must never pass by accident.
    metrics = {
        "pr_auc": float(report["average_precision"]),
        "roc_auc": float(report["roc_auc"]),
        "brier": float(ens.brier(y_te, p_te)),
        "ece": float(ens.expected_calibration_error(y_te, p_te)),
    }
    dev = fit["dev"][0]
    training = {
        "seed": args.seed,
        "days": args.days,
        "n_dev": int(len(dev)),
        "n_test": int(len(test)),
        "n_positive": int(y_te.sum()),
        "n_folds": fit["n_folds"],
        "scale_pos_weight": float(fit["scale_pos_weight"]),
    }
    operating_point = {"threshold": None, "budget": args.budget}

    registry = Registry(args.models)
    version = registry.save(
        model,
        metrics=metrics,
        training=training,
        operating_point=operating_point,
        deployment_prior=fit["deployment_prior"],
        lineage={"git_sha": os.environ.get("GIT_SHA"), "parent": None},
        stage="staging",
    )
    print("saved " + version + " in " + repr(round(time.time() - started, 1)) + "s")
    print(json.dumps(metrics, indent=2, sort_keys=True))
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    registry = Registry(args.models)
    versions = registry.list_versions()
    if not versions:
        print("no versions in " + str(registry.root))
        return 0
    for manifest in versions:
        metrics = manifest.get("metrics") or {}
        print("%-28s %-11s pr_auc=%-8s ece=%-8s %s" % (
            manifest["version"], manifest.get("stage", "?"),
            metrics.get("pr_auc"), metrics.get("ece"), manifest["created_at"]))
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    print(json.dumps(Registry(args.models).show(args.version), indent=2, sort_keys=True))
    return 0


def _cmd_promote(args: argparse.Namespace) -> int:
    registry = Registry(args.models)
    try:
        event = registry.promote(args.version, args.to, actor=args.actor, force=args.force)
    except PromotionRefused as exc:
        print("promotion refused:")
        for problem in exc.problems:
            print("  - " + problem)
        return 1
    print(json.dumps(event, indent=2, sort_keys=True))
    return 0


def _cmd_rollback(args: argparse.Namespace) -> int:
    print(json.dumps(Registry(args.models).rollback(actor=args.actor),
                     indent=2, sort_keys=True))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    registry = Registry(args.models)
    targets = ([m["version"] for m in registry.list_versions()]
               if args.all else [args.version])
    bad = 0
    for version in targets:
        ok = registry.verify(version)
        bad += 0 if ok else 1
        print(("ok   " if ok else "BAD  ") + version)
    return 1 if bad else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sentinelai.registry")
    parser.add_argument("--models", default=os.environ.get("SENTINELAI_MODELS_DIR", "models"))
    sub = parser.add_subparsers(dest="cmd", required=True)

    fit = sub.add_parser("fit", help="fit and register a new staging version")
    fit.add_argument("--days", type=float, default=4.0)
    fit.add_argument("--seed", type=int, default=7)
    fit.add_argument("--budget", type=float, default=50.0)
    fit.set_defaults(func=_cmd_fit)

    lst = sub.add_parser("list")
    lst.set_defaults(func=_cmd_list)

    show = sub.add_parser("show")
    show.add_argument("version")
    show.set_defaults(func=_cmd_show)

    promote = sub.add_parser("promote")
    promote.add_argument("version")
    promote.add_argument("--to", default="production", choices=list(STAGES))
    promote.add_argument("--actor", default="cli")
    promote.add_argument("--force", action="store_true")
    promote.set_defaults(func=_cmd_promote)

    rollback = sub.add_parser("rollback")
    rollback.add_argument("--actor", default="cli")
    rollback.set_defaults(func=_cmd_rollback)

    verify = sub.add_parser("verify")
    verify.add_argument("version", nargs="?")
    verify.add_argument("--all", action="store_true")
    verify.set_defaults(func=_cmd_verify)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())