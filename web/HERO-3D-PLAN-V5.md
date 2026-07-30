# Hero v5 -- Embedding Space

## Why v4 was scrapped

v4 (The Instrument) was rejected on sight, and the criticism was correct.
Three concrete failures, all visible in one screenshot:

1. **The graticule collided with the subtitle.** `herohud.tsx` drew into a
   fixed `1600x900` viewBox with `preserveAspectRatio="xMidYMid slice"`.
   Slice scales to *cover*, so at any aspect ratio other than 16:9 the box is
   cropped inward and the corner readouts walk into the middle of the screen.
   Readouts pinned at `y=210` and `y=234` landed directly on the line
   "anomaly detection built around the analyst budget".
2. **The rings read as noise.** Faint dotted arcs floating behind live text
   are not an instrument, they are visual litter.
3. **The lens was invisible.** Dark glass on a `#0a0b0d` canvas is a smudge.

Underneath all three sits one root cause: the brief said the animejs lens was
a cool *idea*, and it was read as a literal subject. Three versions were spent
polishing an object nobody asked for. A lens has nothing to do with intrusion
detection; it was decoration wearing a technical costume.

## The v5 thesis

Render the model, not a machine. The only thing worth putting on this page is
the thing the project actually produces: 11,326 scored windows and a decision
boundary that costs something.

## Geometry

| Axis | Meaning |
| --- | --- |
| x | PC1 of the 40 standardised features (22.5% of variance) |
| z | PC2 (12.5%) |
| y | model log-odds |
| colour | ground-truth attack family |
| size | probability, plus ignition above the threshold |

Height being the score is the whole trick. It makes the operating threshold an
**exact horizontal plane** at `logit(0.6303) = 0.5337`, not a metaphor for one.
Consequences that fall out for free:

- 10 benign points sit visibly **above** the plane -- the false positives.
- 20 attack points sit visibly **below** it -- the misses.
- The plane counts reproduce the published operating point exactly:
  precision `0.7959`, recall `0.6610`. The generator asserts this and fails
  the build if it ever drifts.

## Six acts

| # | Act | What moves |
| --- | --- | --- |
| 01 | The corpus | 11,326 windows rain in from an unordered shell |
| 02 | Feature space | they settle into the PCA manifold |
| 03 | The score axis | `uLift` 0 to 1 raises the flat sheet into log-odds |
| 04 | Fusion | the stacker equation sets the height |
| 05 | The plane | threshold sweeps in; 49 ignite, 20 stay dark |
| 06 | Response | rank 1 (h002) is caged and selected |

## Rendering notes

- One `THREE.Points` with a custom `ShaderMaterial`. 11,326 points is trivial;
  the point-cloud performance literature is about millions, so no Potree or
  LOD machinery is warranted here.
- Family colour is computed on the CPU into an `aColor` attribute rather than
  branching through a palette in the shader.
- Bloom threshold is `1.0`, so glow is opt-in by authoring: only alerted points
  are pushed above 1 and therefore only they bloom.
- Additive blending, `depthWrite: false`. On a dense benign core, depth-sorted
  alpha produces popping; additive does not.
- The threshold plane is a shader grid, not a solid quad, because an opaque
  sheet would hide the false negatives underneath -- the entire point of it.

## HUD rules (learned the hard way)
