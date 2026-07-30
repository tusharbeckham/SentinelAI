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

## What the graticule contains

All of it in one fixed `viewBox="0 0 1600 900"` with `preserveAspectRatio=
"xMidYMid slice"`, so it crops like a photograph instead of stretching.

1. **Frame brackets** — four corner marks, the classic viewfinder crop.
2. **Bearing rings** — two dashed circles counter-rotating at 64s and 96s.
   Deliberately far slower than feels right in isolation; anything faster reads
   as a loading spinner. The motion should only be detectable against the
   static ticks.
3. **Graduated tick ring** — 72 ticks, every sixth major, as on a real dial.
4. **Broken crosshair** — gapped at the centre so it never crosses the subject.
5. **Focus corners** — four marks that close in act by act. This is the aperture
   stopping down, expressed in vector so the marks stay hairline sharp while
   they travel. Driven purely by a `data-act` attribute and CSS transitions, so
   there is zero per-frame JavaScript cost.
6. **Scan band** — the turbulence-displaced sweep described above.
7. **Readouts** — MODE, a per-act note, ACT n of 06, and an f-stop that closes
   as the acts advance.

A radial mask fades the entire overlay toward the edges, so the graticule never
fights the corners of the frame.

## Acceptance checklist

- [x] The hero is visibly different **at scroll offset zero**, not at 42 percent.
- [x] Lattice idles at 0.34 from the first frame rather than igniting at Act 3.
- [x] Graticule is fixed to the viewport, never parallaxed with the camera.
- [x] All strokes are hairline-exact at any DPR.
- [x] baseFrequency is static; the loop is driven by hueRotate 0 to 360.
- [x] stitchTiles=noStitch is set.
- [x] Overlay is `pointer-events-none` and `aria-hidden`, so it is invisible to
      assistive tech and never intercepts a click.
- [x] `prefers-reduced-motion` stops every ring, the scan, and the focus
      transition.
- [x] CSS brace balance verified as part of the build gate. This is new, and it
      exists because a previous release shipped a broken stylesheet that neither
      tsc nor esbuild could possibly have caught.
