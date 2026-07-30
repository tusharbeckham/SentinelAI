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

## Research log

Each row is a source and the concrete decision it changed. Sources that only
confirmed an existing choice are omitted.

| Source | What it says | What we did |
| --- | --- | --- |
| Codrops, *SVG Filter Effects: Creating Texture with feTurbulence* | fractalNoise plus feDisplacementMap is the canonical way to break a straight edge into organic distortion | scan band is a plain rect displaced by turbulence rather than a hand-drawn wobble |
| Smashing Magazine, *Deep Dive into the Wonderful World of SVG Displacement Filtering* | displacement scale is in user units and compounds with filter region; too small a region clips the effect | filter region set to -20/140 percent on both axes so the displaced band is never clipped |
| Michael Mullany, via Stack Overflow *How to perfectly loop feTurbulence animation* | animating baseFrequency **cannot** loop; the field is resampled and jumps on repeat. Animate an feColorMatrix hueRotate 0 to 360 instead | exactly this: baseFrequency is static, hueRotate animates 0 to 360 over 9s |
| ccprog, same thread | stitchTiles=noStitch avoids seam tearing when the noise field is animated | stitchTiles=noStitch set explicitly |
| MDN, feTurbulence reference | numOctaves cost is roughly linear; 2 is usually enough for shimmer | numOctaves=2, not the default-ish 4 |
| Awwwards, hackvector.io and safe-security | the convincing security aesthetic is instrument panel, not neon cyber cliche: monospace readouts, hairline rules, restrained accent | monospace readouts, single signal accent, no neon, no glitch text |
| designmonks, cybersecurity dashboard roundup | real SOC surfaces put state in small persistent readouts, not big labels | MODE / ACT / f-stop readouts in the corners at 15px |

The SVG loop pitfall is worth restating because it is the one that silently
ruins this kind of effect:

> Do not animate baseFrequency. It looks weird and there is no way to loop it.
> Add an feColorMatrix hue-rotate from 0 to 360 instead.
