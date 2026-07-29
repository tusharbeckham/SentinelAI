/**
 * Scroll-driven lens hero.
 *
 * The effect: a glass lens travels across the headline, magnifying and
 * refracting whatever is under it, then parks in the centre and reveals the
 * detector architecture *through* the glass, then swells to fill the viewport
 * and dissolves, handing the page over to the rest of the console.
 *
 * How it is driven (this is the part that matters):
 *
 * - anime.js v4's ScrollObserver (`onScroll`) is the source of truth. A proxy
 *   object is animated from 0 to 1 with `autoplay: onScroll({ sync: true })`,
 *   so scroll position *is* playback position: scroll up and it runs backwards,
 *   stop and it holds.
 * - A separate rAF loop eases the rendered value toward the scroll-derived
 *   target. Scroll wheels and trackpads deliver jumpy deltas; binding pixels
 *   straight to them looks mechanical. The easing is what makes glass feel
 *   heavy, and it is why this reads like the anime.js site rather than like a
 *   parallax script.
 * - Every write is `utils.set`, a direct write. Tweening toward a scroll-derived
 *   target as well would double-smooth and lag behind the wheel.
 *
 * Why no WebGL: the refraction is an SVG `feTurbulence` + `feDisplacementMap`
 * pair applied to the magnified copy, and the magnification is a `clip-path:
 * circle()` over a scaled duplicate of the same DOM. That is real refraction of
 * real text, with no shader, no model file, and nothing that can fail to load
 * on a Hugging Face Space.
 *
 * Accessibility: under `prefers-reduced-motion` the lens is not rendered at all.
 * The hero content is shown once, statically, at full contrast. The duplicate
 * layer is `aria-hidden` so the headline is never announced twice.
 */

import { animate, createTimeline, onScroll, stagger, utils } from 'animejs'
import { useEffect, useRef, type ReactNode } from 'react'
import { useReducedMotion } from '@/components/anime'

type Leg = { id: string; label: string; sub: string; color: string }

/** The three legs the stacker fuses, revealed inside the lens. */
const LEGS: Leg[] = [
	{
		id: 'iforest',
		label: 'isolation forest',
		sub: 'unsupervised · per-entity baseline',
		color: 'var(--color-signal)',
	},
	{
		id: 'gbdt',
		label: 'gradient boosting',
		sub: 'supervised · carries the signal',
		color: 'var(--color-safe)',
	},
	{
		id: 'graph',
		label: 'graph leg',
		sub: 'lateral movement · weakest leg',
		color: 'var(--color-graph)',
	},
]

/** Smoothstep. Used for phase blending so nothing starts or stops abruptly. */
function ease(edge0: number, edge1: number, x: number): number {
	const t = Math.min(1, Math.max(0, (x - edge0) / (edge1 - edge0)))
	return t * t * (3 - 2 * t)
}

export function LensHero({ children }: { children: ReactNode }) {
	const trackRef = useRef<HTMLDivElement | null>(null)
	const stageRef = useRef<HTMLDivElement | null>(null)
	const reduced = useReducedMotion()

	useEffect(() => {
		const track = trackRef.current
		const stage = stageRef.current
		if (!track || !stage || reduced) return

		const lens = utils.$('[data-lens]', stage)
		const magnified = utils.$('[data-magnified]', stage)
		const fringes = utils.$('[data-fringe]', stage)
		const rim = utils.$('[data-rim]', stage)
		const glint = utils.$('[data-glint]', stage)
		const base = utils.$('[data-base]', stage)
		const model = utils.$('[data-model]', stage)
		const plates = utils.$('[data-plate]', stage)
		const hint = utils.$('[data-hint]', stage)

		// --- the value the whole scene is a function of ---------------------
		const scrolled = { p: 0 }
		let target = 0
		let shown = 0

		// ScrollObserver: playback position === scroll position.
		const scrubber = animate(scrolled, {
			p: 1,
			duration: 1000,
			ease: 'linear',
			autoplay: onScroll({
				target: track,
				enter: 'top top',
				leave: 'bottom bottom',
				sync: true,
			}),
			onUpdate: () => {
				target = scrolled.p
			},
		})

		const render = () => {
			const p = shown

			// Phases. Deliberately overlapping so there is never a dead moment.
			const sweep = ease(0, 0.5, p) // lens crosses the headline
			const reveal = ease(0.42, 0.72, p) // architecture fades in inside the glass
			const bloom = ease(0.74, 1, p) // lens swells and dissolves

			const box = stage.getBoundingClientRect()
			const w = box.width
			const h = box.height

			// A Lissajous path, so the lens curves across the text instead of
			// sliding along a straight line. Amplitude collapses as it parks.
			const park = 1 - reveal
			const x = w * (0.5 + Math.sin(sweep * Math.PI * 1.15 - Math.PI / 2) * 0.27 * park)
			const y = h * (0.5 + Math.sin(sweep * Math.PI * 2.1) * 0.13 * park)

			// Radius: travelling lens, then a wider viewing glass, then bloom out.
			const diag = Math.hypot(w, h)
			const radius =
				(150 + reveal * 105) * (1 - bloom) + bloom * diag * 0.62 + Math.sin(p * 14) * 2 * (1 - bloom)

			const clip = `circle(${radius.toFixed(1)}px at ${x.toFixed(1)}px ${y.toFixed(1)}px)`

			// Magnification relaxes to 1 as the lens blooms, so the reveal lands on
			// the real layout rather than on a still-zoomed copy.
			const zoom = 1.28 - reveal * 0.1 - bloom * 0.18

			utils.set(magnified, {
				clipPath: clip,
				scale: zoom,
				opacity: 1 - bloom * 0.15,
			})

			// Chromatic fringes: same clip, opposite sub-pixel offsets. Real lenses
			// split colour at the edge; this is the cheapest honest imitation.
			fringes.forEach((fringe, i) => {
				const dir = i === 0 ? 1 : -1
				utils.set(fringe, {
					clipPath: clip,
					scale: zoom + dir * 0.012,
					translateX: dir * 3,
					opacity: (0.42 - reveal * 0.16) * (1 - bloom),
				})
			})

			utils.set(lens, {
				translateX: x - radius,
				translateY: y - radius,
				width: radius * 2,
				height: radius * 2,
				opacity: 1 - bloom,
			})
			utils.set(rim, { opacity: (0.5 + reveal * 0.3) * (1 - bloom) })
			// The specular highlight slides around the rim as the lens travels.
			utils.set(glint, {
				rotate: -40 + sweep * 150,
				opacity: (0.55 - reveal * 0.2) * (1 - bloom),
			})

			// Outside the glass the page sits back: dimmer, very slightly blurred.
			utils.set(base, {
				opacity: 0.34 + bloom * 0.66,
				filter: `blur(${(1.4 * (1 - bloom)).toFixed(2)}px)`,
			})

			// The architecture, seen through the lens.
			utils.set(model, { opacity: reveal * (1 - bloom) })
			plates.forEach((plate, i) => {
				const offset = i - 1
				utils.set(plate, {
					translateZ: offset * reveal * 118,
					translateY: offset * reveal * 20,
					rotateX: 46 - reveal * 40,
					rotateZ: -16 + reveal * 16,
					opacity: 0.25 + reveal * 0.75,
				})
			})

			utils.set(hint, { opacity: 1 - ease(0, 0.12, p) })
		}

		// Inertia. The lens is glass; it should not track the wheel tooth for
		// tooth. 0.11 is slow enough to feel weighted, fast enough not to lag.
		let raf = 0
		const loop = () => {
			shown += (target - shown) * 0.11
			if (Math.abs(target - shown) < 0.0002) shown = target
			render()
			raf = requestAnimationFrame(loop)
		}
		raf = requestAnimationFrame(loop)

		const onResize = () => render()
		window.addEventListener('resize', onResize)

		return () => {
			cancelAnimationFrame(raf)
			window.removeEventListener('resize', onResize)
			scrubber.revert()
		}
	}, [reduced])

	// Entrance for the legend, time-based, so it is a timeline.
	useEffect(() => {
		const stage = stageRef.current
		if (!stage || reduced) return
		const rows = utils.$('[data-legend-row]', stage)
		if (rows.length === 0) return
		const timeline = createTimeline({ defaults: { ease: 'out(3)', duration: 620 } })
		timeline.add(rows, { opacity: [0, 1], translateY: [10, 0], delay: stagger(110, { start: 260 }) })
		return () => timeline.pause()
	}, [reduced])

	// Reduced motion: one static, fully legible copy. No lens, no duplicate.
	if (reduced) {
		return <div className="relative">{children}</div>
	}

	return (
		<div ref={trackRef} className="relative h-[300vh]">
			{/* The refraction filter. Displacing the magnified copy by fractal
			    noise is what makes the glass look like glass at its edges. */}
			<svg width="0" height="0" className="absolute" aria-hidden focusable="false">
				<filter id="sentinel-lens-refract" x="-12%" y="-12%" width="124%" height="124%">
					<feTurbulence
						type="fractalNoise"
						baseFrequency="0.009 0.016"
						numOctaves={2}
						seed={11}
						result="noise"
					/>
					<feDisplacementMap
						in="SourceGraphic"
						in2="noise"
						scale={16}
						xChannelSelector="R"
						yChannelSelector="G"
					/>
				</filter>
			</svg>

			<div className="sticky top-0 h-screen overflow-hidden">
				<div ref={stageRef} className="relative h-full w-full">
					{/* Layer 1: the page as it really is, sitting back behind glass. */}
					<div
						data-base
						className="absolute inset-0 overflow-y-auto"
						style={{ opacity: 0.34 }}
					>
						{children}
					</div>

					{/* Layers 2 and 3: chromatic fringes, behind the main magnified copy. */}
					{['var(--color-signal)', 'var(--color-alarm)'].map((tint, i) => (
						<div
							key={tint}
							data-fringe
							aria-hidden
							className="pointer-events-none absolute inset-0"
							style={{
								opacity: 0,
								mixBlendMode: 'screen',
								filter: `url(#sentinel-lens-refract) drop-shadow(0 0 1px ${tint})`,
								clipPath: 'circle(0px at 50% 50%)',
								transformOrigin: 'center',
							}}
						>
							<div style={{ color: tint }}>{children}</div>
						</div>
					))}

					{/* Layer 4: the magnified, refracted copy. This is what you read
					    through the lens. aria-hidden: the base layer already announced it. */}
					<div
						data-magnified
						aria-hidden
						className="pointer-events-none absolute inset-0"
						style={{
							clipPath: 'circle(0px at 50% 50%)',
							filter: 'url(#sentinel-lens-refract)',
							transformOrigin: 'center',
						}}
					>
						{children}
					</div>

					{/* Layer 5: the architecture, only visible through the glass. */}
					<div
						data-model
						aria-hidden
						className="pointer-events-none absolute inset-0 grid place-items-center"
						style={{ opacity: 0, perspective: '1100px' }}
					>
						<div
							className="relative grid h-[260px] w-[330px] place-items-center"
							style={{ transformStyle: 'preserve-3d' }}
						>
							{LEGS.map((leg) => (
								<div
									key={leg.id}
									data-plate
									className="absolute h-[168px] w-[280px] rounded-xl border"
									style={{
										borderColor: leg.color,
										background: `linear-gradient(140deg, color-mix(in oklab, ${leg.color} 18%, transparent), transparent 72%)`,
										boxShadow: `0 0 38px color-mix(in oklab, ${leg.color} 22%, transparent)`,
										transformStyle: 'preserve-3d',
									}}
								>
									<p
										className="absolute left-4 top-3 text-[15px] font-medium"
										style={{ color: leg.color }}
									>
										{leg.label}
									</p>
									<p className="absolute left-4 top-9 text-[13px] text-ink-dim">{leg.sub}</p>
								</div>
							))}
						</div>
					</div>

					{/* Layer 6: the lens body itself. Rim, inner shadow, moving glint. */}
					<div
						data-lens
						aria-hidden
						className="pointer-events-none absolute left-0 top-0 rounded-full"
						style={{ width: 300, height: 300 }}
					>
						<div
							data-rim
							className="absolute inset-0 rounded-full"
							style={{
								border: '1px solid color-mix(in oklab, var(--color-ink) 26%, transparent)',
								boxShadow:
									'inset 0 0 60px color-mix(in oklab, var(--color-signal) 14%, transparent), 0 0 70px color-mix(in oklab, var(--color-signal) 10%, transparent)',
							}}
						/>
						<div
							data-glint
							className="absolute inset-0 rounded-full"
							style={{
								background:
									'conic-gradient(from 0deg, transparent 0deg, color-mix(in oklab, var(--color-ink) 30%, transparent) 18deg, transparent 46deg, transparent 300deg, color-mix(in oklab, var(--color-signal) 24%, transparent) 342deg, transparent 360deg)',
								maskImage:
									'radial-gradient(circle, transparent 76%, black 88%, transparent 100%)',
							}}
						/>
					</div>

					{/* Legend and scroll hint. */}
					<div className="pointer-events-none absolute bottom-8 left-0 right-0 px-6">
						<div className="mx-auto flex max-w-6xl flex-wrap items-end justify-between gap-4">
							<div className="flex flex-col gap-1">
								{LEGS.map((leg) => (
									<div
										key={leg.id}
										data-legend-row
										className="flex items-center gap-2 text-[13px] text-ink-dim"
										style={{ opacity: 0 }}
									>
										<span
											className="h-2 w-2 rounded-full"
											style={{ background: leg.color }}
										/>
										{leg.label}
									</div>
								))}
							</div>
							<p data-hint className="text-[13px] tracking-[0.18em] text-ink-faint uppercase">
								scroll · the lens follows
							</p>
						</div>
					</div>
				</div>
			</div>
		</div>
	)
}
