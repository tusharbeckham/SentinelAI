import { useMemo } from "react"

/*
 * THE GRATICULE -- the SVG half of the instrument.
 *
 * Division of labour, and it is not arbitrary. WebGL is good at light: glass,
 * metal, bloom, depth, things that need a lighting model. It is bad at
 * hairlines -- a one-pixel line inside a 3D scene crawls and shimmers at any
 * device pixel ratio, because it is being rasterised from a quad that never
 * lands on the pixel grid.
 *
 * SVG is the exact inverse. It cannot light anything, but a stroke is exact at
 * every zoom and every DPR, for free, forever.
 *
 * So the layers split the way a real optical instrument splits: the canvas
 * below is the image the instrument forms, and this layer is the graticule
 * etched onto the eyepiece. Critically, the graticule does NOT move with the
 * camera -- it is fixed to the observer, not to the scene. That single rule is
 * what stops an overlay from looking like a sticker.
 */

type Props = { act: number; className?: string }

const TICKS = 72
const CX = 800
const CY = 450

/* Readouts are keyed to the six acts so the graticule annotates what the
 * scene is doing rather than repeating the headline copy. */
const READOUTS: Array<{ mode: string; note: string }> = [
	{ mode: "ACQUIRE", note: "563,619 flows inbound" },
	{ mode: "BAND", note: "40 features / 6 bands" },
	{ mode: "RESOLVE", note: "3 detector legs live" },
	{ mode: "FUSE", note: "stacker weights applied" },
	{ mode: "STOP DOWN", note: "budget 50 alerts/day" },
	{ mode: "CONTAIN", note: "auto_contain armed" },
]

export function HeroHud({ act, className }: Props) {
	/* Tick geometry is deterministic, so it is built once and never rebuilt. */
	const ticks = useMemo(
		() =>
			Array.from({ length: TICKS }, (_, i) => {
				const a = (i / TICKS) * Math.PI * 2
				const major = i % 6 === 0
				const inner = major ? 232 : 244
				return {
					i,
					major,
					x1: CX + Math.cos(a) * inner,
					y1: CY + Math.sin(a) * inner,
					x2: CX + Math.cos(a) * 256,
					y2: CY + Math.sin(a) * 256,
				}
			}),
		[],
	)

	const r = READOUTS[Math.min(act, READOUTS.length - 1)]

	return (
		<svg
			className={className}
			viewBox="0 0 1600 900"
			preserveAspectRatio="xMidYMid slice"
			aria-hidden="true"
			focusable="false"
		>
			<defs>
				{/*
				  * Perlin turbulence displacing a straight edge is what makes the scan
				  * band read as heat shimmer rather than as a gradient rectangle.
				  * stitchTiles=noStitch matters: without it the noise field cannot be
				  * animated continuously without tearing at tile seams.
				  */}
				<filter id="sentinel-shimmer" x="-20%" y="-20%" width="140%" height="140%">
					<feTurbulence
						type="fractalNoise"
						baseFrequency="0.012 0.05"
						numOctaves="2"
						stitchTiles="noStitch"
						result="noise"
					>
						{/*
						  * Animating baseFrequency cannot loop -- the field is resampled
						  * every frame and jumps on repeat. Rotating hue on the noise is
						  * the standard trick: 0 to 360 is seamless by construction.
						  */}
					</feTurbulence>
					<feColorMatrix in="noise" type="hueRotate" values="0" result="spun">
						<animate
							attributeName="values"
							from="0"
							to="360"
							dur="9s"
							repeatCount="indefinite"
						/>
					</feColorMatrix>
					<feDisplacementMap
						in="SourceGraphic"
						in2="spun"
						scale="14"
						xChannelSelector="R"
						yChannelSelector="G"
					/>
				</filter>

				<linearGradient id="sentinel-scan" x1="0" y1="0" x2="0" y2="1">
					<stop offset="0%" stopColor="var(--color-signal)" stopOpacity="0" />
					<stop offset="50%" stopColor="var(--color-signal)" stopOpacity="0.5" />
					<stop offset="100%" stopColor="var(--color-signal)" stopOpacity="0" />
				</linearGradient>

				<radialGradient id="sentinel-fade" cx="0.5" cy="0.5" r="0.5">
					<stop offset="55%" stopColor="#ffffff" stopOpacity="1" />
					<stop offset="100%" stopColor="#ffffff" stopOpacity="0" />
				</radialGradient>
				<mask id="sentinel-vignette">
					<rect x="0" y="0" width="1600" height="900" fill="url(#sentinel-fade)" />
				</mask>
			</defs>

			<g mask="url(#sentinel-vignette)">
				{/* Frame brackets. Fixed to the eyepiece, so they never move. */}
				<g className="hud-line" strokeWidth="1.5" fill="none">
					<path d="M 96 172 L 96 116 L 152 116" />
					<path d="M 1504 172 L 1504 116 L 1448 116" />
					<path d="M 96 728 L 96 784 L 152 784" />
					<path d="M 1504 728 L 1504 784 L 1448 784" />
				</g>

				{/* Outer bearing ring: counter-rotating, dashed, deliberately slow. */}
				<g className="hud-spin" style={{ transformOrigin: "800px 450px" }}>
					<circle
						cx={CX}
						cy={CY}
						r="300"
						fill="none"
						className="hud-line-faint"
						strokeWidth="1"
						strokeDasharray="2 14"
					/>
				</g>
				<g className="hud-spin-rev" style={{ transformOrigin: "800px 450px" }}>
					<circle
						cx={CX}
						cy={CY}
						r="274"
						fill="none"
						className="hud-line-faint"
						strokeWidth="1"
						strokeDasharray="64 40"
					/>
				</g>

				{/* Graduated tick ring. Every sixth tick is major, as on a real dial. */}
				<g className="hud-line-faint">
					{ticks.map((t) => (
						<line
							key={t.i}
							x1={t.x1}
							y1={t.y1}
							x2={t.x2}
							y2={t.y2}
							strokeWidth={t.major ? 1.6 : 0.9}
							opacity={t.major ? 0.85 : 0.4}
						/>
					))}
				</g>

				{/* Crosshair, broken at the centre so it never crosses the subject. */}
				<g className="hud-line" strokeWidth="1">
					<line x1={CX - 214} y1={CY} x2={CX - 58} y2={CY} />
					<line x1={CX + 58} y1={CY} x2={CX + 214} y2={CY} />
					<line x1={CX} y1={CY - 214} x2={CX} y2={CY - 58} />
					<line x1={CX} y1={CY + 58} x2={CX} y2={CY + 214} />
				</g>

				{/* Focus corners: the four marks that close in as the iris stops down. */}
				<g
					className="hud-focus"
					data-act={act}
					style={{ transformOrigin: "800px 450px" }}
					strokeWidth="1.6"
					fill="none"
				>
					<path className="hud-line" d="M -168 -122 L -168 -168 L -122 -168" transform="translate(800 450)" />
					<path className="hud-line" d="M 168 -122 L 168 -168 L 122 -168" transform="translate(800 450)" />
					<path className="hud-line" d="M -168 122 L -168 168 L -122 168" transform="translate(800 450)" />
					<path className="hud-line" d="M 168 122 L 168 168 L 122 168" transform="translate(800 450)" />
				</g>

				{/* Sweeping scan band, displaced by the turbulence field. */}
				<g className="hud-scan" filter="url(#sentinel-shimmer)">
					<rect x="0" y="-60" width="1600" height="120" fill="url(#sentinel-scan)" />
				</g>

				{/* Instrument readout. Monospace, small, never competing with the title. */}
				<g className="hud-text" fontSize="15" letterSpacing="2.4">
					<text x="96" y="210">MODE {r.mode}</text>
					<text x="96" y="234" opacity="0.55">{r.note}</text>
					<text x="1504" y="210" textAnchor="end">
						ACT {String(Math.min(act, 5) + 1).padStart(2, "0")} / 06
					</text>
					<text x="1504" y="234" textAnchor="end" opacity="0.55">
						f/ {(1.4 + act * 0.9).toFixed(1)}
					</text>
				</g>
			</g>
		</svg>
	)
}
