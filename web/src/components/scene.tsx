/**
 * Scroll-scrubbed 3D scene, in the spirit of the anime.js site's hero: the
 * animation is not on a clock, it is bound to scroll position, so scrolling up
 * runs it backwards and stopping halfway holds it halfway.
 *
 * What it shows is the actual architecture. Three detector layers sit stacked in
 * depth; as you scroll they separate, rotate, and label themselves, then
 * collapse back together into the calibrated stacker that fuses them. The
 * separation is the point: the reason this is a hybrid is that the three legs
 * disagree, and the stacker is what decides.
 *
 * Implementation notes:
 * - Scrubbing is driven by one rAF-coalesced scroll read and applied with
 *   anime.js `utils.set`, which is a direct write rather than a tween. Tweening
 *   toward a scroll-derived target is what makes scroll animations feel laggy
 *   and rubbery; writing the exact value keeps it locked to the wheel.
 * - The entrance is a real anime.js timeline, because that one is time-based.
 * - Everything is CSS 3D transforms on a handful of divs. No WebGL, no model
 *   download, nothing that can fail to load on a Hugging Face Space.
 * - Under `prefers-reduced-motion` the scene renders flat, separated and
 *   readable, with no scroll binding at all.
 */

import { createTimeline, stagger, utils } from 'animejs'
import { useEffect, useRef } from 'react'
import { useReducedMotion } from '@/components/anime'

type Layer = {
	id: string
	label: string
	sub: string
	color: string
}

/** The three legs, in the order they are fused. */
const LAYERS: Layer[] = [
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

export function ScrollScene() {
	const trackRef = useRef<HTMLDivElement | null>(null)
	const stageRef = useRef<HTMLDivElement | null>(null)
	const reduced = useReducedMotion()

	// Entrance: time-based, so it is a timeline.
	useEffect(() => {
		const stage = stageRef.current
		if (!stage || reduced) return
		const plates = utils.$('[data-plate]', stage)
		if (plates.length === 0) return
		const timeline = createTimeline({ defaults: { ease: 'out(3)' } })
		timeline.add(plates, {
			opacity: [0, 1],
			scale: [0.9, 1],
			duration: 700,
			delay: stagger(90),
		})
		return () => timeline.pause()
	}, [reduced])

	// Scrub: scroll-based, so it is a direct write per frame.
	useEffect(() => {
		const track = trackRef.current
		const stage = stageRef.current
		if (!track || !stage) return

		const plates = utils.$('[data-plate]', stage)
		const labels = utils.$('[data-plate-label]', stage)
		const fuse = utils.$('[data-fuse]', stage)
		const grid = utils.$('[data-grid]', stage)

		if (reduced) {
			// Flat, separated, legible. No scroll binding.
			plates.forEach((plate, i) => {
				utils.set(plate, {
					opacity: 1,
					translateY: (i - 1) * 96,
					translateZ: 0,
					rotateX: 0,
					rotateZ: 0,
				})
			})
			utils.set(labels, { opacity: 1, translateX: 0 })
			utils.set(fuse, { opacity: 1, scaleX: 1 })
			return
		}

		let frame = 0
		const apply = () => {
			frame = 0
			const box = track.getBoundingClientRect()
			const span = box.height - window.innerHeight
			// 0 when the track's top hits the viewport top, 1 when its bottom does.
			const p = span > 0 ? Math.min(1, Math.max(0, -box.top / span)) : 0

			// Phase one (0 to 0.62): the stack tilts up and pulls apart.
			// Phase two (0.62 to 1): it flattens and closes into the stacker.
			const open = Math.min(1, p / 0.62)
			const close = Math.max(0, (p - 0.62) / 0.38)
			const spread = open * (1 - close * 0.82)

			utils.set(stage, {
				rotateX: 58 - open * 34 - close * 12,
				rotateZ: -22 + open * 22 + close * 6,
			})

			plates.forEach((plate, i) => {
				const offset = i - 1
				utils.set(plate, {
					translateZ: offset * spread * 150,
					translateY: offset * spread * 26,
					scale: 1 - close * 0.06,
					opacity: 0.35 + spread * 0.65,
				})
			})

			labels.forEach((label, i) => {
				// Labels arrive one after another, then leave as the stack closes.
				const start = 0.16 + i * 0.13
				const appeared = Math.min(1, Math.max(0, (p - start) / 0.12))
				utils.set(label, {
					opacity: appeared * (1 - close),
					translateX: (1 - appeared) * 18,
				})
			})

			utils.set(fuse, { opacity: close, scaleX: 0.4 + close * 0.6 })
			utils.set(grid, { opacity: 0.06 + open * 0.16 })
		}

		const onScroll = () => {
			if (frame) return
			frame = requestAnimationFrame(apply)
		}

		apply()
		window.addEventListener('scroll', onScroll, { passive: true })
		window.addEventListener('resize', onScroll)
		return () => {
			window.removeEventListener('scroll', onScroll)
			window.removeEventListener('resize', onScroll)
			if (frame) cancelAnimationFrame(frame)
		}
	}, [reduced])

	return (
		<div ref={trackRef} className="relative h-[240vh]" aria-hidden={false}>
			<div className="sticky top-0 flex h-screen items-center justify-center overflow-hidden">
				{/* Depth grid, behind everything. */}
				<div
					data-grid
					className="pointer-events-none absolute inset-0"
					style={{
						opacity: 0.06,
						backgroundImage:
							'linear-gradient(var(--color-line-strong) 1px, transparent 1px), linear-gradient(90deg, var(--color-line-strong) 1px, transparent 1px)',
						backgroundSize: '56px 56px',
						maskImage: 'radial-gradient(ellipse at center, black 20%, transparent 72%)',
					}}
				/>

				<div className="relative w-full max-w-4xl px-5">
					<p className="text-center text-[13px] tracking-[0.18em] text-ink-faint uppercase">
						three legs, one decision
					</p>

					<div
						className="mt-10 grid place-items-center"
						style={{ perspective: '1200px', perspectiveOrigin: '50% 45%' }}
					>
						<div
							ref={stageRef}
							className="relative grid h-[320px] w-full max-w-xl place-items-center"
							style={{ transformStyle: 'preserve-3d' }}
						>
							{LAYERS.map((layer) => (
								<div
									key={layer.id}
									data-plate
									className="absolute h-[210px] w-[340px] rounded-xl border"
									style={{
										borderColor: layer.color,
										background: `linear-gradient(135deg, color-mix(in oklab, ${layer.color} 14%, transparent), transparent 70%)`,
										boxShadow: `0 0 42px color-mix(in oklab, ${layer.color} 18%, transparent)`,
										transformStyle: 'preserve-3d',
										opacity: 0,
									}}
								>
									<div
										data-plate-label
										className="absolute left-4 top-3"
										style={{ opacity: 0 }}
									>
										<p className="text-[15px] font-medium" style={{ color: layer.color }}>
											{layer.label}
										</p>
										<p className="text-[13px] text-ink-dim">{layer.sub}</p>
									</div>
								</div>
							))}
						</div>
					</div>

					<div className="mt-8 text-center">
						<div
							data-fuse
							className="mx-auto h-[2px] w-56 origin-center rounded-full"
							style={{
								opacity: 0,
								background:
									'linear-gradient(90deg, transparent, var(--color-signal), var(--color-safe), var(--color-graph), transparent)',
							}}
						/>
						<p className="mt-4 text-[15px] leading-relaxed text-ink-dim">
							The legs disagree. A calibrated logistic stacker decides, and it is
							tuned to the analyst&rsquo;s budget rather than to a flattering AUC.
						</p>
					</div>
				</div>
			</div>
		</div>
	)
}
