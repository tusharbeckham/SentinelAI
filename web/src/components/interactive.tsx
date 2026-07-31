/**
 * The two interactive surfaces: entity triage and free-form hunting.
 *
 * Both are driven entirely by measured artifacts. The hunt runs client-side
 * against the shipped index using the exact grammar the CLI implements, so the
 * demo needs no backend and cannot desync from the tool.
 */

import { animate, stagger, utils } from 'animejs'
import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useMemo, useRef, useState } from 'react'
import {
	MagneticButton,
	RiskBar,
	Sparkline,
	SplitText,
	StaggerList,
	TextScramble,
	TiltPanel,
	useReducedMotion,
} from '@/components/anime'
import { cn } from '@/lib/cn'
import { HUNT_EXAMPLES, HuntError, run as runHunt, type HuntRow } from '@/lib/hunt'
import type { EntityRiskBundle, HuntIndex } from '@/lib/data'

function riskColor(risk: number): string {
	if (risk >= 0.99) return 'var(--color-alarm)'
	if (risk >= 0.8) return 'var(--color-watch)'
	if (risk >= 0.3) return 'var(--color-signal)'
	return 'var(--color-ink-faint)'
}

/* -------------------------------------------------------------------------- */

export function EntityRiskSection({ data }: { data: EntityRiskBundle }) {
	const [selected, setSelected] = useState(data.entities[0]?.entity ?? '')
	const [showAll, setShowAll] = useState(false)
	const reduced = useReducedMotion()

	const shown = showAll ? data.entities : data.entities.slice(0, 12)
	const current = data.entities.find((e) => e.entity === selected) ?? data.entities[0]

	// The true-positive ranks tell you whether the ranking is worth trusting.
	const inTopTen = data.ranks_of_true_positive_entities.filter((r) => r <= 10).length
	const totalTrue = data.ranks_of_true_positive_entities.length

	return (
		<section id="entities" className="flex flex-col gap-6 scroll-mt-24">
			<div>
				<p className="text-[13px] tracking-[0.18em] text-ink-faint uppercase">
					<TextScramble text="08 / entity triage" />
				</p>
				<h2 className="mt-2 text-3xl font-semibold tracking-tight">
					<SplitText text="Which host should an analyst open first?" />
				</h2>
				<p className="mt-3 max-w-3xl text-[15px] leading-relaxed text-ink-dim">
					An alert list answers which windows fired. Triage needs a different
					question answered: eight windows at 0.7 on one host deserve the next hour
					more than a single 0.95 elsewhere. Risk is a noisy-or over each entity's
					strongest {data.top_k} windows, which rewards corroboration without
					punishing an entity for being observed often.
				</p>
			</div>

			<div className="panel flex flex-wrap items-center gap-x-8 gap-y-3 p-5">
				<div>
					<p className="tabular text-2xl font-semibold">
						{inTopTen} / {totalTrue}
					</p>
					<p className="text-[13px] text-ink-dim">compromised entities ranked in the top 10</p>
				</div>
				<div>
					<p className="tabular text-2xl font-semibold">{data.entity_count}</p>
					<p className="text-[13px] text-ink-dim">entities ranked</p>
				</div>
				<p className="max-w-md text-[13px] leading-relaxed text-ink-faint">
					Windows inside one episode are correlated, so this is optimistic for a
					sustained attack. It is a ranking, not a probability.
				</p>
			</div>

			<div className="grid gap-4 lg:grid-cols-[1.15fr_1fr]">
				<div className="panel overflow-hidden">
					<StaggerList signature={`${showAll}`} className="divide-y divide-line">
						{shown.map((entity, i) => {
							const active = entity.entity === selected
							const compromised = entity.truth_attack_windows > 0
							return (
								<button
									key={entity.entity}
									data-stagger-row
									type="button"
									onClick={() => setSelected(entity.entity)}
									className={cn(
										'flex w-full items-center gap-4 px-5 py-3 text-left transition-colors',
										active ? 'bg-raised' : 'hover:bg-raised/60',
									)}
									style={{ opacity: reduced ? 1 : 0 }}
								>
									<span className="tabular w-6 text-[13px] text-ink-faint">{i + 1}</span>
									<span className="tabular w-16 text-[15px] font-medium">{entity.entity}</span>
									<span className="flex-1">
										<RiskBar value={entity.risk} index={i} color={riskColor(entity.risk)} />
									</span>
									<span className="tabular w-20 text-right text-[14px]">
										{entity.risk.toFixed(4)}
									</span>
									<span className="tabular w-14 text-right text-[13px] text-ink-dim">
										{entity.alerts} alr
									</span>
									<span
										className="w-16 text-right text-[12px]"
										style={{ color: compromised ? 'var(--color-alarm)' : 'var(--color-safe)' }}
									>
										{compromised ? 'attacked' : 'clean'}
									</span>
								</button>
							)
						})}
					</StaggerList>
					<div className="border-t border-line p-3 text-center">
						<MagneticButton
							onClick={() => setShowAll((v) => !v)}
							className="rounded-md px-4 py-1.5 text-[14px] text-ink-dim hover:text-ink"
						>
							{showAll ? 'Show top 12' : `Show all ${data.entity_count}`}
						</MagneticButton>
					</div>
				</div>

				<AnimatePresence mode="wait">
					{current ? (
						<motion.div
							key={current.entity}
							initial={{ opacity: 0, y: 10 }}
							animate={{ opacity: 1, y: 0 }}
							exit={{ opacity: 0, y: -8 }}
							transition={{ duration: 0.28 }}
						>
							<TiltPanel className="panel h-full p-6">
								<div className="flex items-baseline justify-between">
									<h3 className="tabular text-xl font-semibold">{current.entity}</h3>
									<span
										className="tabular text-[15px]"
										style={{ color: riskColor(current.risk) }}
									>
										risk {current.risk.toFixed(6)}
									</span>
								</div>
								<p className="mt-1 text-[13px] text-ink-faint">
									probability across the test period
								</p>
								<Sparkline
									values={current.timeline}
									stroke={riskColor(current.risk)}
									className="mt-3 h-16 w-full"
								/>
								<dl className="mt-5 grid grid-cols-2 gap-4 text-[14px]">
									{[
										['windows observed', current.windows.toString()],
										['alerts at threshold', current.alerts.toString()],
										['peak probability', current.max_probability.toFixed(4)],
										['mean probability', current.mean_probability.toFixed(6)],
										['true attack windows', current.truth_attack_windows.toString()],
										['families in truth', current.truth_families.join(', ') || 'none'],
									].map(([label, value]) => (
										<div key={label}>
											<dt className="text-ink-faint">{label}</dt>
											<dd className="tabular mt-0.5">{value}</dd>
										</div>
									))}
								</dl>
								{current.truth_attack_windows === 0 && current.alerts > 0 ? (
									<p className="mt-5 rounded-md border border-line px-3 py-2 text-[13px] text-watch">
										This entity is clean in ground truth and still produced
										{' '}
										{current.alerts} alert{current.alerts === 1 ? '' : 's'}. This is what
										the analyst budget is spent on.
									</p>
								) : null}
							</TiltPanel>
						</motion.div>
					) : null}
				</AnimatePresence>
			</div>
		</section>
	)
}

/* -------------------------------------------------------------------------- */

const COLUMNS = ['entity', 'win', 'probability', 'attack'] as const

export function HuntSection({
	index,
	presetQuery,
}: {
	index: HuntIndex
	presetQuery?: string
}) {
	const [query, setQuery] = useState('probability > 0.9 | sort probability desc | limit 10')
	const inputRef = useRef<HTMLInputElement | null>(null)

	useEffect(() => {
		if (presetQuery) setQuery(presetQuery)
	}, [presetQuery])

	const { rows, error } = useMemo(() => {
		try {
			return { rows: runHunt(query, index.rows as HuntRow[], index.fields), error: null }
		} catch (err) {
			// A query that cannot be parsed must show the reason, never quietly
			// fall back to matching everything.
			const message = err instanceof HuntError ? err.message : String(err)
			return { rows: [] as HuntRow[], error: message }
		}
	}, [query, index])

	const hits = rows.slice(0, 40)
	const truePositives = rows.filter((r) => String(r.attack) !== 'benign').length

	return (
		<section id="hunt" className="flex flex-col gap-6 scroll-mt-24">
			<div>
				<p className="text-[13px] tracking-[0.18em] text-ink-faint uppercase">
					<TextScramble text="09 / threat hunting" />
				</p>
				<h2 className="mt-2 text-3xl font-semibold tracking-tight">
					<SplitText text="Ask the data a question directly" />
				</h2>
				<p className="mt-3 max-w-3xl text-[15px] leading-relaxed text-ink-dim">
					Detection finds what the model recognises. Hunting is how an analyst looks
					for what it missed. This grammar is implemented twice, once in Python for
					the CLI and once in TypeScript here, so a query means the same thing in
					both places. Try the query that finds brute force the detector scored
					below threshold.
				</p>
			</div>

			<div className="panel p-5">
				<label htmlFor="hunt-query" className="text-[13px] text-ink-faint">
					query
				</label>
				<input
					id="hunt-query"
					ref={inputRef}
					value={query}
					onChange={(e) => setQuery(e.target.value)}
					spellCheck={false}
					className={cn(
						'tabular mt-2 w-full rounded-md border bg-canvas px-3 py-2.5 text-[14px] outline-none',
						error ? 'border-alarm' : 'border-line focus:border-line-strong',
					)}
				/>
				<div className="mt-3 flex flex-wrap gap-2">
					{HUNT_EXAMPLES.map((example) => (
						<MagneticButton
							key={example.query}
							title={example.query}
							onClick={() => setQuery(example.query)}
							className="rounded-full border border-line px-3 py-1 text-[13px] text-ink-dim hover:border-line-strong hover:text-ink"
						>
							{example.why}
						</MagneticButton>
					))}
				</div>

				<AnimatePresence mode="wait">
					{error ? (
						<motion.p
							key={error}
							initial={{ opacity: 0, y: -4 }}
							animate={{ opacity: 1, y: 0 }}
							exit={{ opacity: 0 }}
							className="mt-3 text-[14px] text-alarm"
						>
							{error}
						</motion.p>
					) : (
						<motion.p
							key={`${rows.length}-ok`}
							initial={{ opacity: 0 }}
							animate={{ opacity: 1 }}
							className="tabular mt-3 text-[14px] text-ink-dim"
						>
							{rows.length} of {index.rows_in_index} indexed windows match &middot;{' '}
							{truePositives} are attacks in ground truth
						</motion.p>
					)}
				</AnimatePresence>
			</div>

			<div className="panel overflow-x-auto">
				<StaggerList signature={`${query}:${rows.length}`}>
					<table className="w-full text-left text-[14px]">
						<thead>
							<tr className="border-b border-line text-[13px] text-ink-faint">
								{COLUMNS.map((c) => (
									<th key={c} className="px-4 py-2.5 font-normal">
										{c}
									</th>
								))}
								<th className="px-4 py-2.5 font-normal">verdict</th>
							</tr>
						</thead>
						<tbody>
							{hits.map((row, i) => {
								const attack = String(row.attack ?? 'benign')
								const fired = Number(row.probability) >= index.threshold
								const benign = attack === 'benign'
								const verdict = benign
									? fired
										? 'false positive'
										: 'true negative'
									: fired
										? 'caught'
										: 'missed'
								const color =
									verdict === 'caught'
										? 'var(--color-safe)'
										: verdict === 'missed'
											? 'var(--color-alarm)'
											: verdict === 'false positive'
												? 'var(--color-watch)'
												: 'var(--color-ink-faint)'
								return (
									<tr
										key={`${row.entity}-${row.win}-${i}`}
										data-stagger-row
										className="border-b border-line/60 last:border-0"
									>
										{COLUMNS.map((c) => {
											const value = row[c]
											return (
												<td key={c} className="tabular px-4 py-2.5">
													{typeof value === 'number' && c === 'probability'
														? value.toFixed(6)
														: String(value)}
												</td>
											)
										})}
										<td className="px-4 py-2.5" style={{ color }}>
											{verdict}
										</td>
									</tr>
								)
							})}
							{hits.length === 0 && !error ? (
								<tr>
									<td colSpan={5} className="px-4 py-6 text-center text-ink-faint">
										No windows match. The index holds {index.rows_in_index} of{' '}
										{index.total_scored_windows} scored windows.
									</td>
								</tr>
							) : null}
						</tbody>
					</table>
				</StaggerList>
			</div>

			<p className="text-[13px] leading-relaxed text-ink-faint">
				{index.sampling}. Run the same grammar in the terminal with{' '}
				<code className="tabular">python -m sentinelai.hunt "your query"</code>.
			</p>
		</section>
	)
}

/* -------------------------------------------------------------------------- */

export type PaletteItem = {
	id: string
	label: string
	hint: string
	run: () => void
}

/** Command palette on Cmd/Ctrl-K. Jumps between sections and loads hunts. */
export function CommandPalette({ items }: { items: PaletteItem[] }) {
	const [open, setOpen] = useState(false)
	const [term, setTerm] = useState('')
	const [cursor, setCursor] = useState(0)
	const panelRef = useRef<HTMLDivElement | null>(null)
	const reduced = useReducedMotion()

	const filtered = useMemo(() => {
		const needle = term.trim().toLowerCase()
		if (!needle) return items
		return items.filter(
			(item) =>
				item.label.toLowerCase().includes(needle) || item.hint.toLowerCase().includes(needle),
		)
	}, [items, term])

	useEffect(() => {
		const onKey = (event: KeyboardEvent) => {
			if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
				event.preventDefault()
				setOpen((v) => !v)
				setTerm('')
				setCursor(0)
				return
			}
			if (event.key === 'Escape') setOpen(false)
		}
		window.addEventListener('keydown', onKey)
		return () => window.removeEventListener('keydown', onKey)
	}, [])

	useEffect(() => {
		const host = panelRef.current
		if (!open || !host || reduced) return
		const rows = utils.$('[data-palette-row]', host)
		if (rows.length === 0) return
		animate(rows, {
			opacity: [0, 1],
			translateY: [8, 0],
			duration: 320,
			delay: stagger(14),
			ease: 'out(3)',
		})
	}, [open, filtered.length, reduced])

	const choose = (item: PaletteItem) => {
		item.run()
		setOpen(false)
	}

	return (
		<>
			<button
				type="button"
				onClick={() => setOpen(true)}
				className="palette-trigger fixed bottom-5 right-5 z-40 rounded-full border border-line bg-surface/90 px-4 py-2 text-[13px] text-ink-dim backdrop-blur hover:text-ink"
			>
				<span className="tabular">&#8984;K</span> commands
			</button>
			<AnimatePresence>
				{open ? (
					<motion.div
						className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 p-4 pt-[14vh] backdrop-blur-sm"
						initial={{ opacity: 0 }}
						animate={{ opacity: 1 }}
						exit={{ opacity: 0 }}
						transition={{ duration: 0.16 }}
						onClick={() => setOpen(false)}
					>
						<motion.div
							ref={panelRef}
							className="panel w-full max-w-xl overflow-hidden"
							initial={{ opacity: 0, y: -12, scale: 0.98 }}
							animate={{ opacity: 1, y: 0, scale: 1 }}
							exit={{ opacity: 0, y: -8, scale: 0.99 }}
							transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
							onClick={(e) => e.stopPropagation()}
						>
							<input
								autoFocus
								value={term}
								placeholder="Jump to a section or load a hunt"
								onChange={(e) => {
									setTerm(e.target.value)
									setCursor(0)
								}}
								onKeyDown={(event) => {
									if (event.key === 'ArrowDown') {
										event.preventDefault()
										setCursor((c) => Math.min(filtered.length - 1, c + 1))
									} else if (event.key === 'ArrowUp') {
										event.preventDefault()
										setCursor((c) => Math.max(0, c - 1))
									} else if (event.key === 'Enter') {
										const item = filtered[cursor]
										if (item) choose(item)
									}
								}}
								className="w-full border-b border-line bg-transparent px-4 py-3 text-[15px] outline-none"
							/>
							<ul className="max-h-[46vh] overflow-y-auto">
								{filtered.map((item, i) => (
									<li key={item.id} data-palette-row>
										<button
											type="button"
											onMouseEnter={() => setCursor(i)}
											onClick={() => choose(item)}
											className={cn(
												'flex w-full items-center justify-between gap-4 px-4 py-2.5 text-left',
												i === cursor ? 'bg-raised' : 'hover:bg-raised/60',
											)}
										>
											<span className="text-[14px]">{item.label}</span>
											<span className="tabular text-[12px] text-ink-faint">{item.hint}</span>
										</button>
									</li>
								))}
								{filtered.length === 0 ? (
									<li className="px-4 py-6 text-center text-[14px] text-ink-faint">
										Nothing matches that.
									</li>
								) : null}
							</ul>
						</motion.div>
					</motion.div>
				) : null}
			</AnimatePresence>
		</>
	)
}
