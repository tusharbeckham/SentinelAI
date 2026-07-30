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
				"  float a = (core + halo) * (0.30 + 0.62 * vP + vAlert * 0.5);",
				"  gl_FragColor = vec4(c, a);",
				"}",
			].join("\n"),
		})

		/*
		 * The threshold plane, drawn as a shader grid rather than a solid quad so
		 * the cloud stays readable through it. An opaque plane would hide the
		 * false negatives underneath, and those are the entire point of showing it.
		 */
		const planeMat = new THREE.ShaderMaterial({
			transparent: true,
			depthWrite: false,
			side: THREE.DoubleSide,
			blending: THREE.AdditiveBlending,
			uniforms: { uOpacity: { value: 0 }, uTime: { value: 0 }, uTint: { value: new THREE.Color(WATCH) } },
			vertexShader: [
				"varying vec2 vUv;",
				"void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
			].join("\n"),
			fragmentShader: [
				"uniform float uOpacity; uniform float uTime; uniform vec3 uTint;",
				"varying vec2 vUv;",
				"void main() {",
				"  vec2 g = abs(fract(vUv * 26.0) - 0.5) / fwidth(vUv * 26.0);",
				"  float line = 1.0 - min(min(g.x, g.y), 1.0);",
				"  float rad = 1.0 - smoothstep(0.18, 0.5, length(vUv - vec2(0.5)));",
				"  float sweep = 0.55 + 0.45 * sin(uTime * 0.8 + vUv.x * 6.0);",
				"  float a = (line * 0.55 + 0.035) * rad * uOpacity * sweep;",
				"  gl_FragColor = vec4(uTint * (1.4 + line * 1.6), a);",
				"}",
			].join("\n"),
		})

		const plane = new THREE.Mesh(new THREE.PlaneGeometry(46, 46, 1, 1), planeMat)
		plane.rotation.x = -Math.PI / 2
		plane.visible = false
		world.add(plane)

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

		/* Load the projected corpus. Until it arrives the hero simply stays dark. */
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
				const rnd = lcg(7)
				const tmp = new THREE.Color()

				for (let i = 0; i < n; i++) {
					/* Act 1 opens as an unordered stream: a wide shell of raw records
					   with no structure, which then collapses into the real manifold. */
					const a = rnd() * Math.PI * 2
					const b = Math.acos(2 * rnd() - 1)
					const rr = 30 + rnd() * 46
					scatter[i * 3] = Math.sin(b) * Math.cos(a) * rr
					scatter[i * 3 + 1] = Math.cos(b) * rr * 0.42
					scatter[i * 3 + 2] = Math.sin(b) * Math.sin(a) * rr

					tmp.setHex(FAMILY_COLOR[emb.fam[i]] ?? FAMILY_COLOR[0])
					color[i * 3] = tmp.r
					color[i * 3 + 1] = tmp.g
					color[i * 3 + 2] = tmp.b
					pArr[i] = emb.p[i]
					rank[i] = i === emb.rank1 ? 1 : 0
				}

				const geo = new THREE.BufferGeometry()
				geo.setAttribute("position", new THREE.BufferAttribute(pos, 3))
				geo.setAttribute("aScatter", new THREE.BufferAttribute(scatter, 3))
				geo.setAttribute("aColor", new THREE.BufferAttribute(color, 3))
				geo.setAttribute("aP", new THREE.BufferAttribute(pArr, 1))
				geo.setAttribute("aRank", new THREE.BufferAttribute(rank, 1))
				geo.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 0, 0), 90)

				const pts = new THREE.Points(geo, pointMat)
				pts.frustumCulled = false
				world.add(pts)

				marker.position.set(pos[emb.rank1 * 3], pos[emb.rank1 * 3 + 1], pos[emb.rank1 * 3 + 2])
				marker.visible = true
				plane.visible = true
				mount.style.transition = "opacity 900ms ease"
				mount.style.opacity = "1"
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

			fx.settle = outCubic(seg(progress, 0.015, 0.19))
			fx.lift = smooth(seg(progress, 0.33, 0.58))
			fx.plane = smooth(seg(progress, 0.58, 0.7))
			fx.ignite = smooth(seg(progress, 0.66, 0.8))
			fx.focus = smooth(seg(progress, 0.84, 0.96))
			fx.spin = progress
			fx.size = 2.05 + fx.focus * 0.5

			const fade = smooth(seg(progress, 0.05, 0.13))
			if (titleRef.current) {
				titleRef.current.style.opacity = String(1 - fade)
				titleRef.current.style.transform = "translateY(" + (-fade * 40).toFixed(2) + "px)"
				titleRef.current.style.pointerEvents = fade > 0.6 ? "none" : "auto"
			}

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
			pointMat.uniforms.uSize.value = fx.size
			planeMat.uniforms.uTime.value = t
			planeMat.uniforms.uOpacity.value = fx.plane

			/* A slow idle yaw so the cloud has depth before the reader scrolls. */
			world.rotation.y = t * 0.035 + fx.spin * 1.15

			plane.position.y = thresholdY * fx.lift
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
		<section id="top" ref={sectionRef} className="relative scroll-mt-24 h-[560vh]">
			<div className="sticky top-0 h-screen overflow-hidden">
				{/* Handoff: the canvas fades out at the end of the pinned range, so
				    without a bridge the console below would simply appear. */}
				<div className="handoff-veil pointer-events-none absolute inset-x-0 bottom-0 z-30 h-44" />

				{/* Instrument chrome: axis triad, readouts, ground-truth key. */}
				<HeroHud act={act} className="pointer-events-none absolute inset-0 z-10 h-full w-full" />
				{/* The Grid. Purely decorative: every fact is also in the HTML below. */}
				<div
					ref={mountRef}
					className="canvas-feather absolute inset-0"
					style={{ opacity: 0 }}
					role="img"
					aria-label="Animated 3D scatter plot of the 11,326 held-out test windows. Horizontal axes are the first two principal components of the 40 features; height is the model log-odds. Colour is the ground-truth attack family. As you scroll, the points settle from a raw stream into the feature manifold, rise into score space, and a horizontal threshold plane sweeps to 0.6303, leaving 49 points above it: 39 true detections and 10 false positives, with 20 attacks left below."
				/>

				{/* Act 0 -- the title. One DOM instance, full contrast, crossfades out. */}
				<div
					ref={titleRef}
					className="absolute inset-0 flex items-center justify-center will-change-transform"
				>
					<div className="title-scrim absolute inset-0" aria-hidden />
					<div className="relative w-full">
						<TitleBlock report={report} live={live} compact />
					</div>
				</div>

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
				<div className="absolute right-4 top-1/2 flex -translate-y-1/2 flex-col gap-2" aria-hidden>
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

			<div className="rule-x mt-11" />

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
				<span className="panel tabular px-2.5 py-1">v1</span>
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
