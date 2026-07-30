# Running the projection on Kaggle

## First, the thing you asked

You are **not training a model** here, and you are **not training a shader** -- shaders
have no weights, they are just programs the GPU runs per pixel. What you are doing is
recomputing the **layout** of the hero point cloud with a better algorithm than the one
that shipped, on a machine that has a GPU.

The shipped layout uses PCA, which is linear and keeps only 35% of the variance, so the
benign windows squash into one grey blob. UMAP is non-linear and keeps local structure,
so attack families separate into their own strands. Same data, same model, same numbers --
only the arrangement in space changes.

Total time: about ten minutes, most of it uploading.

---

## Step 1 - Get a Kaggle account

Sign up at <https://www.kaggle.com>, then **Settings > Phone verification**. This is not
optional: unverified accounts cannot use the GPU or enable internet in notebooks.

## Step 2 - Upload the data as a Dataset

The file you need is already on your machine:

```
C:\projects\sentinelai\artifacts\scored_test_windows.csv    (about 4.5 MB)
```

1. Go to <https://www.kaggle.com/datasets> and click **New Dataset**.
2. Drag that CSV in.
3. Title it exactly `sentinelai-scored` so the default path in the notebook matches.
4. Click **Create**. Wait for it to finish processing.

This is a *dataset*, not a competition and not a model. You are just parking a file where
the notebook can read it.

## Step 3 - Import the notebook

1. Go to <https://www.kaggle.com/code> and click **New Notebook**.
2. In the notebook, **File > Import Notebook**.
3. Upload `notebooks/sentinelai-embedding-kaggle.ipynb` from the project.

Do not paste the cells in by hand. Importing keeps the cell order, and the order matters --
the asserts near the end only mean something if the cells above them ran.

## Step 4 - Turn the GPU on

Open the right-hand sidebar (**...** menu if it is collapsed):

- **Accelerator** -> `GPU T4 x2`
- **Internet** -> `On`  (only needed if the GPU path fails and it has to `pip install umap-learn`)

You get about 30 GPU hours a week for free. This notebook uses roughly one minute of it.

## Step 5 - Attach the dataset

In the same sidebar: **+ Add Input > Datasets >** your `sentinelai-scored`.

It will mount read-only. The sidebar now shows the real path -- expand it and check whether
it is:

```
/kaggle/input/sentinelai-scored/scored_test_windows.csv
```

You do **not** need to edit any path. Kaggle renames dataset folders -- lowercasing,
hyphenating, and sometimes appending a suffix -- so the notebook globs
`/kaggle/input/**/*.csv`, prints everything it can see, and picks the scored file. If
nothing is mounted it says so in plain language instead of throwing a traceback.

## Step 6 - Run it

**Run All**. What you should see, in order:

- `rows 11326 cols 45`
- `log1p applied to 8 of 40 columns`
- `cuML UMAP (GPU)`  -- or `GPU unavailable, CPU fallback:` which is fine, just slower
- `above 49 = 39 true + 10 false | missed 20`
- `precision 0.7959  recall 0.6610`
- `reconciled with the deployed operating point`
- `wrote embedding.json ... bytes n=11326`

If either assert throws, **stop**. It means the projection no longer describes the
deployed model, and shipping it would put a number on screen that the pipeline does not
support. The asserts exist precisely so that a pretty picture cannot quietly become a
false one.

## Step 7 - Bring the file home

Right sidebar, **Output** section (or the **Data** tab) -> `/kaggle/working/embedding.json`
-> download.

Then on your machine:

```powershell
copy %USERPROFILE%\Downloads\embedding.json C:\projects\sentinelai\artifacts\embedding.json
cd C:\projects\sentinelai\web
npm run sync-data
npm run dev
```

Hard-reload with `Ctrl+Shift+R`. Nothing in the app needs editing -- the schema is
identical, so the scene just picks up the new geometry.

---

## Tuning the look

Two numbers in cell 2 control almost everything:

| Knob | Low | High | Default |
|---|---|---|---|
| `MIN_DIST` | `0.0-0.05` dense dramatic filaments | `0.5` evenly spread lawn | `0.02` |
| `N_NEIGHBORS` | `5-15` many tight islands | `50-200` one smooth continent | `25` |

For a hero image, low `MIN_DIST` almost always wins -- you want stringy structure with
voids, not uniform density. Change, re-run, re-download. Nothing else moves.

## The one rule you must not break

**Only `x` and `z` may change. `y` stays the model log-odds.**

Height being the score is the entire reason the decision threshold can be drawn as a flat
plane instead of a suggestive prop, and the reason false positives above it and misses
below it are literally true rather than an artistic arrangement. If you let UMAP produce
three components and use the third as height, the scene becomes decoration and every
claim the HUD makes about precision and recall stops being checkable.

## If it breaks

| Symptom | Cause |
|---|---|
| `Nothing is mounted` | The dataset is not attached: sidebar > + Add Input > Datasets |
| Wrong CSV picked | Detach other datasets, or rename yours to contain `scored` |
| `ModuleNotFoundError: cuml` | Accelerator is not set to GPU; it will fall back to CPU if Internet is on |
| `AssertionError: precision drifted` | Wrong CSV, or rows were filtered -- do not ship it |
| Hero still looks identical | You copied to `artifacts/` but skipped `npm run sync-data` |
| Hero is blank | `web/public/data/embedding.json` is missing; run `npm run sync-data` |

