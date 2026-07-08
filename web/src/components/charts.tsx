/**
 * Charts.
 *
 * Built as hand-rolled SVG in the Bklit UI idiom (bklit.com) rather than pulling
 * a charting dependency: every mark here is driven by a measured artifact value,
 * and the axes are honest (log-scaled budget axis, 0-1 rate axis with no clipped
 * baseline to exaggerate movement).
 *
 * Motion (motion.dev) draws the paths once on entry via pathLength, which is the
 * one animation that genuinely aids comprehension - you watch the trade-off
 * curve accumulate rather than reading a static line.
 */
import { motion, useReducedMotion } from 'motion/react'
import { useId, useMemo, useState } from 'react'
import type { SweepPoint } from '@/lib/data'

type Box = { w: number; h: number; pad: { t: number; r: number; b: number; l: number } }

const BOX: Box = { w: 720, h: 300, pad: { t: 18, r: 18, b: 40, l: 46 } }

function scaleLog(value: number, min: number, max: number, from: number, to: number) {
	const lv = Math.log10(Math.max(value, min))
	const lmin = Math.log10(min)
	const lmax = Math.log10(max)
	return from + ((lv - lmin) / (lmax - lmin)) * (to - from)
}

/* -------------------------------------------------------------------------- */
/* Budget sweep: the headline chart. Recall and precision against alert budget. */
/* -------------------------------------------------------------------------- */

export function SweepChart({
	points,
	chosenBudget,
}: {
	points: SweepPoint[]
	chosenBudget: number
}) {
	const reduced = useReducedMotion()
	const gid = useId()
	const [hover, setHover] = useState<number | null>(null)

	const { w, h, pad } = BOX
	const x0 = pad.l
	const x1 = w - pad.r
	const y0 = h - pad.b
	const y1 = pad.t

	const sorted = useMemo(
		() => [...points].sort((a, b) => a.budget_per_day - b.budget_per_day),
		[points],
	)
	const minB = sorted[0]?.budget_per_day ?? 5
	const maxB = sorted[sorted.length - 1]?.budget_per_day ?? 200

	const px = (b: number) => scaleLog(b, minB, maxB, x0, x1)
	const py = (r: number) => y0 - r * (y0 - y1)

	const path = (key: 'recall' | 'precision_eval') =>
		sorted
			.map((p, i) => `${i === 0 ? 'M' : 'L'}${px(p.budget_per_day).toFixed(1)},${py(p[key]).toFixed(1)}`)
			.join(' ')

	const active = hover === null ? null : sorted[hover]

	return (
		<figure className="m-0">
			<svg
				viewBox={`0 0 ${w} ${h}`}
				role="img"
				aria-label="Recall and precision as a function of the daily alert budget, log scale"
				className="w-full"
				onPointerLeave={() => setHover(null)}
			>
				{/* horizontal gridlines at 0/25/50/75/100% */}
				{[0, 0.25, 0.5, 0.75, 1].map((r) => (
					<g key={r}>
						<line
							x1={x0}
							x2={x1}
							y1={py(r)}
							y2={py(r)}
							stroke="var(--color-line)"
							strokeWidth={1}
						/>
						<text
							x={x0 - 8}
							y={py(r) + 4}
							textAnchor="end"
							fontSize={12}
							fill="var(--color-ink-faint)"
							className="tabular"
						>
							{Math.round(r * 100)}%
						</text>
					</g>
				))}

				{/* chosen operating point */}
				<line
					x1={px(chosenBudget)}
					x2={px(chosenBudget)}
					y1={y1}
					y2={y0}
					stroke="var(--color-watch)"
					strokeWidth={1.5}
					strokeDasharray="4 4"
				/>
				<text
					x={px(chosenBudget) + 6}
					y={y1 + 12}
					fontSize={12}
					fill="var(--color-watch)"
				>
					chosen {chosenBudget}/day
				</text>

				{/* series */}
				{(
					[
						{ key: 'recall' as const, color: 'var(--color-signal)' },
						{ key: 'precision_eval' as const, color: 'var(--color-safe)' },
					]
				).map((series) => (
					<motion.path
						key={series.key}
						d={path(series.key)}
						fill="none"
						stroke={series.color}
						strokeWidth={2}
						strokeLinecap="round"
						initial={reduced ? undefined : { pathLength: 0 }}
						whileInView={reduced ? undefined : { pathLength: 1 }}
						viewport={{ once: true }}
						transition={{ duration: 1.1, ease: 'easeOut' }}
					/>
				))}

				{/* x ticks + hover targets */}
				{sorted.map((p, i) => (
					<g key={p.budget_per_day}>
						<text
							x={px(p.budget_per_day)}
							y={y0 + 18}
							textAnchor="middle"
							fontSize={11}
							fill="var(--color-ink-faint)"
							className="tabular"
						>
							{p.budget_per_day}
						</text>
						<circle
							cx={px(p.budget_per_day)}
							cy={py(p.recall)}
							r={hover === i ? 4 : 2.5}
							fill="var(--color-signal)"
						/>
						<circle
							cx={px(p.budget_per_day)}
							cy={py(p.precision_eval)}
							r={hover === i ? 4 : 2.5}
							fill="var(--color-safe)"
						/>
						<rect
							x={px(p.budget_per_day) - 14}
							y={y1}
							width={28}
							height={y0 - y1}
							fill="transparent"
							onPointerEnter={() => setHover(i)}
						/>
					</g>
				))}

				<text
					x={(x0 + x1) / 2}
					y={h - 6}
					textAnchor="middle"
					fontSize={12}
					fill="var(--color-ink-dim)"
				>
					alerts per day (log scale) - id {gid.slice(0, 0)}
				</text>
			</svg>

			<figcaption className="mt-2 flex flex-wrap items-center gap-4 text-[14px] text-ink-dim">
				<Legend color="var(--color-signal)" label="recall" />
				<Legend color="var(--color-safe)" label="precision" />
				{active ? (
					<span className="tabular text-ink">
						{active.budget_per_day}/day - recall {(active.recall * 100).toFixed(1)}% -
						precision {(active.precision_eval * 100).toFixed(1)}% - PPV@1e-4{' '}
						{active.ppv_at_deployment_prior.toFixed(3)}
					</span>
				) : (
					<span className="text-ink-faint">hover a budget to read the exact trade-off</span>
				)}
			</figcaption>
		</figure>
	)
}

function Legend({ color, label }: { color: string; label: string }) {
	return (
		<span className="inline-flex items-center gap-2">
			<span className="h-2 w-4 rounded-full" style={{ backgroundColor: color }} />
			{label}
		</span>
	)
}

/* -------------------------------------------------------------------------- */
/* Attribution bars: signed Shapley contributions for one alert.               */
/* -------------------------------------------------------------------------- */

export function AttributionBars({
	items,
}: {
	items: Array<{ feature: string; contribution: number; value: number }>
}) {
	const max = Math.max(...items.map((i) => Math.abs(i.contribution)), 0.001)
	const reduced = useReducedMotion()
	return (
		<ul className="m-0 flex list-none flex-col gap-2 p-0">
			{items.map((item) => {
				const pct = (Math.abs(item.contribution) / max) * 100
				const up = item.contribution >= 0
				return (
					<li key={item.feature} className="grid grid-cols-[1fr_auto] gap-x-3">
						<div className="min-w-0">
							<div className="flex items-baseline justify-between gap-2">
								<span className="truncate text-[14px] text-ink">{item.feature}</span>
								<span className="tabular text-[14px] text-ink-faint">
									{item.value.toFixed(2)}
								</span>
							</div>
							<div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-raised">
								<motion.div
									className="h-full rounded-full"
									style={{
										backgroundColor: up ? 'var(--color-alarm)' : 'var(--color-safe)',
									}}
									initial={reduced ? { width: `${pct}%` } : { width: 0 }}
									whileInView={{ width: `${pct}%` }}
									viewport={{ once: true }}
									transition={{ duration: 0.6, ease: 'easeOut' }}
								/>
							</div>
						</div>
						<span
							className="tabular self-center text-[14px]"
							style={{ color: up ? 'var(--color-alarm)' : 'var(--color-safe)' }}
						>
							{up ? '+' : ''}
							{item.contribution.toFixed(2)}
						</span>
					</li>
				)
			})}
		</ul>
	)
}

/* -------------------------------------------------------------------------- */
/* Horizontal comparison bars, used for ablation and per-family recall.        */
/* -------------------------------------------------------------------------- */

export function CompareBars({
	rows,
	format = (v: number) => `${(v * 100).toFixed(1)}%`,
}: {
	rows: Array<{ label: string; value: number; color?: string; note?: string }>
	format?: (v: number) => string
}) {
	const reduced = useReducedMotion()
	const max = Math.max(...rows.map((r) => r.value), 0.0001)
	return (
		<ul className="m-0 flex list-none flex-col gap-3 p-0">
			{rows.map((row) => (
				<li key={row.label}>
					<div className="flex items-baseline justify-between gap-3">
						<span className="text-[14px] text-ink">{row.label}</span>
						<span className="tabular text-[14px] text-ink-dim">
							{format(row.value)}
							{row.note ? <span className="text-ink-faint"> {row.note}</span> : null}
						</span>
					</div>
					<div className="mt-1 h-2 w-full overflow-hidden rounded-full bg-raised">
						<motion.div
							className="h-full rounded-full"
							style={{ backgroundColor: row.color ?? 'var(--color-signal)' }}
							initial={reduced ? { width: `${(row.value / max) * 100}%` } : { width: 0 }}
							whileInView={{ width: `${(row.value / max) * 100}%` }}
							viewport={{ once: true }}
							transition={{ duration: 0.7, ease: 'easeOut' }}
						/>
					</div>
				</li>
			))}
		</ul>
	)
}
