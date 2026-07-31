# The Boundary — one object, not a field

Supersedes HERO-STAGE-PLAN.md's model section. The staging contract in that
document is unchanged and correct; only the thing being staged changes.

## Why the lattice is being dropped

Asked directly what about animejs.com mattered, the answer was: **one clear
object, not a particle field.**

Every hero I have built for this project has been a particle field — lens,
galaxy, lattice. Each rejection said the same thing in different words:
"pixel bs", "pixel shit", "not pixel make it real", "dull and dumb". I read
those as complaints about *fidelity* and kept trying to make better particles.
They were complaints about *form*. A cloud of anything reads as noise no
matter how well each speck is drawn. anime.js works because there is a single
legible solid on screen with a silhouette you could draw from memory.

## The object

**A closed surface with things outside it.**

This is not decoration; it is the literal picture of what the model does. A
novelty detector learns a region of normal behaviour. Everything inside the
surface is benign traffic. Everything outside is an anomaly. The threshold is
not a number bolted onto the visual — it *is* the surface.

Geometry: an icosahedral solid, detail 4, radius ~7. Not a sphere — facets
give it an edge language and a readable silhouette, and they carry specular
highlights that a smooth ball cannot.

Material, in three layers, drawn back to front:

1. **Body.** Dark, near-black, lit only by a fresnel rim in signal blue. It
   reads as a solid of smoked glass, not as a wireframe ball.
2. **Edges.** `EdgesGeometry` over the icosahedron, drawn as true line
   segments with a thresholdAngle so only real facet creases survive. This is
   the crispness. Lines are geometry, so they are resolution-independent and
   cannot go soft the way a point sprite does.
3. **Interior.** The 11,257 benign windows, drawn small and dim *inside* the
   hull and visible only through it. The data is still honest and still on
   screen, but it is contained by the object instead of being the object.

## What it does across the six acts

| Act | The object |
| --- | --- |
| 01 The corpus | Closed, opaque, slowly turning. A sealed solid. |
| 02 Feature space | Facets separate slightly; the interior becomes visible through the gaps. |
| 03 The score axis | The surface stretches along y into a lobe — the log-odds axis. |
| 04 Fusion | Three nested hulls (isolation forest, gradient boosting, graph) converge into one. |
| 05 The plane | The surface goes fully transparent; 49 exterior points ignite outside it. |
| 06 Response | Everything recedes; one exterior point remains, ringed. |

Act 04 is the fusion transition that has been outstanding since v2.3.0. Three
surfaces merging into one is a far better picture of a stacked ensemble than
anything the point cloud could show, so the debt gets paid by the redesign
rather than patched around.

## Rules carried forward

- Entrance 0.10–0.17, exit 0.955–1.0, hero exit 0.02–0.075, seam at 0.075–0.100.
  Unchanged.
- Monochrome at rest. Colour only ever means severity.
- No bloom smear on the hull. Rim light and edges do the work.
- The grade pass fades to the canvas token, never to pure black.

## Honest risks

- `EdgesGeometry` line width is 1px on almost every platform; `linewidth` is
  ignored outside of a fat-line implementation. If the edges read as too thin,
  the fallback is `Line2`/`LineMaterial` from three's examples, which is a
  real dependency addition and should be a deliberate decision.
- Transparency ordering: hull, interior points and exterior points all
  transparent means depth sorting matters. Interior points must be drawn with
  `depthWrite: false` and the hull rendered last.
- None of this has been compiled or rendered here. No GPU in this sandbox.
