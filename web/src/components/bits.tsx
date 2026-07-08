/**
 * Animated primitives.
 *
 * ATTRIBUTION AND HONESTY NOTE
 * ---------------------------------------------------------------------------
 * These are adaptations, written from the published patterns of the registries
 * listed below, not verbatim copies pulled from their CLIs (this repo is built
 * offline and vendors its own source so the demo has no registry dependency):
 *
 *   React Bits    (MIT, reactbits.dev)        -> ShinyText, CountUp, ScrambleIn
 *   Magic UI      (MIT, magicui.design)       -> BentoGrid, Marquee, AnimatedList
 *   Skiper UI     (skiper-ui.com)             -> Dock nav interaction model
 *   Vengeance UI  (vengenceui.com)            -> GlowCard pointer spotlight
 *   Bklit UI      (bklit.com)                 -> ShimmeringText loading state
 *   Motion        (MIT, motion.dev)           -> all declarative/spring motion
 *   anime.js v4   (MIT, animejs.com)          -> imperative timeline in ThreatField
 *
 * Everything here obeys prefers-reduced-motion, because a SOC console that
 * cannot be read during an incident is a broken SOC console.
 */
import {
	animate,
	motion,
	useInView,
	useMotionValue,
	useReducedMotion,
	type Variants,
} from 'motion/react'
import { createTimeline, utils } from 'animejs'
import {
	useEffect,
	useMemo,
	useRef,
	useState,
	type CSSProperties,
	type ReactNode,
} from 'react'
import { cn } from '@/lib/cn'

/* -------------------------------------------------------------------------- */
/* ScrollReveal - Motion. The one wrapper every section uses.                  */
/* -------------------------------------------------------------------------- */

const revealVariants: Variants = {
	hidden: { opacity: 0, y: 18 },
	shown: { opacity: 1, y: 0 },
}

export function ScrollReveal({
	children,
	delay = 0,
	className,
}: {
	children: ReactNode
	delay?: number
	className?: string
}) {
	const reduced = useReducedMotion()
	return (
		<motion.div
			className={className}
			initial={reduced ? 'shown' : 'hidden'}
			whileInView="shown"
			viewport={{ once: true, amount: 0.2 }}
			variants={revealVariants}
			transition={{ duration: 0.5, delay, ease: [0.22, 1, 0.36, 1] }}
		>
			{children}
		</motion.div>
	)
}

/* -------------------------------------------------------------------------- */
/* ShinyText - React Bits pattern, used only on the wordmark.                   */
/* -------------------------------------------------------------------------- */

export function ShinyText({
	text,
	className,
}: {
	text: string
	className?: string
}) {
	const reduced = useReducedMotion()
	if (reduced) return <span className={className}>{text}</span>
	return (
		<span
			className={cn('bg-clip-text text-transparent', className)}
			style={{
				backgroundImage:
					'linear-gradient(110deg, var(--color-ink-faint) 35%, var(--color-ink) 50%, var(--color-ink-faint) 65%)',
				backgroundSize: '220% 100%',
				animation: 'shine 4.5s linear infinite',
			}}
		>
			{text}
			<style>{'@keyframes shine{to{background-position:-220% 0}}'}</style>
		</span>
	)
}

/* -------------------------------------------------------------------------- */
/* CountUp - React Bits pattern on Motion's animate(). Metrics count once,      */
/* on entry, and land on the exact measured value (never an eased approximation)*/
/* -------------------------------------------------------------------------- */

export function CountUp({
	to,
	decimals = 0,
	suffix = '',
	prefix = '',
	duration = 1.1,
	className,
}: {
	to: number
	decimals?: number
	suffix?: string
	prefix?: string
	duration?: number
	className?: string
}) {
	const ref = useRef<HTMLSpanElement>(null)
	const inView = useInView(ref, { once: true, amount: 0.5 })
	const reduced = useReducedMotion()
	const [shown, setShown] = useState(reduced ? to : 0)

	useEffect(() => {
		if (!inView || reduced) {
			if (reduced) setShown(to)
			return
		}
		const controls = animate(0, to, {
			duration,
			ease: [0.16, 1, 0.3, 1],
			onUpdate: (v) => setShown(v),
			// Guarantee the final frame is the exact value, not 66.09999.
			onComplete: () => setShown(to),
		})
		return () => controls.stop()
	}, [inView, reduced, to, duration])

	return (
		<span ref={ref} className={cn('tabular', className)}>
			{prefix}
			{shown.toFixed(decimals)}
			{suffix}
		</span>
	)
}

/* -------------------------------------------------------------------------- */
/* ShimmeringText - Bklit UI loading pattern.                                   */
/* -------------------------------------------------------------------------- */

export function ShimmeringText({ label = 'Loading' }: { label?: string }) {
	return (
		<span className="inline-flex gap-[0.15em] text-ink-dim" aria-live="polite">
			{label.split('').map((ch, i) => (
				<motion.span
					key={`${ch}-${i}`}
					initial={{ opacity: 0.25 }}
					animate={{ opacity: [0.25, 1, 0.25] }}
					transition={{ duration: 1.4, repeat: Infinity, delay: i * 0.06 }}
				>
					{ch}
				</motion.span>
			))}
		</span>
	)
}

/* -------------------------------------------------------------------------- */
/* GlowCard - Vengeance UI pointer spotlight. Pure motion values, no re-render. */
/* -------------------------------------------------------------------------- */

export function GlowCard({
	children,
	className,
	accent = 'var(--color-signal)',
}: {
	children: ReactNode
	className?: string
	accent?: string
}) {
	const x = useMotionValue(-9999)
	const y = useMotionValue(-9999)
	const reduced = useReducedMotion()
	// Hoisted above the conditional render: hooks must never live inside JSX
	// branches, or React's hook order breaks on the first reduced-motion toggle.
	const spotlight = useMemo(
		() =>
			`radial-gradient(220px circle at var(--gx) var(--gy), ${accent}22, transparent 70%)`,
		[accent],
	)

	return (
		<div
			className={cn('panel group relative overflow-hidden', className)}
			onPointerMove={(e) => {
				if (reduced) return
				const r = e.currentTarget.getBoundingClientRect()
				x.set(e.clientX - r.left)
				y.set(e.clientY - r.top)
			}}
			onPointerLeave={() => {
				x.set(-9999)
				y.set(-9999)
			}}
		>
			{!reduced && (
				<motion.div
					aria-hidden
					className="pointer-events-none absolute -inset-px opacity-0 transition-opacity duration-300 group-hover:opacity-100"
					style={{
						background: spotlight,
						['--gx' as string]: x,
						['--gy' as string]: y,
					} as CSSProperties}
				/>
			)}
			{children}
		</div>
	)
}

/* -------------------------------------------------------------------------- */
/* BentoGrid + Marquee - Magic UI patterns.                                     */
/* -------------------------------------------------------------------------- */

export function BentoGrid({
	children,
	className,
}: {
	children: ReactNode
	className?: string
}) {
	return (
		<div className={cn('grid gap-4 sm:grid-cols-2 lg:grid-cols-4', className)}>
			{children}
		</div>
	)
}

export function Marquee({
	items,
	className,
}: {
	items: string[]
	className?: string
}) {
	const reduced = useReducedMotion()
	const doubled = [...items, ...items]
	return (
		<div
			className={cn('relative flex overflow-hidden', className)}
			style={{
				maskImage:
					'linear-gradient(to right, transparent, black 8%, black 92%, transparent)',
			}}
		>
			<motion.div
				className="flex shrink-0 gap-8 pr-8"
				animate={reduced ? undefined : { x: ['0%', '-50%'] }}
				transition={{ duration: 38, repeat: Infinity, ease: 'linear' }}
			>
				{doubled.map((item, i) => (
					<span
						key={`${item}-${i}`}
						className="tabular text-[14px] whitespace-nowrap text-ink-faint"
					>
						{item}
					</span>
				))}
			</motion.div>
		</div>
	)
}

/* -------------------------------------------------------------------------- */
/* Dock - Skiper UI / React Bits interaction, used as the section nav.          */
/* -------------------------------------------------------------------------- */

export function Dock({
	items,
	active,
	onSelect,
}: {
	items: Array<{ id: string; label: string }>
	active: string
	onSelect: (id: string) => void
}) {
	return (
		<nav
			aria-label="Sections"
			className="panel sticky top-3 z-40 mx-auto flex w-fit max-w-[95vw] gap-1 overflow-x-auto p-1 backdrop-blur"
			style={{ backgroundColor: 'rgba(18,19,22,0.82)' }}
		>
			{items.map((item) => {
				const is = item.id === active
				return (
					<button
						key={item.id}
						type="button"
						aria-current={is ? 'true' : undefined}
						onClick={() => onSelect(item.id)}
						className={cn(
							'relative rounded-md px-3 py-1.5 text-[14px] whitespace-nowrap transition-colors',
							is ? 'text-ink' : 'text-ink-faint hover:text-ink-dim',
						)}
					>
						{is && (
							<motion.span
								layoutId="dock-pill"
								className="absolute inset-0 rounded-md"
								style={{ backgroundColor: 'var(--color-raised)' }}
								transition={{ type: 'spring', stiffness: 380, damping: 32 }}
							/>
						)}
						<span className="relative">{item.label}</span>
					</button>
				)
			})}
		</nav>
	)
}

/* -------------------------------------------------------------------------- */
/* ThreatField - anime.js v4 timeline.                                          */
/*                                                                            */
/* This is the one decorative element, and it is still honest: each dot is a    */
/* monitored entity, and the highlighted ones are the count of windows the      */
/* model actually alerted on. anime.js drives an imperative stagger that would  */
/* be awkward in declarative Motion - which is precisely why both libraries are */
/* here rather than one of them being decoration.                               */
/* -------------------------------------------------------------------------- */

export function ThreatField({
	entities = 40,
	flagged = 5,
}: {
	entities?: number
	flagged?: number
}) {
	const root = useRef<HTMLDivElement>(null)
	const reduced = useReducedMotion()

	const flaggedSet = useMemo(() => {
		// Deterministic spread so the visual is stable across reloads.
		const step = Math.max(1, Math.floor(entities / Math.max(1, flagged)))
		const set = new Set<number>()
		for (let i = 0; i < flagged; i += 1) set.add((i * step + 3) % entities)
		return set
	}, [entities, flagged])

	useEffect(() => {
		if (reduced || !root.current) return
		const dots = utils.$('[data-dot]')
		const tl = createTimeline({ loop: true, defaults: { ease: 'inOutSine' } })
		tl.add(dots, {
			opacity: [0.18, 0.5],
			scale: [1, 1.25],
			duration: 1400,
			delay: (_el: unknown, i: number) => i * 45,
			alternate: true,
		})
		return () => {
			tl.pause()
			tl.revert()
		}
	}, [reduced])

	return (
		<div
			ref={root}
			aria-hidden
			className="pointer-events-none absolute inset-0 flex flex-wrap content-start gap-2 p-6 opacity-70"
		>
			{Array.from({ length: entities }, (_, i) => (
				<span
					key={i}
					data-dot
					className="h-1.5 w-1.5 rounded-full"
					style={{
						backgroundColor: flaggedSet.has(i)
							? 'var(--color-alarm)'
							: 'var(--color-ink-faint)',
						opacity: flaggedSet.has(i) ? 0.85 : 0.18,
					}}
				/>
			))}
		</div>
	)
}

/* -------------------------------------------------------------------------- */
/* AnimatedList - Magic UI pattern, used for the live triage queue.             */
/* -------------------------------------------------------------------------- */

export function AnimatedList({
	children,
	className,
}: {
	children: ReactNode[]
	className?: string
}) {
	const reduced = useReducedMotion()
	return (
		<div className={cn('flex flex-col gap-2', className)}>
			{children.map((child, i) => (
				<motion.div
					key={i}
					initial={reduced ? undefined : { opacity: 0, x: -8 }}
					whileInView={reduced ? undefined : { opacity: 1, x: 0 }}
					viewport={{ once: true }}
					transition={{ duration: 0.35, delay: Math.min(i * 0.035, 0.5) }}
				>
					{child}
				</motion.div>
			))}
		</div>
	)
}
