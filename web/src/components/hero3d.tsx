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
		color: "var(--color-alarm)",
		body: "One point stands highest: h002, rank 1 of 11,326. Policy-driven playbooks act on what crossed the gate, every decision hash-chained into the audit log.",
		chips: ["h002 \u00B7 p = 0.9866", "auto_contain", "chain valid"],
	},
]

/** Progress boundaries where acts 1..6 begin (act 0 is the title). */
const ACT_BOUNDARIES = [0.1, 0.26, 0.42, 0.58, 0.73, 0.86]

type Embedding = {
	n: number
	thresholdY: number
	rank1: number
	pos: number[]
	p: number[]
	fam: number[]
}

export function NetworkHero({ report, live }: { report: Bundle["report"]; live: LiveStatus }) {
	const reduced = useReducedMotion()
	const sectionRef = useRef<HTMLElement | null>(null)
	const mountRef = useRef<HTMLDivElement | null>(null)
	const probRef = useRef<HTMLSpanElement | null>(null)
	const titleRef = useRef<HTMLDivElement | null>(null)
	const [act, setAct] = useState(0)
	const [failed, setFailed] = useState(false)

	useEffect(() => {
		if (reduced) return
		const mount = mountRef.current
		const section = sectionRef.current
		if (!mount || !section) return

		let renderer: THREE.WebGLRenderer
		try {
			renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" })
		} catch {
			setFailed(true)
			return
		}
		renderer.setPixelRatio(Math.min(window.devicePixelRatio, mount.clientWidth < 768 ? 1.5 : 2))
		renderer.setSize(mount.clientWidth, Math.max(mount.clientHeight, 1))
		renderer.toneMapping = THREE.ACESFilmicToneMapping
		renderer.toneMappingExposure = 1.06
		renderer.outputColorSpace = THREE.SRGBColorSpace
		mount.appendChild(renderer.domElement)

		const scene = new THREE.Scene()
		scene.background = new THREE.Color(CANVAS)
		scene.fog = new THREE.FogExp2(CANVAS, 0.021)
		const camera = new THREE.PerspectiveCamera(50, mount.clientWidth / Math.max(mount.clientHeight, 1), 0.1, 260)
		camera.position.set(0, 3.2, 34)

		const composer = new EffectComposer(renderer)
		composer.addPass(new RenderPass(scene, camera))
		/*
		 * Bloom threshold 1.0 makes glow opt-in by authoring: a colour blooms only
		 * if a channel exceeds 1. Alerted points are pushed above 1 in the shader
		 * and benign points sit far below, so ignition reads as light rather than
		 * as a blur smeared over everything.
		 */
		const bloom = new UnrealBloomPass(
			new THREE.Vector2(mount.clientWidth, Math.max(mount.clientHeight, 1)),
			0.72,
			0.6,
			1,
		)
		composer.addPass(bloom)
		composer.addPass(new OutputPass())

		const world = new THREE.Group()
		scene.add(world)

		const fx = { settle: 0, lift: 0, ignite: 0, plane: 0, focus: 0, spin: 0, size: 2.05 }
		let thresholdY = 0.224
		let disposed = false

		const pointMat = new THREE.ShaderMaterial({
			transparent: true,
			depthWrite: false,
			blending: THREE.AdditiveBlending,
			uniforms: {
				uSettle: { value: 0 },
				uLift: { value: 0 },
				uIgnite: { value: 0 },
				uFocus: { value: 0 },
				uThresh: { value: thresholdY },
				uSize: { value: 2.05 },
				uTime: { value: 0 },
				uDpr: { value: renderer.getPixelRatio() },
			},
			vertexShader: [
				"uniform float uSettle; uniform float uLift; uniform float uIgnite;",
				"uniform float uThresh; uniform float uSize; uniform float uTime;",
				"uniform float uFocus; uniform float uDpr;",
				"attribute vec3 aScatter; attribute vec3 aColor; attribute float aP; attribute float aRank;",
				"varying vec3 vColor; varying float vP; varying float vAlert; varying float vRank;",
				"void main() {",
				"  vec3 target = position;",
				"  target.y *= uLift;",
				"  vec3 p = mix(aScatter, target, uSettle);",
				"  float ph = aP * 43.0 + aRank * 7.0;",
				"  p += vec3(sin(uTime * 0.35 + ph), sin(uTime * 0.29 + ph * 1.7), cos(uTime * 0.31 + ph)) * 0.055;",
				"  float above = step(uThresh * uLift, target.y);",
				"  vAlert = above * uIgnite;",
				"  vColor = aColor; vP = aP; vRank = aRank;",
				"  vec4 mv = modelViewMatrix * vec4(p, 1.0);",
				"  float grow = 1.0 + vAlert * 1.9 + aRank * uFocus * 5.0;",
				"  gl_PointSize = uSize * uDpr * grow * (46.0 / max(-mv.z, 0.6));",
				"  gl_Position = projectionMatrix * mv;",
				"}",
			].join("\n"),
			fragmentShader: [
				"varying vec3 vColor; varying float vP; varying float vAlert; varying float vRank;",
				"void main() {",
				"  vec2 d = gl_PointCoord - vec2(0.5);",
				"  float r = length(d);",
				"  if (r > 0.5) discard;",
				"  float core = smoothstep(0.5, 0.04, r);",
				"  float halo = smoothstep(0.5, 0.22, r) * 0.4;",
				"  vec3 c = vColor * (0.30 + 0.85 * vP);",
				"  c = mix(c, c * 3.6 + vec3(0.22, 0.12, 0.05), vAlert);",
				"  c += vColor * vRank * 2.4;",
