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
