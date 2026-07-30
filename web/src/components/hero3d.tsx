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
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js'
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js'
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js'
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js'
import { ShaderPass } from 'three/examples/jsm/postprocessing/ShaderPass.js'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'
import { createTimer, createTimeline, onScroll } from 'animejs'
import { motion } from 'motion/react'
import { useReducedMotion } from '@/components/anime'
import { cn } from '@/lib/cn'
import { HeroHud } from '@/components/herohud'
import type { Bundle, LiveStatus } from '@/lib/data'

const CANVAS = 0x0a0b0d
const SIGNAL = 0x5e9fe8
const SAFE = 0x72bc8f
const WATCH = 0xde9255
const ALARM = 0xe97366
const GRAPH = 0xbf8eda

/** Deterministic PRNG (seed 7, like the pipeline) so the layout never changes. */
function lcg(seed: number) {
	let s = seed >>> 0
	return () => {
		s = (s * 1664525 + 1013904223) >>> 0
		return s / 4294967296
	}
}

/*
 * Bloom is selective *by threshold*, not by layer: with the pass threshold at
 * 1.0, only colours whose channels exceed 1 glow. So authoring a material above
 * or below 1.0 is the art direction -- above means "evidence", below means
 * "scaffolding". This avoids the official selective-bloom recipe entirely, which
 * darkens every other material and renders the scene a second time each frame.
 */
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

		/*
		 * ACES rolls the HDR highlights off instead of clipping them flat, which is
		 * what makes the glow read as light rather than as a blurred sprite.
		 */
		renderer.toneMapping = THREE.ACESFilmicToneMapping
		renderer.toneMappingExposure = 1.05
		renderer.outputColorSpace = THREE.SRGBColorSpace

		const composer = new EffectComposer(renderer)
		composer.addPass(new RenderPass(scene, camera))
		const bloom = new UnrealBloomPass(
			new THREE.Vector2(mount.clientWidth, Math.max(mount.clientHeight, 1)),
			0.85,
			0.55,
			1,
		)
		composer.addPass(bloom)
		/*
		 * Final grade. Bloom alone still reads as a clean CG render; what sells a
		 * *photograph* of an instrument is the lens and the sensor being imperfect.
		 * Three cheap, physically motivated defects, all pure fragment math:
		 *   - lateral chromatic aberration that scales with r^2, because real glass
		 *     only splits colour toward the edge of the field;
		 *   - a vignette, because the barrel occludes off-axis rays;
		 *   - sensor grain, applied here (pre-tone-map) so it lives in the midtones
		 *     rather than getting crushed into the blacks.
		 */
		const gradePass = new ShaderPass({
			uniforms: {
				tDiffuse: { value: null },
				uTime: { value: 0 },
				uAmount: { value: 1 },
			},
			vertexShader: `
				varying vec2 vUv;
				void main() {
					vUv = uv;
					gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
				}`,
			fragmentShader: `
				uniform sampler2D tDiffuse;
				uniform float uTime;
				uniform float uAmount;
				varying vec2 vUv;
				float hash(vec2 p) {
					return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
				}
				void main() {
					vec2 d = vUv - 0.5;
					float r = length(d);
					vec2 off = d * r * r * 0.0042 * uAmount;
					vec4 c;
					c.r = texture2D(tDiffuse, vUv + off).r;
					c.g = texture2D(tDiffuse, vUv).g;
					c.b = texture2D(tDiffuse, vUv - off).b;
					c.a = 1.0;
					c.rgb *= mix(1.0, smoothstep(0.96, 0.26, r), 0.8 * uAmount);
					float g = hash(vUv * 920.0 + fract(uTime) * 97.0) - 0.5;
					c.rgb += g * 0.016 * uAmount;
					gl_FragColor = c;
				}`,
		})
		composer.addPass(gradePass)
		// Without OutputPass the composer skips tone mapping and the sRGB
		// conversion the plain renderer does: the single most common bloom bug.
		composer.addPass(new OutputPass())

		const world = new THREE.Group()
		scene.add(world)

		/*
		 * THE APERTURE -- design contract in web/HERO-3D-PLAN-V3.md.
		 *
		 * A lens is what this pipeline literally does: scattered rays in, one focal
		 * point out. And the iris IS the alert budget -- stopping it down is what
		 * raising the threshold does. So the hero is one machined instrument the
		 * reader watches operate, not a field of drifting dots.
		 */

		/*
		 * Metal and glass are only convincing if there is something to reflect.
		 * RoomEnvironment is a procedural interior -- no asset, no network request --
		 * prefiltered once into an env map. This is the single biggest difference
		 * between "shaded" and "machined".
		 */
		const pmrem = new THREE.PMREMGenerator(renderer)
		const envRT = pmrem.fromScene(new RoomEnvironment(), 0.04)
		scene.environment = envRT.texture
		scene.environmentIntensity = 0.3

		const keyLight = new THREE.DirectionalLight(0xdce8ff, 2.1)
		keyLight.position.set(7, 9, 12)
		scene.add(keyLight)
		const rimLight = new THREE.DirectionalLight(SIGNAL, 1.35)
		rimLight.position.set(-9, -4, -7)
		scene.add(rimLight)
		scene.add(new THREE.AmbientLight(0x141a24, 1.4))

		/*
		 * Machined-surface normal map, generated into a canvas at runtime -- no asset,
		 * no request. Perfectly smooth metal is the tell that a render is synthetic:
		 * real turned aluminium carries lathe grooves plus a fine random grain, and
		 * both show up as *moving highlights* the moment the camera travels. Encoded
		 * tangent-space: R/G carry the surface slope, B points up.
		 */
		const makeMachinedNormal = () => {
			const S = 512
			const cv = document.createElement('canvas')
			cv.width = S
			cv.height = S
			const ctx = cv.getContext('2d')
			if (!ctx) return null
			const img = ctx.createImageData(S, S)
			const d = img.data
			let seed = 20260730
			const rnd = () => {
				seed = (seed * 1664525 + 1013904223) >>> 0
				return seed / 4294967296
			}
			for (let y = 0; y < S; y++) {
				// One groove profile per row, so the brushing runs in a single direction.
				const groove = Math.sin(y * 0.85) * 0.5 + Math.sin(y * 3.9) * 0.16 + Math.sin(y * 11.3) * 0.05
				for (let x = 0; x < S; x++) {
					const i = (y * S + x) * 4
					const grain = rnd() - 0.5
					d[i] = 128 + grain * 40
					d[i + 1] = 128 + (groove * 46 + grain * 10)
					d[i + 2] = 255
					d[i + 3] = 255
				}
			}
			ctx.putImageData(img, 0, 0)
			const tex = new THREE.CanvasTexture(cv)
			tex.wrapS = THREE.RepeatWrapping
			tex.wrapT = THREE.RepeatWrapping
			tex.repeat.set(6, 6)
			return tex
		}
		const machined = makeMachinedNormal()

		const metal = (c: number, roughness: number) => {
			const m = new THREE.MeshStandardMaterial({ color: c, metalness: 0.94, roughness })
			if (machined) {
				m.normalMap = machined
				m.normalScale = new THREE.Vector2(0.32, 0.32)
			}
			return m
		}

		/* --- Housing: two barrel sections the optic sits between. --- */
		const barrelGeo = new THREE.CylinderGeometry(5.1, 5.1, 1.4, 128, 1, true)
		barrelGeo.rotateX(Math.PI / 2)
		const barrelMat = metal(0x15181d, 0.3)
		barrelMat.side = THREE.DoubleSide
		for (const z of [-3.0, 3.0]) {
			const m = new THREE.Mesh(barrelGeo, barrelMat)
			m.position.z = z
			world.add(m)
		}
		const knurlMat = metal(0x24282f, 0.2)
		for (const z of [-3.7, -2.3, 2.3, 3.7]) {
			const r = new THREE.Mesh(new THREE.TorusGeometry(5.12, 0.1, 12, 180), knurlMat)
			r.position.z = z
			world.add(r)
		}

		/*
		 * --- Iris: nine blades on nine pivots. ---
		 * Each blade is an extruded Shape with a bevel; the bevel is what catches the
		 * key light along the blade edge and makes the assembly read as machined
		 * rather than as flat cutouts.
		 */
		const BLADES = 9
		const bladeShape = new THREE.Shape()
		bladeShape.moveTo(0, 0)
		bladeShape.quadraticCurveTo(2.7, 0.55, 5.0, 0.25)
		bladeShape.quadraticCurveTo(3.6, 3.1, 0.25, 4.7)
		bladeShape.quadraticCurveTo(0.06, 2.0, 0, 0)
		const bladeGeo = new THREE.ExtrudeGeometry(bladeShape, {
			depth: 0.1,
			bevelEnabled: true,
			bevelSize: 0.04,
			bevelThickness: 0.035,
			bevelSegments: 2,
			curveSegments: 28,
		})
		const bladeMat = metal(0x2b3038, 0.16)
		const bladePivots: THREE.Group[] = []
		const bladeBase: number[] = []
		for (let i = 0; i < BLADES; i++) {
			const a = (i / BLADES) * Math.PI * 2
			const pivot = new THREE.Group()
			pivot.position.set(Math.cos(a) * 5.0, Math.sin(a) * 5.0, 0)
			const blade = new THREE.Mesh(bladeGeo, bladeMat)
			// Stagger in depth so the blades overlap like a real iris instead of z-fighting.
			blade.position.z = -0.05 + i * 0.012
			pivot.add(blade)
			bladePivots.push(pivot)
			bladeBase.push(a + Math.PI)
			world.add(pivot)
		}
		const IRIS_OPEN = 1.02
		const IRIS_SHUT = 0.2

		/* --- Three lens elements, one per detector leg. --- */
		const LEG_Z = [-6.5, 0, 6.5]
		const LEG_C = [SIGNAL, SAFE, GRAPH]
		const lensGeo = new THREE.SphereGeometry(3.9, 96, 48)
		const bezelGeo = new THREE.TorusGeometry(3.88, 0.16, 14, 180)
		const bezelMat = metal(0x1d2128, 0.24)
		const lensMats: THREE.MeshPhysicalMaterial[] = []
		const lensGroups: THREE.Group[] = []
		for (let i = 0; i < 3; i++) {
			const mat = new THREE.MeshPhysicalMaterial({
				color: new THREE.Color(LEG_C[i]).lerp(new THREE.Color(0xffffff), 0.62),
				metalness: 0,
				roughness: 0.02,
				// thickness is the property that actually sells glass; ior 1.46 is crown.
				transmission: 1,
				thickness: 1.8,
				ior: 1.46,
				clearcoat: 1,
				clearcoatRoughness: 0.03,
				transparent: true,
				emissive: new THREE.Color(LEG_C[i]),
				emissiveIntensity: 0,
			})
			const element = new THREE.Mesh(lensGeo, mat)
			element.scale.set(1, 1, 0.12)
			const g = new THREE.Group()
			g.add(element)
			g.add(new THREE.Mesh(bezelGeo, bezelMat))
			g.position.z = LEG_Z[i]
			lensMats.push(mat)
			lensGroups.push(g)
			world.add(g)
		}

		/*
		 * --- Activation lattice: the AI half of the instrument. ---
		 *
		 * Each optical element carries a grid of cells on its face. That is not
		 * decoration: three stacked grids, lighting in sequence, IS a forward pass,
		 * and each grid is the feature map of one detector leg. The optic focuses
		 * light; the lattice is what the model actually computes while it does.
		 *
		 * They are children of the lens groups, so when the three elements stack into
		 * one optic at fusion the three feature maps superimpose -- which is exactly
		 * what the stacker does to the three leg scores.
		 */
		const GRID = 13
		const CELL_PITCH = 0.5
		const cellGeo = new THREE.BoxGeometry(0.2, 0.2, 0.05)
		const latMeshes: THREE.InstancedMesh[] = []
		const latPos: Array<Float32Array> = []
		const tmpC = new THREE.Color()
		for (let l = 0; l < 3; l++) {
			const xs: number[] = []
			const ys: number[] = []
			for (let gx = 0; gx < GRID; gx++) {
				for (let gy = 0; gy < GRID; gy++) {
					const x = (gx - (GRID - 1) / 2) * CELL_PITCH
					const y = (gy - (GRID - 1) / 2) * CELL_PITCH
					// Clip to the clear aperture so the map reads as circular, like the element.
					if (Math.sqrt(x * x + y * y) > 3.25) continue
					xs.push(x)
					ys.push(y)
				}
			}
			const n = xs.length
			const buf = new Float32Array(n * 2)
			const cellMat = new THREE.MeshBasicMaterial({
				transparent: true,
				blending: THREE.AdditiveBlending,
				depthWrite: false,
			})
			const im = new THREE.InstancedMesh(cellGeo, cellMat, n)
			im.frustumCulled = false
			const od = new THREE.Object3D()
			for (let i = 0; i < n; i++) {
				buf[i * 2] = xs[i]
				buf[i * 2 + 1] = ys[i]
				// Sit just proud of the glass so the cells are not swallowed by transmission.
				od.position.set(xs[i], ys[i], 0.16)
				od.updateMatrix()
				im.setMatrixAt(i, od.matrix)
				im.setColorAt(i, tmpC.setRGB(0, 0, 0))
			}
			im.instanceMatrix.needsUpdate = true
			lensGroups[l].add(im)
			latMeshes.push(im)
			latPos.push(buf)
		}

		/* --- Rays: one instanced draw call, authored above the bloom threshold. --- */
		const RAYS = 340
		const rayGeo = new THREE.CylinderGeometry(0.02, 0.02, 1, 6, 1, true)
		rayGeo.translate(0, 0.5, 0)
		const rayMat = new THREE.MeshBasicMaterial({
			transparent: true,
			opacity: 0.95,
			blending: THREE.AdditiveBlending,
			depthWrite: false,
		})
		const rays = new THREE.InstancedMesh(rayGeo, rayMat, RAYS)
		rays.instanceMatrix.setUsage(THREE.DynamicDrawUsage)
		rays.frustumCulled = false
		world.add(rays)

		const BAND_TINT = [SIGNAL, 0x7fb2ee, SAFE, WATCH, GRAPH, 0x9ad2ff]
		const rr = lcg(11)
		const rayOrigin: THREE.Vector3[] = []
		const rayFar: THREE.Vector3[] = []
		const rayT = new Float32Array(RAYS)
		const raySpeed = new Float32Array(RAYS)
		const rayRank = new Float32Array(RAYS)
		const rayBand = new Uint8Array(RAYS)
		const bandColor = new THREE.Color()
		for (let i = 0; i < RAYS; i++) {
			const a = rr() * Math.PI * 2
			// sqrt keeps the disc of origins uniform instead of crowding the axis
			const rad = 1.4 + Math.sqrt(rr()) * 4.4
			rayOrigin.push(new THREE.Vector3(Math.cos(a) * rad, Math.sin(a) * rad, -26 - rr() * 10))
			rayFar.push(new THREE.Vector3(Math.cos(a) * rad, Math.sin(a) * rad, 14))
			rayT[i] = rr()
			raySpeed[i] = 0.11 + rr() * 0.17
			rayRank[i] = rr()
			rayBand[i] = Math.floor(rr() * 6)
			bandColor.copy(hdr(BAND_TINT[rayBand[i]], 2.0))
			rays.setColorAt(i, bandColor)
		}
		if (rays.instanceColor) rays.instanceColor.needsUpdate = true
		const FOCUS = new THREE.Vector3(0, 0, 9.2)
		const RAY_UP = new THREE.Vector3(0, 1, 0)
		const rayA = new THREE.Vector3()
		const rayB = new THREE.Vector3()
		const rayEnd = new THREE.Vector3()
		const rayDir = new THREE.Vector3()
		const dummy = new THREE.Object3D()

		/* --- The focal core: what survives the aperture. --- */
		const coreMat = new THREE.MeshBasicMaterial({
			color: hdr(ALARM, 2.8),
			transparent: true,
			opacity: 0,
		})
		const core = new THREE.Mesh(new THREE.IcosahedronGeometry(0.34, 3), coreMat)
		core.position.copy(FOCUS)
		world.add(core)

		/* --- Sensor plane behind the focus: the instrument reticle. --- */
		const grid = new THREE.PolarGridHelper(6.4, 9, 5, 96, WATCH, WATCH)
		const gridMat = grid.material as THREE.LineBasicMaterial
		gridMat.transparent = true
		gridMat.opacity = 0
		gridMat.depthWrite = false
		grid.rotation.x = Math.PI / 2
		grid.position.z = FOCUS.z + 2.4
		world.add(grid)

		/*
		 * --- Containment field. ---
		 * Fresnel rather than a wireframe: the field glows along its silhouette and
		 * stays clear through the middle, so the contained host is still readable
		 * inside its own containment.
		 */
		const shellMat = new THREE.ShaderMaterial({
			uniforms: {
				uColor: { value: hdr(ALARM, 2.4) },
				uOpacity: { value: 0 },
				uPower: { value: 2.4 },
			},
			vertexShader: `
				varying vec3 vNormalView;
				varying vec3 vViewDir;
				void main() {
					vec4 mv = modelViewMatrix * vec4(position, 1.0);
					vNormalView = normalize(normalMatrix * normal);
					vViewDir = normalize(-mv.xyz);
					gl_Position = projectionMatrix * mv;
				}
			`,
			fragmentShader: `
				uniform vec3 uColor;
				uniform float uOpacity;
				uniform float uPower;
				varying vec3 vNormalView;
				varying vec3 vViewDir;
				void main() {
					float f = 1.0 - abs(dot(normalize(vNormalView), normalize(vViewDir)));
					gl_FragColor = vec4(uColor, pow(f, uPower) * uOpacity);
				}
			`,
			transparent: true,
			blending: THREE.AdditiveBlending,
			depthWrite: false,
			side: THREE.DoubleSide,
		})
		const shell = new THREE.Mesh(new THREE.IcosahedronGeometry(1, 3), shellMat)
		shell.position.copy(FOCUS)
		shell.scale.setScalar(0.01)
		world.add(shell)

		/* --- Dust: atmosphere only, deliberately dim. --- */
		const P = 260
		const pPos = new Float32Array(P * 3)
		const pScale = new Float32Array(P)
		const prand = lcg(99)
		for (let i = 0; i < P; i++) {
			const a = prand() * Math.PI * 2
			const rad = 1 + prand() * 8
			pPos.set([Math.cos(a) * rad, Math.sin(a) * rad, -24 + prand() * 38], i * 3)
			pScale[i] = 0.5 + prand() * 0.9
		}
		const pGeo = new THREE.BufferGeometry()
		pGeo.setAttribute('position', new THREE.BufferAttribute(pPos, 3))
		pGeo.setAttribute('aScale', new THREE.BufferAttribute(pScale, 1))

		/*
		 * Point size is world-relative, not pixel-relative: dividing the pixel scale
		 * by view depth keeps apparent size stable across resizes and 4K displays.
		 */
		const pixelScale = () =>
			(Math.max(mount.clientHeight, 1) * 0.5) / Math.tan(((camera.fov * Math.PI) / 180) / 2)
		const pMat = new THREE.ShaderMaterial({
			uniforms: {
				uColor: { value: hdr(SIGNAL, 1.4) },
				uOpacity: { value: 0 },
				uSize: { value: 0.05 },
				uPixelScale: { value: pixelScale() },
			},
			vertexShader: `
				uniform float uSize;
				uniform float uPixelScale;
				attribute float aScale;
				void main() {
					vec4 mv = modelViewMatrix * vec4(position, 1.0);
					gl_PointSize = uSize * aScale * uPixelScale / max(-mv.z, 0.001);
					gl_Position = projectionMatrix * mv;
				}
			`,
			fragmentShader: `
				uniform vec3 uColor;
				uniform float uOpacity;
				void main() {
					float d = length(gl_PointCoord - vec2(0.5));
					if (d > 0.5) discard;
					float a = pow(smoothstep(0.5, 0.0, d), 1.8);
					gl_FragColor = vec4(uColor, a * uOpacity);
				}
			`,
			transparent: true,
			blending: THREE.AdditiveBlending,
			depthWrite: false,
		})
		const dust = new THREE.Points(pGeo, pMat)
		world.add(dust)

		/*
		 * Choreography. anime.js animates plain JS objects; a createTimer render loop
		 * copies them into the scene. The scrub is an onScroll observer with smooth
		 * sync -- the page scrolls natively, the timeline just tracks it.
		 */
		const cam = { x: 0, y: 6, z: 26 }
		const tgt = { x: 0, y: 0, z: 0 }
		const fx = {
			rayFlow: 1,
			rayConverge: 0,
			band: 0,
			lensSpread: 1,
			legA: 0,
			legB: 0,
			legC: 0,
			iris: 1,
			irisPass: 1,
			focus: 0,
			shell: 0,
			gridOpacity: 0,
			dust: 0.3,
			prob: 0,
			lattice: 0.34,
			grade: 1,
		}

		const tl = createTimeline({
			defaults: { ease: 'inOut(3)' },
			autoplay: onScroll({
				target: section,
				enter: 'top top',
				leave: 'bottom bottom',
				sync: 0.18,
			}),
		})
		tl.add(cam, { x: 2, y: 3, z: 19, duration: 960 }, 600)
			.add(fx, { rayFlow: 2.6, dust: 1, duration: 960 }, 600)
			.add(cam, { x: -9, y: 4, z: 12, duration: 960 }, 1560)
			.add(fx, { band: 1, rayFlow: 1.8, duration: 960 }, 1560)
			.add(cam, { x: 0, y: 0.8, z: 17, duration: 960 }, 2520)
			.add(fx, { legA: 1, legB: 1, legC: 1, lattice: 1, duration: 960 }, 2520)
			.add(cam, { x: 7, y: 5, z: 11, duration: 900 }, 3480)
			.add(
				fx,
				{ lensSpread: 0, rayConverge: 1, focus: 0.55, prob: 0.9866137, duration: 900, ease: 'out(3)' },
				3480,
			)
			.add(cam, { x: 0, y: 1.4, z: 13.5, duration: 900 }, 4380)
			.add(tgt, { x: 0, y: 0, z: 2, duration: 900 }, 4380)
			.add(fx, { iris: 0, irisPass: 0.16, gridOpacity: 0.5, duration: 900 }, 4380)
			.add(cam, { x: 2.6, y: 1.1, z: 15.5, duration: 840 }, 5160)
			.add(tgt, { x: FOCUS.x, y: FOCUS.y, z: FOCUS.z, duration: 840 }, 5160)
			.add(fx, { shell: 1, focus: 1, duration: 840, ease: 'out(4)' }, 5160)

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
			const dpr = Math.min(window.devicePixelRatio, w < 768 ? 1.5 : 2)
			renderer.setSize(w, h)
			renderer.setPixelRatio(dpr)
			// The composer keeps its own render targets: resizing the renderer
			// alone leaves the bloom sampling a stale buffer.
			composer.setSize(w, h)
			composer.setPixelRatio(dpr)
			bloom.setSize(w, h)
			pMat.uniforms.uPixelScale.value = pixelScale()
			camera.aspect = w / h
			camera.updateProjectionMatrix()
		})
		ro.observe(mount)

		let lastAct = -1
		let loggedError = false
		const tmpV = new THREE.Vector3()
		const timer = createTimer({
			onUpdate: (t) => {
				if (!visible) return
				try {
				const dt = Math.min(t.deltaTime / 1000, 0.05)
				const time = t.currentTime / 1000

				camera.position.set(cam.x, cam.y, cam.z)
				camera.lookAt(tgt.x, tgt.y, tgt.z)

				/* Iris: one angle drives all nine pivots. Closing it IS raising the gate. */
				const irisA = IRIS_SHUT + (IRIS_OPEN - IRIS_SHUT) * fx.iris
				for (let i = 0; i < BLADES; i++) {
					bladePivots[i].rotation.z = bladeBase[i] - irisA
				}

				/* The three elements stack into one optic during fusion. */
				const legGlow = [fx.legA, fx.legB, fx.legC]
				for (let i = 0; i < 3; i++) {
					lensGroups[i].position.z = LEG_Z[i] * fx.lensSpread
					lensMats[i].emissiveIntensity = legGlow[i] * 0.55
				}

				/*
				 * Feature maps. Each cell is a travelling-wave activation raised to a power,
				 * which keeps most of the grid dark and a few cells hot -- activations are
				 * sparse, and a uniformly lit grid would read as a keyboard, not a tensor.
				 * Only the instance colours change per frame; the matrices are written once.
				 */
				for (let l = 0; l < 3; l++) {
					const im = latMeshes[l]
					const buf = latPos[l]
					const gate = fx.lattice * (0.28 + 0.72 * legGlow[l])
					const n = buf.length / 2
					for (let i = 0; i < n; i++) {
						const x = buf[i * 2]
						const y = buf[i * 2 + 1]
						const wave =
							0.5 +
							0.5 *
								Math.sin(time * 1.6 + x * 0.95 + y * 0.55 + l * 2.1) *
								Math.cos(time * 0.9 - y * 0.8 + l * 1.3)
						const a = wave * wave * wave * gate
						tmpC.setHex(LEG_C[l]).multiplyScalar(a * 2.4)
						im.setColorAt(i, tmpC)
					}
					if (im.instanceColor) im.instanceColor.needsUpdate = true
				}

				gradePass.uniforms.uTime.value = time
				gradePass.uniforms.uAmount.value = fx.grade

				/* Rays: travel, band, converge, and die at the blade plane if stopped. */
				for (let i = 0; i < RAYS; i++) {
					let t = rayT[i] + raySpeed[i] * fx.rayFlow * dt
					if (t >= 1) t -= 1
					rayT[i] = t

					const origin = rayOrigin[i]
					// Fan the six feature bands onto their own radii.
					const bandK = 1 + (rayBand[i] - 2.5) * 0.1 * fx.band
					tmpV.set(origin.x * bandK, origin.y * bandK, origin.z)
					rayEnd.lerpVectors(rayFar[i], FOCUS, fx.rayConverge)

					const tail = t < 0.05 ? 0 : t - 0.05
					rayA.lerpVectors(tmpV, rayEnd, tail)
					rayB.lerpVectors(tmpV, rayEnd, t)

					// Rays the aperture rejects stop at z = 0 rather than fading in mid-air.
					const span = rayEnd.z - tmpV.z
					const gateT = span === 0 ? 1 : (0 - tmpV.z) / span
					const alive = rayRank[i] <= fx.irisPass || t < gateT

					rayDir.subVectors(rayB, rayA)
					const len = rayDir.length()
					if (!alive || len < 1e-5) {
						dummy.scale.set(0, 0, 0)
						dummy.position.copy(rayA)
						dummy.quaternion.identity()
					} else {
						dummy.position.copy(rayA)
						dummy.quaternion.setFromUnitVectors(RAY_UP, rayDir.divideScalar(len))
						dummy.scale.set(1, len, 1)
					}
					dummy.updateMatrix()
					rays.setMatrixAt(i, dummy.matrix)
				}
				rays.instanceMatrix.needsUpdate = true

				core.scale.setScalar(0.4 + fx.focus * (1.1 + 0.12 * Math.sin(time * 5)))
				coreMat.opacity = fx.focus

				shell.scale.setScalar(Math.max(fx.shell * 2.6, 0.01))
				shellMat.uniforms.uOpacity.value = fx.shell * 0.9
				shell.rotation.y += dt * 0.4

				gridMat.opacity = fx.gridOpacity
				grid.rotation.y += dt * 0.06

				pMat.uniforms.uOpacity.value = 0.35 * fx.dust
				dust.rotation.z += dt * 0.03

				world.rotation.z += dt * 0.02

				if (probRef.current) probRef.current.textContent = fx.prob.toFixed(4)

				const prog = tl.progress

				/*
				 * Layer choreography. The scene used to be drawn under the title
				 * from the very first frame, so the node field read as noise behind
				 * the words. Now the first screen belongs to the title alone: the
				 * canvas only fades up once the reader has actually scrolled, and it
				 * fades back out before the section ends so the Explain console
				 * arrives on clean canvas instead of colliding with the grid.
				 */
				const sceneFade = clamp01((prog - 0.035) / 0.075) * (1 - clamp01((prog - 0.9) / 0.1))
				const sceneStr = sceneFade.toFixed(3)
				if (mount.style.opacity !== sceneStr) mount.style.opacity = sceneStr

				const titleEl = titleRef.current
				if (titleEl) {
					const show = 1 - clamp01((prog - 0.012) / 0.055)
					const away = 1 - show
					titleEl.style.opacity = show.toFixed(3)
					titleEl.style.transform =
						'translate3d(0,' + (-30 * away).toFixed(1) + 'px,0) scale(' + (1 - 0.05 * away).toFixed(4) + ')'
					titleEl.style.filter = away > 0.001 ? 'blur(' + (8 * away).toFixed(2) + 'px)' : 'none'
					titleEl.style.pointerEvents = show < 0.06 ? 'none' : 'auto'
				}
				let idx = 0
				for (let b = 0; b < ACT_BOUNDARIES.length; b++) {
					if (prog >= ACT_BOUNDARIES[b]) idx = b + 1
				}
				if (idx !== lastAct) {
					lastAct = idx
					setAct(idx)
				}

				composer.render()
				} catch (err) {
					// A throw inside this callback used to kill the render call at the
					// bottom of the frame, leaving a blank canvas with no console trace
					// anyone would connect to the hero. Log once, keep drawing.
					if (!loggedError) {
						loggedError = true
						console.error('[hero3d] frame update failed:', err)
					}
					composer.render()
				}
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

				{/* Vector graticule over the raster image the instrument forms. */}
				<HeroHud act={act} className="pointer-events-none absolute inset-0 z-10 h-full w-full" />
				{/* The Grid. Purely decorative: every fact is also in the HTML below. */}
				<div
					ref={mountRef}
					className="canvas-feather absolute inset-0"
					style={{ opacity: 0 }}
					role="img"
					aria-label="Animated 3D network of forty hosts. As you scroll, the camera flies through the detection pipeline: telemetry, features, three detector rings, fusion, the threshold plane, and containment of the alerted host."
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
