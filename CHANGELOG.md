# Changelog

Notable changes to SentinelAI. Versions follow semver: the major bump here is
honest, because the hero is replaced rather than iterated.

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

