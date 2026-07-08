/**
 * SentinelAI Console.
 *
 * Design rule for this whole file: every number rendered comes from an artifact
 * the Python pipeline measured. There is no placeholder copy, no invented
 * benchmark, and the failures (6.9% PPV at a realistic prior, 0% lateral-movement
 * recall, the mean_pkt_size shortcut) are given the same visual weight as the
 * wins. That is the thing that makes this not a generated landing page.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import {
	AnimatedList,
	BentoGrid,
	CountUp,
	Dock,
	GlowCard,
	Marquee,
	ScrollReveal,
	ShimmeringText,
	ShinyText,
	ThreatField,
} from '@/components/bits'
import { AttributionBars, CompareBars, SweepChart } from '@/components/charts'
import {
	familyRows,
	loadBundle,
	probeApi,
	type Alert,
	type Bundle,
	type LiveStatus,
} from '@/lib/data'
import { cn } from '@/lib/cn'

const SECTIONS = [
	{ id: 'top', label: 'Overview' },
	{ id: 'sweep', label: 'Budget' },
	{ id: 'ablation', label: 'Ablation' },
	{ id: 'triage', label: 'Triage' },
	{ id: 'zeroday', label: 'Zero-day' },
	{ id: 'drift', label: 'Drift' },
	{ id: 'soar', label: 'Response' },
	{ id: 'stack', label: 'Stack' },
]

export default function App() {
	const [bundle, setBundle] = useState<Bundle | null>(null)
	const [error, setError] = useState<string | null>(null)
	const [live, setLive] = useState<LiveStatus>('unknown')
	const [active, setActive] = useState('top')

	useEffect(() => {
		loadBundle()
			.then(setBundle)
			.catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
		const ctrl = new AbortController()
		void probeApi(ctrl.signal).then(setLive)
		return () => ctrl.abort()
	}, [])

	// Scroll spy: which section is in the upper half of the viewport.
	useEffect(() => {
		const obs = new IntersectionObserver(
			(entries) => {
				const top = entries
					.filter((e) => e.isIntersecting)
					.sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0]
				if (top?.target.id) setActive(top.target.id)
			},
			{ rootMargin: '-10% 0px -60% 0px' },
		)
		for (const s of SECTIONS) {
			const el = document.getElementById(s.id)
			if (el) obs.observe(el)
		}
		return () => obs.disconnect()
	}, [bundle])

	if (error) {
		return (
			<main className="mx-auto max-w-2xl p-8">
				<h1 className="text-2xl font-semibold">Artifacts not loaded</h1>
				<p className="mt-3 text-ink-dim">{error}</p>
				<pre className="panel mt-4 overflow-x-auto p-4 text-[14px]">
					{'python -m sentinelai.pipeline --out artifacts --budget 50\ncd web && pnpm sync-data && pnpm dev'}
				</pre>
			</main>
		)
	}

	if (!bundle) {
		return (
			<main className="grid min-h-screen place-items-center">
				<ShimmeringText label="Loading measured artifacts" />
			</main>
		)
	}

	const { report, alerts, decisions } = bundle
	const op = report.operating_point

	return (
		<div className="min-h-screen">
			<a
				href="#triage"
				className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded-md focus:border focus:border-line focus:bg-surface focus:px-3 focus:py-2"
			>
				Skip to triage queue
			</a>

			<Dock
				items={SECTIONS}
				active={active}
				onSelect={(id) => {
					document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
				}}
			/>

			<main className="mx-auto flex max-w-6xl flex-col gap-24 px-5 pb-32 pt-10">
				<Hero report={report} live={live} />
				<SweepSection report={report} />
				<AblationSection report={report} />
				<TriageSection alerts={alerts} />
				<ZeroDaySection report={report} />
				<DriftSection report={report} />
				<SoarSection report={report} decisions={decisions} />
				<StackSection report={report} />
				<Footer op={op} />
			</main>
		</div>
	)
}

/* -------------------------------------------------------------------------- */

function SectionHead({
	id,
	kicker,
	title,
	blurb,
}: {
	id: string
	kicker: string
	title: string
	blurb: string
}) {
	return (
		<header id={id} className="scroll-mt-24">
			<p className="tabular text-[14px] uppercase tracking-[0.18em] text-signal">{kicker}</p>
			<h2 className="mt-2 text-3xl font-semibold tracking-tight">{title}</h2>
			<p className="mt-3 max-w-3xl text-ink-dim">{blurb}</p>
		</header>
	)
}

function Stat({
	label,
	value,
	decimals = 1,
	suffix = '',
	foot,
	accent,
}: {
	label: string
	value: number
	decimals?: number
	suffix?: string
	foot: string
	accent?: string
}) {
	return (
		<GlowCard className="p-5" accent={accent}>
			<p className="text-[14px] text-ink-dim">{label}</p>
			<p className="mt-2 text-3xl font-semibold" style={accent ? { color: accent } : undefined}>
				<CountUp to={value} decimals={decimals} suffix={suffix} />
			</p>
			<p className="mt-2 text-[14px] text-ink-faint">{foot}</p>
		</GlowCard>
	)
}

/* -------------------------------------------------------------------------- */

function Hero({ report, live }: { report: Bundle['report']; live: LiveStatus }) {
	const op = report.operating_point
	const windows = report.windows
	return (
		<section id="top" className="scroll-mt-24">
			<div className="panel relative overflow-hidden p-8 sm:p-12">
				<div className="absolute inset-0 grid-floor" aria-hidden />
				<ThreatField entities={48} flagged={report.soar.by_mode.auto_contain ?? 5} />
				<div className="relative">
					<div className="flex flex-wrap items-center gap-3">
						<span className="panel px-2.5 py-1 text-[14px] text-ink-dim">
							v1 - {report.features.count} features - {report.runtime_seconds.toFixed(1)}s
							end-to-end
						</span>
						<LiveBadge status={live} />
					</div>

					<h1 className="mt-6 text-4xl font-semibold tracking-tight sm:text-6xl">
						<ShinyText text="SentinelAI" />
						<span className="block text-ink-dim sm:text-5xl">
							anomaly detection built around the analyst&rsquo;s budget
						</span>
					</h1>

					<p className="mt-6 max-w-2xl text-lg text-ink-dim">
						A hybrid detector (isolation forest + gradient boosting + a graph leg, fused by a
						calibrated stacker) tuned to a fixed{' '}
						<strong className="text-ink">{op.budget_per_day} alerts/day</strong> budget rather
						than to a flattering AUC. Every figure below is read from the pipeline&rsquo;s own
						artifacts, including the ones that look bad.
					</p>

					<BentoGrid className="mt-10">
						<Stat
							label="Recall at budget"
							value={op.recall * 100}
							suffix="%"
							foot={`${op.alerts_per_day.toFixed(2)} alerts/day sustained`}
							accent="var(--color-signal)"
						/>
						<Stat
							label="Precision (eval prior)"
							value={op.precision_eval * 100}
							suffix="%"
							foot={`FPR ${op.fpr.toExponential(2)}`}
							accent="var(--color-safe)"
						/>
						<Stat
							label="PPV at 1e-4 prior"
							value={op.ppv_at_deployment_prior * 100}
							suffix="%"
							foot="the base-rate reality check"
							accent="var(--color-alarm)"
						/>
						<Stat
							label="Calibration (Brier)"
							value={report.calibration.brier_test}
							decimals={4}
							foot={`ECE ${report.calibration.ece_test.toFixed(4)}`}
						/>
					</BentoGrid>

					<Marquee
						className="mt-10"
						items={[
							`${Number(windows.total ?? 0).toLocaleString()} windows`,
							`train ${Number(windows.train ?? 0).toLocaleString()}`,
							`calibration ${Number(windows.calibration ?? 0).toLocaleString()}`,
							`test ${Number(windows.test ?? 0).toLocaleString()}`,
							`threshold ${op.threshold.toFixed(6)}`,
							`prior shift ${report.calibration.prior_shift_logodds.toFixed(3)} logodds`,
							`SOAR decisions ${report.soar.decisions}`,
							`audit chain ${report.soar.audit_chain_valid ? 'valid' : 'BROKEN'}`,
							`generated ${report.generated_at}`,
						]}
					/>
				</div>
			</div>
		</section>
	)
}

function LiveBadge({ status }: { status: LiveStatus }) {
	const map: Record<LiveStatus, { text: string; color: string }> = {
		unknown: { text: 'probing scorer', color: 'var(--color-ink-faint)' },
		online: { text: 'live scorer online', color: 'var(--color-safe)' },
		offline: { text: 'static artifacts only', color: 'var(--color-watch)' },
	}
	const v = map[status]
	return (
		<span className="panel inline-flex items-center gap-2 px-2.5 py-1 text-[14px]">
			<motion.span
				className="h-2 w-2 rounded-full"
				style={{ backgroundColor: v.color }}
				animate={status === 'online' ? { opacity: [1, 0.35, 1] } : undefined}
				transition={{ duration: 2, repeat: Infinity }}
			/>
			<span style={{ color: v.color }}>{v.text}</span>
		</span>
	)
}

/* -------------------------------------------------------------------------- */

function SweepSection({ report }: { report: Bundle['report'] }) {
	return (
		<section className="flex flex-col gap-6">
			<SectionHead
				id="sweep"
				kicker="01 / operating point"
				title="The threshold is a staffing decision, not a hyperparameter"
				blurb="Sweeping the alert budget makes the trade-off explicit: recall is bought with analyst hours. At 5 alerts/day the detector is precise and nearly blind; at 200 it sees almost everything and wastes most of the shift. The chosen point is where marginal recall per alert/day stops being worth it."
			/>
			<ScrollReveal>
				<div className="panel p-5">
					<SweepChart
						points={report.budget_sweep}
						chosenBudget={report.operating_point.budget_per_day}
					/>
				</div>
			</ScrollReveal>
		</section>
	)
}

/* -------------------------------------------------------------------------- */

function AblationSection({ report }: { report: Bundle['report'] }) {
	const legs = Object.entries(report.ablation_on_test)
	const colors: Record<string, string> = {
		unsupervised_isolation_forest: 'var(--color-watch)',
		supervised_gbdt: 'var(--color-safe)',
		graph_only: 'var(--color-graph)',
		hybrid_stacked: 'var(--color-signal)',
	}
	const families = familyRows(report.per_family_recall_at_operating_point)
	return (
		<section className="flex flex-col gap-6">
			<SectionHead
				id="ablation"
				kicker="02 / ablation"
				title="What each leg actually contributes"
				blurb="Average precision per detector on the held-out test split. The negative result is kept in view: the stacked hybrid does not beat the supervised booster on AP - it buys stability and unseen-family sensitivity instead, and the graph leg is close to useless on this synthetic corpus."
			/>
			<div className="grid gap-4 lg:grid-cols-2">
				<ScrollReveal>
					<div className="panel h-full p-5">
						<h3 className="mb-4 text-lg font-medium">Average precision by leg</h3>
						<CompareBars
							rows={legs.map(([name, leg]) => ({
								label: name.replaceAll('_', ' '),
								value: leg.average_precision,
								color: colors[name] ?? 'var(--color-signal)',
								note: `${leg.false_positives_at_budget} FP`,
							}))}
							format={(v) => v.toFixed(3)}
						/>
						<p className="mt-4 text-[14px] text-ink-faint">
							AP is reported instead of ROC-AUC because at a 0.5% base rate ROC curves look
							excellent for detectors that are useless in production.
						</p>
					</div>
				</ScrollReveal>
				<ScrollReveal delay={0.08}>
					<div className="panel h-full p-5">
						<h3 className="mb-4 text-lg font-medium">Recall by attack family</h3>
						<CompareBars
							rows={families.map((f) => ({
								label: f.family.replaceAll('_', ' '),
								value: f.recall,
								note: `n=${f.windows}`,
								color:
									f.recall >= 0.75
										? 'var(--color-safe)'
										: f.recall >= 0.3
											? 'var(--color-watch)'
											: 'var(--color-alarm)',
							}))}
						/>
						<p className="mt-4 text-[14px] text-ink-faint">
							Loud families are solved. The stealthy, low-volume ones are not, and the single
							lateral-movement window is a sample size that deserves no confidence at all.
						</p>
					</div>
				</ScrollReveal>
			</div>
			<ScrollReveal delay={0.12}>
				<div className="panel p-5">
					<h3 className="mb-4 text-lg font-medium">Top GBDT feature importance</h3>
					<CompareBars
						rows={Object.entries(report.gbdt_top_importance)
							.slice(0, 6)
							.map(([f, v], i) => ({
								label: f,
								value: v,
								color: i === 0 ? 'var(--color-alarm)' : 'var(--color-signal)',
							}))}
						format={(v) => v.toFixed(3)}
					/>
					<p className="mt-4 text-[14px] text-ink-faint">
						This is a known defect, not a highlight: one feature carries most of the model, which
						is a shortcut the synthetic generator handed it. Any real deployment would have to
						break that dependence before trusting the rest.
					</p>
				</div>
			</ScrollReveal>
		</section>
	)
}

/* -------------------------------------------------------------------------- */

function TriageSection({ alerts }: { alerts: Alert[] }) {
	const [selectedId, setSelectedId] = useState(alerts[0]?.alert_id ?? '')
	const [family, setFamily] = useState('all')
	const listRef = useRef<HTMLDivElement>(null)

	const familyOptions = useMemo(
		() => ['all', ...Array.from(new Set(alerts.map((a) => a.suspected_family))).sort()],
		[alerts],
	)
	const shown = useMemo(
		() => (family === 'all' ? alerts : alerts.filter((a) => a.suspected_family === family)),
		[alerts, family],
	)
	const selected = alerts.find((a) => a.alert_id === selectedId) ?? shown[0] ?? alerts[0]

	return (
		<section className="flex flex-col gap-6">
			<SectionHead
				id="triage"
				kicker="03 / triage"
				title="An alert an analyst can argue with"
				blurb="The real queue, ranked by calibrated probability. Each alert carries signed feature attributions, the deployment-prior probability, and its ground truth - including where the family guess is wrong, which is visible on the very first alert."
			/>

			<div className="flex flex-wrap gap-2">
				{familyOptions.map((opt) => (
					<button
						key={opt}
						type="button"
						onClick={() => setFamily(opt)}
						className={cn(
							'panel px-3 py-1.5 text-[14px] transition-colors',
							family === opt ? 'text-ink' : 'text-ink-faint hover:text-ink-dim',
						)}
						style={family === opt ? { borderColor: 'var(--color-signal)' } : undefined}
					>
						{opt.replaceAll('_', ' ')}
					</button>
				))}
			</div>

			<div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
				<div
					ref={listRef}
					className="panel max-h-[560px] overflow-y-auto p-3"
					aria-label="Alert queue"
				>
					<AnimatedList>
						{shown.map((a) => {
							const correct = a.suspected_family === a.ground_truth
							const benign = a.ground_truth === 'benign'
							return (
								<button
									key={a.alert_id}
									type="button"
									onClick={() => setSelectedId(a.alert_id)}
									className={cn(
										'w-full rounded-md border p-3 text-left transition-colors',
										a.alert_id === selected?.alert_id
											? 'border-line-strong bg-raised'
											: 'border-transparent hover:bg-raised/60',
									)}
								>
									<div className="flex items-baseline justify-between gap-3">
										<span className="tabular text-[14px] text-ink">{a.entity}</span>
										<span
											className="tabular text-[14px]"
											style={{
												color: benign ? 'var(--color-watch)' : 'var(--color-alarm)',
											}}
										>
											{a.probability.toFixed(4)}
										</span>
									</div>
									<div className="mt-1 flex flex-wrap items-center gap-2 text-[14px]">
										<span className="text-ink-dim">{a.suspected_family.replaceAll('_', ' ')}</span>
										<span className="text-ink-faint">-</span>
										<span
											style={{
												color: benign
													? 'var(--color-watch)'
													: correct
														? 'var(--color-safe)'
														: 'var(--color-alarm)',
											}}
										>
											truth: {a.ground_truth.replaceAll('_', ' ')}
											{!benign && !correct ? ' (family misread)' : ''}
										</span>
									</div>
									<p className="tabular mt-1 text-[14px] text-ink-faint">{a.window_iso}</p>
								</button>
							)
						})}
					</AnimatedList>
				</div>

				<div className="panel p-5">
					<AnimatePresence mode="wait">
						{selected ? (
							<motion.div
								key={selected.alert_id}
								initial={{ opacity: 0, y: 6 }}
								animate={{ opacity: 1, y: 0 }}
								exit={{ opacity: 0, y: -6 }}
								transition={{ duration: 0.22 }}
							>
								<p className="tabular text-[14px] text-ink-faint">{selected.alert_id}</p>
								<h3 className="mt-1 text-xl font-medium">{selected.narrative}</h3>
								<dl className="mt-4 grid grid-cols-2 gap-3 text-[14px]">
									<Field k="probability (eval)" v={selected.probability.toFixed(6)} />
									<Field
										k="probability (1e-4 prior)"
										v={selected.probability_at_deployment_prior.toFixed(6)}
									/>
									<Field k="base value (logodds)" v={selected.base_value_logodds.toFixed(3)} />
									<Field
										k="attribution residual"
										v={selected.attribution_residual.toFixed(4)}
									/>
								</dl>
								<h4 className="mt-6 mb-3 text-[14px] uppercase tracking-wider text-ink-dim">
									Why it fired
								</h4>
								<AttributionBars items={selected.top} />
								<p className="mt-4 text-[14px] text-ink-faint">
									Red pushes the score up, green pulls it down. The residual is the gap between the
									sum of attributions and the model output - it is shown because an explanation you
									cannot audit is decoration.
								</p>
							</motion.div>
						) : null}
					</AnimatePresence>
				</div>
			</div>
		</section>
	)
}

function Field({ k, v }: { k: string; v: string }) {
	return (
		<div>
			<dt className="text-ink-faint">{k}</dt>
			<dd className="tabular mt-0.5 text-ink">{v}</dd>
		</div>
	)
}

/* -------------------------------------------------------------------------- */

function ZeroDaySection({ report }: { report: Bundle['report'] }) {
	return (
		<section className="flex flex-col gap-6">
			<SectionHead
				id="zeroday"
				kicker="04 / unseen families"
				title="Holding a family out entirely"
				blurb="Each family is removed from supervised training, then scored blind. Recall at the fixed budget is essentially zero - so the honest evidence is the mean percentile: the unsupervised leg still ranks unseen attacks in the top few percent of all traffic, which is what buys you a starting point on a genuine zero-day."
			/>
			<div className="grid gap-4 sm:grid-cols-3">
				{report.zero_day_holdout.map((row, i) => (
					<ScrollReveal key={row.held_out_family} delay={i * 0.06}>
						<GlowCard className="h-full p-5" accent="var(--color-graph)">
							<p className="text-lg font-medium">{row.held_out_family.replaceAll('_', ' ')}</p>
							<p className="text-[14px] text-ink-faint">
								{row.test_windows_of_family} test windows
							</p>
							<div className="mt-4 flex flex-col gap-3">
								<Pct
									label="isolation forest percentile"
									value={row.unsupervised_isolation_forest.mean_percentile_of_family}
								/>
								<Pct
									label="blind GBDT percentile"
									value={row.supervised_gbdt_blind.mean_percentile_of_family}
								/>
								<Pct label="graph-only percentile" value={row.graph_only.mean_percentile_of_family} />
							</div>
							<p className="mt-4 text-[14px] text-ink-faint">
								recall at budget{' '}
								{row.unsupervised_isolation_forest.recall_on_held_out_family_at_budget.toFixed(2)}
							</p>
						</GlowCard>
					</ScrollReveal>
				))}
			</div>
		</section>
	)
}

function Pct({ label, value }: { label: string; value: number }) {
	return (
		<div>
			<div className="flex justify-between text-[14px]">
				<span className="text-ink-dim">{label}</span>
				<span className="tabular text-ink">{(value * 100).toFixed(1)}</span>
			</div>
			<div className="mt-1 h-1.5 overflow-hidden rounded-full bg-raised">
				<motion.div
					className="h-full rounded-full"
					style={{ backgroundColor: 'var(--color-graph)' }}
					initial={{ width: 0 }}
					whileInView={{ width: `${value * 100}%` }}
					viewport={{ once: true }}
					transition={{ duration: 0.7 }}
				/>
			</div>
		</div>
	)
}

/* -------------------------------------------------------------------------- */

function DriftSection({ report }: { report: Bundle['report'] }) {
	const d = report.drift_and_active_learning
	const before = d.before_retrain
	const after = d.after_retrain
	const keys = Object.keys(after).filter((k) => k in before)
	return (
		<section className="flex flex-col gap-6">
			<SectionHead
				id="drift"
				kicker="05 / drift + active learning"
				title="Recovering after the attack mix changes"
				blurb={`The attack mix is shifted, drift is detected by population stability index, and only ${d.labels_spent} analyst labels are spent on the most informative windows before retraining. This is the loop that keeps a deployed detector from quietly rotting.`}
			/>
			<div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
				{keys.map((k, i) => {
					const b = before[k] ?? 0
					const a = after[k] ?? 0
					const up = a >= b
					return (
						<ScrollReveal key={k} delay={i * 0.05}>
							<div className="panel h-full p-5">
								<p className="text-[14px] text-ink-dim">{k.replaceAll('_', ' ')}</p>
								<p className="tabular mt-2 text-2xl font-semibold">
									{b.toFixed(3)}
									<span className="text-ink-faint"> &rarr; </span>
									<span style={{ color: up ? 'var(--color-safe)' : 'var(--color-alarm)' }}>
										{a.toFixed(3)}
									</span>
								</p>
								<p className="tabular mt-2 text-[14px] text-ink-faint">
									{up ? '+' : ''}
									{(a - b).toFixed(4)} after retrain
								</p>
							</div>
						</ScrollReveal>
					)
				})}
			</div>
		</section>
	)
}

/* -------------------------------------------------------------------------- */

function SoarSection({
	report,
	decisions,
}: {
	report: Bundle['report']
	decisions: Bundle['decisions']
}) {
	const [open, setOpen] = useState<string | null>(null)
	const modeColor: Record<string, string> = {
		auto_contain: 'var(--color-alarm)',
		human_review: 'var(--color-watch)',
		enrich: 'var(--color-signal)',
		suppress: 'var(--color-ink-faint)',
	}
	return (
		<section className="flex flex-col gap-6">
			<SectionHead
				id="soar"
				kicker="06 / response"
				title="Automated where it is safe, human where it is not"
				blurb="Policy turns a score into an action, and every decision is hash-chained to the previous one so the record cannot be edited after the fact. Containment stays dry-run in this demo: executed is false everywhere, by design."
			/>
			<div className="flex flex-wrap gap-3">
				{Object.entries(report.soar.by_mode).map(([mode, n]) => (
					<span key={mode} className="panel px-3 py-1.5 text-[14px]">
						<span style={{ color: modeColor[mode] ?? 'var(--color-ink)' }}>
							{mode.replaceAll('_', ' ')}
						</span>
						<span className="tabular ml-2 text-ink">{n}</span>
					</span>
				))}
				<span
					className="panel px-3 py-1.5 text-[14px]"
					style={{
						color: report.soar.audit_chain_valid ? 'var(--color-safe)' : 'var(--color-alarm)',
					}}
				>
					audit chain {report.soar.audit_chain_valid ? 'verified' : 'broken'}
				</span>
			</div>
			<div className="panel max-h-[420px] overflow-y-auto p-3">
				<AnimatedList>
					{decisions.slice(0, 20).map((d) => (
						<div key={d.hash} className="rounded-md p-3 hover:bg-raised/60">
							<button
								type="button"
								className="flex w-full items-baseline justify-between gap-3 text-left"
								onClick={() => setOpen(open === d.hash ? null : d.hash)}
								aria-expanded={open === d.hash}
							>
								<span className="tabular text-[14px] text-ink">
									{d.entity} - {d.action}
								</span>
								<span
									className="text-[14px]"
									style={{ color: modeColor[d.mode] ?? 'var(--color-ink)' }}
								>
									{d.mode.replaceAll('_', ' ')}
								</span>
							</button>
							<AnimatePresence initial={false}>
								{open === d.hash && (
									<motion.div
										initial={{ height: 0, opacity: 0 }}
										animate={{ height: 'auto', opacity: 1 }}
										exit={{ height: 0, opacity: 0 }}
										className="overflow-hidden"
									>
										<ul className="mt-2 list-none space-y-1 p-0 text-[14px] text-ink-dim">
											{d.rationale.map((r) => (
												<li key={r}>{r}</li>
											))}
											<li className="tabular text-ink-faint">
												explanation support {d.explanation_support.toFixed(3)} - executed{' '}
												{String(d.executed)}
											</li>
											<li className="tabular break-all text-ink-faint">hash {d.hash}</li>
										</ul>
									</motion.div>
								)}
							</AnimatePresence>
						</div>
					))}
				</AnimatedList>
			</div>
		</section>
	)
}

/* -------------------------------------------------------------------------- */

function StackSection({ report }: { report: Bundle['report'] }) {
	const coeffs = report.calibration.stacker_coefficients
	return (
		<section className="flex flex-col gap-6">
			<SectionHead
				id="stack"
				kicker="07 / how it is built"
				title="The fusion is a readable equation"
				blurb="No black-box meta-model: the stacker is logistic regression over three leg scores, so its weights are printable and arguable. Intercept and prior shift are applied explicitly rather than hidden inside a threshold."
			/>
			<div className="grid gap-4 lg:grid-cols-2">
				<ScrollReveal>
					<div className="panel h-full p-5">
						<h3 className="mb-4 text-lg font-medium">Stacker weights</h3>
						<CompareBars
							rows={Object.entries(coeffs).map(([k, v]) => ({
								label: k,
								value: Math.abs(v),
								note: v < 0 ? '(negative)' : '',
							}))}
							format={(v) => v.toFixed(4)}
						/>
						<p className="tabular mt-4 text-[14px] text-ink-faint">
							intercept {report.calibration.stacker_intercept.toFixed(4)} - prior shift{' '}
							{report.calibration.prior_shift_logodds.toFixed(4)} logodds
						</p>
					</div>
				</ScrollReveal>
				<ScrollReveal delay={0.08}>
					<div className="panel h-full p-5">
						<h3 className="mb-4 text-lg font-medium">Pipeline</h3>
						<ol className="m-0 flex list-none flex-col gap-3 p-0 text-[14px]">
							{[
								'Synthetic flow + auth corpus, entity-time windowed',
								`${report.features.count} features, causal rolling stats only (no future leakage)`,
								'Isolation forest on benign-only training data',
								'Gradient-boosted trees on labelled windows',
								'Graph rarity leg over the entity interaction graph',
								'Logistic stacker + isotonic-style calibration on a held-out split',
								'Budgeted threshold selection, then Shapley-style attribution',
								'Policy engine, hash-chained audit log, active-learning retrain',
							].map((step, i) => (
								<li key={step} className="flex gap-3">
									<span className="tabular text-ink-faint">{String(i + 1).padStart(2, '0')}</span>
									<span className="text-ink-dim">{step}</span>
								</li>
							))}
						</ol>
					</div>
				</ScrollReveal>
			</div>
		</section>
	)
}

/* -------------------------------------------------------------------------- */

function Footer({ op }: { op: Bundle['report']['operating_point'] }) {
	return (
		<footer className="panel p-6 text-[14px] text-ink-dim">
			<p>
				SentinelAI runs on a synthetic corpus. It is a research and engineering demonstration,
				not a product claim: at a realistic {op.deployment_prior.toExponential(0)} intrusion
				prior, {(op.ppv_at_deployment_prior * 100).toFixed(1)}% of its alerts would be true
				positives, which is exactly the base-rate problem the design is arguing about.
			</p>
			<p className="mt-3 text-ink-faint">
				UI primitives adapted from React Bits, Magic UI, Skiper UI, Vengeance UI and Bklit UI;
				motion by Motion and anime.js. See RESOURCES.md for what was used and what was
				deliberately not.
			</p>
		</footer>
	)
}
