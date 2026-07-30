# Changelog

Notable changes to SentinelAI. Versions follow semver: the major bump here is
honest, because the hero is replaced rather than iterated.

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

