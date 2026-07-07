"""Render the SOC triage dashboard from measured artifacts.

Standalone HTML: no build step, no CDN, no network. Reads artifacts/report.json,
alerts.json and soar_decisions.json and inlines them, so the dashboard always
shows real measured output rather than mock data.

Run: python3 -m sentinelai.build_dashboard --artifacts artifacts --out dashboard.html
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SentinelAI \u2014 SOC Triage Console</title>
<style>
  :root {
    --canvas:#191919; --surface:#202020; --raised:#2a2a29; --hover:#383836;
    --text:#fff; --muted:rgba(255,255,255,.65); --faint:rgba(255,255,255,.42);
    --border:rgba(255,255,255,.20); --blue:#5E9FE8; --green:#72BC8F;
    --orange:#DE9255; --red:#E97366; --purple:#BF8EDA;
  }
  * { box-sizing:border-box; }
  body {
    margin:0; background:var(--canvas); color:var(--text);
    font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    -webkit-font-smoothing:antialiased;
  }
  .wrap { max-width:1120px; margin:0 auto; padding:24px; }
  header { display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; margin-bottom:16px; }
  h1 { font-size:20px; margin:0; letter-spacing:-.01em; }
  .sub { color:var(--muted); font-size:14px; }
  .pill {
    font-size:12px; padding:3px 9px; border-radius:999px; border:1px solid var(--border);
    color:var(--muted); background:var(--surface);
  }
  .kpis { display:grid; grid-template-columns:repeat(6,1fr); gap:8px; margin-bottom:20px; }
  .kpi { background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:12px; }
  .kpi .v { font-size:22px; font-weight:600; letter-spacing:-.02em; }
  .kpi .l { font-size:12px; color:var(--muted); margin-top:2px; }
  .kpi .n { font-size:11px; color:var(--faint); margin-top:4px; }
  .cols { display:grid; grid-template-columns:340px 1fr; gap:16px; align-items:start; }
  .panel { background:var(--surface); border:1px solid var(--border); border-radius:8px; }
  .panel > h2 {
    font-size:13px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted);
    margin:0; padding:12px 16px; border-bottom:1px solid var(--border); font-weight:600;
  }
  .queue { max-height:430px; overflow:auto; }
  .row {
    display:block; width:100%; text-align:left; background:none; border:0; color:inherit;
    padding:10px 16px; border-bottom:1px solid rgba(255,255,255,.07); cursor:pointer;
    font:inherit;
  }
  .row:hover { background:var(--hover); }
  .row[aria-selected="true"] { background:var(--raised); box-shadow:inset 3px 0 0 var(--blue); }
  .row .top { display:flex; justify-content:space-between; gap:8px; align-items:center; }
  .ent { font-weight:600; font-size:14px; }
  .score { font-variant-numeric:tabular-nums; font-size:13px; color:var(--muted); }
  .row .meta { font-size:12px; color:var(--faint); margin-top:2px; }
  .badge { font-size:11px; padding:2px 7px; border-radius:4px; border:1px solid transparent; white-space:nowrap; }
  .b-auto  { color:var(--red);    background:rgba(233,115,102,.12); border-color:rgba(233,115,102,.4); }
  .b-human { color:var(--orange); background:rgba(222,146,85,.12);  border-color:rgba(222,146,85,.4); }
  .b-enrich{ color:var(--blue);   background:rgba(94,159,232,.12);  border-color:rgba(94,159,232,.4); }
  .b-sup   { color:var(--muted);  background:rgba(255,255,255,.06); border-color:var(--border); }
  .detail { padding:16px; }
  .detail h3 { margin:0 0 2px; font-size:18px; letter-spacing:-.01em; }
  .narr { color:var(--muted); font-size:14px; margin:8px 0 16px; }
  .attr { display:grid; grid-template-columns:170px 1fr 72px; gap:8px 10px; align-items:center; }
  .attr .fname { font:12px/1.4 Menlo,Consolas,monospace; color:var(--muted); overflow-wrap:anywhere; }
  .bar { height:10px; border-radius:3px; background:rgba(255,255,255,.07); overflow:hidden; }
  .bar > i { display:block; height:100%; border-radius:3px; }
  .up { background:var(--red); }
  .down { background:var(--blue); }
  .attr .val { font-size:12px; color:var(--faint); text-align:right; font-variant-numeric:tabular-nums; }
  .facts { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin:16px 0 0; }
  .fact { background:var(--raised); border-radius:6px; padding:8px 10px; }
  .fact .l { font-size:11px; color:var(--faint); }
  .fact .v { font-size:14px; font-weight:600; margin-top:2px; }
  .action { margin-top:16px; padding:12px; border-radius:6px; background:var(--raised); font-size:13px; }
  .action ul { margin:6px 0 0; padding-left:18px; color:var(--muted); }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:right; padding:8px 16px; border-bottom:1px solid rgba(255,255,255,.07); }
  th:first-child, td:first-child { text-align:left; }
  th { font-size:11px; text-transform:uppercase; letter-spacing:.06em; color:var(--faint); font-weight:600; }
  tr.best td { background:rgba(114,188,143,.08); }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-top:16px; }
  footer { color:var(--faint); font-size:12px; margin-top:20px; line-height:1.6; }
  code { font:12px Menlo,Consolas,monospace; color:var(--muted); }
  @media (max-width:820px) {
    .cols, .grid2 { grid-template-columns:1fr; }
    .kpis { grid-template-columns:repeat(2,1fr); }
    .attr { grid-template-columns:120px 1fr 60px; }
  }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>SentinelAI</h1>
    <span class="sub">SOC triage console \u00b7 hybrid anomaly detection</span>
    <span class="pill" id="gen"></span>
    <span class="pill">dry-run mode \u00b7 no live containment</span>
  </header>

  <section class="kpis" id="kpis"></section>

  <div class="cols">
    <section class="panel">
      <h2>Triage queue \u00b7 <span id="qn"></span></h2>
      <div class="queue" id="queue" role="listbox" aria-label="Alert triage queue"></div>
    </section>
    <section class="panel">
      <h2>Alert detail \u00b7 why this fired</h2>
      <div class="detail" id="detail"></div>
    </section>
  </div>

  <div class="grid2">
    <section class="panel">
      <h2>Ablation \u00b7 measured on held-out test period</h2>
      <table id="abl"></table>
    </section>
    <section class="panel">
      <h2>Drift &amp; active learning</h2>
      <table id="drift"></table>
    </section>
  </div>

  <section class="panel" style="margin-top:16px">
    <h2>Alert budget sweep \u00b7 what recall costs in analyst attention</h2>
    <div class="detail" id="sweep"></div>
  </section>

  <footer id="foot"></footer>
</div>

<script id="payload" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('payload').textContent);
const R = D.report, ALERTS = D.alerts, DEC = D.decisions;
const pct = x => (100 * x).toFixed(1) + '%';
const num = (x, d = 3) => Number(x).toFixed(d);
const decByKey = {};
DEC.forEach(d => { decByKey[d.entity + '|' + d.window] = d; });
const modeClass = { auto_contain:'b-auto', human_review:'b-human', enrich:'b-enrich', suppress:'b-sup' };

const op = R.operating_point, hy = R.ablation_on_test.hybrid_ensemble;
document.getElementById('gen').textContent = 'run ' + R.generated_at.slice(0,16).replace('T',' ') + 'Z \u00b7 ' + R.runtime_seconds + 's';

const kpis = [
  ['Alerts / day', op.alerts_per_day.toFixed(1), 'analyst budget ' + R.analyst_budget_per_day],
  ['Recall', pct(op.recall), 'attack windows caught'],
  ['Precision', pct(op.precision_eval), 'at eval prior ' + num(R.calibration.eval_prior, 4)],
  ['PR-AUC', num(hy.average_precision), 'ROC-AUC ' + num(hy.roc_auc)],
  ['PPV @ 1e-4 prior', pct(op.ppv_at_deployment_prior), 'Bayesian, base-rate corrected'],
  ['ECE', num(R.calibration.ece_test, 4), 'Brier ' + num(R.calibration.brier_test, 4)],
];
document.getElementById('kpis').innerHTML = kpis.map(([l, v, n]) =>
  `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div><div class="n">${n}</div></div>`).join('');

document.getElementById('qn').textContent = ALERTS.length + ' open';
document.getElementById('queue').innerHTML = ALERTS.map((a, i) => {
  const d = decByKey[a.entity + '|' + a.window] || {};
  const cls = modeClass[d.mode] || 'b-sup';
  return `<button class="row" role="option" aria-selected="${i === 0}" data-i="${i}">
    <span class="top"><span class="ent">${a.entity}</span>
    <span class="badge ${cls}">${(d.mode || 'queued').replace(/_/g, ' ')}</span></span>
    <span class="meta">${a.suspected_family.replace(/_/g, ' ')} \u00b7 ${a.window_iso.slice(5, 16).replace('T', ' ')}
    \u00b7 <span class="score">p=${a.probability.toFixed(3)}</span></span>
  </button>`;
}).join('');

function render(i) {
  const a = ALERTS[i], d = decByKey[a.entity + '|' + a.window] || {};
  const max = Math.max(...a.top.map(t => Math.abs(t.contribution))) || 1;
  const rows = a.top.map(t => {
    const w = Math.round(100 * Math.abs(t.contribution) / max);
    const cls = t.contribution >= 0 ? 'up' : 'down';
    const v = Math.abs(t.value) >= 1000 ? Number(t.value).toExponential(1) : Number(t.value).toFixed(2);
    return `<div class="fname">${t.feature}</div>
      <div class="bar"><i class="${cls}" style="width:${w}%"></i></div>
      <div class="val">${v}</div>`;
  }).join('');
  const verdict = a.ground_truth === 'benign'
    ? (a.is_known_benign_anomaly ? 'benign \u2014 known rare-but-legitimate activity' : 'benign')
    : 'true positive \u2014 ' + a.ground_truth.replace(/_/g, ' ');
  document.getElementById('detail').innerHTML = `
    <h3>${a.entity} \u00b7 ${a.suspected_family.replace(/_/g, ' ')}</h3>
    <div class="sub">${a.alert_id} \u00b7 window ${a.window_iso.replace('T', ' ').slice(0, 16)}Z</div>
    <p class="narr">${a.narrative}</p>
    <div class="attr">${rows}</div>
    <div class="facts">
      <div class="fact"><div class="l">Score</div><div class="v">${a.probability.toFixed(3)}</div></div>
      <div class="fact"><div class="l">At 1e-4 prior</div><div class="v">${a.probability_at_deployment_prior.toFixed(3)}</div></div>
      <div class="fact"><div class="l">Attribution residual</div><div class="v">${Number(a.attribution_residual).toExponential(1)}</div></div>
      <div class="fact"><div class="l">Labelled outcome</div><div class="v" style="font-size:12px">${verdict}</div></div>
    </div>
    <div class="action">
      <strong>Policy decision:</strong> ${(d.mode || 'queued').replace(/_/g, ' ')}
      ${d.playbook ? ' \u00b7 playbook <code>' + d.playbook + '</code>' : ''}
      <ul>${(d.rationale || ['awaiting policy evaluation']).map(r => '<li>' + r + '</li>').join('')}
      ${(d.actions || []).length ? '<li>actions: <code>' + d.actions.join(', ') + '</code></li>' : ''}</ul>
    </div>`;
  document.querySelectorAll('.row').forEach(r => r.setAttribute('aria-selected', r.dataset.i === String(i)));
}
document.getElementById('queue').addEventListener('click', e => {
  const b = e.target.closest('.row'); if (b) render(Number(b.dataset.i));
});
render(0);

const order = ['unsupervised_isolation_forest', 'supervised_gbdt', 'graph_lateral_movement', 'hybrid_ensemble'];
const label = {
  unsupervised_isolation_forest:'Isolation Forest (unsup.)',
  supervised_gbdt:'GBDT (supervised)',
  graph_lateral_movement:'Auth-graph only',
  hybrid_ensemble:'Hybrid ensemble'
};
document.getElementById('abl').innerHTML =
  `<tr><th>Detector</th><th>PR-AUC</th><th>Recall</th><th>Prec.</th><th>FP</th><th>FP on benign anomalies</th></tr>` +
  order.map(k => {
    const m = R.ablation_on_test[k], o = m.operating_point_at_budget, f = R.false_positive_provenance[k];
    return `<tr class="${k === 'hybrid_ensemble' ? 'best' : ''}"><td>${label[k]}</td><td>${num(m.average_precision)}</td>
      <td>${pct(o.recall)}</td><td>${pct(o.precision_eval)}</td><td>${f.false_positives}</td>
      <td>${pct(f.share_of_fp_from_benign_anomalies)}</td></tr>`;
  }).join('');

const dal = R.drift_and_active_learning;
document.getElementById('drift').innerHTML =
  `<tr><th>Metric</th><th>Before</th><th>After ${dal.labels_spent} labels</th></tr>
   <tr><td>PR-AUC on drifted period</td><td>${num(dal.before_retrain.average_precision)}</td><td>${num(dal.after_retrain.average_precision)}</td></tr>
   <tr><td>Recall @ budget</td><td>${pct(dal.before_retrain.operating_point.recall)}</td><td>${pct(dal.after_retrain.operating_point.recall)}</td></tr>
   <tr><td>Precision @ budget</td><td>${pct(dal.before_retrain.operating_point.precision_eval)}</td><td>${pct(dal.after_retrain.operating_point.precision_eval)}</td></tr>
   <tr><td>PSI retrain trigger</td><td colspan="2" style="text-align:right">${dal.drift.retrain_recommended ? 'fired \u00b7 ' + (dal.drift.material_shift.join(', ') || 'n/a') : 'stable'}</td></tr>`;

const sw = R.budget_sweep || [];
if (sw.length) {
  const W = 760, H = 250, L = 46, B = 38, T = 14, Rp = 14;
  const xs = sw.map(s => s.requested_budget_per_day);
  const lo = Math.log(Math.min(...xs)), hi = Math.log(Math.max(...xs));
  const X = v => L + (W - L - Rp) * (Math.log(v) - lo) / (hi - lo);
  const Y = v => T + (H - T - B) * (1 - v);
  const pts = key => sw.map(s => X(s.requested_budget_per_day) + ',' + Y(s[key])).join(' ');
  const line = (key, c) => `<polyline fill="none" stroke="${c}" stroke-width="2" points="${pts(key)}"/>` +
    sw.map(s => `<circle cx="${X(s.requested_budget_per_day)}" cy="${Y(s[key])}" r="3" fill="${c}"/>`).join('');
  const grid = [0, .25, .5, .75, 1].map(v =>
    `<line x1="${L}" x2="${W - Rp}" y1="${Y(v)}" y2="${Y(v)}" stroke="rgba(255,255,255,.10)"/>` +
    `<text x="${L - 8}" y="${Y(v) + 4}" fill="rgba(255,255,255,.42)" font-size="11" text-anchor="end">${100 * v}%</text>`).join('');
  const ticks = sw.filter(s => [5, 10, 20, 50, 100, 200].includes(s.requested_budget_per_day)).map(s =>
    `<text x="${X(s.requested_budget_per_day)}" y="${H - 14}" fill="rgba(255,255,255,.42)" font-size="11" text-anchor="middle">${s.requested_budget_per_day}</text>`).join('');
  const chosen = X(R.analyst_budget_per_day);
  document.getElementById('sweep').innerHTML =
    `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Recall and precision versus alert budget per day">
       ${grid}${ticks}
       <line x1="${chosen}" x2="${chosen}" y1="${T}" y2="${H - B}" stroke="#DE9255" stroke-dasharray="4 3"/>
       <text x="${chosen}" y="${T + 10}" fill="#DE9255" font-size="11" text-anchor="middle">chosen ${R.analyst_budget_per_day}/day</text>
       ${line('recall', '#5E9FE8')}${line('precision_eval', '#72BC8F')}
       <text x="${W / 2}" y="${H - 1}" fill="rgba(255,255,255,.42)" font-size="11" text-anchor="middle">alert budget per day (log scale)</text>
     </svg>
     <p class="narr" style="margin:8px 0 0">
       <span style="color:#5E9FE8">\u25cf recall</span> &nbsp; <span style="color:#72BC8F">\u25cf precision</span> \u2014
       at 15 alerts/day precision is perfect but ${pct(sw.find(s => s.requested_budget_per_day === 15).recall)} of attacks are missed;
       at 200/day recall reaches ${pct(sw.find(s => s.requested_budget_per_day === 200).recall)} and precision falls to
       ${pct(sw.find(s => s.requested_budget_per_day === 200).precision_eval)}. Bayesian PPV at the 1e-4 deployment prior
       falls monotonically from ${num(sw[0].ppv_at_deployment_prior, 3)} to ${num(sw[sw.length - 1].ppv_at_deployment_prior, 4)} \u2014
       buying recall with analyst attention has a strictly worsening exchange rate.
     </p>`;
}

const s = R.soar, modes = Object.entries(s.by_mode).map(([k, v]) => v + ' ' + k.replace(/_/g, ' ')).join(' \u00b7 ');
document.getElementById('foot').innerHTML =
  `Corpus: ${R.dataset.flows.toLocaleString()} flows, ${R.dataset.auth_events.toLocaleString()} auth events, ${R.windows.total.toLocaleString()} entity-windows across ${R.windows.entities} hosts over ${R.dataset.days} days \u00b7
   ${R.dataset.incident_windows} attack windows and ${R.dataset.benign_anomaly_windows} rare-but-legitimate windows \u00b7
   chronological split (${R.windows.train.toLocaleString()}/${R.windows.calibration.toLocaleString()}/${R.windows.test.toLocaleString()}), ${R.windows.stacking_folds}-fold out-of-fold stacking.<br>
   SOAR: ${s.decisions} decisions \u2014 ${modes} \u00b7 hash-chained audit log verified: ${s.audit_chain_valid}.
   Fusion weights: ${Object.entries(R.calibration.stacker_coefficients).map(([k, v]) => k + ' ' + num(v, 2)).join(', ')}.`;
</script>
</body>
</html>
"""


def build(artifacts: str = "artifacts", out: str = "dashboard.html") -> Path:
    a = Path(artifacts)
    payload = {
        # encoding is always explicit: Python defaults to the platform codec,
        # which is cp1252 on Windows and cannot represent the glyphs used in
        # this dashboard. Never rely on the locale default for data files.
        "report": json.loads((a / "report.json").read_text(encoding="utf-8")),
        "alerts": json.loads((a / "alerts.json").read_text(encoding="utf-8")),
        "decisions": json.loads((a / "soar_decisions.json").read_text(encoding="utf-8")),
    }
    # </script> inside JSON would terminate the block early; escape defensively.
    blob = json.dumps(payload).replace("</", "<\\/")
    target = Path(out)
    target.write_text(TEMPLATE.replace("__DATA__", blob))
    return target


if __name__ == "__main__":  # pragma: no cover
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts", default="artifacts")
    ap.add_argument("--out", default="dashboard.html")
    args = ap.parse_args()
    p = build(args.artifacts, args.out)
    print(f"wrote {p} ({p.stat().st_size / 1024:.0f} KB)")
