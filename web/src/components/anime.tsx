/**
 * Motion primitives driven by anime.js v4 (https://animejs.com).
 *
 * Motion is used here to carry information, not decoration: a risk bar grows to
 * its measured value, a sparkline draws along the entity's real timeline, and a
 * pulse marks a live connection. Nothing loops forever except the live indicator,
 * because a security console is read during incidents and constant movement in
 * peripheral vision is genuinely tiring.
 *
 * Every component below short-circuits under `prefers-reduced-motion`, landing on
 * the final state immediately rather than animating to it. That is the accessible
 * behaviour and it also keeps the numbers correct in screenshots.
 *
 * anime.js is used for imperative, per-element and staggered work (draws,
 * scrambles, pointer springs); Motion handles declarative layout and presence in
 * the sibling files. Using each for what it is best at is deliberate.
 */

import { animate, createTimeline, stagger, utils } from 'animejs'
import {
	useEffect,
	useMemo,
	useRef,
	useState,
	type CSSProperties,
	type ReactNode,
} from 'react'
import { cn } from '@/lib/cn'

/** True when the user has asked the OS to reduce motion. */
export function useReducedMotion(): boolean {
	const [reduced, setReduced] = useState(false)
	useEffect(() => {
		const query = window.matchMedia('(prefers-reduced-motion: reduce)')
		setReduced(query.matches)
		const onChange = (e: MediaQueryListEvent) => setReduced(e.matches)
		query.addEventListener('change', onChange)
		return () => query.removeEventListener('change', onChange)
	}, [])
	return reduced
}

/** Fires once when the element first enters the viewport. */
function useInViewOnce<T extends HTMLElement>(margin = '0px 0px -12% 0px') {
	const ref = useRef<T | null>(null)
	const [seen, setSeen] = useState(false)
	useEffect(() => {
		const el = ref.current
		if (!el || seen) return
		const observer = new IntersectionObserver(
			([entry]) => {
				if (entry?.isIntersecting) {
					setSeen(true)
					observer.disconnect()
				}
			},
			{ rootMargin: margin, threshold: 0.15 },
		)
		observer.observe(el)
		return () => observer.disconnect()
	}, [seen, margin])
	return { ref, seen }
}

/* -------------------------------------------------------------------------- */

/** Reading-progress bar. Cheap orientation cue on a long console. */
export function ScrollProgress() {
	const ref = useRef<HTMLDivElement | null>(null)
	useEffect(() => {
		const el = ref.current
		if (!el) return
		let frame = 0
		const onScroll = () => {
			if (frame) return
			// Coalesce to one write per frame: scroll fires far more often than paint.
			frame = requestAnimationFrame(() => {
				frame = 0
				const max = document.documentElement.scrollHeight - window.innerHeight
				const ratio = max > 0 ? window.scrollY / max : 0
				utils.set(el, { scaleX: Math.min(1, Math.max(0, ratio)) })
			})
		}
		onScroll()
		window.addEventListener('scroll', onScroll, { passive: true })
		return () => {
			window.removeEventListener('scroll', onScroll)
			if (frame) cancelAnimationFrame(frame)
		}
	}, [])
	return (
		<div className="fixed inset-x-0 top-0 z-50 h-[2px] bg-transparent" aria-hidden>
			<div
				ref={ref}
				className="h-full origin-left"
				style={{
					transform: 'scaleX(0)',
					background:
						'linear-gradient(90deg, var(--color-signal), var(--color-graph), var(--color-alarm))',
				}}
			/>
		</div>
	)
}

/** Headline that assembles per character on first view. */
export function SplitText({
	text,
	className,
	delay = 0,
}: {
	text: string
	className?: string
	delay?: number
}) {
	const { ref, seen } = useInViewOnce<HTMLSpanElement>()
	const reduced = useReducedMotion()
	const chars = useMemo(() => Array.from(text), [text])

	useEffect(() => {
		const host = ref.current
		if (!host || !seen || reduced) return
		const targets = utils.$('[data-char]', host)
		if (targets.length === 0) return
		animate(targets, {
			opacity: [0, 1],
			translateY: [14, 0],
			filter: ['blur(6px)', 'blur(0px)'],
			duration: 620,
			delay: stagger(16, { start: delay }),
			ease: 'out(3)',
		})
	}, [ref, seen, reduced, delay])

	return (
		<span ref={ref} className={className}>
			{chars.map((char, i) => (
				<span
					key={`${char}-${i}`}
					data-char
					style={{ display: 'inline-block', opacity: reduced ? 1 : 0 }}
				>
					{char === ' ' ? '\u00a0' : char}
				</span>
			))}
		</span>
	)
}

const GLYPHS = '01!<>-_\\/[]{}=+*^?#'

/** Text that resolves out of noise. Used only on labels that describe scanning. */
export function TextScramble({
	text,
	className,
	duration = 900,
}: {
	text: string
	className?: string
	duration?: number
}) {
	const [shown, setShown] = useState(text)
	const reduced = useReducedMotion()
	const { ref, seen } = useInViewOnce<HTMLSpanElement>()

	useEffect(() => {
		if (reduced || !seen) {
			setShown(text)
			return
		}
		// anime.js can tween a plain object, which keeps the scramble on the same
		// clock as every other animation instead of a private setInterval.
		const state = { progress: 0 }
		const instance = animate(state, {
			progress: 1,
			duration,
			ease: 'linear',
			onUpdate: () => {
				const settled = Math.floor(state.progress * text.length)
				const next = Array.from(text)
					.map((char, i) => {
						if (i < settled || char === ' ') return char
						return GLYPHS[Math.floor(Math.random() * GLYPHS.length)] ?? char
					})
					.join('')
				setShown(next)
			},
			onComplete: () => setShown(text),
		})
		return () => {
			instance.pause()
			setShown(text)
		}
	}, [text, duration, reduced, seen])

	return (
		<span ref={ref} className={className}>
			{shown}
		</span>
	)
}

/** Button that leans toward the pointer. Subtle: 6px of travel, not a toy. */
export function MagneticButton({
	children,
	onClick,
	className,
	title,
}: {
	children: ReactNode
	onClick?: () => void
	className?: string
	title?: string
}) {
	const ref = useRef<HTMLButtonElement | null>(null)
	const reduced = useReducedMotion()

	const move = (event: React.PointerEvent<HTMLButtonElement>) => {
		const el = ref.current
		if (!el || reduced) return
		const box = el.getBoundingClientRect()
		const dx = (event.clientX - (box.left + box.width / 2)) / box.width
		const dy = (event.clientY - (box.top + box.height / 2)) / box.height
		animate(el, { x: dx * 6, y: dy * 6, duration: 240, ease: 'out(3)' })
	}

	const reset = () => {
		const el = ref.current
		if (!el || reduced) return
		animate(el, { x: 0, y: 0, duration: 420, ease: 'out(4)' })
	}

	return (
		<button
			ref={ref}
			type="button"
			title={title}
			onClick={onClick}
			onPointerMove={move}
			onPointerLeave={reset}
			className={cn('will-change-transform', className)}
		>
			{children}
		</button>
	)
}

/** A soft light that trails the pointer across the page. */
export function CursorGlow() {
	const ref = useRef<HTMLDivElement | null>(null)
	const reduced = useReducedMotion()

	useEffect(() => {
		const el = ref.current
		if (!el || reduced) return
		// Coarse pointers have no hover position to follow.
		if (window.matchMedia('(pointer: coarse)').matches) return
		const onMove = (event: PointerEvent) => {
			animate(el, {
				left: event.clientX,
				top: event.clientY,
				opacity: 1,
				duration: 520,
				ease: 'out(3)',
			})
		}
		const onLeave = () => animate(el, { opacity: 0, duration: 300 })
		window.addEventListener('pointermove', onMove, { passive: true })
		window.addEventListener('pointerleave', onLeave)
		return () => {
			window.removeEventListener('pointermove', onMove)
			window.removeEventListener('pointerleave', onLeave)
		}
	}, [reduced])

	if (reduced) return null
	return (
		<div
			ref={ref}
			aria-hidden
			className="pointer-events-none fixed z-0 h-[380px] w-[380px] -translate-x-1/2 -translate-y-1/2 opacity-0"
			style={{
				background:
					'radial-gradient(circle, color-mix(in oklab, var(--color-signal) 16%, transparent) 0%, transparent 68%)',
			}}
		/>
	)
}

/** Sparkline that draws itself along the entity's real timeline. */
export function Sparkline({
	values,
	height = 34,
	stroke = 'var(--color-signal)',
	className,
}: {
	values: number[]
	height?: number
	stroke?: string
	className?: string
}) {
	const pathRef = useRef<SVGPathElement | null>(null)
	const reduced = useReducedMotion()
	const width = 120

	const d = useMemo(() => {
		if (values.length === 0) return ''
		const peak = Math.max(...values, 0.0001)
		return values
			.map((v, i) => {
				const x = (i / Math.max(1, values.length - 1)) * width
				const y = height - (v / peak) * (height - 3) - 1.5
				return `${i === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`
			})
			.join(' ')
	}, [values, height])

	useEffect(() => {
		const path = pathRef.current
		if (!path || !d) return
		const length = path.getTotalLength()
		if (reduced) {
			utils.set(path, { strokeDasharray: 'none', strokeDashoffset: 0 })
			return
		}
		utils.set(path, { strokeDasharray: length, strokeDashoffset: length })
		animate(path, { strokeDashoffset: 0, duration: 900, ease: 'inOut(2)' })
	}, [d, reduced])

	return (
		<svg
			className={className}
			viewBox={`0 0 ${width} ${height}`}
			preserveAspectRatio="none"
			role="img"
			aria-label="probability over the test period"
		>
			<path
				ref={pathRef}
				d={d}
				fill="none"
				stroke={stroke}
				strokeWidth={1.4}
				strokeLinejoin="round"
				strokeLinecap="round"
			/>
		</svg>
	)
}

/** Horizontal bar that grows to a measured fraction, staggered down a list. */
export function RiskBar({
	value,
	index = 0,
	color = 'var(--color-signal)',
	height = 6,
}: {
	value: number
	index?: number
	color?: string
	height?: number
}) {
	const ref = useRef<HTMLDivElement | null>(null)
	const reduced = useReducedMotion()
	const pct = `${Math.max(0, Math.min(1, value)) * 100}%`

	useEffect(() => {
		const el = ref.current
		if (!el) return
		if (reduced) {
			utils.set(el, { width: pct })
			return
		}
		animate(el, {
			width: ['0%', pct],
			duration: 760,
			delay: 60 + index * 28,
			ease: 'out(3)',
		})
	}, [pct, index, reduced])

	return (
		<div className="w-full overflow-hidden rounded-full bg-white/6" style={{ height }}>
			<div ref={ref} className="h-full rounded-full" style={{ width: 0, background: color }} />
		</div>
	)
}

/** Looping pulse. Reserved for genuinely live state. */
export function PulseRing({ color = 'var(--color-safe)', size = 8 }: { color?: string; size?: number }) {
	const ref = useRef<HTMLSpanElement | null>(null)
	const reduced = useReducedMotion()

	useEffect(() => {
		const el = ref.current
		if (!el || reduced) return
		const instance = animate(el, {
			scale: [1, 2.6],
			opacity: [0.55, 0],
			duration: 1600,
			ease: 'out(2)',
			loop: true,
		})
		return () => instance.pause()
	}, [reduced])

	return (
		<span className="relative inline-flex" style={{ width: size, height: size }}>
			<span
				ref={ref}
				className="absolute inset-0 rounded-full"
				style={{ background: color, opacity: 0 }}
			/>
			<span className="absolute inset-0 rounded-full" style={{ background: color }} />
		</span>
	)
}

/**
 * Staggers its children in whenever `signature` changes. Used for query results,
 * so a new result set reads as a new answer rather than a silent swap.
 */
export function StaggerList({
	children,
	signature,
	className,
	step = 22,
}: {
	children: ReactNode
	signature: string
	className?: string
	step?: number
}) {
	const ref = useRef<HTMLDivElement | null>(null)
	const reduced = useReducedMotion()

	useEffect(() => {
		const host = ref.current
		if (!host) return
		const rows = utils.$('[data-stagger-row]', host)
		if (rows.length === 0) return
		if (reduced) {
			utils.set(rows, { opacity: 1, translateY: 0 })
			return
		}
		const timeline = createTimeline({ defaults: { ease: 'out(3)', duration: 460 } })
		timeline.add(rows, {
			opacity: [0, 1],
			translateY: [10, 0],
			delay: stagger(step),
		})
		return () => timeline.pause()
	}, [signature, reduced, step])

	return (
		<div ref={ref} className={className}>
			{children}
		</div>
	)
}

/** Panel that lifts slightly and tilts toward the pointer. */
export function TiltPanel({
	children,
	className,
	style,
}: {
	children: ReactNode
	className?: string
	style?: CSSProperties
}) {
	const ref = useRef<HTMLDivElement | null>(null)
	const reduced = useReducedMotion()

	const move = (event: React.PointerEvent<HTMLDivElement>) => {
		const el = ref.current
		if (!el || reduced) return
		const box = el.getBoundingClientRect()
		const dx = (event.clientX - (box.left + box.width / 2)) / box.width
		const dy = (event.clientY - (box.top + box.height / 2)) / box.height
		// Kept under 3 degrees: enough to feel responsive, not enough to distort
		// the numbers being read.
		animate(el, { rotateY: dx * 3, rotateX: -dy * 3, duration: 300, ease: 'out(3)' })
	}

	const reset = () => {
		const el = ref.current
		if (!el || reduced) return
		animate(el, { rotateX: 0, rotateY: 0, duration: 520, ease: 'out(4)' })
	}

	return (
		<div
			ref={ref}
			onPointerMove={move}
			onPointerLeave={reset}
			className={className}
			style={{ transformStyle: 'preserve-3d', ...style }}
		>
			{children}
		</div>
	)
}
