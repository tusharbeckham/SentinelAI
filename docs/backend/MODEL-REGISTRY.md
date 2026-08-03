# Model registry

Expands Phase 3 of [`BACKEND-PLAN.md`](./BACKEND-PLAN.md). This is the phase
that removes the 136-second boot.

## 1. The problem

`serve.py` calls `fit_pipeline(seed, days)` at startup. That means:

1. The service is down for over two minutes on every deploy.
2. Two replicas fit **two different models** -- same seed, but any drift in
   NumPy version or thread scheduling in the tree builder gives different
   splits. Two instances of the same service disagreeing about a score is the
   kind of bug that costs a week.
3. There is no record of what was served yesterday, so an alert from last week
   cannot be explained.
4. Rollback is impossible. There is nothing to roll back *to*.

A model registry is the standard answer, and the parts that matter are
versioning, an immutable artifact, a promotion gate and lineage.

## 2. On-disk layout

```
models/
  v20260802T124107Z-a3f91c/
    manifest.json
    ensemble.npz          # all fitted arrays, np.savez_compressed
    features.json         # the ordered feature contract
    metrics.json          # copied from the training run
  v20260731T081400Z-77e8f3/
    ...
  stages.json             # {"production": "v2026...", "staging": "..."}
```

A directory per version, named `v<UTC timestamp>-<content hash prefix>`. The
timestamp makes the directory listing chronological; the hash makes it
verifiable. `stages.json` is a pointer file, written atomically (write temp,
`os.replace`) so a crash mid-promotion cannot leave a half-written pointer.

Artifacts are immutable. Promotion moves a **pointer**, never a file. The
alternative -- overwriting `production/model.npz` -- destroys the thing you need
when the new model is worse.

## 3. The manifest

```json
{
  "version": "v20260802T124107Z-a3f91c",
  "created_at": "2026-08-02T12:41:07+00:00",
  "content_hash": "a3f91c...",
  "feature_contract": {"n": 40, "hash": "9d2b...", "names": ["..."]},
  "training": {"seed": 7, "days": 4.0, "window_s": 300,
               "n_train": 22673, "n_calib": 11341, "n_positive": 171},
  "metrics": {"pr_auc": 0.811, "roc_auc": 0.998, "recall_at_budget": 0.661,
              "precision_at_budget": 0.796, "brier": 0.0024, "ece": 0.0018},
  "operating_point": {"threshold": 0.6303419959358633, "budget": 50,
                      "alerts_per_day": 49.839},
  "deployment_prior": 1e-4,
  "stacker": {"coef": [0.342546, 1.100883, 0.113480],
              "intercept": -8.433968927814007},
  "lineage": {"git_sha": "...", "pipeline_version": "2.9.2", "parent": null}
}
```

`content_hash` is SHA-256 over the canonical array contents: for each array, in
sorted key order, the key, its dtype string, its shape and its contiguous bytes.

This paragraph previously said the hash was taken over the concatenated `.npz`
bytes and the *sorted* feature names. Both halves were wrong, and implementing
the spec exposed it:

- An `.npz` is a zip archive, and zip records modification times. Two runs
  producing bit-identical models therefore produce different archive bytes, so a
  hash over the file could never be stable and the CI determinism check would
  have been decorative after all.
- Sorting the feature names would let two positionally incompatible models hash
  equal. The model indexes feature columns by position, so the contract is the
  *ordered* list. `feature_hash` joins the names in their given order; a model
  handed the right names in the wrong order scores confident garbage, and that
  is precisely what the hash exists to prevent.

`stacker.coef` and `intercept` are duplicated into the manifest even though they
live in the `.npz`, because they are the numbers a human reads during an
incident and nobody should need NumPy to read them. Note that the intercept here
is `-8.433968927814007` -- the value the artifacts actually carry.

## 4. The promotion gate

`promote(version, stage)` refuses unless **all** hold:

1. The manifest exists and `content_hash` recomputes correctly. A corrupted
   artifact must not become production.
2. `feature_contract.hash` equals the incumbent's. A model expecting different
   features than the feature pipeline produces will not error -- it will score
   garbage confidently, which is far worse.
3. `metrics.pr_auc >= incumbent.pr_auc - tolerance` (default `0.02`).
4. `metrics.ece <= 0.05`. An uncalibrated model breaks the entire base-rate
   argument the project is built on: a "probability" that is not calibrated makes
   the Bayesian PPV computation meaningless.
5. The target stage is a legal transition: `staging -> production`,
   `production -> archived`. A version cannot jump straight to production.

Override is possible via `force=True`, which is recorded in the promotion event
with the actor. Forcing is sometimes correct; forcing *silently* never is.

Every promotion writes to `model_versions` and to the outbox.

## 5. `model_versions` table

The shipped table is narrower than this section originally described. The
columns `content_hash`, `feature_hash`, `threshold`, `promoted_by` and `forced`
were specified here but never existed in migration 4:

| Column | Type | Notes |
| --- | --- | --- |
| `version` | TEXT PK | directory name |
| `stage` | TEXT NOT NULL | `staging` / `production` / `archived` |
| `created_at`, `promoted_at` | TEXT | |
| `manifest` | TEXT NOT NULL | full JSON, for the record |
| `pr_auc`, `ece` | REAL | promotion-gate inputs |
| `notes` | TEXT | the promotion event as JSON |

The missing columns were not added, because adding them would duplicate fields
that already live in `manifest` and invite the two copies to disagree. The
filesystem manifest is the source of truth; this table is a queryable mirror.

The one genuinely important omission was `promoted_by` and `forced` -- who
overrode the gate, and that they did. Those go into the hash-chained audit log
via `append_audit`, and into `notes`. An append-only, verifiable chain is a
better home for "who forced this" than a plain column an operator can UPDATE.

A partial unique index enforces at most one production version:

```sql
CREATE UNIQUE INDEX one_production
  ON model_versions(stage) WHERE stage = 'production';
```

The database refuses two production models. That invariant is too important to
leave to application code -- and it earned its keep immediately: the first
implementation of `_record` inserted the new production row without stepping the
displaced one down, and the index caught it as an `IntegrityError` during the
rollback test. `_record` now demotes the incumbent in the same call.

## 6. Serialisation

No `pickle`. Loading a pickle executes arbitrary code, and a security product
that deserialises untrusted pickles is an embarrassment. Every fitted structure
is reducible to arrays:

* **Isolation forest** -- 150 trees, each a set of parallel arrays (feature,
  threshold, left, right, size). Packed into a ragged `.npz` with an offsets
  array.
* **GBDT** -- same shape, plus leaf values and the base score.
* **Logistic stacker** -- `coef`, `intercept`, `mu`, `sd`. The standardisation
  parameters are part of the model; omitting them reproduces exactly the
  `reconstruction_error = 0.0193` bug documented in README §9.2.
* **Graph leg** -- baseline edge sets, serialised as a sorted index array.

`save()` then `load()` must reproduce scores **bit-identically** on the test
matrix. A test asserts `np.array_equal`, not `allclose` -- a round-trip that
loses a bit is a round-trip with a bug, and tolerance would hide it.

## 7. CLI

```bash
python -m sentinelai.registry fit --days 4 --seed 7 --stage staging
python -m sentinelai.registry list
python -m sentinelai.registry show v20260802T124107Z-a3f91c
python -m sentinelai.registry promote v20260802T124107Z-a3f91c --to production
python -m sentinelai.registry rollback
python -m sentinelai.registry verify --all
```

`rollback` promotes the most recently archived production version and is the
command you want to be one word long at 3am.

## 8. Boot path after this phase

```
Settings.from_env() -> store.migrate() -> registry.load_production()
  -> ScoringService(model) -> serve()
```

No fitting. Target: **under 2 seconds**, dominated by importing NumPy. If no
production model is registered the service starts anyway and `/readyz` returns
503 with `no production model` -- a service that cannot score should say so
clearly rather than refusing to boot, so that the operator can reach `/metrics`
and the registry CLI to fix it.
