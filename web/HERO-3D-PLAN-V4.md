# Hero 3D — Plan V4: The Instrument

## Why V3 looked unchanged to the user

The V3 work was real, but it was invisible at rest. The activation lattice only
ignited at timeline offset 2520, which is Act 3, roughly 42 percent of the way
through the scroll. The machined normal map and the photographic grade pass are
both subtle by design. Net effect: **the first screen was pixel-similar to the
previous release**, and the first screen is the only thing most visitors judge.

The lesson is a general one and it is now a rule for this file:

> An improvement that only exists after 40 percent of a scroll does not exist.

V4 therefore front-loads. The scene must be visibly different in frame one,
before any scrolling at all.

## The core idea: split the work the way a real instrument does

WebGL and SVG are good at opposite things, and the previous versions used only
one of them.

| | WebGL is good at | WebGL is bad at |
| --- | --- | --- |
| Light | glass, transmission, metal, bloom, depth | — |
| Line | — | hairlines crawl and shimmer at any DPR |

| | SVG is good at | SVG is bad at |
| --- | --- | --- |
| Line | exact strokes at every zoom and DPR | — |
| Light | — | no lighting model at all |

So: **the canvas is the image the instrument forms, and the SVG is the graticule
etched onto the eyepiece.** This is not decoration bolted on to satisfy a
request for SVG. It is the division of labour an actual optical instrument uses,
and it is the reason the overlay is allowed to exist.

The single most important consequence: **the graticule does not move with the
camera.** A graticule is fixed to the observer, not to the scene. Overlays that
parallax with the 3D camera always read as stickers. Ours is nailed to the
viewport.
