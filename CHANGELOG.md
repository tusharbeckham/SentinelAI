# Changelog

Notable changes to SentinelAI. Versions follow semver: the major bump here is
honest, because the hero is replaced rather than iterated.

## v2.9.0 -- One clear object

The object rendered in v2.8.0 but read as a bright ring rather than a thing.
That was a material fault, not a tuning problem: the surface was additively
blended, wrote no depth, and was shaded by fresnel alone, which can only ever
produce an outline. No amount of colour work would have fixed it.

- **A solid, not a glow.** The body now writes depth, blends normally, renders
  front faces only, and derives its normals from screen-space derivatives so
  every triangle shades flat. It has a lit side, a dark side and a silhouette.
- **Everything else is deleted.** Marker rings, the three leg hulls, the
  threshold torus and the containment shockwave are gone from the file, not
  dimmed. One object on stage.
- **Swipe.** Yaw is driven by scroll position rather than the clock, so the
  object turns because you are turning it. A slow clock term keeps it alive
  while the page is still.
- **A verb per act, and no more stretching.** 02 folds to a sheet. 03 terraces
  the surface into seven score bands. 04 twists, peaking mid-act and unwinding
  so the lobes resolve into one locked form. 05 splits the body along the
  decision height, everything above the cut lifting away from everything below.
  06 contracts and goes cold.
- **Hand-off to the console.** The model finishes leaving at 0.995, then the
  Explain section rises into the same slot of the viewport across the last
  3.5% of the hero. One is fully gone before the other begins -- the same
  contract as the 0.075-0.10 seam on the way in, pointed the other way.

### Not verified here

No GPU in the build sandbox: the GLSL is never compiled and no frame is ever
produced. Static gates only. The flat-normal shading relies on dFdx/dFdy, which
needs WebGL2; three r166 defaults to it, but that is the one hard dependency
introduced by this release. Facet contrast and the act-05 split distance are
the most likely things to need tuning on real hardware.

## v2.8.0 -- Act choreography

The Boundary renders, so this release gives it something to do in every act
instead of relying on a camera push-in to carry five of them.

- **Act 02, feature space.** The hull folds into a dense sheet -- the same
  principal-component collapse the card describes -- and spins hardest through
  the middle of the move. Rotation is driven by f*(1-f), not f, so the gesture
  starts and finishes at rest instead of stopping dead on a scroll boundary.
- **Act 03, the score axis.** The sheet unfolds and stretches 2.35x along y as
  height takes on meaning, with a skew term so the lobe is asymmetric rather
  than a stretched ball. The creases brighten as it grows.
- **Act 04, fusion.** The floor grid is deleted. In its place three boundaries,
  one per leg of the stacker, sized by their fitted weights (0.3425 isolation
  forest, 1.1009 GBDT, 0.1135 graph), arrive apart and converge onto the single
  hull that ships. GBDT is visibly largest because it visibly dominates the
  coefficients. This closes the fusion-act gap open since v2.3.0.
- **Act 05, the plane.** The threshold is no longer scenery under the object.
  It is cut through the surface itself: a luminous band at the decision height,
  everything above it tinted warn, everything below it left cold. Height is
  read from the artifact threshold and carried up with the act-03 stretch.
- **Act 06, response.** Containment draws the surface inward and dims the body
  while a single ring leaves the rank-1 alert and snaps shut.
- **The ending.** Act 06 finishes at 0.95 and nothing happens until 0.97 -- the
  object holds, lit -- then the canvas cuts out in one move. The console is
  never underneath a half-transparent scene. This mirrors the 0.075-0.10 seam
  on the way in: a gap, never a cross-fade.

### Not verified here

There is no GPU in the build sandbox. The GLSL is never compiled and no frame
is ever produced. Static gates only: esbuild 12/12, TS2304/TS6133 scope checks,
brace and paren balance. The three-leg convergence and the cut band are the two
places most likely to need tuning on real hardware.

## v2.7.0 -- The Boundary

### Added

- The hero model is one object now, not a particle field. A closed faceted
  surface standing for the region of normal behaviour the detector learns:
  benign traffic inside, anomalies outside, so the threshold is the surface
  rather than a number bolted onto the picture.
- Facet creases are drawn as real line geometry. A line segment cannot go soft
  the way a point sprite does, which is where every previous hero lost its
  edge.
- Transitions at both ends. The hull fades up across the same 0.10-0.17 window
  as the rest of the stage, opens as the corpus settles, and collapses on the
  outro.

### Fixed

- The model is built at init and added unconditionally. Every earlier version
  was constructed inside the corpus fetch callback, so a slow or failed request
  left the canvas completely empty. This is the most likely cause of the empty
  canvas at ACT 01.

## v2.6.3 -- Black is black again

### Fixed

- The grey wash over the hero, introduced by v2.6.2. The grade pass had the
  canvas token #0a0b0d hardcoded into it, but OutputPass runs after grade and
  applies ACES tone mapping plus sRGB encoding, so a display-space constant
  written there is re-encoded on the way out and lands near #343434.
- The reveal no longer depends on shader arithmetic at all. The canvas keeps
  its own opaque CANVAS background and the mount is faded with CSS. Page and
  canvas backgrounds are the same token, so at every opacity between 0 and 1
  they are the same colour and a seam cannot occur by construction.
- The build stamp was left at 2.6.1 during the v2.6.2 release, which made the
  running build unreadable from the page. It is now 2.6.3.

## v2.6.2 -- The seam above the title

### Fixed

- The black line above the SentinelAI title. The grade pass multiplied the
  composite by uFade, which is zero while the hero is up, driving the canvas
  to pure #000000 while the page background is the canvas token #0a0b0d. Two
  different blacks meeting at the top edge of the sticky stage rendered as a
  hard horizontal seam. The pass now fades toward the token colour, so the
  canvas is indistinguishable from the page until the model arrives. This was
  introduced by the v2.5.0 decision to make the seam literally black.

### Added

- web/HERO-OBJECT-PLAN.md. The hero becomes a single closed surface -- the
  learned normal region, with anomalies outside it -- rather than a particle
  field. Every previous hero was a cloud; the repeated rejections were about
  form, not fidelity.

## v2.6.1 -- Build stamp and entrance

### Added

- The chip row now reads the build number instead of a hardcoded v1. There is
  no other way to tell from the page which build is running, which has made
  the last two rounds of feedback impossible to act on.
- The model entrance is now a move rather than a brightness ramp: point size
  goes from 34 percent to full and the lattice scales from 0.82 to 1.0 across
  the same 0.10-0.17 window, with a matching 10 percent push on exit.

### Removed

- The horizontal rule between the hero copy and the stats grid.

## v2.6.0 -- The model, actually on screen

### Fixed

- The lattice was invisible. It was built as a flat sheet on the y=0 plane
  while the camera sits at y=3.2 looking at the origin -- about five degrees
  above the plane. A flat sheet seen at five degrees projects to a hairline.
  The grid now stands upright in the camera plane, 56 x 29 units, sized to
  overfill the frame at the opening camera distance.
- The idle yaw is gated on uSettle. Previously it turned the world at a
  constant rate, so even a correctly oriented wall would swing edge-on within
  about twenty seconds of the page sitting still.
- The point shader scaled both colour and alpha by predicted probability,
  which is near zero for the 11,257 benign windows -- the overwhelming
  majority of the cloud. Combined they put most of the lattice at roughly
  five percent effective opacity. Both now have a floor.

### Removed

- The nebula haze and the ambient dust layer, the last two pieces of the
  galaxy build. With the lattice invisible these were the only thing actually
  rendering, which is why the scene still looked like a leftover galaxy.
- The keyboard skip link, which rendered as a black bar across the top of the
  page. This is an accessibility regression and is tracked as such.

## v2.5.0 -- One stage, in sequence

### Changed

- The hero and the model now occupy the same screen space, staged one after
  the other on a single sticky section. Neither is above the other and
  neither is behind the other.
- The two ranges do not overlap. The hero is gone by p=0.075; the model does
  not begin to arrive until p=0.100. The 0.025 gap makes it a cut, not a
  crossfade. The fade runs through the grade pass, so during the seam the
  composite is literally black rather than a translucent layer.
- The model is a regular lattice now, not a galaxy: one flat, evenly spaced
  cell per held-out window, monochrome steel, which then deforms to the true
  embedding. Order is what makes the deformation legible.
- Points are drawn as an SDF chip -- a square with a cut corner, antialiased
  with fwidth instead of discard. Alerts get a ring rather than a bloom.
- Colour is withheld until it means something: family after the morph, alarm
  above the threshold.

### Removed

- The SVG hero mark. It read as a loader.
- The galaxy generator, its three-population split and its stellar palette.

## v2.4.0 -- Hero and scene, separated

### Fixed

- The black rectangle around the canvas. It was never the shader: the scene
  sat inside main.mx-auto.max-w-6xl.px-5, so absolute inset-0 resolved to a
  1152px padded box instead of the viewport. The scene section is now
  full-bleed via left-1/2 / w-screen / -translate-x-1/2.

### Changed

- The hero is its own section in normal flow with nothing rendering behind
  it. The title no longer lives inside the pinned scene, so the two are never
  layered on top of each other.
- The scene begins only once the hero has been scrolled past.
- Hand-off transition: the scene fades up as its section climbs into view, so
  the hero and the scene are never both at full strength.
- Galaxy retheme onto the project palette -- warm core, signal-blue arms,
  graph-violet HII knots.

### Added

- An SVG hero mark: the threshold plane with the benign mass packed beneath
  it, the windows that clear it above, a sweep across the decision line and a
  ping on the rank-1 alert. Seeded geometry, and it honours
  prefers-reduced-motion.

## v2.3.0 -- Galaxy shape

### Added

- The hero now opens on a fully formed spiral galaxy, built from the standard
  disc decomposition: a 16% spheroidal bulge, a 4% sparse halo, and an 80%
  exponential disc laid on two logarithmic arms at a 14 degree pitch angle.
- Arm scatter is gaussian and grows with radius, so arms are crisp in the core
  and fray at the rim instead of reading as a drawn line.
- Stellar populations: warm amber core, blue-white arms, and pink HII knots.

### Changed

- The galaxy is a rest state, not a costume. On scroll every star flies to its
  true embedding position and the colour crosses from stellar to ground-truth
  family. The pretty state and the state that reports figures are never mixed.
- No entrance animation. The scene is fully formed before the first scroll.
- The act dot rail is hidden at rest and fades in once the morph begins.

### Removed

- Corner bracket chrome.
- All three blend layers: the title scrim, the canvas feather mask and the
  handoff veil. Nothing is dimmed or feathered over the scene any more.

## v2.2.1 -- Hero renders again

### Fixed

- The v2.2.0 nebula tint referenced SIGNAL and GRAPH, which were never declared
  in this module. The hero threw ReferenceError: GRAPH is not defined on mount
  and the whole component failed to render. Both are now real constants next to
  CANVAS, WATCH and ALARM, carrying the same hex values as the design tokens.

### Tooling

- Added a scope gate to the check sequence. esbuild validates syntax only and
  treats an undefined identifier as a legal global, which is how this shipped.
  tsc filtered to TS2304 catches it directly and runs over every source file.

## v2.2.0 -- Galaxy

### Changed

- Point size now follows a power law rather than being uniform. A real star
  field is a few bright anchors over a long faint tail; uniform sizing was the
  main reason the cloud read as flat pixel dust.
- Bright points render diffraction spikes. A photographed star is not a round
  blob, and the missing cross is a large part of why point clouds look
  synthetic.
- Per-point colour temperature spreads the palette between cool blue and warm
  amber instead of one flat hue per family.
- Removed the circular discard, which was shearing the spikes at the sprite
  edge. The gaussian is ~0.0005 by the quad corner, so no square is visible.

### Added

- A nebula haze layer: 110 large, very faint additive sprites in a flattened
  disc inside the cloud. Light now pools between the points instead of every
  star floating in vacuum. Capped near 2% alpha so it cannot obscure data.
- Differential drift: the haze rotates slower, and counter to, the cloud.

### Integrity

- All motion added here is ambient. Data point positions are never sheared.
  x and z remain the projection, y remains the model log-odds, and the
  threshold plane stays an exact plane.

## v2.1.2 -- UMAP no longer stalls on CPU

### Fixed

- Passing `random_state` to umap-learn silently disables parallelism. On the
  CPU fallback that turned a two minute job into a half hour stall. The CPU
  path now runs multi-threaded with `init=pca` and `n_epochs=200`.
- The GPU and CPU branches announce which one ran, and the cell reports its
  wall time, so a silent fallback is visible instead of looking like a hang.

### Notes

- The CPU layout is no longer bit-reproducible. This cannot affect any reported
  metric: UMAP only sets x and z, while height is the model log-odds. The
  precision 0.7959 and recall 0.6610 asserts still hold exactly.

## v2.1.1 -- Kaggle input discovery

### Fixed

- The notebook hardcoded `/kaggle/input/sentinelai-scored/...`, which throws
  `FileNotFoundError` because Kaggle rewrites dataset folder names on upload.
  It now globs `/kaggle/input`, lists what it finds, and selects the scored file.
- An unmounted dataset now reports what to click instead of a pandas traceback.

## v2.1.0 -- Realism pass

### Added

- Entry transition: the cloud condenses out of a wide shell over 1.9s, staggered
  per point so it sweeps in rather than snapping.
- Exit transition: the last 4% of the section lifts and dissolves the field,
  handing off to the page instead of cutting.
- A dust layer in world space. It does not rotate with the cloud, and that
  differential motion is what makes the camera moves read as depth.
- Final grade pass: radial chromatic aberration, vignette and luminance-weighted
  film grain.

### Changed

- Points render with a gaussian profile instead of a hard disc, plus aerial
  perspective so distance drains intensity.

### Notes

- `docs/KAGGLE-GUIDE.md` explains how to recompute the layout with UMAP on a GPU.
  PCA keeps only 35% of the variance, which is the real reason the cloud looked
  flat; no amount of shading fixes a structureless blob.

## v2.0.0 -- Embedding Space

### Changed

- The hero is replaced, not iterated. The lens scene is deleted.
- The scene now renders the 11,326 held-out test windows: x and z are the
  first two principal components of the 40 features, y is the model log-odds,
  colour is the ground-truth family.
- Because height is the score, the operating threshold is an exact horizontal
  plane. The 10 false positives above it and the 20 misses below it are
  visible as geometry rather than described in prose.

### Fixed

- The HUD no longer uses a full-bleed sliced viewBox, which cropped its corner
  readouts onto the subtitle at every aspect ratio except 16:9.

### Verification

- `scripts/make_embedding.py` regenerates the projection from the scored CSV
  and asserts the plane reproduces precision 0.7959 and recall 0.6610.
- PC1 22.5% and PC2 12.5% of variance; 49 windows above the plane.
- The sandbox has no GPU and no network, so the shaders could not be compiled
  or run here. Syntax, types and CSS balance are gated; the visual is not.

