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