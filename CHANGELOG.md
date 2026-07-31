# Changelog

Notable changes to SentinelAI. Versions follow semver: the major bump here is
honest, because the hero is replaced rather than iterated.

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

