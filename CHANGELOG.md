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

