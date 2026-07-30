# The Grid, v2 — graphics, theme and choreography

Supersedes the render decisions in `HERO-3D-PLAN.md`. The choreography contract
(six acts, one per pipeline stage) is unchanged; this document covers how the
scene is *drawn*, and why each technique was chosen over the alternatives.

## 1. Research log

| Question | Source | Decision |
| --- | --- | --- |
| How do you make only *some* objects glow? | three.js forum, `drcmda` (r3f maintainer) on selective bloom | **Do not use the official selective-bloom example.** It darkens every non-bloom material, renders the scene twice, and mixes with a `ShaderPass`. Bloom is already selective: set `threshold: 1.0` and only colours whose RGB exceeds 1 bloom. One composer, one render. |
| Why do bloom scenes wash out or go white? | three.js #14899, forum "white screen" thread | The composer skips tone mapping and the sRGB conversion the plain renderer does. **`OutputPass` must be the final pass** — this is the single most common bloom bug. |
| How do you get soft round particles? | Codrops dissolve-effect article, three.js `ShaderMaterial` docs | `PointsMaterial` gives hard squares (or forces a texture fetch). A custom `ShaderMaterial` with `gl_PointCoord` distance falloff is one instruction cheaper than a texture and scales perfectly. |
| How should point size behave with distance? | three.js docs, point-size discussions | Size in *world* terms, not pixels: `uSize * aScale * uPixelScale / -mvPosition.z`, where `uPixelScale = height / (2·tan(fov/2))`. Recomputed on resize, so particles do not change apparent size between a laptop and a 4K monitor. |
| How do you make a containment field read as a field? | Three.js Roadmap rim-lighting tutorial, Otano fresnel material | Fresnel: `1 - |dot(normal, viewDir)|` raised to a power. Edges glow, the centre stays clear, so the alerted host remains visible *through* its own containment shell. A wireframe sphere cannot do this. |
| Per-node colour without extra draw calls? | three.js `InstancedMesh.setColorAt` docs | `setColorAt` + `instanceColor.needsUpdate`. Final colour is `material.color × instanceColor`, so the material stays white and the instance colour carries both hue **and** HDR intensity. Still one draw call for 39 hosts. |
| Dark-theme rules for an operations console | AdminLTE dark-dashboard survey, Aufait UX cybersecurity-dashboard guide, New Relic on mission-critical dark mode | Not pure black; off-white rather than pure white text; **elevation via surface steps, not shadows** (shadows barely register on dark); saturated colour reserved for meaning; thin bright strokes with translucent fills for charts. |
| Scroll-driven 3D without annoying people | NN/g scrolljacking, r/webdev consensus (carried over from v1) | Scrub, never hijack. The scrollbar always works and the page can be stopped mid-act. |

## 2. Render pipeline

```
scene ──▶ RenderPass ──▶ UnrealBloomPass ──▶ OutputPass ──▶ canvas
                          threshold 1.0
                          strength   0.85
                          radius     0.55
```

- `ACESFilmicToneMapping`, exposure `1.05`. ACES rolls off the HDR highlights
  instead of clipping them to flat white, which is what makes the glow read as
  light rather than as a blurred sprite.
- **The threshold is the art direction.** Every material is authored either
  below 1.0 (structure: grid, threshold plane, dim edges) or above 1.0
  (signal: nodes, particles, rings, the alert, the containment field). A
  colour is therefore a statement about whether the thing it describes is
  *evidence* or *scaffolding*.
- Multipliers: hosts ×1.35, particles ×2.2, rings ×1.6, alert node ×2.6,
  containment ×2.4. The alert is the brightest object in the scene at every
  moment after act 3 — the eye is never asked where to look.

## 3. Materials

| Object | Material | Why |
| --- | --- | --- |
| 39 hosts | `InstancedMesh` + `setColorAt` | One draw call. Cluster tint (signal / safe / graph) makes the three subnets legible before any label appears. |
| h002 | separate `MeshBasicMaterial`, HDR alarm | Must animate independently and outshine everything. |
| ~90 edges | `LineSegments`, additive, sub-1.0 | Scaffolding. Present, never competing. |
| 600 flow particles | custom `ShaderMaterial` points | Soft round falloff, `pow(a, 1.8)` for a tight core and a wide halo — the profile bloom likes. |
| 3 detector rings | `TorusGeometry`, HDR per leg | Colour maps to the leg, so convergence in act 4 is literally three detectors agreeing. |
| Threshold plane | `GridHelper`, watch, **sub-1.0** | A gate is scaffolding, not evidence. Deliberately does not bloom. |
| Containment | Fresnel `ShaderMaterial` | Glows at the silhouette, transparent at the centre: containment you can see through. |

## 4. Theme

The palette is unchanged — it was already correct and the research confirms the
rules it follows. What changes is *depth*:

- Surfaces are separated by lightness steps (`canvas → surface → raised`) plus a
  1px inner top highlight, never by drop shadows.
- Accent colour appears only where it carries meaning. Severity colours never
  decorate.
- Charts use thin bright strokes over translucent fills.
- Type: Inter with optical sizing for display, JetBrains Mono with slashed zero
  for every measured number.

## 5. Engineering contract

- DPR clamped to 2 (1.5 under 768px). Composer and bloom resized together with
  the renderer, and `uPixelScale` recomputed in the same handler.
- `IntersectionObserver` pauses the loop when the hero is off-screen.
- Reduced motion and WebGL failure both fall back to the static stacked cards.
- The frame body is wrapped: a throw logs once and still reaches the composer,
  so a future bug can never blank the canvas silently again.
- Draw calls stay in single digits; the particle system is one buffer update.

## 6. Acceptance

1. Title holds the first screen alone; canvas fades in only after scrolling.
2. Scene fades out before the Explain console.
3. Alert node is the brightest object from act 3 onward.
4. Threshold plane visibly does **not** bloom.
5. Containment field is transparent at its centre.
6. Particles are round at every zoom level.
7. Resizing does not change apparent particle size.
8. No scrolljacking; the page can stop mid-act.
9. Reduced-motion path renders every fact as text.
