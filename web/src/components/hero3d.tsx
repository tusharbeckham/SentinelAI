/**
 * NetworkHero -- the scroll-driven hero for the SentinelAI console.
 *
 * THE SUBJECT IS THE MODEL, NOT A MACHINE.
 *
 * Earlier versions rendered an imaginary optical instrument. It was pretty and
 * it was meaningless: nothing on screen came from the pipeline, so it could
 * have fronted any product at all. This version renders the one thing that is
 * actually ours -- the scored test set, drawn in the space the model sees.
 *
 * The geometry is literal:
 *   x, z  PCA of the 40 standardised features (PC1 22.5%, PC2 12.5% of variance)
 *   y     the model log-odds for that window
 *
 * Because the vertical axis IS the score, the operating threshold is not a
 * metaphor -- it is an exact horizontal plane at logit(0.6303). Everything
 * above it fires, everything below waits. Colour is ground truth. So the
 * central problem of the whole project becomes visible as geometry: the ten
 * benign points above the plane are the false positives an analyst pays for,
 * and the twenty coloured points stranded below it are the attacks we miss.
 *
 * The projection is recomputed from artifacts/scored_test_windows.csv into
 * public/data/embedding.json, and it reconciles with the reported operating
 * point exactly: 49 alerts, precision 0.7959, recall 0.6610.
 */
import { useEffect, useRef, useState, type RefObject } from "react"
import * as THREE from "three"
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js"
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js"
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js"
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js"
import { motion } from "motion/react"
import { useReducedMotion } from "@/components/anime"
import { cn } from "@/lib/cn"
import { HeroHud } from "@/components/herohud"
import type { Bundle, LiveStatus } from "@/lib/data"

const CANVAS = 0x0a0b0d
const WATCH = 0xde9255
const ALARM = 0xe97366

/*
 * Ground-truth palette, ordered to match families[] in embedding.json.
 * Benign is deliberately desaturated slate rather than a colour: 11,257 of the
 * 11,326 points are benign, so anything saturated would drown the six attack
 * families that are the only reason to look at this at all.
 */
const FAMILY_COLOR: number[] = [
	0x39414f, // benign
	0xe97366, // brute_force
	0xbf8eda, // dns_tunnel
	0xde9255, // dos
	0xe0b15a, // exfil
	0x5e9fe8, // lateral_movement
	0x72bc8f, // portscan
]

/** Deterministic PRNG (seed 7, like the pipeline) so the layout never changes. */
function lcg(seed: number) {
	let s = seed >>> 0
	return () => {
		s = (s * 1664525 + 1013904223) >>> 0
		return s / 4294967296
	}
}

const hdr = (hex: number, k: number) => new THREE.Color(hex).multiplyScalar(k)
const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v)

type Act = {
	n: string
	id: string
	name: string
	color: string
	body: string
	chips: string[]
}

const ACTS: Act[] = [
	{
		n: "01",
		id: "windows",
		name: "The corpus",
		color: "var(--color-signal)",
		body: "Every point is one host-window from the held-out test split -- five minutes of one machine, described by forty features. Nothing here is decorative; the cloud is the evaluation set.",
		chips: ["11,326 windows", "40 features", "300s each"],
	},
	{
		n: "02",
		id: "manifold",
		name: "Feature space",
		color: "var(--color-signal)",
		body: "Projected onto two principal components. Normal behaviour collapses into one dense sheet because most windows look alike -- which is exactly why this problem is hard, and why accuracy is a worthless metric here.",
		chips: ["PC1 22.5% var", "PC2 12.5% var", "11,257 benign"],
	},
	{
		n: "03",
		id: "lift",
		name: "The score axis",
		color: "var(--color-graph)",
		body: "Height becomes the model log-odds. The sheet lifts, and structure invisible in feature space appears: suspicion runs close to orthogonal to position.",
		chips: ["log-odds \u221210.40 to +4.30", "isolation forest + GBDT + graph"],
	},
	{
		n: "04",
		id: "fusion",
		name: "Fusion",
		color: "var(--color-safe)",
		body: "A calibrated logistic stacker sets that height -- the exact arithmetic the Explain section reproduces line by line.",
		chips: ["0.3425\u00B7z_if + 1.1009\u00B7z_gb + 0.1135\u00B7z_g \u2212 8.434"],
	},
	{
		n: "05",
		id: "threshold",
		name: "The plane",
		color: "var(--color-watch)",
		body: "The gate is set by the analyst budget, not by a flattering AUC. Look at what it costs: ten benign points sit above the plane, and twenty real attacks are stranded below it. That is the honest picture.",
		chips: ["threshold 0.6303", "49 fire", "39 true / 10 false", "20 missed"],
	},
	{
		n: "06",
		id: "response",
		name: "Response",
