# SentinelAI — scroll-driven 3D hero: design plan

Status: **design locked, implementation follows this document**
Component: `web/src/components/hero3d.tsx` · Section: `#top` (replaces the static hero)

---

## 1. What we are building, in one sentence

A pinned, scroll-scrubbed Three.js scene of **the SentinelAI detection pipeline itself** —
40 host nodes streaming flows — that the visitor flies through in six acts, one per pipeline
stage, with every callout annotated with real numbers from `artifacts/`.

Not a lens. Not a generic globe. The model **is** the product story: telemetry → features →
detector legs → fusion → threshold → response. The same six stages the Explain section walks
through with real arithmetic — the hero is the cinematic version, the explainer is the proof.

## 2. Research summary (what we learned and from where)

| Source | What it taught us | Decision |
| --- | --- | --- |
| animejs.com hero + docs | Their site demos each feature with a live visual + short copy + tiny code snippet, revealed per scroll segment via `onScroll()` | Same structure: one visual, six annotated acts |
| anime.js v4 `onScroll()` docs | `sync: true` = hard scrub, `sync: 0–1` = smooth catch-up (lower = lazier); full threshold/callback set | `sync: 0.18` — buttery but responsive; no wheel hijack |
| anime.js v4 Three.js adapter docs (4.5.0) | Adapter flattens mesh props (`x`, `rotateY` deg, `opacity`) — but requires animejs ≥ 4.5.0 | **Skip the adapter.** v4 core animates plain JS objects; `mesh.position`, `mesh.material` are JS objects, so `createTimeline().add(mesh.position, { x: … })` already works in 4.0.2. Zero version risk. |
| threejsresources / three.js journey scroll lessons | Camera-follows-scroll, cursor parallax, per-section triggers, flash-free initial state, batched updates | Adopted all four |
| r/webdev, r/UXDesign, NN/g "Scrolljacking 101" | Scroll**jacking** (stealing the wheel) is despised; scroll**scrubbing** (page scrolls normally, animation tracks position) is accepted when it doesn't block content | Strict scrub. Native scrollbar always works. Content never waits for animation. |
| Lens hero post-mortem (our own) | Duplicated/magnified text layers ghosted; motion landed on words | One copy of every text element. Motion only on the 3D scene and card positions/opacity. |

## 3. The model: "The Grid"

A 3D network built from primitives — no external GLTF (offline-friendly, no asset pipeline,
no loading flash). Everything is code.

| Element | Three.js primitive | Count | Colour token |
| --- | --- | --- | --- |
| Host nodes | `InstancedMesh(IcosahedronGeometry(0.22, 1))` | **40** (matches dataset `n_hosts`) | `--color-signal` dimmed; alert node = `--color-alarm` |
| Flow edges | `LineSegments` (vertex-coloured) | ~90 | `rgba(94,159,232,0.28)` additive |
| Flow particles | `THREE.Points` | 600 | signal blue, additive, `depthWrite:false` |
| Detector rings | `TorusGeometry(r, 0.02, 8, 128)` | 3 | signal / safe / graph (one per leg) |
| Threshold plane | `GridHelper(26, 26)` + fade plane | 1 | `--color-watch` at 35% opacity |
| Alert beam | `CylinderGeometry(0.03, 0.03, h)` | 1 | `--color-alarm` |
| Containment shell | `IcosahedronGeometry(1, 1)` wireframe | 1 | `--color-alarm` |
| Ambient depth | `FogExp2(0x0a0b0d, 0.026)` | — | canvas |

Node layout: 3 loose clusters (mimics 3 subnets), seeded LCG so the layout is identical
every load (deterministic, like the pipeline: seed 7). Edges = 2 nearest neighbours per node
+ ~10 random long-range links (the lateral-movement-looking ones).

The alert node is **h002** — the same entity the explainer walks through. Hero drama and
section proof point at the same real alert.

## 4. The six acts (scroll choreography)

Section `#top` is `560vh` tall; a `sticky` viewport holds the canvas + overlay.
Timeline duration 6000 (arbitrary units); each act ≈ 1000. Scrub via
`onScroll({ target: '#top', sync: 0.18 })`.

| Act | Progress | Name | Scene beats | Callout copy (real numbers) |
| --- | --- | --- | --- | --- |
| 0 | 0.00–0.10 | **Title** | Network idles, slow rotation, particles drift. Title + lead + 4-stat strip fade up with stagger | `563,619 flows · 49,535 auth events · 40 hosts · 4 days` |
| 1 | 0.10–0.26 | **Telemetry** | Camera pushes from (0,6,26) → (10,4,18). Particle speed ×3, edges brighten to 0.55 alpha | "Zeek-style flow records + Windows auth logs, windowed every 300s → **45,340 host-windows**" |
| 2 | 0.26–0.42 | **Features** | Camera → (−12,8,14). Nodes swell by per-node feature weight (alert node ×2.4, neighbours ×1.3–1.6). A slow radar sweep line | "**40 features** per host-window — volume, reach, shape, identity, naming, graph" |
| 3 | 0.42–0.58 | **Legs** | Camera rises to (0,14,20). Three tilted rings fade in and orbit at different tilts/speeds. h002 ignites alarm-red and starts pulsing | "isolation forest AP **0.298** · gradient boosting AP **0.858** · graph leg AP **0.039**" |
| 4 | 0.58–0.73 | **Fusion** | Camera dives to (6,3,10) beside h002. Rings shrink r 8→2.2 and converge onto it. Beam rises. **Probability counter sweeps 0.0000 → 0.9866 live** | "0.3425·z_if + 1.1009·z_gb + 0.1135·z_g − 8.434 → **p = 0.9866**" |
| 5 | 0.73–0.86 | **Threshold** | Camera pulls back (0,10,16). Watch-amber grid plane slides in at threshold height; all nodes except h002 dim to 20%; rank chip appears | "threshold **0.6303** → 49.8 alerts/day · recall 0.661 · rank **#1 of 11,326**" |
| 6 | 0.86–1.00 | **Response** | Camera closes to (4,2,8). Wireframe shell scales 0→3 around h002 (`out(4)`), its edges sever (fade to 0.06). Final chip, then hint to keep scrolling | "auto_contain → **rate_limit_src_at_edge** · 50 decisions · audit chain valid" |

Camera targets interpolate on the same timeline (JS-object proxies `{cx,cy,cz,tx,ty,tz}`
copied to `camera.position` + `lookAt` in `onUpdate`). Easings: `inOut(3)` between acts,
`out(4)` for shell/containment, linear inside acts.

## 5. Overlay & typography rules (learned from the lens failure)

- **Exactly one DOM instance of every piece of text.** The title exists once, at full
  contrast, and crossfades out as Act 1 begins. No duplication, no magnification, no
  chromatic copies — ever.
- Callout cards: `panel` surface at 82% opacity + backdrop-blur, alternating left/right
  edges, `max-width 340px`. Each card: act index (`ACT 03 / 06`), stage name in the leg's
  colour, 2–3 lines of copy, and a hairline leader-line pointing at the scene.
- Progress rail: 6 dots on the right edge, active dot fills with the act colour.
- Probability counter (Act 4): tabular numerals, driven by a timeline proxy
  `{p: 0 → 0.9866137}` rendered with `toFixed(4)` — the visitor watches the sigmoid land
  on the exact published probability.
- Cards animate `opacity` + `translateY(12px)` only. No text effects. Words are for reading.
- All callout numbers are marketing-frozen copies of `artifacts/` values (comment in code
  says so); the Explain section below remains the live, regenerated source of truth.

## 6. Engineering contract

**Files**
- `web/src/components/hero3d.tsx` — everything: scene build, timeline, overlay markup.
  Self-contained `useEffect` with full cleanup (dispose geometries, materials, renderer,
  revert anime instances).
- `web/src/App.tsx` — replace static `<Hero>` with the pinned `<section id="top">` +
  `<NetworkHero/>`. Section rail unchanged (`top/Overview` still first).
- `web/src/index.css` — ~40 lines: `.act-card`, `.act-rail`, `.leader`, radar-sweep
  keyframes.
- `web/package.json` — add `three ^0.166.1` (deps) + `@types/three ^0.166.0` (dev).
  `animejs` stays `4.0.2` — everything used exists since 4.0.0.

**Performance budget**
- DPR clamp `min(devicePixelRatio, 2)` (1.5 below 768px width).
- One `InstancedMesh` draw call for all 40 nodes; 1 for particles; ≤ 12 draw calls total.
- `createTimer({ onUpdate: render })` drives renders on anime's own clock — scene and DOM
  stay in perfect sync.
- `IntersectionObserver` on the sticky viewport: timer paused when hero is off-screen.
- Particles advance by elapsed time, not frames — same speed at 30fps and 144fps.

**Accessibility**
- `prefers-reduced-motion`: no pin, no scrub — static composed pose (Act 3 framing),
  all six cards stacked statically below the title, canvas renders one frame only.
- `role="img"` + `aria-label` on the canvas describing the scene; a "skip to console ↓"
  anchor (`pointer-events: auto`) for keyboard users.
- All text lives in HTML (never in canvas) — selectable, findable, translatable.

**Honesty / fallback**
- If WebGL init throws, the overlay renders without the canvas (title + cards still read
  perfectly; the site worked as pure HTML before 3D existed).
- Sandbox has no network: `three` can't be installed here, so verification is esbuild
  transpile only. First real render happens on the user's machine after `npm install`.

## 7. Acceptance checklist

- [ ] 560vh scroll = six acts, scrubbed, native scrollbar always live
- [ ] Probability counter lands on exactly `0.9866` at end of Act 4
- [ ] h002 is visually identifiable from Act 3 onward (alarm red, pulsing)
- [ ] Rings visibly converge on h002 during Act 4
- [ ] One DOM instance of the title; zero ghosting at any scroll position
- [ ] 60fps on a mid laptop (check devtools: ≤ 12 draw calls)
- [ ] Reduced-motion path renders statically
- [ ] No console errors/warnings; esbuild passes on all sources
- [ ] `npm install` adds `three`; `npm run dev` runs clean
