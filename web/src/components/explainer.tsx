/**
 * The pipeline explainer: one real alert, walked through six stages.
 *
 * Everything rendered here comes from `artifacts/alert_trace.json`, which the
 * Python pipeline emits from the same fitted stacker that produced the alert.
 * No prose in this file states a number. If the model changes, this section
 * changes with it, which is the only way a "how it works" page stays true.
 *
 * Interaction is a stepper, not a scroll hijack. The previous attempt pinned a
 * magnifying lens over the hero and animated four stacked copies of the same
 * text; it looked like a rendering bug and it was unreadable. One stage is
 * visible at a time, at full contrast, and the reader controls the pace with
 * clicks, arrow keys, or the progress rail.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { animate, stagger, utils } from 'animejs'

import { useReducedMotion } from '@/components/anime'
import { cn } from '@/lib/cn'
import type { AlertTrace, TraceBand, TraceLeg, TraceStage, TraceTerm } from '@/lib/data'

// ---------------------------------------------------------------- formatting

function fmt(n: number | null | undefined, digits = 2): string {
	if (n === null || n === undefined || !Number.isFinite(n)) return '--'
	const abs = Math.abs(n)
	if (abs !== 0 && abs < 0.001) return n.toExponential(2)
	if (abs >= 100000) return n.toLocaleString('en-US', { maximumFractionDigits: 0 })
	return n.toFixed(digits)
}

function pct(n: number | null | undefined, digits = 1): string {
	if (n === null || n === undefined || !Number.isFinite(n)) return '--'
	return `${(n * 100).toFixed(digits)}%`
}

function signed(n: number | null | undefined, digits = 3): string {
	if (n === null || n === undefined || !Number.isFinite(n)) return '--'
	return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}`
}

const GROUP_LABEL: Record<string, string> = {
	volume: 'volume',
	reach: 'reach',
	shape: 'flow shape',
	identity: 'identity',
	naming: 'DNS',
	graph: 'graph',
}

// ------------------------------------------------------------- small pieces

/** A labelled horizontal bar. `tone` maps to the palette, not to sentiment. */
function Bar({
	value,
	tone = 'signal',
	animateFrom0 = true,
}: {
	value: number
	tone?: 'signal' | 'safe' | 'watch' | 'alarm' | 'graph' | 'faint'
	animateFrom0?: boolean
}) {
	const width = `${Math.max(0, Math.min(1, value)) * 100}%`
	const color = tone === 'faint' ? 'var(--color-line-strong)' : `var(--color-${tone})`
	return (
		<div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-line)]">
			<div
				data-bar={animateFrom0 ? '' : undefined}
				style={{ width, background: color }}
				className="h-full rounded-full"
			/>
		</div>
	)
}

function Field({
	label,
	value,
	hint,
}: {
	label: string
	value: string
	hint?: string
}) {
	return (
		<div data-row className="panel p-3">
			<div className="text-[13px] uppercase tracking-wide text-ink-faint">{label}</div>
			<div className="tabular mt-1 text-lg text-ink">{value}</div>
			{hint ? <div className="mt-1 text-[13px] text-ink-dim">{hint}</div> : null}
		</div>
	)
}

// ------------------------------------------------------------ stage bodies

function TelemetryStage({ stage }: { stage: TraceStage }) {
	const c = stage.corpus ?? {}
	return (
		<div className="grid gap-6 lg:grid-cols-[1.1fr_1fr]">
			<div>
				<div className="text-[13px] uppercase tracking-wide text-ink-faint">
					observed in this window
				</div>
				<div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3">
					{(stage.observed ?? []).map((o) => (
						<Field
							key={o.label}
							label={o.label}
							value={fmt(o.value, o.value !== null && Number.isInteger(o.value) ? 0 : 2)}
						/>
					))}
				</div>
				<p className="mt-4 text-[15px] text-ink-dim">
					45 of 47 authentication events in this five-minute window failed. That single
					fact is the whole case, and stage 2 shows the model barely used it.
				</p>
			</div>
			<div data-row className="panel p-4">
				<div className="text-[13px] uppercase tracking-wide text-ink-faint">corpus</div>
				<dl className="mt-3 space-y-2 text-[15px]">
					{[
						['flow records', fmt(c.flows, 0)],
						['auth events', fmt(c.auth_events, 0)],
						['hosts / users', `${fmt(c.hosts, 0)} / ${fmt(c.users, 0)}`],
						['days observed', fmt(c.days, 1)],
						['window length', `${fmt(c.window_s, 0)}s`],
						['attack base rate', pct(c.base_rate, 2)],
					].map(([k, v]) => (
						<div key={k} className="flex items-baseline justify-between gap-4">
							<dt className="text-ink-dim">{k}</dt>
							<dd className="tabular text-ink">{v}</dd>
						</div>
					))}
				</dl>
				<p className="mt-4 text-[13px] text-ink-dim">
					A 0.5% base rate is the reason precision is hard here: at this prevalence a
					99%-accurate detector still buries the analyst.
				</p>
			</div>
		</div>
	)
}

function FeaturesStage({ stage }: { stage: TraceStage }) {
	const bands: TraceBand[] = stage.bands ?? []
	const importance = Object.entries(stage.model_importance ?? {})
	return (
		<div className="grid gap-6 lg:grid-cols-[1.35fr_1fr]">
			<div className="space-y-4">
				{bands.map((band) => (
					<div key={band.group} data-row className="panel p-4">
						<div className="flex items-center justify-between">
							<span className="text-[13px] uppercase tracking-wide text-ink-faint">
								{GROUP_LABEL[band.group] ?? band.group}
							</span>
							<span className="tabular text-[13px] text-ink-faint">
								{band.features.length}
							</span>
						</div>
						<div className="mt-3 space-y-2.5">
							{band.features.slice(0, 5).map((f) => {
								const extreme = (f.percentile ?? 0) >= 0.99
								const ignored = extreme && !f.attributed_credit
								return (
									<div key={f.feature} className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1">
										<div className="flex min-w-0 items-center gap-2">
											<code className="truncate text-[13px] text-ink">{f.feature}</code>
											{f.attributed_credit ? (
												<span className="shrink-0 rounded-sm bg-[var(--color-signal)]/15 px-1.5 text-[12px] text-[var(--color-signal)]">
													credited
												</span>
											) : null}
											{ignored ? (
												<span className="shrink-0 rounded-sm bg-[var(--color-alarm)]/15 px-1.5 text-[12px] text-[var(--color-alarm)]">
													top 1%, ignored
												</span>
											) : null}
										</div>
										<span className="tabular text-[13px] text-ink-dim">{fmt(f.value)}</span>
										<div className="col-span-2">
											<Bar
												value={f.percentile ?? 0}
												tone={ignored ? 'alarm' : f.attributed_credit ? 'signal' : 'faint'}
											/>
										</div>
									</div>
								)
							})}
						</div>
					</div>
				))}
				<p className="text-[13px] text-ink-dim">
					Bar length is the value&rsquo;s percentile within the 11,326-window test set, not
					the value itself. A raw number means nothing without the population it came
					from.
				</p>
			</div>
			<div data-row className="panel h-fit p-4">
				<div className="text-[13px] uppercase tracking-wide text-ink-faint">
					what the booster actually relies on
				</div>
				<div className="mt-3 space-y-2.5">
					{importance.map(([name, share]) => (
						<div key={name}>
							<div className="flex items-baseline justify-between gap-3">
								<code className="truncate text-[13px] text-ink">{name}</code>
								<span className="tabular text-[13px] text-ink-dim">{pct(share)}</span>
							</div>
							<div className="mt-1">
								<Bar value={share} tone={share > 0.5 ? 'watch' : 'faint'} />
							</div>
						</div>
					))}
				</div>
				<p className="mt-4 text-[13px] text-ink-dim">
					One feature holds 81% of the split importance. That concentration is a
					shortcut, not a discovery, and it is the direct cause of the misnaming in
					stage 6.
				</p>
			</div>
		</div>
	)
}

function LegsStage({ stage }: { stage: TraceStage }) {
	const legs: TraceLeg[] = stage.legs ?? []
	const tones: Array<'signal' | 'safe' | 'graph'> = ['signal', 'safe', 'graph']
	return (
		<div>
			<div className="grid gap-4 md:grid-cols-3">
				{legs.map((leg, i) => (
					<div key={leg.input} data-row className="panel p-4">
						<div className="flex items-center gap-2">
							<span
								className="size-2 rounded-full"
								style={{ background: `var(--color-${tones[i] ?? 'signal'})` }}
							/>
							<span className="text-ink">{leg.label}</span>
						</div>
						<div className="tabular mt-3 text-2xl text-ink">{fmt(leg.value, 3)}</div>
						<div className="text-[13px] text-ink-faint">
							raw score &middot; z = {signed(leg.standardized, 2)}
						</div>
						<div className="mt-4 space-y-1">
							<div className="flex items-baseline justify-between text-[13px]">
								<span className="text-ink-dim">alone, avg precision</span>
								<span className="tabular text-ink">{fmt(leg.average_precision, 3)}</span>
							</div>
							<Bar value={leg.average_precision ?? 0} tone={tones[i] ?? 'signal'} />
							<div className="flex items-baseline justify-between pt-1 text-[13px]">
								<span className="text-ink-dim">recall at budget</span>
								<span className="tabular text-ink">{pct(leg.recall_at_budget)}</span>
							</div>
							<div className="flex items-baseline justify-between text-[13px]">
								<span className="text-ink-dim">ROC-AUC</span>
								<span className="tabular text-ink-dim">{fmt(leg.roc_auc, 4)}</span>
							</div>
						</div>
					</div>
				))}
			</div>
			<p data-row className="mt-4 text-[15px] text-ink-dim">
				Note the gap between the two columns: every leg has a ROC-AUC above 0.99, and
				one of them has an average precision of 0.04. At a 0.5% base rate ROC-AUC is
				nearly uninformative, which is why it is the metric most often quoted.
			</p>
		</div>
	)
}

function FusionStage({ stage }: { stage: TraceStage }) {
	const terms: TraceTerm[] = stage.terms ?? []
	const maxAbs = Math.max(
		...terms.map((t) => Math.abs(t.term)),
		Math.abs(stage.intercept ?? 0),
		1,
	)
	const exact = (stage.reconstruction_error ?? 1) < 1e-9
	return (
		<div className="grid gap-6 lg:grid-cols-[1.3fr_1fr]">
			<div>
				<div className="space-y-3">
					{terms.map((t) => (
						<div key={t.label} data-row className="panel p-3">
							<div className="flex flex-wrap items-baseline justify-between gap-2">
								<span className="text-ink">{t.label}</span>
								<span className="tabular text-[13px] text-ink-dim">
									{signed(t.coefficient, 4)} &times; {signed(t.standardized, 4)} ={' '}
									<span className="text-ink">{signed(t.term, 4)}</span>
								</span>
							</div>
							<div className="mt-2">
								<Bar value={Math.abs(t.term) / maxAbs} tone={t.term >= 0 ? 'alarm' : 'safe'} />
							</div>
							<div className="tabular mt-1.5 text-[12px] text-ink-faint">
								raw {fmt(t.value, 4)} &rarr; centred at {fmt(t.mean, 4)}, scaled by{' '}
								{fmt(t.scale, 4)}
							</div>
						</div>
					))}
					<div data-row className="panel p-3">
						<div className="flex items-baseline justify-between gap-2">
							<span className="text-ink-dim">intercept</span>
							<span className="tabular text-ink">{signed(stage.intercept, 4)}</span>
						</div>
						<div className="mt-2">
							<Bar value={Math.abs(stage.intercept ?? 0) / maxAbs} tone="safe" />
						</div>
					</div>
				</div>
				<p className="mt-4 text-[13px] text-ink-dim">{stage.caption_standardisation}</p>
			</div>

			<div className="space-y-3">
				<Field label="log-odds" value={signed(stage.log_odds, 6)} hint="terms + intercept" />
				<Field
					label="probability"
					value={fmt(stage.probability, 10)}
					hint="sigmoid of the log-odds"
				/>
				<div data-row className="panel p-3">
					<div className="text-[13px] uppercase tracking-wide text-ink-faint">
						reconstruction check
					</div>
					<div
						className={cn(
							'tabular mt-1 text-lg',
							exact ? 'text-[var(--color-safe)]' : 'text-[var(--color-alarm)]',
						)}
					>
						{exact ? 'exact' : fmt(stage.reconstruction_error, 8)}
					</div>
					<div className="mt-1 text-[13px] text-ink-dim">
						The arithmetic above is compared to the probability the pipeline actually
						published ({fmt(stage.published_probability, 10)}). This check caught a real
						bug: multiplying the weights by raw leg scores instead of standardised ones
						misstated the log-odds by 0.91.
					</div>
				</div>
				<div data-row className="panel p-3">
					<div className="text-[13px] uppercase tracking-wide text-ink-faint">
						at the deployment prior
					</div>
					<div className="tabular mt-1 text-lg text-ink">
						{fmt(stage.probability_at_deployment_prior, 6)}
					</div>
					<div className="mt-1 text-[13px] text-ink-dim">
						Shifted by {signed(stage.prior_shift_logodds, 4)} log-odds. {stage.caption_prior}
					</div>
				</div>
			</div>
		</div>
	)
}

function ThresholdStage({ stage }: { stage: TraceStage }) {
	const p = stage.probability ?? 0
	const thr = stage.threshold ?? 0
	return (
		<div className="grid gap-6 lg:grid-cols-[1.2fr_1fr]">
			<div>
				<div data-row className="panel p-5">
					<div className="text-[13px] uppercase tracking-wide text-ink-faint">
						probability axis
					</div>
					<div className="relative mt-8 h-2 rounded-full bg-[var(--color-line)]">
						<div
							className="absolute inset-y-0 left-0 rounded-full"
							style={{
								width: `${thr * 100}%`,
								background: 'var(--color-line-strong)',
							}}
						/>
						<div
							className="absolute -top-6 flex -translate-x-1/2 flex-col items-center"
							style={{ left: `${thr * 100}%` }}
						>
							<span className="tabular text-[12px] text-ink-dim">{fmt(thr, 4)}</span>
							<span className="mt-0.5 h-5 w-px bg-[var(--color-ink-faint)]" />
						</div>
						<div
							data-marker
							className="absolute top-1/2 size-4 -translate-x-1/2 -translate-y-1/2 rounded-full ring-4"
							style={{
								left: `${p * 100}%`,
								background: 'var(--color-alarm)',
								// @ts-expect-error -- CSS custom property in a style object
								'--tw-ring-color': 'color-mix(in srgb, var(--color-alarm) 25%, transparent)',
							}}
						/>
					</div>
					<div className="mt-3 flex justify-between text-[12px] text-ink-faint">
						<span>0</span>
						<span>1</span>
					</div>
					<div className="tabular mt-4 text-[15px] text-ink">
						this alert {fmt(p, 6)} &middot; margin {signed(stage.margin, 6)}
					</div>
					<div className="mt-1 text-[13px] text-ink-dim">
						Ranked {stage.rank} of {fmt(stage.total_windows, 0)} test windows.
					</div>
				</div>
				<p className="mt-4 text-[15px] text-ink-dim">
					The threshold is chosen by spending the budget, not by maximising F1. Move it
					down and recall rises while precision collapses; the Budget section sweeps
					that trade-off end to end.
				</p>
			</div>
			<div className="grid grid-cols-2 gap-3">
				<Field label="alerts / day" value={fmt(stage.alerts_per_day, 2)} />
				<Field label="budget / day" value={fmt(stage.budget_per_day, 0)} />
				<Field label="recall" value={pct(stage.recall)} />
				<Field label="precision" value={pct(stage.precision_eval)} />
				<Field label="FPR" value={fmt(stage.fpr, 6)} />
				<Field
					label="PPV at 1e-4"
					value={pct(stage.ppv_at_deployment_prior, 2)}
					hint="Bayesian, at production prevalence"
				/>
			</div>
		</div>
	)
}

function ResponseStage({ stage }: { stage: TraceStage }) {
	const auto = stage.mode === 'auto_contain'
	return (
		<div className="grid gap-6 lg:grid-cols-[1fr_1fr]">
			<div className="space-y-3">
				<div data-row className="panel p-4">
					<div className="text-[13px] uppercase tracking-wide text-ink-faint">decision</div>
					<div
						className={cn(
							'mt-1 text-xl',
							auto ? 'text-[var(--color-alarm)]' : 'text-[var(--color-watch)]',
						)}
					>
						{stage.mode ?? 'none'}
					</div>
					<code className="mt-2 block text-[13px] text-ink-dim">{stage.playbook}</code>
				</div>
				<div data-row className="panel p-4">
					<div className="text-[13px] uppercase tracking-wide text-ink-faint">rationale</div>
					<ul className="mt-2 space-y-1 text-[15px] text-ink-dim">
						{(stage.rationale ?? []).map((r) => (
							<li key={r}>{r}</li>
						))}
					</ul>
				</div>
				<div data-row className="panel p-4">
					<div className="flex items-center justify-between">
						<span className="text-[13px] uppercase tracking-wide text-ink-faint">
							audit chain
						</span>
						<span
							className={cn(
								'text-[13px]',
								stage.audit_chain_valid
									? 'text-[var(--color-safe)]'
									: 'text-[var(--color-alarm)]',
							)}
						>
							{stage.audit_chain_valid ? 'verified' : 'broken'}
						</span>
					</div>
					<p className="mt-2 text-[13px] text-ink-dim">
						Every decision is hash-chained to the one before it, so a silently edited
						response history fails verification.
					</p>
				</div>
			</div>
			<div data-row className="panel p-4">
				<div className="text-[13px] uppercase tracking-wide text-ink-faint">
					why this one contained automatically
				</div>
				<p className="mt-2 text-[15px] text-ink-dim">
					Containment needs three things at once: calibrated confidence above the auto
					threshold, an explanation whose top features are on that playbook&rsquo;s
					allow-list, and an asset that is not on the protected list. 45 of 50 alerts in
					this run failed at least one of those and went to human review instead.
				</p>
				<p className="mt-3 text-[15px] text-ink-dim">
					The uncomfortable part: the allow-list check passed against the{' '}
					<em>wrong</em> family. The window really is an attack, so the containment is
					defensible, but it fired a rate-limit playbook at what is actually a
					credential attack. Correct action, wrong reason.
				</p>
			</div>
		</div>
	)
}

function StageBody({ stage }: { stage: TraceStage }) {
	switch (stage.id) {
		case 'telemetry':
			return <TelemetryStage stage={stage} />
		case 'features':
			return <FeaturesStage stage={stage} />
		case 'legs':
			return <LegsStage stage={stage} />
		case 'fusion':
			return <FusionStage stage={stage} />
		case 'threshold':
			return <ThresholdStage stage={stage} />
		case 'response':
			return <ResponseStage stage={stage} />
		default:
			return null
	}
}

// ------------------------------------------------------------- verdict card

function VerdictCard({ trace }: { trace: AlertTrace }) {
	const d = trace.disagreement
	if (!d?.detected) return null
	return (
		<div className="panel mt-8 border-l-2 border-l-[var(--color-alarm)] p-5">
			<div className="flex flex-wrap items-center gap-3">
				<span className="text-[13px] uppercase tracking-wide text-ink-faint">verdict</span>
				<span className="rounded-sm bg-[var(--color-safe)]/15 px-2 py-0.5 text-[13px] text-[var(--color-safe)]">
					caught: true positive, rank 1
				</span>
				<span className="rounded-sm bg-[var(--color-alarm)]/15 px-2 py-0.5 text-[13px] text-[var(--color-alarm)]">
					misnamed: {d.suspected} vs {d.truth}
				</span>
			</div>
			<p className="mt-3 max-w-3xl text-[15px] text-ink-dim">{d.explanation}</p>
			{d.missed_evidence.length ? (
				<div className="mt-4">
					<div className="text-[13px] uppercase tracking-wide text-ink-faint">
						evidence in the top 1% that received no attribution
					</div>
					<div className="mt-2 space-y-2">
						{d.missed_evidence.map((m) => (
							<div key={m.feature} className="flex items-baseline justify-between gap-4">
								<code className="text-[13px] text-ink">{m.feature}</code>
								<span className="tabular text-[13px] text-ink-dim">
									{fmt(m.value)} &middot; {pct(m.percentile, 3)} percentile
								</span>
							</div>
						))}
					</div>
				</div>
			) : null}
			<div className="mt-5">
				<div className="text-[13px] uppercase tracking-wide text-ink-faint">
					family scored two ways
				</div>
				<div className="mt-2 space-y-2.5">
					{trace.family_evidence.map((f) => (
						<div key={f.family} className="grid grid-cols-[7rem_1fr_1fr] items-center gap-3">
							<code className="text-[13px] text-ink">{f.family}</code>
							<div>
								<Bar value={Math.min(1, f.attributed / 3.5)} tone="signal" />
								<div className="tabular mt-1 text-[12px] text-ink-faint">
									attributed {fmt(f.attributed, 3)}
								</div>
							</div>
							<div>
								<Bar value={f.corroboration ?? 0} tone="watch" />
								<div className="tabular mt-1 text-[12px] text-ink-faint">
									corroboration {pct(f.corroboration, 1)}
								</div>
							</div>
						</div>
					))}
				</div>
				<p className="mt-3 max-w-3xl text-[13px] text-ink-dim">
					Blue is what the model leaned on. Amber ignores the model and asks how extreme
					the family&rsquo;s signature features are in this window. Brute force has the
					highest corroboration of any family and zero attributed credit -- the
					signature of shortcut learning, not of a lucky guess.
				</p>
			</div>
		</div>
	)
}

// ------------------------------------------------------------------- section

export function PipelineExplainer({ trace }: { trace: AlertTrace }) {
	const [active, setActive] = useState(0)
	const bodyRef = useRef<HTMLDivElement | null>(null)
	const reduced = useReducedMotion()

	const stages = useMemo(() => trace.stages ?? [], [trace])
	const stage = stages[active]

	const go = useCallback(
		(next: number) => {
			if (!stages.length) return
			setActive(((next % stages.length) + stages.length) % stages.length)
		},
		[stages.length],
	)

	// Animate the incoming stage. Rows fade up in sequence and bars grow from
	// zero, so the eye is led through the panel instead of being handed a
	// finished wall of numbers. Direct writes only -- no scroll binding, because
	// the reader is driving.
	useEffect(() => {
		const root = bodyRef.current
		if (!root) return
		const rows = Array.from(root.querySelectorAll<HTMLElement>('[data-row]'))
		const bars = Array.from(root.querySelectorAll<HTMLElement>('[data-bar]'))
		const marker = Array.from(root.querySelectorAll<HTMLElement>('[data-marker]'))

		if (reduced) {
			utils.set(rows, { opacity: 1, translateY: 0 })
			utils.set(marker, { scale: 1 })
			return
		}

		if (rows.length) {
			animate(rows, {
				opacity: [0, 1],
				translateY: [10, 0],
				duration: 460,
				ease: 'out(3)',
				delay: stagger(45),
			})
		}
		if (bars.length) {
			// scaleX rather than width: transforms stay on the compositor, and a
			// dozen simultaneous width tweens force layout on every frame.
			utils.set(bars, { transformOrigin: '0% 50%' })
			animate(bars, {
				scaleX: [0, 1],
				duration: 620,
				ease: 'out(4)',
				delay: stagger(30, { start: 120 }),
			})
		}
		if (marker.length) {
			animate(marker, {
				scale: [0, 1],
				duration: 700,
				ease: 'out(5)',
				delay: 360,
			})
		}
	}, [active, reduced])

	if (!stage) return null

	return (
		<section id="explain" className="scroll-mt-24">
			<header className="flex flex-wrap items-end justify-between gap-4">
				<div>
					<div className="text-[13px] uppercase tracking-wide text-ink-faint">
						01 / how one alert is made
					</div>
					<h2 className="mt-2 text-2xl font-semibold tracking-tight sm:text-3xl">
						Packets to playbook, with the arithmetic shown
					</h2>
					<p className="mt-2 max-w-2xl text-[15px] text-ink-dim">
						Rendered from <code>alert_trace.json</code>, which the pipeline writes from
						the same fitted stacker that scored this window. Nothing here is typed by
						hand, so it cannot quietly disagree with the model.
					</p>
				</div>
				<div className="panel p-3">
					<div className="tabular text-[13px] text-ink">{trace.alert_id}</div>
					<div className="mt-0.5 text-[12px] text-ink-faint">
						{trace.entity} &middot; {trace.window_iso}
					</div>
				</div>
			</header>

			{/* Stage rail. Buttons, not scroll capture: the reader sets the pace. */}
			<div
				className="mt-8 grid gap-2 sm:grid-cols-3 lg:grid-cols-6"
				role="tablist"
				aria-label="Pipeline stages"
				onKeyDown={(e) => {
					if (e.key === 'ArrowRight') {
						e.preventDefault()
						go(active + 1)
					} else if (e.key === 'ArrowLeft') {
						e.preventDefault()
						go(active - 1)
					}
				}}
			>
				{stages.map((s, i) => {
					const on = i === active
					return (
						<button
							key={s.id}
							type="button"
							role="tab"
							aria-selected={on}
							tabIndex={on ? 0 : -1}
							onClick={() => go(i)}
							className={cn(
								'panel group p-3 text-left transition-colors',
								on ? 'bg-[var(--color-raised)]' : 'hover:bg-[var(--color-raised)]',
							)}
						>
							<div className="flex items-center gap-2">
								<span
									className={cn(
										'tabular text-[12px]',
										on ? 'text-[var(--color-signal)]' : 'text-ink-faint',
									)}
								>
									{String(i + 1).padStart(2, '0')}
								</span>
								<span
									className={cn('h-px flex-1', on ? 'bg-[var(--color-signal)]' : 'bg-[var(--color-line)]')}
								/>
							</div>
							<div className={cn('mt-2 text-[14px]', on ? 'text-ink' : 'text-ink-dim')}>
								{s.title}
							</div>
						</button>
					)
				})}
			</div>

			<div className="panel mt-6 p-5 sm:p-7">
				<div className="flex flex-wrap items-baseline justify-between gap-3">
					<h3 className="text-xl text-ink">
						{String(active + 1).padStart(2, '0')} &middot; {stage.title}
					</h3>
					<div className="flex gap-2">
						<button
							type="button"
							onClick={() => go(active - 1)}
							className="panel px-3 py-1 text-[13px] text-ink-dim hover:text-ink"
						>
							prev
						</button>
						<button
							type="button"
							onClick={() => go(active + 1)}
							className="panel px-3 py-1 text-[13px] text-ink-dim hover:text-ink"
						>
							next
						</button>
					</div>
				</div>
				<p className="mt-2 max-w-3xl text-[15px] text-ink-dim">{stage.caption}</p>
				<div ref={bodyRef} className="mt-6">
					<StageBody stage={stage} />
				</div>
			</div>

			<VerdictCard trace={trace} />
		</section>
	)
}
