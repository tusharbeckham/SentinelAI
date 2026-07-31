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
import { ShaderPass } from "three/examples/jsm/postprocessing/ShaderPass.js"
import { motion } from "motion/react"
import { useReducedMotion } from "@/components/anime"
import { cn } from "@/lib/cn"
import { HeroHud } from "@/components/herohud"
import type { Bundle, LiveStatus } from "@/lib/data"

const CANVAS = 0x0a0b0d
const WATCH = 0xde9255
const ALARM = 0xe97366
/* Haze tints. Same two hues the palette uses for signal and graph, kept as
   named constants so the scene never drifts away from the design tokens. */
const SIGNAL = 0x5e9fe8
const GRAPH = 0xbf8eda

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
const ACT_BOUNDARIES = [0.17, 0.3, 0.44, 0.58, 0.73, 0.86]

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
		/*
		 * Final grade. Bloom alone looks synthetic; a camera adds aberration,
		 * falloff and noise. This pass also owns both transitions, so the fade to
		 * and from black happens after everything else is composited.
		 */
		const grade = new ShaderPass({
			uniforms: {
				tDiffuse: { value: null },
				uTime: { value: 0 },
				uFade: { value: 0 },
				uOutro: { value: 0 },
				uRes: { value: new THREE.Vector2(1, 1) },
			},
			vertexShader: [
				"varying vec2 vUv;",
				"void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
			].join("\n"),
			fragmentShader: [
				"uniform sampler2D tDiffuse; uniform float uTime; uniform float uFade;",
				"uniform float uOutro; uniform vec2 uRes;",
				"varying vec2 vUv;",
				"float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }",
				"void main() {",
				"  vec2 uv = vUv;",
				"  vec2 off = uv - vec2(0.5);",
				"  float rr = dot(off, off);",
				"  /* Lateral chromatic aberration: real lenses fail to focus every wavelength",
				"     on one plane, and the error grows with distance from the axis. Scaling by",
				"     r^2 keeps the centre clean and only smears the extreme corners. */",
				"  float ca = (0.0016 + uOutro * 0.0075) * rr;",
				"  vec3 c;",
				"  c.r = texture2D(tDiffuse, uv - off * ca).r;",
				"  c.g = texture2D(tDiffuse, uv).g;",
				"  c.b = texture2D(tDiffuse, uv + off * ca).b;",
				"  /* Vignette, cos^4-ish rather than a hard ring. */",
				"  float vig = smoothstep(1.05, 0.28, length(off) * 1.42);",
				"  c *= mix(0.55, 1.0, vig);",
				"  /* Animated grain, scaled by luminance so it lives in the shadows where a",
				"     sensor actually shows noise, instead of speckling the bright points. */",
				"  float lum = dot(c, vec3(0.2126, 0.7152, 0.0722));",
				"  float n = hash(uv * uRes + fract(uTime) * 91.7) - 0.5;",
				"  c += n * 0.030 * (1.0 - smoothstep(0.0, 0.55, lum));",
				"  c *= (1.0 - uOutro * 0.92);",
				"  gl_FragColor = vec4(c, 1.0);",
				"}",
			].join("\n"),
		})
		composer.addPass(grade)
		composer.addPass(new OutputPass())

		const world = new THREE.Group()
		scene.add(world)

		const fx = { settle: 0, lift: 0, ignite: 0, fold: 0, axis: 0, fuse: 0, cut: 0, respond: 0, focus: 0, spin: 0, size: 2.05, intro: 0, outro: 0, enter: 0 }
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
				uIntro: { value: 0 },
				uOutro: { value: 0 },
			},
			vertexShader: [
				"uniform float uSettle; uniform float uLift; uniform float uIgnite;",
				"uniform float uThresh; uniform float uSize; uniform float uTime;",
				"uniform float uFocus; uniform float uDpr; uniform float uIntro; uniform float uOutro;",
				"attribute vec3 aScatter; attribute vec3 aColor; attribute float aP; attribute float aRank;",
				"attribute vec3 aGCol;",
				"attribute float aMag; attribute float aTemp;",
				"varying vec3 vColor; varying float vP; varying float vAlert; varying float vRank;",
				"varying float vDepth; varying float vTw; varying float vFade;",
				"varying float vMag; varying float vTemp;",
				"void main() {",
				"  vec3 target = position;",
				"  target.y *= uLift;",
				"  /* aScatter holds the galaxy. uSettle is 0 before any scroll, so the",
				"     hero opens on a fully formed spiral with no entrance animation,",
				"     then every star flies to its true embedding position on scroll. */",
				"  float arrive = 1.0;",
				"  float warp = uSettle * (1.0 - uSettle) * 4.0;",
				"  vec3 p = mix(aScatter, target, smoothstep(0.0, 1.0, uSettle));",
				"  p.y += warp * sin(aP * 31.0 + aRank * 5.0) * 1.6;",
				"  float ph = aP * 43.0 + aRank * 7.0;",
				"  p += vec3(sin(uTime * 0.35 + ph), sin(uTime * 0.29 + ph * 1.7), cos(uTime * 0.31 + ph)) * 0.055;",
				"  /* Outro: the field exhales upward and thins, handing off to the page. */",
				"  p.y += uOutro * (5.0 + aP * 9.0);",
				"  p.xz *= 1.0 + uOutro * 0.22;",
				"  float above = step(uThresh * uLift, target.y);",
				"  vAlert = above * uIgnite;",
				"  vColor = mix(aGCol, aColor, smoothstep(0.15, 0.95, uSettle)); vP = aP; vRank = aRank;",
				"  vMag = aMag; vTemp = aTemp;",
				"  vTw = 0.82 + 0.18 * sin(uTime * 1.7 + ph * 2.3);",
				"  vFade = arrive * (1.0 - uOutro);",
				"  vec4 mv = modelViewMatrix * vec4(p, 1.0);",
				"  vDepth = -mv.z;",
				"  float grow = 1.0 + vAlert * 1.9 + aRank * uFocus * 5.0;",
				"  gl_PointSize = uSize * uDpr * grow * aMag * (46.0 / max(-mv.z, 0.6)) * (0.35 + 0.65 * arrive);",
				"  gl_Position = projectionMatrix * mv;",
				"}",
			].join("\n"),
			fragmentShader: [
				"uniform float uOutro;",
				"varying vec3 vColor; varying float vP; varying float vAlert; varying float vRank;",
				"varying float vDepth; varying float vTw; varying float vFade;",
				"varying float vMag; varying float vTemp;",
				"void main() {",
				"  vec2 d = gl_PointCoord - vec2(0.5);",
				"  float r = length(d);",
				"  /* A chip, not a star. anime.js reads as crisp because its elements have",
				"     real edges; gaussian blobs read as dust, which is what looked cheap.",
				"     Square with the top-right corner cut, as an SDF, antialiased with",
				"     fwidth rather than discard -- discard is exactly what produces the",
				"     stair-stepped edge (three.js forum, sharp particle edges). */",
				"  vec2 ad = abs(d);",
				"  float sd = max(max(ad.x, ad.y) - 0.34, dot(d, vec2(0.7071, -0.7071)) - 0.20);",
				"  float w = fwidth(sd) * 1.1 + 1e-5;",
				"  float chip = 1.0 - smoothstep(-w, w, sd);",
				"  /* Thin inner outline, so the glyph still reads as a drawn object when it",
				"     is only a few pixels across. */",
				"  float edge = (1.0 - smoothstep(-w, w, abs(sd + 0.055) - 0.026)) * 0.85;",
				"  /* Alerts get a ring, not a bloom smear: it survives at small size and it",
				"     does not bleed into its neighbours. */",
				"  float ring = (1.0 - smoothstep(-w, w, abs(r - 0.46) - 0.020)) * vAlert;",
				"  vec3 tint = mix(vec3(0.78, 0.86, 1.00), vec3(1.06, 0.98, 0.86), vTemp * 0.5 + 0.5);",
				"  vec3 c = vColor * tint * (0.85 + 0.80 * vP) * vTw;",
				"  c += vColor * edge * 0.9;",
				"  c = mix(c, c * 3.2 + vec3(0.24, 0.10, 0.04), vAlert);",
				"  c += vColor * vRank * 2.4;",
				"  c += vec3(1.00, 0.55, 0.42) * ring * 1.6;",
				"  float a = (chip + edge * 0.6 + ring) * (0.80 + 0.35 * vP + vAlert * 0.4);",
				"  a *= atmo * vFade * (1.0 - uOutro * 0.85);",
				"  gl_FragColor = vec4(c, a);",
				"}",
			].join("\n"),
		})

		/* Rank-1 marker: a caged point, so the eye can find h002 immediately. */
		const marker = new THREE.Group()
		const ringA = new THREE.Mesh(
			new THREE.TorusGeometry(0.85, 0.012, 8, 96),
			new THREE.MeshBasicMaterial({ color: hdr(ALARM, 2.6), transparent: true, opacity: 0 }),
		)
		ringA.rotation.x = -Math.PI / 2
		const ringB = new THREE.Mesh(
			new THREE.TorusGeometry(0.55, 0.01, 8, 96),
			new THREE.MeshBasicMaterial({ color: hdr(ALARM, 2.2), transparent: true, opacity: 0 }),
		)
		marker.add(ringA, ringB)
		marker.visible = false
		world.add(marker)

		/*
		 * THE BOUNDARY. One object, not a field.
		 *
		 * A novelty detector learns a region of normal behaviour. Benign traffic
		 * lives inside it; anomalies fall outside. The threshold is therefore not
		 * a number bolted onto the picture -- it is the surface itself.
		 *
		 * Built at init and added to the world unconditionally. Every earlier
		 * version of this model was constructed inside the corpus fetch callback,
		 * so one slow or failed request left the canvas completely empty with no
		 * way to tell from the page which had happened. The hull does not depend
		 * on the data load at all.
		 *
		 * Icosahedron rather than a sphere: facets give a readable silhouette and
		 * real creases for the edge pass to find. Fresnel only, since there are no
		 * lights in this scene, so the body reads as smoked glass lit from behind.
		 */
		const hullGeo = new THREE.IcosahedronGeometry(7.4, 3)
		const hullMat = new THREE.ShaderMaterial({
			transparent: true,
			depthWrite: false,
			side: THREE.DoubleSide,
			blending: THREE.AdditiveBlending,
			uniforms: {
				uTime: { value: 0 },
				uOpen: { value: 0 },
				uOpacity: { value: 0 },
				uFold: { value: 0 },
				uAxis: { value: 0 },
				uCut: { value: 0 },
				uCutY: { value: 0 },
				uRespond: { value: 0 },
				uRim: { value: new THREE.Color(SIGNAL) },
				uWarn: { value: new THREE.Color(WATCH) },
			},
			vertexShader: [
				"uniform float uTime; uniform float uOpen; uniform float uFold;",
				"uniform float uAxis; uniform float uRespond;",
				"varying vec3 vN; varying vec3 vView; varying float vY;",
				"void main() {",
				"  vec3 n = normalize(normal);",
				"  float b = sin(uTime * 0.45 + position.x * 0.62 + position.y * 0.83);",
				"  vec3 p = position + n * uOpen * (0.85 + 0.45 * b);",
				"  float fold = uFold * (1.0 - uAxis);",
				"  p.y *= mix(1.0, 0.13, fold);",
				"  p.xz *= mix(1.0, 1.34, fold);",
				"  float sk = 1.0 + 0.42 * uAxis * (p.y / 7.4);",
				"  p.y *= mix(1.0, 2.35, uAxis);",
				"  p.xz *= mix(1.0, 0.74, uAxis) * sk;",
				"  p *= 1.0 - uRespond * 0.42;",
				"  vY = p.y;",
				"  vec4 mv = modelViewMatrix * vec4(p, 1.0);",
				"  vN = normalize(normalMatrix * n);",
				"  vView = normalize(-mv.xyz);",
				"  gl_Position = projectionMatrix * mv;",
				"}",
			].join("\n"),
			fragmentShader: [
				"uniform vec3 uRim; uniform vec3 uWarn; uniform float uOpacity;",
				"uniform float uOpen; uniform float uCut; uniform float uCutY;",
				"uniform float uRespond;",
				"varying vec3 vN; varying vec3 vView; varying float vY;",
				"void main() {",
				"  float f = 1.0 - abs(dot(normalize(vN), normalize(vView)));",
				"  float rim = pow(f, 2.3);",
				"  float core = pow(f, 6.5);",
				"  vec3 c = uRim * (rim * 1.25 + core * 2.40);",
				"  c += vec3(0.10, 0.13, 0.18) * (1.0 - f) * 0.55;",
				"  float d = vY - uCutY;",
				"  float band = exp(-d * d * 4.5) * uCut;",
				"  float above = smoothstep(-0.30, 0.30, d) * uCut;",
				"  c = mix(c, uWarn * 1.9, above * 0.50);",
				"  c += uWarn * band * 2.60;",
				"  c *= 1.0 - uRespond * 0.55;",
				"  float a = (rim * 0.50 + core * 0.55 + 0.045) * uOpacity;",
				"  a *= 1.0 - uOpen * 0.35;",
				"  a += band * 0.30 * uOpacity;",
				"  gl_FragColor = vec4(c, a);",
				"}",
			].join("\n"),
		})
		const hull = new THREE.Mesh(hullGeo, hullMat)
		hull.frustumCulled = false
		world.add(hull)

		/* The creases, as real geometry. A line segment cannot go soft the way a
		   point sprite does, which is where every previous hero lost its edge. */
		const edgeMat = new THREE.LineBasicMaterial({
			color: new THREE.Color(SIGNAL),
			transparent: true,
			opacity: 0,
			depthWrite: false,
			blending: THREE.AdditiveBlending,
		})
		const edgeGeo = new THREE.EdgesGeometry(hullGeo, 14)
		const edges = new THREE.LineSegments(edgeGeo, edgeMat)
		edges.frustumCulled = false
		world.add(edges)

		/*
		 * ACT 04 -- FUSION, drawn as three converging boundaries.
		 *
		 * Each leg of the stacker learns its own notion of normal, so each leg
		 * gets its own hull. They arrive apart, sized by their fitted weights
		 * (0.3425 isolation forest, 1.1009 GBDT, 0.1135 graph), and collapse
		 * onto the single boundary the logistic layer actually ships. GBDT is
		 * visibly the largest because it visibly dominates the coefficients.
		 * That replaces the floor grid: the arithmetic, drawn.
		 */
		const LEGS = [
			{ color: SIGNAL, weight: 0.3425, offset: -9.5 },
			{ color: GRAPH, weight: 1.1009, offset: 0.0 },
			{ color: WATCH, weight: 0.1135, offset: 9.5 },
		]
		const ghosts = LEGS.map((leg) => {
			const gm = new THREE.LineBasicMaterial({
				color: new THREE.Color(leg.color),
				transparent: true,
				opacity: 0,
				depthWrite: false,
				blending: THREE.AdditiveBlending,
			})
			const go = new THREE.LineSegments(edgeGeo, gm)
			go.frustumCulled = false
			go.visible = false
			world.add(go)
			return { obj: go, mat: gm, leg }
		})

		/*
		 * ACT 05 -- the threshold, cut through the surface instead of laid
		 * under it as a floor. The decision boundary is not a piece of scenery
		 * the object stands on; it is a line across the object itself, with
		 * everything above it firing and everything below it missed.
		 */
		const cutMat = new THREE.MeshBasicMaterial({
			color: hdr(WATCH, 2.4),
			transparent: true,
			opacity: 0,
			depthWrite: false,
			blending: THREE.AdditiveBlending,
		})
		const cutRing = new THREE.Mesh(new THREE.TorusGeometry(7.4, 0.026, 8, 160), cutMat)
		cutRing.rotation.x = -Math.PI / 2
		cutRing.frustumCulled = false
		world.add(cutRing)

		/* ACT 06 -- containment, as a shockwave leaving the alert and closing. */
		const waveMat = new THREE.MeshBasicMaterial({
			color: hdr(ALARM, 2.2),
			transparent: true,
			opacity: 0,
			depthWrite: false,
			blending: THREE.AdditiveBlending,
		})
		const wave = new THREE.Mesh(new THREE.TorusGeometry(1, 0.018, 8, 128), waveMat)
		wave.rotation.x = -Math.PI / 2
		wave.frustumCulled = false
		world.add(wave)



		/* Load the projected corpus. Until it arrives the hero simply stays dark. */
		let loaded = false
		const ac = new AbortController()
		fetch(new URL("data/embedding.json", document.baseURI).toString(), { signal: ac.signal })
			.then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
			.then((emb: Embedding) => {
				if (disposed) return
				const n = emb.n
				thresholdY = emb.thresholdY
				pointMat.uniforms.uThresh.value = thresholdY

				const pos = new Float32Array(emb.pos)
				const scatter = new Float32Array(n * 3)
				const color = new Float32Array(n * 3)
				const pArr = new Float32Array(n)
				const rank = new Float32Array(n)
				const mag = new Float32Array(n)
				const temp = new Float32Array(n)
				const gcol = new Float32Array(n * 3)
				/* Lattice dimensions. The grid stands UPRIGHT in the camera plane rather
				   than lying flat on the floor. Lying flat was the bug: the camera sits at
				   y=3.2 looking at the origin, roughly five degrees above a ground plane,
				   so a flat sheet projected to a one-pixel hairline and the whole model was
				   invisible. Sized to overfill a 34-unit-distant 50deg frame. */
				const latCols = Math.ceil(Math.sqrt(n * 1.9))
				const latRows = Math.ceil(n / latCols)
				const latStepX = 56 / latCols
				const latStepY = 29 / latRows
				const rnd = lcg(7)
				const tmp = new THREE.Color()
				const gTmp = new THREE.Color()
				const gTmp2 = new THREE.Color()

				for (let i = 0; i < n; i++) {
					/* A perfectly regular lattice: one cell per held-out window, dead flat
					   and evenly spaced. Before any model touches it the corpus is an
					   undifferentiated table, and a grid is the honest picture of a table.
					   Order is also what makes the deformation legible -- a cloud morphing
					   into a cloud reads as noise; a lattice collapsing does not. That is
					   the property worth taking from anime.js, not the lens shape. */
					const col = i % latCols
					const row = Math.floor(i / latCols)
					/* A hair of jitter only, to kill the moire the eye gets from a pixel-exact
					   grid at glancing angles. Not enough to read as randomness. */
					scatter[i * 3] = (col - (latCols - 1) / 2) * latStepX + (rnd() - 0.5) * 0.04
					scatter[i * 3 + 1] = ((latRows - 1) / 2 - row) * latStepY + (rnd() - 0.5) * 0.04
					scatter[i * 3 + 2] = (rnd() - 0.5) * 0.06

					/* Monochrome steel. Colour is held back so it can mean something later:
					   ground-truth family only arrives with the morph, alarm only above the
					   threshold. The slow shade wave keeps the grid from reading as print. */
					const shade = 0.72 + 0.28 * Math.abs(Math.sin(row * 0.07 + col * 0.05))
					gTmp.setHex(0x9fb4cc)
					gTmp2.setHex(0x46536a)
					gTmp.lerp(gTmp2, 1 - shade)
					gcol[i * 3] = gTmp.r
					gcol[i * 3 + 1] = gTmp.g
					gcol[i * 3 + 2] = gTmp.b

					tmp.setHex(FAMILY_COLOR[emb.fam[i]] ?? FAMILY_COLOR[0])
					color[i * 3] = tmp.r
					color[i * 3 + 1] = tmp.g
					color[i * 3 + 2] = tmp.b
					pArr[i] = emb.p[i]
					rank[i] = i === emb.rank1 ? 1 : 0
					/* Apparent magnitude follows a power law in any real star field: a
					   handful of bright anchors and a long tail of faint ones. Uniform
					   sizing is precisely what made this read as flat pixel dust. */
					mag[i] = 0.35 + Math.pow(rnd(), 3.4) * 3.2
					temp[i] = rnd() * 2 - 1
				}

				const geo = new THREE.BufferGeometry()
				geo.setAttribute("position", new THREE.BufferAttribute(pos, 3))
				geo.setAttribute("aScatter", new THREE.BufferAttribute(scatter, 3))
				geo.setAttribute("aColor", new THREE.BufferAttribute(color, 3))
				geo.setAttribute("aP", new THREE.BufferAttribute(pArr, 1))
				geo.setAttribute("aRank", new THREE.BufferAttribute(rank, 1))
				geo.setAttribute("aMag", new THREE.BufferAttribute(mag, 1))
				geo.setAttribute("aTemp", new THREE.BufferAttribute(temp, 1))
				geo.setAttribute("aGCol", new THREE.BufferAttribute(gcol, 3))
				geo.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 0, 0), 90)

				const pts = new THREE.Points(geo, pointMat)
				pts.frustumCulled = false
				world.add(pts)

				marker.position.set(pos[emb.rank1 * 3], pos[emb.rank1 * 3 + 1], pos[emb.rank1 * 3 + 2])
				marker.visible = true
				loaded = true
			})
			.catch(() => {
				if (!disposed) setFailed(true)
			})

		/*
		 * Choreography reads the native scroll position directly instead of going
		 * through a timeline library. The hero has to stay exactly in step with
		 * the scrollbar -- no easing lag, no hijacked wheel -- and sampling the
		 * section rect each frame is both simpler and impossible to desynchronise.
		 */
		const seg = (p: number, a: number, b: number) => clamp01((p - a) / Math.max(b - a, 1e-6))
		const smooth = (t: number) => t * t * (3 - 2 * t)
		const outCubic = (t: number) => 1 - Math.pow(1 - t, 3)

		const CAM: Array<{ p: number; pos: [number, number, number]; look: [number, number, number] }> = [
			{ p: 0.0, pos: [0, 3.2, 34], look: [0, 0, 0] },
			{ p: 0.18, pos: [6.5, 2.2, 24], look: [0, 0, 0] },
			{ p: 0.36, pos: [-10, 1.1, 16], look: [0, 0.4, 0] },
			{ p: 0.54, pos: [-4, 7.5, 19], look: [0, 1.2, 0] },
			{ p: 0.72, pos: [9, 5.2, 18], look: [0, 1.6, 0] },
			{ p: 0.88, pos: [2.5, 3.4, 12], look: [0, 1.9, 0] },
			{ p: 1.0, pos: [0.8, 2.6, 9.5], look: [0, 2.0, 0] },
		]
		const camPos = new THREE.Vector3(0, 3.2, 34)
		const camLook = new THREE.Vector3(0, 0, 0)

		function sampleCam(p: number) {
			let i = 0
			while (i < CAM.length - 2 && p > CAM[i + 1].p) i++
			const a = CAM[i]
			const b = CAM[i + 1]
			const t = smooth(clamp01((p - a.p) / Math.max(b.p - a.p, 1e-6)))
			camPos.set(
				a.pos[0] + (b.pos[0] - a.pos[0]) * t,
				a.pos[1] + (b.pos[1] - a.pos[1]) * t,
				a.pos[2] + (b.pos[2] - a.pos[2]) * t,
			)
			camLook.set(
				a.look[0] + (b.look[0] - a.look[0]) * t,
				a.look[1] + (b.look[1] - a.look[1]) * t,
				a.look[2] + (b.look[2] - a.look[2]) * t,
			)
		}

		let progress = 0
		let currentAct = -1

		function readScroll() {
			const rect = section.getBoundingClientRect()
			const span = Math.max(rect.height - window.innerHeight, 1)
			progress = clamp01(-rect.top / span)
			/* Hand-off. The hero is gone by 0.075 and the model does not begin to
			   arrive until 0.1, so there is a hard seam between them, never a blend. */
			fx.enter = smooth(seg(progress, 0.1, 0.17))

			fx.settle = outCubic(seg(progress, 0.2, 0.34))

			/* The hero exits on its own, finished before anything else starts. */
			const exit = smooth(seg(progress, 0.02, 0.075))
			if (titleRef.current) {
				titleRef.current.style.opacity = String(1 - exit)
				titleRef.current.style.transform = "translateY(" + (-exit * 56).toFixed(2) + "px)"
				titleRef.current.style.visibility = exit >= 1 ? "hidden" : "visible"
			}
			fx.lift = smooth(seg(progress, 0.33, 0.58))
			/* One driver per act, each inset from its own boundary so the card
			   lands first and the object answers it, instead of both moving at
			   once and neither being read. */
			fx.fold = smooth(seg(progress, 0.31, 0.43))
			fx.axis = smooth(seg(progress, 0.45, 0.57))
			fx.fuse = smooth(seg(progress, 0.59, 0.72))
			fx.cut = smooth(seg(progress, 0.74, 0.85))
			fx.respond = smooth(seg(progress, 0.87, 0.95))
			fx.ignite = smooth(seg(progress, 0.66, 0.8))
			fx.focus = smooth(seg(progress, 0.84, 0.96))
			fx.spin = progress
			fx.size = 2.05 + fx.focus * 0.5
			/* The ending is a beat, not a blend. Act 06 finishes at 0.95 and then
			   nothing happens at all for two percent of the scroll -- the object
			   simply holds, lit. Only after that pause does it collapse and the
			   canvas cut out, hard, before the console. This mirrors the
			   0.075-0.10 seam on the way in: a gap, never a cross-fade. */
			fx.outro = smooth(seg(progress, 0.97, 1.0))

			const fade = smooth(seg(progress, 0.05, 0.13))

			if (probRef.current) {
				const k = smooth(seg(progress, 0.5, 0.64))
				probRef.current.textContent = (0.9866137 * k).toFixed(4)
			}

			let a = 0
			for (let i = 0; i < ACT_BOUNDARIES.length; i++) if (progress >= ACT_BOUNDARIES[i]) a = i + 1
			if (a !== currentAct) {
				currentAct = a
				setAct(a)
			}
		}

		readScroll()
		window.addEventListener("scroll", readScroll, { passive: true })

		const clock = new THREE.Clock()
		renderer.setAnimationLoop(() => {
			const t = clock.getElapsedTime()
			pointMat.uniforms.uTime.value = t
			pointMat.uniforms.uSettle.value = fx.settle
			pointMat.uniforms.uLift.value = fx.lift
			pointMat.uniforms.uIgnite.value = fx.ignite
			pointMat.uniforms.uFocus.value = fx.focus
			/* The entrance is a real move, not just a brightness ramp. Points grow
			   from a third of their size and the whole lattice scales up into the
			   frame across the same 0.10-0.17 window that uFade uses, so the model
			   arrives rather than simply becoming less dark. */
			pointMat.uniforms.uSize.value = fx.size * (0.34 + 0.66 * fx.enter)
			world.scale.setScalar((0.82 + 0.18 * fx.enter) * (1 + fx.outro * 0.10))
			/* Entry: a 1.9s condense from the shell, independent of scroll. */
			fx.intro = outCubic(clamp01((t - 0.12) / 1.9))
			pointMat.uniforms.uIntro.value = fx.intro
			pointMat.uniforms.uOutro.value = fx.outro
			grade.uniforms.uTime.value = t
			grade.uniforms.uFade.value = fx.intro * fx.enter
			/* The model is revealed by fading the whole canvas against the page.
			   Both are the CANVAS token, so no value of this opacity can produce a
			   visible edge. Nothing about the seam depends on shader arithmetic. */
			/* Hard cut-out. fx.outro does not begin until 0.97, so the object holds
			   lit through the pause after act 06 and then the canvas leaves in one
			   move. The console is never underneath a half-transparent scene. */
			mount.style.opacity = loaded ? String(fx.intro * fx.enter * (1 - fx.outro)) : "0"
			grade.uniforms.uOutro.value = fx.outro

			/* The idle yaw is gated on uSettle. At rest the lattice must face the
			   camera dead-on -- a drifting yaw would turn a flat wall edge-on within
			   twenty seconds of sitting on the page. It only starts once the grid has
			   begun collapsing into a volume, where depth is the point. */
			world.rotation.y = fx.settle * t * 0.035 + fx.spin * 1.15

			/* The Boundary. It fades up across the same 0.10-0.17 window as the rest
			   of the stage, opens as the corpus settles so the interior shows through
			   the facets, and collapses again on the outro. Its own slow tumble runs
			   independently of the world yaw so the silhouette never sits still. */
			hullMat.uniforms.uTime.value = t
			hullMat.uniforms.uOpen.value = fx.settle * 0.55 + fx.fuse * 0.22
			hullMat.uniforms.uOpacity.value = fx.enter * (1 - fx.outro)
			hullMat.uniforms.uFold.value = fx.fold
			hullMat.uniforms.uAxis.value = fx.axis
			hullMat.uniforms.uRespond.value = fx.respond

			/* Acts 02 and 03 are the two places the object should be working
			   hardest, so rotation rate is tied to f*(1-f) rather than to f.
			   That peaks mid-act and returns to rest at both ends, so the move
			   reads as a gesture with a beginning and an end rather than a
			   speed change that stops dead on a scroll boundary. */
			const foldSpin = fx.fold * (1 - fx.fold) * 4
			const axisSpin = fx.axis * (1 - fx.axis) * 4
			hull.rotation.y = t * (0.055 + foldSpin * 0.55 + axisSpin * 0.30)
			hull.rotation.x = Math.sin(t * 0.19) * 0.14 * (1 - fx.axis) + fx.axis * 0.10
			hull.rotation.z = foldSpin * 0.22 * Math.sin(t * 0.9)
			edges.rotation.copy(hull.rotation)
			const hullScale = (1 - fx.outro * 0.92) * (1 + foldSpin * 0.06)
			hull.scale.setScalar(hullScale)
			edges.scale.setScalar(hullScale)
			edgeMat.opacity =
				fx.enter * (1 - fx.outro) * (0.55 - fx.settle * 0.25 + fx.axis * 0.30 + fx.respond * 0.45)

			/* The three legs converge. Opacity uses the same f*(1-f) envelope as
			   the act rotations, so they fade in as they separate and are gone the
			   instant they land on the shipped boundary -- the merge is the point,
			   not the ghosts. They are hidden outside the act so two hundred extra
			   line segments are not drawn for the other five. */
			const fuseVis = fx.fuse > 0.001 && fx.fuse < 0.999
			for (const g of ghosts) {
				g.obj.visible = fuseVis
				if (!fuseVis) {
					g.mat.opacity = 0
					continue
				}
				const k = 1 - fx.fuse
				g.obj.scale.setScalar(hullScale * (0.55 + g.leg.weight * 0.62) * (1 + k * 0.35))
				g.obj.position.set(g.leg.offset * k, 0, 0)
				g.obj.rotation.copy(hull.rotation)
				g.obj.rotation.y += k * 1.1
				g.mat.opacity = Math.min(1, fx.fuse * (1 - fx.fuse) * 4) * 0.75
			}

			/* Height of the cut is read from the artifact threshold, then carried
			   up with the act-03 stretch so it stays on the same slice of the
			   surface as the geometry grows underneath it. */
			const cutY = (2.2 + thresholdY * 2.6) * (1 + fx.axis * 1.4)
			hullMat.uniforms.uCut.value = fx.cut * (1 - fx.respond * 0.6)
			hullMat.uniforms.uCutY.value = cutY
			cutRing.position.y = cutY
			cutRing.scale.setScalar(hullScale * (0.76 + 0.03 * Math.sin(t * 1.1)))
			cutMat.opacity = fx.cut * (1 - fx.respond * 0.7) * 0.9

			/* One ring, leaving h002 and snapping shut. sin(pi*x) opens and closes
			   it inside the act so the gesture completes rather than being cut off
			   by the scroll position. */
			wave.position.copy(marker.position)
			wave.scale.setScalar(0.4 + fx.respond * 9.0)
			waveMat.opacity = Math.sin(Math.PI * Math.min(1, fx.respond * 1.15)) * 0.85

			marker.scale.setScalar(0.6 + fx.focus * 1.5)
			marker.rotation.z = t * 0.6
			for (const child of marker.children) {
				const m = (child as THREE.Mesh).material as THREE.MeshBasicMaterial
				m.opacity = fx.focus
			}

			sampleCam(progress)
			camera.position.lerp(camPos, 0.085)
			camera.lookAt(camLook)
			composer.render()
		})

		const onResize = () => {
			const w = mount.clientWidth
			const h = Math.max(mount.clientHeight, 1)
			camera.aspect = w / h
			camera.updateProjectionMatrix()
			renderer.setSize(w, h)
			composer.setSize(w, h)
			bloom.setSize(w, h)
			pointMat.uniforms.uDpr.value = renderer.getPixelRatio()
			grade.uniforms.uRes.value.set(w * renderer.getPixelRatio(), h * renderer.getPixelRatio())
			readScroll()
		}
		window.addEventListener("resize", onResize)

		return () => {
			disposed = true
			ac.abort()
			renderer.setAnimationLoop(null)
			window.removeEventListener("scroll", readScroll)
			window.removeEventListener("resize", onResize)
			scene.traverse((o) => {
				const any = o as THREE.Mesh
				if (any.geometry) any.geometry.dispose()
				const mat = any.material as THREE.Material | THREE.Material[] | undefined
				if (Array.isArray(mat)) mat.forEach((m) => m.dispose())
				else if (mat) mat.dispose()
			})
			grade.material.dispose()
			composer.dispose()
			renderer.dispose()
			if (renderer.domElement.parentNode === mount) mount.removeChild(renderer.domElement)
		}
	}, [reduced])

	const showScene = !reduced && !failed

	/*
	 * Reduced motion (or no WebGL): the same story as a static page -- one
	 * composed title, then every act card stacked in order. Nothing is hidden
	 * behind an interaction the reader chose not to have.
	 */
	if (!showScene) {
		return (
			<section id="top" className="scroll-mt-24">
				<TitleBlock report={report} live={live} />
				<div className="mx-auto mt-12 grid max-w-3xl gap-4">
					{ACTS.map((a) => (
						<ActCard key={a.id} act={a} live probRef={probRef} prob="0.9866" />
					))}
				</div>
			</section>
		)
	}

	return (
		<section
			id="top"
			ref={sectionRef}
			className="relative left-1/2 h-[560vh] w-screen -translate-x-1/2 scroll-mt-24"
		>
				<div className="sticky top-0 h-screen overflow-hidden">
					{/* Stage one: the hero, alone. Nothing renders alongside it -- at this
					    point the grade pass is at zero, so the canvas underneath is literally
					    black, not a translucent layer sitting over the scene. */}
					<div
						ref={titleRef}
						className="absolute inset-0 z-20 flex items-center justify-center"
					>
						<TitleBlock report={report} live={live} compact />
					</div>

					{/* Instrument chrome: axis triad, readouts, ground-truth key. */}
					<HeroHud act={act} className="pointer-events-none absolute inset-0 z-10 h-full w-full" />
					{/* The Grid. Purely decorative: every fact is also in the HTML below. */}
					<div
						ref={mountRef}
						className="absolute inset-0"
						style={{ opacity: 0 }}
						role="img"
						aria-label="Animated 3D scatter plot of the 11,326 held-out test windows. Horizontal axes are the first two principal components of the 40 features; height is the model log-odds. Colour is the ground-truth attack family. As you scroll, the points settle from a raw stream into the feature manifold, rise into score space, and a horizontal threshold plane sweeps to 0.6303, leaving 49 points above it: 39 true detections and 10 false positives, with 20 attacks left below."
					/>


					{/* Acts 1-6 -- one card at a time, alternating edges. */}
					{ACTS.map((a, i) => (
						<div
							key={a.id}
							className={cn(
								'absolute top-1/2 w-[min(340px,82vw)] -translate-y-1/2 transition-all duration-[620ms] ease-[cubic-bezier(0.22,1,0.36,1)]',
								i % 2 === 0 ? 'left-[clamp(16px,6vw,96px)]' : 'right-[clamp(16px,6vw,96px)]',
								act === i + 1
									? 'translate-y-[-50%] scale-100 opacity-100 blur-none'
									: 'pointer-events-none translate-y-[calc(-50%+20px)] scale-[0.97] opacity-0 blur-[3px]',
							)}
						>
							<ActCard act={a} live={act === i + 1} probRef={probRef} />
						</div>
					))}

					{/* Progress rail */}
					{/* Act rail. Hidden at rest: before the first scroll the hero is just
					    the galaxy and the title, with no chrome competing with it. */}
					<div
						className="absolute right-4 top-1/2 flex -translate-y-1/2 flex-col gap-2 transition-opacity duration-500"
						style={{ opacity: act > 0 ? 1 : 0 }}
						aria-hidden
					>
						{ACTS.map((a, i) => (
							<span
								key={a.id}
								className="h-1.5 w-1.5 rounded-full transition-all duration-300"
								style={{
									backgroundColor: act === i + 1 ? a.color : 'var(--color-line-strong)',
									transform: act === i + 1 ? 'scale(1.6)' : undefined,
								}}
							/>
						))}
					</div>

					<a
						href="#explain"
						className="panel absolute bottom-5 left-1/2 -translate-x-1/2 px-3 py-1.5 text-[13px] text-ink-dim transition-colors hover:text-ink"
					>
						skip to the console &darr;
					</a>
				</div>
			</section>
	)
}

function TitleBlock({
	report,
	live,
	compact = false,
}: {
	report: Bundle['report']
	live: LiveStatus
	compact?: boolean
}) {
	/* Frozen copies of the corpus figures the hero flies through. */
	const stats = [
		{ k: 'flows', v: '563,619' },
		{ k: 'auth events', v: '49,535' },
		{ k: 'hosts', v: '40' },
		{ k: 'days observed', v: '4' },
	]
	return (
		<div className={cn('mx-auto w-full max-w-4xl px-6', compact && 'select-none')}>
			<div className="flex flex-col items-center text-center">
				{/*
				 * One DOM instance of every word, at full contrast. The gradient is
				 * on the fill, not a second stacked copy -- the mistake that made the
				 * earlier lens hero render four ghosted titles at once.
				 */}
				<h1 className="text-[clamp(2.75rem,7.6vw,5.5rem)]">
					<span className="block">
						<span className="bg-gradient-to-b from-white via-white to-[#8d99a9] bg-clip-text text-transparent">
							Sentinel
						</span>
						<span className="ai-gradient">AI</span>
					</span>
					<span className="mt-4 block text-[clamp(1.1rem,2.5vw,1.9rem)] font-normal leading-[1.2] tracking-[-0.02em] text-ink-dim">
						anomaly detection built around
						<span className="text-ink"> the analyst&rsquo;s budget</span>
					</span>
				</h1>

				<p className="mt-7 max-w-xl text-[15px] leading-relaxed text-ink-dim sm:text-base">
					Scroll to fly through the pipeline &mdash; raw telemetry to a contained alert.
					Every figure is read from the pipeline&rsquo;s own artifacts, including the ones
					that look bad.
				</p>

				<div className="mt-8 flex flex-wrap items-center justify-center gap-2.5">
					<a href="#explain" className="btn btn-primary">
						Read one alert end to end &rarr;
					</a>
					<a href="#sweep" className="btn">
						Budget curve
					</a>
				</div>
			</div>

			<div className="mt-11" />

			{/* Hairline-separated cells: one border, shared by four figures. */}
			<dl className="mt-7 grid grid-cols-2 gap-px overflow-hidden rounded-[10px] border border-line bg-line sm:grid-cols-4">
				{stats.map((s) => (
					<div key={s.k} className="bg-surface px-4 py-3.5 text-left">
						<dt className="kicker text-ink-faint">{s.k}</dt>
						<dd className="tabular mt-1.5 text-lg text-ink">{s.v}</dd>
					</div>
				))}
			</dl>

			<div className="mt-5 flex flex-wrap items-center justify-center gap-2 text-[13px] text-ink-faint">
				<span className="panel tabular px-2.5 py-1">build 2.7.0</span>
				<span className="panel tabular px-2.5 py-1">{report.features.count} features</span>
				<span className="panel tabular px-2.5 py-1">
					{report.runtime_seconds.toFixed(1)}s end-to-end
				</span>
				<LiveChip status={live} />
			</div>

			{compact ? (
				<div className="mt-10 flex flex-col items-center gap-2" aria-hidden>
					<span className="kicker text-ink-faint">scroll</span>
					<span className="h-9 w-px animate-pulse bg-gradient-to-b from-line-strong to-transparent" />
				</div>
			) : null}
		</div>
	)
}

function LiveChip({ status }: { status: LiveStatus }) {
	const map: Record<LiveStatus, { text: string; color: string }> = {
		unknown: { text: 'probing scorer', color: 'var(--color-ink-faint)' },
		online: { text: 'live scorer online', color: 'var(--color-safe)' },
		offline: { text: 'static artifacts only', color: 'var(--color-watch)' },
	}
	const v = map[status]
	return (
		<span className="panel inline-flex items-center gap-2 px-2.5 py-1">
			<motion.span
				className="h-2 w-2 rounded-full"
				style={{ backgroundColor: v.color }}
				animate={status === 'online' ? { opacity: [1, 0.35, 1] } : undefined}
				transition={{ duration: 2, repeat: Infinity }}
			/>
			<span style={{ color: v.color }}>{v.text}</span>
		</span>
	)
}

function ActCard({
	act,
	live,
	probRef,
	prob,
}: {
	act: Act
	live: boolean
	probRef?: RefObject<HTMLSpanElement | null>
	prob?: string
}) {
	return (
		<article
			className="panel border-l-2 p-5 backdrop-blur-sm"
			style={{ borderLeftColor: act.color, backgroundColor: 'color-mix(in srgb, var(--color-surface) 82%, transparent)' }}
		>
			<div className="flex items-baseline justify-between gap-3">
				<span className="text-[12px] font-medium tracking-widest text-ink-faint">
					ACT {act.n} / 06
				</span>
				<span
					className="h-1.5 w-1.5 rounded-full transition-opacity"
					style={{ backgroundColor: act.color, opacity: live ? 1 : 0.4 }}
				/>
			</div>
			<h3 className="mt-2 text-xl font-semibold" style={{ color: act.color }}>
				{act.name}
			</h3>
			<p className="mt-2 text-[15px] leading-relaxed text-ink-dim">{act.body}</p>
			{act.id === 'fusion' ? (
				<p className="tabular mt-3 text-2xl font-semibold text-ink">
					p = <span ref={probRef}>{prob ?? '0.0000'}</span>
				</p>
			) : null}
			<div className="mt-3 flex flex-wrap gap-1.5">
				{act.chips.map((c) => (
					<span key={c} className="tabular rounded-md bg-canvas px-2 py-1 text-[12px] text-ink-dim">
						{c}
					</span>
				))}
			</div>
		</article>
	)
}
