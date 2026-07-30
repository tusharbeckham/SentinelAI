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