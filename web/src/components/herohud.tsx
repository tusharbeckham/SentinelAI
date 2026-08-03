/**
 * HeroHud -- the instrument chrome layered over the embedding scene.
 *
 * WHY THIS IS NOT AN SVG WITH A VIEWBOX ANY MORE.
 *
 * Version 1 drew everything into a fixed 1600x900 viewBox with
 * preserveAspectRatio="xMidYMid slice". Slice scales to cover, so on any
 * viewport narrower or shorter than 16:9 the box is cropped inward and the
 * corner readouts march toward the middle of the screen -- straight through
 * the subtitle. It looked fine at one aspect ratio and broken at every other.
 *
 * So the chrome is now laid out with normal flow and absolute corners. It
 * cannot collide with the title, because it is anchored to the edges and the
 * title owns the middle. The only SVG left is the axis triad, which lives in
 * its own small box where a viewBox is harmless.
 *
 * Nothing here is invented: every readout is a real figure from the run.
 */
import { motion } from "motion/react"
import { cn } from "@/lib/cn"

type Props = { act: number; className?: string }
/* One readout set per act. Act 0 is deliberately empty: the title page stays
   clean, which is the whole complaint the previous version earned. */
const READOUTS: Array<Array<[string, string]>> = [
	[],
	[
		["corpus", "11,326 windows"],
		["features", "40"],
		["split", "held-out test"],
	],
	[
		["PC1", "22.5% var"],
		["PC2", "12.5% var"],
		["benign", "11,257"],
	],
	[
		["y axis", "model log-odds"],
		["range", "\u221210.40 \u2026 +4.30"],
	],
	[
		["stacker", "logistic, calibrated"],
		["p rank 1", "0.9858"],
	],
	[
		["threshold", "0.6252"],
		["above plane", "49 = 39 true + 10 false"],
		["missed", "20"],
		["precision / recall", "0.857 / 0.712"],
	],
	[
		["alert", "AL-1767491700-h002"],
		["action", "auto_contain"],
		["audit chain", "valid"],
	],
]

export function HeroHud({ act, className }: Props) {
	const rows = READOUTS[Math.min(Math.max(act, 0), READOUTS.length - 1)] ?? []
	const on = act > 0

	return (
		<div className={cn("hud-text select-none", className)} aria-hidden="true">
			{/* Axis triad, bottom-left. This is the key to reading the scene: two
			    principal components on the floor, model confidence as height. */}
			<motion.div
				className="absolute bottom-6 left-6 flex items-end gap-3"
				initial={false}
				animate={{ opacity: on ? 1 : 0, y: on ? 0 : 8 }}
				transition={{ duration: 0.6, ease: "easeOut" }}
			>
				<svg viewBox="0 0 60 60" className="h-12 w-12 opacity-80">
					<g stroke="currentColor" strokeWidth="1" fill="none" className="hud-line-faint">
						<path d="M14 46 L52 46" />
						<path d="M14 46 L2 56" />
						<path d="M14 46 L14 6" />
					</g>
					<g fill="currentColor" className="hud-line-faint">
						<path d="M52 46 l-5 -2.4 v4.8 z" />
						<path d="M14 6 l-2.4 5 h4.8 z" />
					</g>
				</svg>
				<div className="space-y-0.5 text-[10px] leading-tight tracking-widest uppercase">
					<div style={{ color: "var(--color-ink-faint)" }}>x &#183; PC1</div>
					<div style={{ color: "var(--color-ink-faint)" }}>z &#183; PC2</div>
					<div style={{ color: "var(--color-signal)" }}>y &#183; log-odds</div>
				</div>
			</motion.div>

			{/* Readouts, bottom-right. Bottom-anchored so they can never reach the
			    subtitle no matter how the viewport is shaped. */}
			<div className="absolute right-6 bottom-6 hidden max-w-[46vw] flex-col items-end gap-1 sm:flex">
				{rows.map(([k, v]) => (
					<motion.div
						key={k + v}
						initial={{ opacity: 0, x: 10 }}
						animate={{ opacity: 1, x: 0 }}
						transition={{ duration: 0.45, ease: "easeOut" }}
						className="flex items-baseline gap-2 text-[11px]"
					>
						<span className="tracking-widest uppercase" style={{ color: "var(--color-ink-faint)" }}>
							{k}
						</span>
						<span className="tabular" style={{ color: "var(--color-ink-dim)" }}>
							{v}
						</span>
					</motion.div>
				))}
			</div>
		</div>
	)
}
