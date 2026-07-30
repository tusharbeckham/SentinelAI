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

/** Ground-truth families, in the colour order the scene uses. */
const LEGEND: Array<{ k: string; c: string }> = [
	{ k: "benign", c: "#39414f" },
	{ k: "brute_force", c: "#e97366" },
	{ k: "dns_tunnel", c: "#bf8eda" },
	{ k: "dos", c: "#de9255" },
	{ k: "exfil", c: "#e0b15a" },
	{ k: "lateral", c: "#5e9fe8" },
	{ k: "portscan", c: "#72bc8f" },
]

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
		["p rank 1", "0.9866"],
	],
	[
		["threshold", "0.6303"],
		["above plane", "49 = 39 true + 10 false"],
		["missed", "20"],
		["precision / recall", "0.796 / 0.661"],
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
			{/* Corner brackets -- a frame, not a dial. Static, so they never read
			    as spinning decoration competing with the scene. */}
			<motion.div
				className="absolute inset-6 hidden md:block"
				initial={false}
				animate={{ opacity: on ? 0.5 : 0.16 }}
				transition={{ duration: 0.7, ease: "easeOut" }}
			>
				{[
					"left-0 top-0 border-l border-t",
					"right-0 top-0 border-r border-t",
					"left-0 bottom-0 border-l border-b",
					"right-0 bottom-0 border-r border-b",
				].map((pos) => (
					<span key={pos} className={cn("hud-line absolute h-5 w-5", pos)} />
				))}
			</motion.div>

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
