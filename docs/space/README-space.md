---
title: SentinelAI
emoji: 🛡
colorFrom: gray
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Hybrid intrusion detection built around the analyst budget
---

# SentinelAI

Hybrid intrusion detection built around the analyst's budget, not the ROC
curve. Isolation Forest and gradient boosting, fused by an out-of-fold
logistic stacker, with Shapley explanations on every alert and a hash-chained
SOAR audit log. Pure NumPy - no scikit-learn, no XGBoost.

At 50 alerts per day the model catches **71.2%** of attack windows at
**85.7%** precision. It also misses every held-out attack family it was never
trained on, and the console says so on the page rather than in a footnote.

Source, methodology and the full write-up:
<https://github.com/tusharbeckham/SentinelAI>

## What you are looking at

The charts render measured artifacts committed to the repository, produced by
a 4-day synthetic corpus of 45,340 windows. The live `/v1/score` endpoint is
backed by a smaller model fitted at container boot, and still requires a
signed JWT - the demo does not disable its own authentication.
