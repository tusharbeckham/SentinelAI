"""Project the scored test windows into the space the hero renders.

The hero is not a decoration, so its coordinates must be reproducible. This
script is the whole provenance of web/public/data/embedding.json:

    x, z  first two principal components of the 40 standardised features
    y     the model log-odds, scaled

Putting the log-odds on the vertical axis is the point of the whole figure. It
makes the operating threshold an exact horizontal plane at logit(0.6303)
rather than an artistic impression of one, so the false positives are visibly
above it and the missed attacks are visibly below it.

Run:  python scripts/make_embedding.py
Out:  artifacts/embedding.json  (copied to web/public/data by npm run sync-data)

No sklearn: the PCA is a plain SVD of the centred matrix, which keeps this
runnable anywhere numpy is available.
"""

import json
import math
import pathlib

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
CSV = ROOT / "artifacts" / "scored_test_windows.csv"
OUT = ROOT / "artifacts" / "embedding.json"

# Operating threshold chosen by the budget sweep (50 alerts/day).
TH = 0.6303419959358633
# World units per unit of log-odds. Tuned so the full -10.4..+4.3 range is a
# readable column rather than a skyscraper.
SCALE = 0.42
# Identity and outcome columns are not features.
DROP = {"entity", "win", "attack", "label", "baseline_cutoff", "probability"}


def main() -> None:
    df = pd.read_csv(CSV)
    feats = [c for c in df.columns if c not in DROP]
    X = df[feats].to_numpy(dtype="float64")
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    # Byte and packet counters span six orders of magnitude. Left raw they own
    # every principal component and the projection becomes a plot of traffic
    # volume, not of behaviour. Signed log1p compresses them without discarding
    # direction.
    compressed = 0
    for j in range(X.shape[1]):
        if np.abs(X[:, j]).max() > 1000:
            X[:, j] = np.sign(X[:, j]) * np.log1p(np.abs(X[:, j]))
