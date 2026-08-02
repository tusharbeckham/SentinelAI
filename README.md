# SentinelAI — Adaptive Threat Detection & Auto-Response Platform

A **working** hybrid intrusion-detection platform: entity-behaviour telemetry generation,
windowed feature engineering, three complementary detectors fused by a calibrated stacker,
Shapley explanations on every alert, a policy-driven SOAR response engine with a hash-chained
audit log, an authenticated scoring API, an analyst triage dashboard, and an active-learning /
drift loop. Every number in this document was produced by running the code in this repo.

```
python3 -m unittest discover -s tests        # 198 tests, ~5.3 s
python3 -m sentinelai.pipeline --out artifacts   # full experiment, ~136 s
python3 -m sentinelai.build_dashboard            # dashboard.html from measured artifacts
python3 -m sentinelai.api                        # authenticated scoring service
```

Zero third-party ML dependencies: the isolation forest, the gradient-boosted trees, the
logistic stacker, the Shapley sampler and the HTTP/JWT layer are implemented from the papers
in NumPy + the Python standard library (sandbox has no scikit-learn / XGBoost / FastAPI).
That is deliberate — it means every modelling choice below is inspectable, not delegated.

---

## 1. The scientific problem: the base-rate fallacy, taken literally

Axelsson's result is the design constraint, not a footnote: with a realistic prior of an
intrusion in ~1e-4 of observed units, even a detector with excellent ROC behaviour produces a
PPV low enough to destroy analyst trust.

So the platform is built around **three commitments**:

1. **Report Bayesian PPV at the deployment prior, not just test-set precision.**
   `ensemble.bayesian_ppv(tpr, fpr, prior)` is computed for every operating point, and the
   stacker applies an explicit log-odds **prior shift** (`-3.92` here) so that a served
   probability means something at 1e-4 rather than at the training prior (5.2e-3).
2. **Threshold on an analyst budget, not on 0.5.** `choose_threshold(...)` picks the score
   cut that yields ≤ *N* alerts/day (50/day here) and reports what recall that buys. A
   detector that cannot be run at a survivable alert volume is not a detector.
3. **Evaluate against confusable benign anomalies.** The corpus contains six families of
   *rare-but-legitimate* behaviour deliberately built to mimic each attack family
   (nightly backup ↔ exfiltration, vuln scan ↔ portscan, patch burst ↔ DoS, admin maintenance
   ↔ lateral movement, password-reset storm ↔ brute force, CDN DNS churn ↔ DNS tunnelling),
   all labelled benign. Any AP near 1.0 is a sign the corpus is broken, not that the model is good.

### Why synthetic telemetry instead of CICIDS2017

CICIDS2017 is the obvious choice and it is also **measurably defective**: Engelen et al.
(WTMC 2021) found feature-extraction and labelling errors severe enough to change results;
Lanvin et al. (2022) and Dube (2024) independently catalogue duplicate rows, mislabelled
attack windows and a payload artefact that lets trivial models score ~0.99. Reporting
0.99 F1 on a dataset whose labels are wrong is worse than reporting an honest 0.80.

`synth.py` instead generates telemetry from an explicit **generative model** where ground
truth is exact by construction: diurnal Gaussian activity envelope
(`0.30 + 0.70·exp(-½((h-14)/4.5)²)`), per-host and per-user behavioural profiles, six attack
families with a 40 % **stealth** variant (attenuated magnitude, long dwell), and the benign
anomaly families above. Swapping in a real corpus means replacing one module: the feature
contract (`features.FEATURES`) is what the rest of the system depends on.

**Measured corpus:** 563,619 flows · 49,535 auth events · 45,340 entity-windows
(40 hosts, 30 users, 4 days, 300 s windows) · 230 attack windows
(brute_force 57, portscan 53, lateral_movement 34, exfil 34, dos 32, dns_tunnel 20) ·
922 benign-anomaly windows. Prevalence ≈ 0.5 % of windows.

---

## 2. Detection architecture

### 2.1 Features (`features.py`) — 40 per entity-window

22 absolute features (flow count, bytes out/in, p95 bytes, out/in ratio, mean packet size,
distinct dports/dsts, port entropy, SYN ratio, short-flow ratio, admin-port ratio, DNS flow
ratio, max DNS name entropy, external-destination ratio, night flag, auth events, failed
logins, failed-login ratio, distinct auth targets, interactive logons) **plus 5 relative
features** (`z_*`) computed against each entity's own baseline from a held-out warm-up period,
**plus 7 trailing-window features** (`roll_*`) and **plus 6 graph features**. The `z_*`
features are what make this *entity behaviour analytics* rather than global thresholding:
89 flows is routine for a proxy host and a red flag for a print server.

The `roll_*` features aggregate each entity's own history over the trailing hour (12 windows:
port reach, destination reach, DNS flow volume, mean DNS entropy, bytes out, admin-port flows,
active windows). They exist because a detector that only ever sees one 5-minute window
*structurally cannot* catch low-and-slow attacks — stealth portscans and DNS tunnels attenuate
their per-window magnitude below the decision surface and are only anomalous in accumulation.
Every aggregate is strictly backward-looking (windows `[t-11, t]`, never `t+1`), and a test
hand-recomputes one entity's trailing sums to assert no future leakage. §3.7 reports what this
actually bought, which was less than hoped.

### 2.2 Three complementary detectors

| Leg | Implementation | Detects |
|---|---|---|
| Isolation Forest | `models/iforest.py` — Liu, Ting & Zhou (ICDM 2008); 150 trees, ψ=256, `c(n)` path-length normalisation | novel behaviour, no labels needed |
| Gradient-boosted trees | `models/gbdt.py` — Chen & Guestrin (KDD 2016) §2.2 exact split gain, histogram binning, λ/γ regularisation, `scale_pos_weight` for 1:200 imbalance | known attack families, high precision |
| Authentication graph | `graphlm.py` — new-edge rarity, breadth z-score, longest **new-edge chain** per (window, user) | lateral movement invisible to flow features |

### 2.3 Fusion done correctly (`ensemble.py`, `pipeline.py`)

The first honest version of this system fused raw probabilities on a 7-positive calibration
slice, and the hybrid **lost to the supervised leg alone**. Two defects, both fixed:

* **Log-odds inputs.** Probabilities saturate at 0/1, so a linear stacker can't separate
  "0.999" from "0.99999". Fusion inputs are `logit(iforest)`, `logit(gbdt)`, `graph_score`.
* **Out-of-fold stacking.** Legs are fit on 3 contiguous **time blocks** of train+calibration;
  each block's fusion inputs come from legs that never saw it; the stacker trains on the
  assembled OOF matrix (171 positives instead of 42), then legs are refit on everything.
  Contiguous blocks — not random KFold — because random folds leak attack episodes across
  the split boundary and inflate everything.

Learned weights: `gbdt_logit 1.101`, `iforest_logit 0.343`, `graph_score 0.113`,
intercept `-8.433969`. (Before the trailing-window features the unsupervised weight was `0.098`;
giving Isolation Forest a memory more than tripled how much the stacker trusts it.) The stacker itself tells you the supervised leg carries the aggregate
signal on this corpus — see §3.2 for where the other two legs actually earn their place.

### 2.4 Explainability (`explain.py`)

Permutation-sampled Shapley values (Štrumbelj & Kononenko 2014) in **log-odds space**, so
contributions are additive. Local accuracy is asserted, not assumed: every alert carries
`attribution_residual = score - (base_value + Σφ)`, measured at **~1e-15**, and a unit test
fails the build if it drifts. `narrate()` turns the top-k attribution into an analyst
sentence; `suspected_family()` maps attributed features to an attack family via signatures.

### 2.5 Response layer (`soar.py`)

Tiered policy — `auto_contain` ≥ 0.90, `human_review` ≥ 0.50, `enrich` ≥ 0.20, else
`suppress` — with four production guardrails:

* **Explanation-gated automation:** an alert may only auto-contain if its top attributions
  are in `AUTO_ALLOWED_FEATURES` with ≥ 0.55 of attribution mass. Unexplainable → human.
* **Protected assets** (e.g. domain controllers) always escalate to a human.
* **Rate limit** on automated actions per hour, so a model failure cannot self-inflict a DoS.
* **Hash-chained audit log** (SHA-256 over the previous record) — `audit.verify()` detects
  tampering; a unit test mutates a record and asserts the chain breaks.
* Dry-run by default (`execute=False`).

### 2.6 Active learning & drift (`active_learning.py`)

PSI per feature with 0.10 / 0.25 warn / alarm bands drives `retrain_recommended`. Review
selection is **uncertainty + top-risk + entity-diversity** capped so one noisy host cannot
eat the budget. `merge_feedback()` weights analyst labels 3× against the historical set.

### 2.7 Securing the platform itself (`api.py`)

Hand-rolled HS256 JWTs (`iss/aud/exp/nbf` all verified, constant-time signature compare,
tamper + wrong-secret + expiry + audience tests), **role hierarchy** viewer < analyst <
responder < admin enforced per route, token-bucket rate limiting, strict input validation
(422 on bad feature vectors), secret **redaction in every log line**, and hardened response
headers (`nosniff`, `DENY`, CSP, no-store). Secret is env-only (`SENTINELAI_JWT_SECRET`),
never a default. Routes: `/healthz` public · `GET /v1/alerts` viewer · `POST /v1/score`
analyst · `POST /v1/feedback` analyst · `POST /v1/respond` responder · `GET /v1/audit` admin.

---

## 3. Measured results

Strictly chronological split — 22,673 train / 11,341 calibration / 11,326 test windows
(129 / 42 / 59 positives). No shuffling across attack episodes.

### 3.1 Operating point at a 50-alert/day analyst budget

| Metric | Value |
|---|---|
| Threshold | 0.6303 |
| Alerts/day | 49.8 |
| Recall | 66.1 % |
| Precision (test prior 5.2e-3) | 79.6 % |
| FPR | 8.9e-4 |
| **Bayesian PPV @ prior 1e-4** | **6.9 %** |
| Brier / ECE | 0.0024 / 0.0018 |

That 6.9 % is the honest headline. A well-calibrated detector at a realistic prior still
means most alerts are false — which is *exactly why* the explanation layer, the tiered SOAR
policy and the human-in-the-loop tiers exist rather than blind auto-blocking.

### 3.2 Ablation (same budget, same test period)

| Detector | PR-AUC | ROC-AUC | Recall | Precision | FP | FPs on benign anomalies |
|---|---|---|---|---|---|---|
| Isolation Forest (unsup.) | 0.298 | 0.992 | 20.3 % | 24.5 % | 37 | **100 %** |
| GBDT (supervised) | 0.858 | 0.998 | 71.2 % | 85.7 % | 7 | **100 %** |
| Auth-graph only | 0.039 | 0.660 | 1.7 % | 2.0 % | 50 | 92 % |
| **Hybrid ensemble** | 0.811 | 0.998 | 66.1 % | 79.6 % | 10 | **100 %** |

**Two findings I am not going to hide:**

1. **The hybrid does not beat the supervised leg on aggregate PR-AUC here** (0.811 vs 0.858).
   The literature's "ensembling cuts false positives" result holds when the unsupervised leg
   contributes independent signal; on this corpus the GBDT already dominates on the six
   families it was *trained on*, and blending in two weaker legs costs a little aggregate
   precision. The ensemble earns its cost on **unseen** families (§3.4), not on this table.
   Reporting the reverse would be the easy lie.
2. **100 % of every leg's false positives land on the confusable-benign windows.** The
   corpus is doing its job — the models are not tripping on ordinary traffic, they are
   tripping on exactly the rare-but-legitimate activity that fools real SOC tooling.

### 3.3 Per-family recall at the operating point

| Family | Test windows | Recall |
|---|---|---|
| dos | 8 | 100 % |
| brute_force | 20 | 95 % |
| exfil | 12 | 75 % |
| dns_tunnel | 5 | 20 % |
| portscan | 13 | 15.4 % |
| lateral_movement | 1 | 0 % |

Portscan and DNS tunnelling are the stealth-variant-heavy families — attenuated, long-dwell
variants are genuinely near the benign-anomaly manifold. §3.7 documents a direct attempt to
fix them and how partially it worked.

### 3.4 Zero-day holdout — where the unsupervised and graph legs pay off

A family's labels are zeroed across the **entire** development period, legs and stacker are
refit blind, and we ask how the blind system ranks that family's windows:

| Held-out family | n | GBDT (blind) mean percentile | Isolation Forest | Graph only |
|---|---|---|---|---|
| exfil | 12 | 0.906 | **0.980** | 0.838 |
| dns_tunnel | 5 | 0.980 | **0.989** | 0.813 |
| lateral_movement | 1 | 0.985 | 0.986 | **1.000** (recall 1.0 @ budget) |

The unsupervised leg ranks never-labelled attacks *above* the supervised leg, and the graph
leg is the only one that fires on held-out lateral movement at budget — the specific
justification for keeping all three legs. **Caveat stated plainly:** recall at budget is 0.0
for the larger held-out families and n is 1–12 windows, so this is directional evidence, not
a significance claim. A longer corpus is required to make it one.

### 3.5 Drift and active learning

Injected distribution shift (exfil/DNS-tunnel-heavy mix): PSI flags `g_rarity` at **2.54**
(material) and `graph_score` at 0.137 (moderate) → `retrain_recommended = True`.
After **60** analyst labels selected by the review policy:

| Metric | Before | After |
|---|---|---|
| PR-AUC on drifted period | 0.772 | **0.822** |
| Recall @ budget | 60.9 % | **67.0 %** |
| Precision @ budget | 71.4 % | **78.6 %** |

### 3.6 Alert budget sweep

The analyst budget is the most consequential deployment knob in the whole system, so it is
swept rather than assumed. Hybrid ensemble, same test period:

| Budget/day | Alerts/day | Recall | Precision | Bayesian PPV @ 1e-4 |
|---|---|---|---|---|
| 5 | 5.1 | 8.5 % | 100 % | 1.000 |
| 15 | 15.3 | 25.4 % | 100 % | 1.000 |
| 20 | 20.3 | 32.2 % | 95.0 % | 0.266 |
| 30 | 29.5 | 44.1 % | 89.7 % | 0.142 |
| **50 (chosen)** | **49.8** | **66.1 %** | **79.6 %** | **0.069** |
| 75 | 75.3 | 79.7 % | 63.5 % | 0.032 |
| 100 | 99.7 | 86.4 % | 52.0 % | 0.020 |
| 200 | 200.4 | 98.3 % | 29.4 % | 0.008 |

This is the base-rate fallacy made operational. Recall is purchasable with analyst attention,
but at a strictly worsening exchange rate: the first 15 alerts/day are all true positives, the
jump from 100 to 200 alerts/day buys 12 points of recall and halves precision. 50/day is chosen
because it is the last point where precision stays near 80 % — not because it maximises any
single metric.

### 3.7 What the trailing-window features actually bought (negative-ish result)

Adding the 7 `roll_*` features to attack the two weak families produced a **mixed** result,
reported as measured rather than as hoped:

| Metric | 33 features | 40 features |
|---|---|---|
| Hybrid PR-AUC | 0.804 | **0.811** |
| Isolation Forest PR-AUC | 0.246 | **0.298** |
| `iforest_logit` stacker weight | 0.098 | **0.343** |
| dns_tunnel recall | 0 % | **20 %** |
| exfil recall | 58.3 % | **75 %** |
| portscan recall | 23.1 % | **15.4 %** |
| brute_force recall | 100 % | 95 % |
| Overall recall @ 50/day | 66.1 % | 66.1 % |
| GBDT zero-day percentile on blind exfil | 0.906 | **0.974** |

**Honest reading.** DNS tunnelling moved off zero and exfil gained 17 points, the unsupervised
leg got materially stronger (PR-AUC +0.05, stacker weight 3.5×), and blind-family ranking
improved — but **overall recall at budget did not move at all**, and portscan got *worse*. At a
fixed budget the detector is trading which families it catches, not catching more overall. The
lateral_movement column swinging 100 % → 0 % is a single test window and should be read as
noise, which is precisely why n is printed next to every recall figure. Verdict: kept, because
the unsupervised and zero-day gains are the ensemble's actual purpose, but this did **not**
solve the stealth-family problem. `mean_pkt_size` still holds 0.81 of GBDT importance and
dominates the decision surface; that shortcut, not the feature set, is the real next target.

### 3.8 SOAR

50 decisions on the live alert set → 5 `auto_contain`, 45 `human_review`, audit chain
verified `True`. The 90 %-confidence gate plus the explanation gate is why only 10 % of
alerts were eligible for automation.

---

## 4. Repository map

```
sentinelai/
  synth.py            generative telemetry: attacks, stealth variants, confusable benign anomalies
  features.py         40-feature windowing, per-entity baselines, causal trailing-hour rollups, chronological splitter
  models/iforest.py   isolation forest (Liu et al. 2008)
  models/gbdt.py      histogram gradient boosting (Chen & Guestrin 2016)
  graphlm.py          authentication-graph lateral-movement features
  ensemble.py         ROC/PR/AP, Brier, ECE, Bayesian PPV, logistic stacker + prior shift, budgeted threshold
  explain.py          sampled Shapley with asserted local accuracy, narration
  soar.py             tiered policy, playbooks, rate limits, SHA-256 audit chain
  active_learning.py  PSI drift, review selection, weighted feedback merge
  api.py              JWT auth, RBAC, rate limit, redaction, hardened HTTP service
  pipeline.py         end-to-end experiment: OOF stacking, ablation, zero-day, drift, artifacts
  serve.py            runnable authenticated scoring service (prints role tokens)
  ingest.py           CICIDS2017 + UNSW-NB15 loaders onto the same flow contract, with caveats attached to the data
  build_dashboard.py  renders dashboard.html from measured artifacts (incl. budget-sweep chart)
Dockerfile            non-root runtime image; runs its own test suite during build; fails closed without a signing secret
requirements.txt      two runtime dependencies: numpy, pandas
.github/workflows/ci.yml  tests on py3.11-3.14, determinism check, ruff, bandit SAST, pip-audit, gitleaks, Trivy, container build
tests/                198 tests: closed-form metric checks, Shapley local accuracy, leakage,
                      auth/tamper/RBAC, audit-chain tamper detection, live HTTP route tests
artifacts/            report.json, alerts.json, soar_decisions.json, audit_log.json,
                      drift_psi.json, scored_test_windows.csv
dashboard.html        analyst triage console (standalone, no network)
```

## 5. Mapping to the production stack

| This repo | Production |
|---|---|
| `synth.py` generator | Zeek / NetFlow + Windows auth logs → **Kafka** topics |
| `features.py` windowing | **Spark Structured Streaming** 5-min tumbling windows, watermarked; entity baselines in a state store |
| `models/*`, `ensemble.py` | training on Spark/GPU, artefacts + metrics + prior in **MLflow**; the fitted stacker is the promoted artefact |
| `api.py` | **FastAPI** + Uvicorn behind mTLS, OIDC-issued JWTs, secrets from Vault |
| `artifacts/*.json` | **TimescaleDB** (features/metrics), **Elasticsearch** (alerts), **Postgres** (case management) |
| `dashboard.html` | **React** + WebSocket live feed (same JSON contract) |
| `soar.py` | firewall/EDR APIs, ticketing; audit chain to append-only WORM storage |
| `tests/` | CI with SAST/DAST, dependency and container scanning; nightly drift job gating retrains |

The seam is intentional: each module is a pure function over a documented contract, so
replacing a runtime never changes the science.

## 6. Threat model of the platform itself

| Threat | Control |
|---|---|
| Stolen/forged analyst token | HS256 verification, `aud`/`iss`/`exp`/`nbf` checked, constant-time compare, short TTL |
| Privilege escalation via API | role hierarchy enforced per route; containment needs `responder`, audit needs `admin` |
| Attacker weaponises auto-response (self-DoS) | per-hour automated-action rate limit, protected-asset escalation, dry-run default |
| Adversarial model input | strict schema validation, feature-count/finiteness checks, 422 on malformed input |
| Poisoned analyst feedback | feedback weighting is bounded, drift-gated retrains, chronological evaluation |
| Log-based secret leakage | `redact()` on every log line, tested against bearer tokens and API keys |
| Silent tampering with response history | SHA-256 hash-chained audit log with verification endpoint |
| Alert fatigue as an attack surface | budgeted thresholding + Bayesian PPV reporting + explanation gating |

## 7. Honest limitations

1. Synthetic corpus. Ground truth is exact and the confusables are adversarially designed,
   but it is not production traffic. The defensible claim is *methodological*, not "this
   generalises to your network".
2. `mean_pkt_size` carries 0.81 of GBDT importance — the generator gives it more separating
   power than real traffic would. A real corpus would flatten that distribution.
3. The hybrid trails supervised-only on aggregate PR-AUC on this corpus (§3.2).
4. Zero-day holdout n = 1–12 windows: directional, underpowered.
5. `suspected_family` is a signature heuristic over attributions and can mislabel a family
   (the top alert in the dashboard is a brute-force window called `dos`); the ground-truth
   column is shown so the analyst is never misled.
6. Graph leg alone is weak (PR-AUC 0.039) — it is a specialist, not a detector.

## 8. References

- Axelsson, *The Base-Rate Fallacy and its Implications for the Difficulty of Intrusion Detection*, ACM CCS 1999 / TISSEC 3(3):186–205, 2000.
- Engelen, Rimmer & Joosen, *Troubleshooting an Intrusion Detection Dataset: the CICIDS2017 Case Study*, IEEE WTMC 2021.
- Lanvin et al., *Errors in the CICIDS2017 Dataset and the Significant Differences in Detection Performances It Makes*, 2022.
- Dube, *Faulty use of the CIC-IDS 2017 dataset in information security research*, J. Computer Virology & Hacking Techniques 20:203–211, 2024.
- Sharafaldin, Lashkari & Ghorbani, *Toward Generating a New Intrusion Detection Dataset and Intrusion Traffic Characterization*, ICISSP 2018.
- Liu, Ting & Zhou, *Isolation Forest*, ICDM 2008.
- Chen & Guestrin, *XGBoost: A Scalable Tree Boosting System*, KDD 2016.
- Štrumbelj & Kononenko, *Explaining prediction models and individual predictions with feature contributions*, KAIS 41:647–665, 2014.
- Larroche, *Designing a Reliable Lateral Movement Detector Using a Graph Foundation Model*, 2025.
- *LMDetect: Lateral Movement Detection via Time-aware Subgraph Classification on Authentication Logs*, 2024.
- Soheily-Khah, Marteau & Béchet, *Intrusion Detection in Network Systems Through Hybrid Supervised and Unsupervised Mining*, IEEE 2018.
- Bohara et al. / Wiley 2024, *Hybrid supervised + unsupervised stacking for intrusion detection*.

## 9. The alert explainer (`explain_trace.py`)

Every pipeline run writes `artifacts/alert_trace.json`: a single real alert
walked through six stages -- `telemetry -> features -> legs -> fusion ->
threshold -> response` -- with the arithmetic exposed at each step. The console
renders it as the **Explain** section. Nothing in that section is written by
hand, so a "how it works" walkthrough cannot drift away from the model that is
actually shipping.

The alert it selects is deliberately not a clean win. `select_alert()` prefers a
window where the model disagrees with ground truth, because those are the ones
that teach something.

### 9.1 The case: right answer, wrong reason

`AL-1767491700-h002`, p = 0.9866137, ranked **1st of 11,326** test windows.
It is a true positive: the window really is an attack. The model named it `dos`.
The ground truth is `brute_force`.

The evidence for brute force was sitting in the same feature vector:

| feature | value | percentile | attributed credit |
| --- | --- | --- | --- |
| `failed_logins` | 45.0 | 99.912 | **0.000** |
| `failed_login_ratio` | 0.957 | -- | 0.000 |
| `z_failed_logins` | 45.0 | 99.912 | **0.000** |
| `mean_pkt_size` | 201.98 | -- | 3.327 |

45 of 47 authentication events in a five-minute window failed, a top-0.1%
reading, and the explanation gave it exactly zero weight. Meanwhile
`mean_pkt_size` -- which holds **80.9%** of the booster's split importance --
carried the entire narrative. That is textbook shortcut learning: the detector
ranks the window correctly while reasoning about the wrong signal, so the SOAR
layer fires a rate-limit playbook at what is really a credential attack.

Ranking quality and reasoning quality are separate properties. The explainer
shows both failing independently, which no aggregate metric on this repo would
have revealed.

### 9.2 The fusion arithmetic, and a bug the artifact caught

Stage 4 renders the stacker as visible arithmetic:

```
 isolation forest    0.342546 x  +4.8906  =  +1.6753
 gradient boosting   1.100883 x  +9.6462  = +10.6193
 graph leg           0.113480 x  +3.8724  =  +0.4394
 intercept                                 -8.433969
                                          ----------
 log-odds                                  +4.300047
 sigmoid(log-odds)                          0.9866137
```

The trace then compares that reconstruction to the probability the pipeline
actually published and records the gap as `reconstruction_error`. It is `0.0`.

That check earned its place immediately. The first implementation multiplied
each coefficient by the leg's **raw** score, which produced entirely plausible
numbers and a `reconstruction_error` of `0.0193`. `LogisticStacker.decision()`
standardises its inputs first (`Z = (X - mu) / sd`, then `Z @ w + b`), so the
term is `coefficient x standardised input`. Reading logistic weights against
raw features is one of the most common ways to misreport a model, and the only
reason it did not ship here is that the artifact was required to reproduce a
number it could not fake. `tests/test_explain_trace.py::TestStandardisedFusion`
now fails if anyone reintroduces it.

### 9.3 Two ways of scoring the family

`family_evidence()` scores each attack family twice: **attributed** credit (what
the model leaned on) and **corroboration** (how extreme that family's signature
features are in this window, ignoring the model entirely). For this alert:

| family | attributed | corroboration |
| --- | --- | --- |
| `dos` | 3.007 | 0.983 |
| `brute_force` | **0.000** | **0.995** |
| `lateral_movement` | 0.000 | 0.988 |

Brute force has the highest corroboration of any family and no attributed
credit at all. A model-only explanation cannot express that; two independent
views can.
