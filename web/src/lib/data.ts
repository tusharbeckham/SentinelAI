/**
 * Typed access to the artifacts the Python pipeline writes.
 *
 * Every type here was derived from the real artifacts/*.json produced by
 * `python -m sentinelai.pipeline`, not from a guess: if the pipeline schema
 * changes, `pnpm typecheck` is the thing that catches it.
 *
 * Two data paths, deliberately:
 *   STATIC  - fetch ./data/report.json etc. Works on a free static host with no
 *             backend at all, so the public demo can never 500.
 *   LIVE    - POST /v1/score against the authenticated Python API for on-demand
 *             scoring. Enabled only when a token is supplied.
 */

export type OperatingPoint = {
	threshold: number
	alerts_per_day: number
	recall: number
	precision_eval: number
	fpr: number
	tpr: number
	ppv_at_deployment_prior: number
	deployment_prior: number
	budget_per_day: number
}

export type SweepPoint = OperatingPoint & {
	requested_budget_per_day: number
	recall_per_alert_per_day: number
}

export type AblationLeg = {
	n: number
	positives: number
	roc_auc: number
	average_precision: number
	recall_at_budget: number
	precision_at_budget: number
	false_positives_at_budget: number
}

export type FamilyRecall = { windows: number; recall: number }

export type ZeroDayLeg = {
	recall_on_held_out_family_at_budget: number
	mean_percentile_of_family: number
}

export type ZeroDayRow = {
	held_out_family: string
	test_windows_of_family: number
	supervised_gbdt_blind: ZeroDayLeg
	unsupervised_isolation_forest: ZeroDayLeg
	graph_only: ZeroDayLeg
}

export type Calibration = {
	brier_test: number
	ece_test: number
	stacker_coefficients: Record<string, number>
	stacker_intercept: number
	eval_prior: number
	deployment_prior: number
	prior_shift_logodds: number
}

/**
 * A retrain leg is NOT a flat number map: alongside its scalar metrics it
 * carries a nested `operating_point` object. Typing it as Record<string, number>
 * was wrong and let a generic Object.keys loop call .toFixed on that object,
 * which threw and blanked the entire page.
 */
export type RetrainLeg = {
	n: number
	positives: number
	eval_base_rate: number
	roc_auc: number
	average_precision: number
	'tpr_at_fpr_1e-2': number
	'tpr_at_fpr_1e-3': number
	operating_point: OperatingPoint
}

export type Report = {
	generated_at: string
	runtime_seconds: number
	windows: Record<string, number | string>
	features: { count: number; graph: string[]; absolute_and_relative: string[] }
	analyst_budget_per_day: number
	budget_sweep: SweepPoint[]
	ablation_on_test: Record<string, AblationLeg>
	zero_day_holdout: ZeroDayRow[]
	operating_point: OperatingPoint
	per_family_recall_at_operating_point: Record<string, FamilyRecall | number>
	calibration: Calibration
	gbdt_top_importance: Record<string, number>
	soar: {
		decisions: number
		by_mode: Record<string, number>
		audit_chain_valid: boolean
	}
	drift_and_active_learning: {
		labels_spent: number
		drift: Record<string, unknown>
		before_retrain: RetrainLeg
		after_retrain: RetrainLeg
		delta: Record<string, number>
	}
}

export type Attribution = { feature: string; contribution: number; value: number }

export type Alert = {
	alert_id: string
	entity: string
	window: number
	window_iso: string
	probability: number
	probability_at_deployment_prior: number
	suspected_family: string
	ground_truth: string
	is_known_benign_anomaly: boolean
	narrative: string
	top: Attribution[]
	base_value_logodds: number
	attribution_residual: number
}

export type Decision = {
	ts: number
	entity: string
	window: number
	probability: number
	suspected_family: string
	mode: 'auto_contain' | 'human_review' | 'enrich' | 'suppress'
	action: string
	executed: boolean
	explanation_support: number
	rationale: string[]
	prev_hash: string
	hash: string
}

export type Bundle = {
	report: Report
	alerts: Alert[]
	decisions: Decision[]
}

async function getJson<T>(path: string): Promise<T> {
	const res = await fetch(path, { headers: { accept: 'application/json' } })
	if (!res.ok) {
		throw new Error(`${path} responded ${res.status}. Run "pnpm sync-data" first.`)
	}
	return (await res.json()) as T
}

/** Load the measured artifacts. Base path is relative so subpath hosting works. */
export async function loadBundle(base = './data'): Promise<Bundle> {
	const [report, alerts, decisions] = await Promise.all([
		getJson<Report>(`${base}/report.json`),
		getJson<Alert[]>(`${base}/alerts.json`),
		getJson<Decision[]>(`${base}/soar_decisions.json`),
	])
	return { report, alerts, decisions }
}

export type LiveStatus = 'unknown' | 'offline' | 'online'

/** Is the authenticated Python API reachable? /healthz is its only public route. */
export async function probeApi(signal?: AbortSignal): Promise<LiveStatus> {
	try {
		const res = await fetch('/healthz', signal ? { signal } : undefined)
		return res.ok ? 'online' : 'offline'
	} catch {
		return 'offline'
	}
}

export type ScoreResponse = {
	probability: number
	probability_at_deployment_prior?: number
	top?: Attribution[]
	narrative?: string
}

/**
 * Score a feature vector against the live model. Requires an analyst-role JWT;
 * the server returns 401/403 rather than degrading, and we surface that verbatim
 * instead of pretending the request succeeded.
 */
export async function scoreLive(
	features: Record<string, number>,
	token: string,
): Promise<ScoreResponse> {
	const res = await fetch('/v1/score', {
		method: 'POST',
		headers: {
			'content-type': 'application/json',
			authorization: `Bearer ${token}`,
		},
		body: JSON.stringify({ features }),
	})
	const body = (await res.json().catch(() => ({}))) as Record<string, unknown>
	if (!res.ok) {
		const detail = typeof body.error === 'string' ? body.error : res.statusText
		throw new Error(`${res.status} ${detail}`)
	}
	return body as ScoreResponse
}

/** Normalise the per-family map, which also carries a scalar FP-rate key. */
export function familyRows(
	map: Record<string, FamilyRecall | number>,
): Array<{ family: string; windows: number; recall: number }> {
	return Object.entries(map)
		.filter((entry): entry is [string, FamilyRecall] => typeof entry[1] === 'object')
		.map(([family, v]) => ({ family, windows: v.windows, recall: v.recall }))
		.sort((a, b) => b.recall - a.recall || b.windows - a.windows)
}

export type DriftRow = { label: string; before: number; after: number }

/**
 * The before/after retrain comparison, as an explicit list of numeric pairs.
 * Named deliberately rather than derived from Object.keys, so a nested object
 * in the artifact can never again be rendered as if it were a number.
 */
export function driftRows(d: Report['drift_and_active_learning']): DriftRow[] {
	const b = d.before_retrain
	const a = d.after_retrain
	return [
		{ label: 'average precision', before: b.average_precision, after: a.average_precision },
		{
			label: `recall @ ${a.operating_point.budget_per_day}/day`,
			before: b.operating_point.recall,
			after: a.operating_point.recall,
		},
		{
			label: `precision @ ${a.operating_point.budget_per_day}/day`,
			before: b.operating_point.precision_eval,
			after: a.operating_point.precision_eval,
		},
		{
			label: 'tpr @ fpr 1e-3',
			before: b['tpr_at_fpr_1e-3'],
			after: a['tpr_at_fpr_1e-3'],
		},
	].filter((r) => typeof r.before === 'number' && typeof r.after === 'number')
}
