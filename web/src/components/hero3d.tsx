/**
 * NetworkHero -- the scroll-driven 3D hero ("The Grid") for the SentinelAI console.
 * Design + choreography contract: web/HERO-3D-PLAN.md (six acts, one per pipeline
 * stage: telemetry -> features -> legs -> fusion -> threshold -> response).
 *
 * Rules inherited from the lens-hero failure:
 *  - exactly one DOM instance of every piece of text, always at full contrast;
 *  - motion lives in the WebGL scene and in card opacity/translate -- never on words;
 *  - the scrub follows the native scroll position (anime.js onScroll, sync 0.18);
 *    the wheel is never hijacked, the scrollbar always works;
 *  - every number in the callouts is a frozen copy of an artifacts/ value; the
 *    Explain section below remains the live, regenerated source of truth.
 */
import { useEffect, useRef, useState, type RefObject } from 'react'
import * as THREE from 'three'
import { createTimer, createTimeline, onScroll } from 'animejs'
import { motion } from 'motion/react'
import { ShinyText } from '@/components/bits'
import { useReducedMotion } from '@/components/anime'
import { cn } from '@/lib/cn'
import type { Bundle, LiveStatus } from '@/lib/data'

const CANVAS = 0x0a0b0d
const SIGNAL = 0x5e9fe8
const SAFE = 0x72bc8f
const WATCH = 0xde9255
const ALARM = 0xe97366
const GRAPH = 0xbf8eda

/** h002 -- the same entity the Explain section walks through. */
const ALERT_POS = new THREE.Vector3(2.2, 0.6, 1.8)

/** Deterministic PRNG (seed 7, like the pipeline) so the layout never changes. */
function lcg(seed: number) {
	let s = seed >>> 0
	return () => {
		s = (s * 1664525 + 1013904223) >>> 0
		return s / 4294967296
	}
}

type Layout = {
	nodes: THREE.Vector3[]
	swell: number[]
	edges: Array<[number, number]>
	alertEdges: Array<[number, number]>
}

function buildLayout(): Layout {
	const rand = lcg(7)
	const gauss = () => (rand() + rand() + rand() - 1.5) * 2.2
	const nodes: THREE.Vector3[] = [ALERT_POS.clone()]
	const clusters = [
		new THREE.Vector3(0, 0, 0),
		new THREE.Vector3(-9, 1, -4),
		new THREE.Vector3(8, -0.5, 6),
	]
	for (let i = 0; i < 39; i++) {
		const c = clusters[i % 3]
		nodes.push(new THREE.Vector3(c.x + gauss(), c.y + gauss() * 0.6, c.z + gauss()))
	}
	const swell = nodes.map(() => 0.3 + rand() * 0.7)
	const seen = new Set<string>()
	const edges: Array<[number, number]> = []
	const alertEdges: Array<[number, number]> = []
	const push = (a: number, b: number) => {
		if (a === b) return
		const key = a < b ? a + '-' + b : b + '-' + a
		if (seen.has(key)) return
		seen.add(key)
		if (a === 0 || b === 0) alertEdges.push([a, b])
		else edges.push([a, b])
	}
	nodes.forEach((p, i) => {
		const dists = nodes
			.map((q, j) => ({ j, d: p.distanceToSquared(q) }))
			.filter((e) => e.j !== i)
			.sort((x, y) => x.d - y.d)
		push(i, dists[0].j)
		push(i, dists[1].j)
	})
	for (let k = 0; k < 10; k++) {
		push(1 + Math.floor(rand() * 39), 1 + Math.floor(rand() * 39))
	}
	return { nodes, swell, edges, alertEdges }
}

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
		n: '01',
		id: 'telemetry',
		name: 'Telemetry',
		color: 'var(--color-signal)',
		body: 'Zeek-style flow records and Windows auth events, windowed every 300 seconds. The grid you are flying through is the dataset itself.',
		chips: ['563,619 flows', '49,535 auth events', '45,340 host-windows'],
	},
	{
		n: '02',
		id: 'features',
		name: 'Features',
		color: 'var(--color-signal)',
		body: 'Forty features per host-window across six bands. Nodes swell by how much signal their window carries; z-scores are measured against each entity\u2019s own baseline.',
		chips: ['40 features', '6 bands', 'per-entity baselines'],
	},
	{
		n: '03',
		id: 'legs',
		name: 'Detector legs',
		color: 'var(--color-graph)',
		body: 'Three detectors score every window alone. Their disagreement is the point \u2014 h002 ignites as all three start seeing something.',
		chips: ['isolation forest \u00b7 AP 0.298', 'gradient boosting \u00b7 AP 0.858', 'graph leg \u00b7 AP 0.039'],
	},
	{
		n: '04',
		id: 'fusion',
		name: 'Fusion',
		color: 'var(--color-safe)',
		body: 'A calibrated logistic stacker fuses the legs on standardised scores \u2014 the exact arithmetic the Explain section reproduces. Watch the probability land.',
		chips: ['0.3425\u00b7z_if + 1.1009\u00b7z_gb + 0.1135\u00b7z_g \u2212 8.434'],
	},
	{
		n: '05',
		id: 'threshold',
		name: 'Threshold',
		color: 'var(--color-watch)',
		body: 'The gate is set by the analyst\u2019s budget, not by a flattering AUC. Above the amber plane fires; below it waits. This alert outranked everything.',
		chips: ['threshold 0.6303', '49.8 alerts/day', 'recall 0.661', 'rank #1 of 11,326'],
	},
	{
		n: '06',
		id: 'response',
		name: 'Response',
		color: 'var(--color-alarm)',
		body: 'Policy-driven SOAR playbooks act on what crossed the gate \u2014 every decision hash-chained into the audit log.',
		chips: ['auto_contain \u2192 rate_limit_src_at_edge', '50 decisions', 'chain valid'],
	},
]

/** Progress boundaries where acts 1..6 begin (act 0 is the title). */
const ACT_BOUNDARIES = [0.1, 0.26, 0.42, 0.58, 0.73, 0.86]

export function NetworkHero({ report, live }: { report: Bundle['report']; live: LiveStatus }) {
	const reduced = useReducedMotion()
	const sectionRef = useRef<HTMLElement | null>(null)
	const mountRef = useRef<HTMLDivElement | null>(null)
	const probRef = useRef<HTMLSpanElement | null>(null)
	const [act, setAct] = useState(0)
	const [failed, setFailed] = useState(false)

	useEffect(() => {
		if (reduced) return
		const mount = mountRef.current
		const section = sectionRef.current
		if (!mount || !section) return

		let renderer: THREE.WebGLRenderer
		try {
			renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' })
		} catch {
			setFailed(true)
			return
		}
		renderer.setPixelRatio(Math.min(window.devicePixelRatio, mount.clientWidth < 768 ? 1.5 : 2))
		renderer.setSize(mount.clientWidth, mount.clientHeight)
		mount.appendChild(renderer.domElement)

		const scene = new THREE.Scene()
		scene.background = new THREE.Color(CANVAS)
		scene.fog = new THREE.FogExp2(CANVAS, 0.026)
		const camera = new THREE.PerspectiveCamera(50, mount.clientWidth / Math.max(mount.clientHeight, 1), 0.1, 120)
		camera.position.set(0, 6, 26)

		const world = new THREE.Group()
		scene.add(world)
		const layout = buildLayout()

		/* Host nodes: 39 instanced + the alert node kept separate so it can glow. */
		const nodeGeo = new THREE.IcosahedronGeometry(0.22, 1)
		const nodeMat = new THREE.MeshBasicMaterial({ color: SIGNAL, transparent: true, opacity: 0.85 })
		const inst = new THREE.InstancedMesh(nodeGeo, nodeMat, layout.nodes.length - 1)
		world.add(inst)
		const dummy = new THREE.Object3D()
		const alertNode = new THREE.Mesh(
			new THREE.IcosahedronGeometry(0.3, 1),
			new THREE.MeshBasicMaterial({ color: ALARM, transparent: true, opacity: 0.4 }),
		)
		alertNode.position.copy(ALERT_POS)
		world.add(alertNode)

		/* Flow edges, with h002's edges on their own material so they can sever. */
		const mkLines = (pairs: Array<[number, number]>, opacity: number) => {
			const pos = new Float32Array(pairs.length * 6)
			pairs.forEach(([a, b], i) => {
				const p = layout.nodes[a]
				const q = layout.nodes[b]
				pos.set([p.x, p.y, p.z, q.x, q.y, q.z], i * 6)
			})
			const g = new THREE.BufferGeometry()
			g.setAttribute('position', new THREE.BufferAttribute(pos, 3))
			const m = new THREE.LineBasicMaterial({
				color: SIGNAL,
				transparent: true,
				opacity,
				blending: THREE.AdditiveBlending,
				depthWrite: false,
			})
			const l = new THREE.LineSegments(g, m)
			world.add(l)
			return m
		}
		const edgeMat = mkLines(layout.edges, 0.28)
		const alertEdgeMat = mkLines(layout.alertEdges, 0.4)
		const allPairs = [...layout.edges, ...layout.alertEdges]

		/* Flow particles: 600 points walking the edges. */
		const P = 600
		const pPos = new Float32Array(P * 3)
		const pEdge = new Uint32Array(P)
		const pT = new Float32Array(P)
		const pSpeed = new Float32Array(P)
		const prand = lcg(99)
		for (let i = 0; i < P; i++) {
			pEdge[i] = Math.floor(prand() * allPairs.length)
			pT[i] = prand()
			pSpeed[i] = 0.1 + prand() * 0.25
		}
		const pGeo = new THREE.BufferGeometry()
		pGeo.setAttribute('position', new THREE.BufferAttribute(pPos, 3))
		const pMat = new THREE.PointsMaterial({
			color: SIGNAL,
			size: 0.09,
			transparent: true,
			opacity: 0.85,
			blending: THREE.AdditiveBlending,
			depthWrite: false,
		})
		const points = new THREE.Points(pGeo, pMat)
		world.add(points)

		/* Detector rings: one per leg, converging on h002 during fusion. */
		const rings = new THREE.Group()
		const ringDefs = [
			{ r: 6.2, c: SIGNAL, tilt: 1.15 },
			{ r: 7.4, c: SAFE, tilt: 0.75 },
			{ r: 8.6, c: GRAPH, tilt: 1.5 },
		]
		const ringMats: THREE.MeshBasicMaterial[] = []
		const ringMeshes: THREE.Mesh[] = []
		ringDefs.forEach((d) => {
			const m = new THREE.MeshBasicMaterial({ color: d.c, transparent: true, opacity: 0, depthWrite: false })
			ringMats.push(m)
			const t = new THREE.Mesh(new THREE.TorusGeometry(d.r, 0.02, 8, 128), m)
			t.rotation.x = d.tilt
			ringMeshes.push(t)
			rings.add(t)
		})
		const RING_HOME = new THREE.Vector3(0, 1.2, 0)
		rings.position.copy(RING_HOME)
		world.add(rings)

		/* Threshold plane: amber grid that slides in at the gate's height. */
		const grid = new THREE.GridHelper(26, 26, WATCH, WATCH)
		const gridMat = grid.material as THREE.LineBasicMaterial
		gridMat.transparent = true
		gridMat.opacity = 0
		gridMat.depthWrite = false
		grid.position.y = ALERT_POS.y + 2.2
		world.add(grid)

		/* Alert beam rising from h002 during fusion. */
		const beamGeo = new THREE.CylinderGeometry(0.03, 0.03, 1, 8, 1, true)
		beamGeo.translate(0, 0.5, 0)
		const beamMat = new THREE.MeshBasicMaterial({
			color: ALARM,
			transparent: true,
			opacity: 0,
			blending: THREE.AdditiveBlending,
			depthWrite: false,
			side: THREE.DoubleSide,
		})
		const beam = new THREE.Mesh(beamGeo, beamMat)
		beam.position.copy(ALERT_POS)
		beam.scale.y = 0.01
		world.add(beam)

		/* Containment shell closing over h002 in the final act. */
		const shell = new THREE.Mesh(
			new THREE.IcosahedronGeometry(1, 1),
			new THREE.MeshBasicMaterial({ color: ALARM, wireframe: true, transparent: true, opacity: 0 }),
		)
		shell.position.copy(ALERT_POS)
		shell.scale.setScalar(0.01)
		world.add(shell)

		/* Radar sweep line for the features act. */
		const sweepGeo = new THREE.BufferGeometry().setFromPoints([
			new THREE.Vector3(0, 0.5, 0),
			new THREE.Vector3(10, 0.5, 0),
		])
		const sweepMat = new THREE.LineBasicMaterial({
			color: SIGNAL,
			transparent: true,
			opacity: 0,
			blending: THREE.AdditiveBlending,
			depthWrite: false,
		})
		const sweep = new THREE.Line(sweepGeo, sweepMat)
		world.add(sweep)

		/*
		 * Choreography. anime.js animates plain JS objects; a createTimer render
		 * loop copies them into the three.js scene. The scrub is an onScroll
		 * observer with smooth sync -- the page scrolls natively, the timeline
		 * simply tracks its position. No wheel hijacking.
		 */
		const cam = { x: 0, y: 6, z: 26 }
		const tgt = { x: 0, y: 1, z: 0 }
		const fx = {
			particleSpeed: 1,
			edgeAlpha: 0.28,
			nodeSwell: 0,
			ringOpacity: 0,
			alertGlow: 0,
			ringConverge: 0,
			beam: 0,
			prob: 0,
			gridOpacity: 0,
			dimOthers: 0,
			shell: 0,
			sweep: 0,
			alertEdgeFade: 0,
		}

		const tl = createTimeline({
			defaults: { ease: 'inOut(3)' },
			autoplay: onScroll({
				target: section,
				// Map progress 0..1 to exactly the pinned range: the timeline starts
				// when the section's top reaches the viewport top and completes when
				// its bottom reaches the viewport bottom. The defaults ('end start' /
				// 'start end') would burn the first ~15% of the timeline while the
				// section is still approaching the viewport.
				enter: 'top top',
				leave: 'bottom bottom',
				sync: 0.18,
			}),
		})
		tl.add(cam, { x: 10, y: 4, z: 18, duration: 960 }, 600)
			.add(fx, { particleSpeed: 3, edgeAlpha: 0.55, duration: 960 }, 600)
			.add(cam, { x: -12, y: 8, z: 14, duration: 960 }, 1560)
			.add(fx, { nodeSwell: 1, sweep: 0.5, particleSpeed: 1.4, edgeAlpha: 0.4, duration: 960 }, 1560)
			.add(cam, { x: 0, y: 14, z: 20, duration: 960 }, 2520)
			.add(fx, { ringOpacity: 1, alertGlow: 1, sweep: 0, nodeSwell: 0.4, duration: 960 }, 2520)
			.add(cam, { x: 6, y: 3, z: 10, duration: 900 }, 3480)
			.add(tgt, { x: ALERT_POS.x, y: ALERT_POS.y, z: ALERT_POS.z, duration: 900 }, 3480)
			.add(fx, { ringConverge: 1, beam: 1, prob: 0.9866137, duration: 900, ease: 'out(3)' }, 3480)
			.add(cam, { x: 0, y: 10, z: 16, duration: 900 }, 4380)
			.add(tgt, { x: ALERT_POS.x, y: ALERT_POS.y + 1.6, z: ALERT_POS.z, duration: 900 }, 4380)
			.add(fx, { gridOpacity: 0.35, dimOthers: 1, duration: 900 }, 4380)
			.add(cam, { x: 4, y: 2, z: 8, duration: 840 }, 5160)
			.add(tgt, { x: ALERT_POS.x, y: ALERT_POS.y, z: ALERT_POS.z, duration: 840 }, 5160)
			.add(fx, { shell: 1, alertEdgeFade: 1, duration: 840, ease: 'out(4)' }, 5160)

		let visible = true
		const io = new IntersectionObserver(
			(entries) => {
				visible = entries[0]?.isIntersecting ?? true
			},
			{ threshold: 0.02 },
		)
		io.observe(mount)

		const ro = new ResizeObserver(() => {
			const w = mount.clientWidth
			const h = Math.max(mount.clientHeight, 1)
			renderer.setSize(w, h)
			renderer.setPixelRatio(Math.min(window.devicePixelRatio, w < 768 ? 1.5 : 2))
			camera.aspect = w / h
			camera.updateProjectionMatrix()
		})
		ro.observe(mount)

		let lastAct = -1
		const tmpV = new THREE.Vector3()
		const timer = createTimer({
			onUpdate: (t) => {
				if (!visible) return
				const dt = Math.min(t.deltaTime / 1000, 0.05)
				const time = t.currentTime / 1000

				camera.position.set(cam.x, cam.y, cam.z)
				camera.lookAt(tgt.x, tgt.y, tgt.z)

				for (let i = 1; i < layout.nodes.length; i++) {
					const p = layout.nodes[i]
					dummy.position.copy(p)
					dummy.scale.setScalar(1 + fx.nodeSwell * layout.swell[i] * 1.4)
					dummy.updateMatrix()
					inst.setMatrixAt(i - 1, dummy.matrix)
				}
				inst.instanceMatrix.needsUpdate = true
				nodeMat.opacity = 0.85 * (1 - fx.dimOthers * 0.8)

				edgeMat.opacity = fx.edgeAlpha * (1 - fx.dimOthers * 0.7)
				alertEdgeMat.opacity = 0.4 * (1 - fx.alertEdgeFade * 0.94)

				for (let i = 0; i < P; i++) {
					let tt = pT[i] + pSpeed[i] * fx.particleSpeed * dt
					let e = pEdge[i]
					if (tt >= 1) {
						tt = 0
						e = Math.floor(Math.random() * allPairs.length)
						pEdge[i] = e
					}
					pT[i] = tt
					const pair = allPairs[e]
					tmpV.lerpVectors(layout.nodes[pair[0]], layout.nodes[pair[1]], tt)
					pPos.set([tmpV.x, tmpV.y, tmpV.z], i * 3)
				}
				pGeo.attributes.position.needsUpdate = true
				pMat.opacity = 0.85 * (1 - fx.dimOthers * 0.5)

				ringMeshes[0].rotation.z += dt * 0.35
				ringMeshes[1].rotation.z -= dt * 0.25
				ringMeshes[2].rotation.z += dt * 0.18
				const c = fx.ringConverge
				rings.position.lerpVectors(RING_HOME, ALERT_POS, c)
				rings.scale.setScalar(1 - c * 0.7)
				ringMats.forEach((m) => {
					m.opacity = fx.ringOpacity * 0.9
				})

				const pulse = 1 + fx.alertGlow * (0.12 * Math.sin(time * 4) + 0.5)
				alertNode.scale.setScalar(pulse)
				alertNode.material.opacity = 0.4 + fx.alertGlow * 0.55

				beam.scale.y = Math.max(fx.beam * 9, 0.01)
				beamMat.opacity = fx.beam * 0.6

				gridMat.opacity = fx.gridOpacity
				grid.rotation.y += dt * 0.05

				shell.scale.setScalar(Math.max(fx.shell * 3, 0.01))
				shellMat.opacity = fx.shell * 0.75
				shell.rotation.y += dt * 0.4

				sweepMat.opacity = fx.sweep
				sweep.rotation.y = time * 1.4

				world.rotation.y += dt * 0.03

				if (probRef.current) probRef.current.textContent = fx.prob.toFixed(4)

				const prog = tl.progress
				let idx = 0
				for (let b = 0; b < ACT_BOUNDARIES.length; b++) {
					if (prog >= ACT_BOUNDARIES[b]) idx = b + 1
				}
				if (idx !== lastAct) {
					lastAct = idx
					setAct(idx)
				}

				renderer.render(scene, camera)
			},
		})

		return () => {
			io.disconnect()
			ro.disconnect()
			timer.revert()
			tl.revert()
			scene.traverse((obj) => {
				const mesh = obj as THREE.Mesh
				if (mesh.geometry) mesh.geometry.dispose()
				const mat = mesh.material as THREE.Material | THREE.Material[] | undefined
				if (Array.isArray(mat)) mat.forEach((m) => m.dispose())
				else if (mat) mat.dispose()
			})
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
				{/* The Grid. Purely decorative: every fact is also in the HTML below. */}
				<div
					ref={mountRef}
					className="absolute inset-0"
					role="img"
					aria-label="Animated 3D network of forty hosts. As you scroll, the camera flies through the detection pipeline: telemetry, features, three detector rings, fusion, the threshold plane, and containment of the alerted host."
				/>

				{/* Act 0 -- the title. One DOM instance, full contrast, crossfades out. */}
				<div
					className={cn(
						'absolute inset-0 flex items-center justify-center transition-opacity duration-500',
						act === 0 ? 'opacity-100' : 'pointer-events-none opacity-0',
					)}
				>
					<TitleBlock report={report} live={live} compact />
				</div>

				{/* Acts 1-6 -- one card at a time, alternating edges. */}
				{ACTS.map((a, i) => (
					<div
						key={a.id}
						className={cn(
							'absolute top-1/2 w-[min(340px,82vw)] -translate-y-1/2 transition-all duration-500',
							i % 2 === 0 ? 'left-[clamp(16px,6vw,96px)]' : 'right-[clamp(16px,6vw,96px)]',
							act === i + 1
								? 'translate-y-[-50%] opacity-100'
								: 'pointer-events-none translate-y-[calc(-50%+12px)] opacity-0',
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
	return (
		<div className={cn('mx-auto max-w-3xl px-6 text-center', compact && 'pointer-events-none')}>
			<span className="panel inline-block px-2.5 py-1 text-[14px] text-ink-dim">
				v1 &middot; {report.features.count} features &middot; {report.runtime_seconds.toFixed(1)}s end-to-end
			</span>
			<h1 className="mt-6 text-4xl font-semibold tracking-tight sm:text-6xl">
				<ShinyText text="SentinelAI" />
				<span className="block text-ink-dim sm:text-5xl">
					anomaly detection built around the analyst&rsquo;s budget
				</span>
			</h1>
			<p className="mx-auto mt-6 max-w-2xl text-lg text-ink-dim">
				Scroll to fly through the pipeline &mdash; from raw telemetry to a contained
				alert. Every figure is read from the pipeline&rsquo;s own artifacts, including
				the ones that look bad.
			</p>
			<div className="mt-8 flex flex-wrap items-center justify-center gap-2 text-[13px] text-ink-dim">
				{['563,619 flows', '49,535 auth events', '40 hosts', '4 days'].map((s) => (
					<span key={s} className="panel tabular px-2.5 py-1">
						{s}
					</span>
				))}
				<LiveChip status={live} />
			</div>
			{compact ? (
				<p className="mt-10 animate-pulse text-[13px] text-ink-faint">scroll &darr;</p>
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
