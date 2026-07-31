# Hero staging + the Lattice

Supersedes HERO-GALAXY-PLAN.md. The galaxy is dropped.

## 1. What was wrong

Two failed attempts, two different mistakes:

- **v2.3.0** rendered the galaxy *behind* the title. That is a blend. Rejected.
- **v2.4.0** split them into two stacked sections, so the scene sat *below*
  the hero and you scrolled from one to the other. Also rejected.

The brief is neither. One place, sequentially: hero alone, then a transition,
then the model alone in the same screen space. They must never share a frame.

## 2. Staging

One `<section id="top">`, 560vh, full-bleed, with a `sticky top-0 h-screen`
stage inside it. Everything happens on that one stage. Scroll progress p:

| p | Stage |
| --- | --- |
| 0.000 - 0.020 | Hero, still. No entrance animation. Canvas renders pure black. |
| 0.020 - 0.075 | Hero exits: lifts 56px and fades to 0, then `visibility: hidden`. |
| 0.075 - 0.100 | Dead zone. Nothing on screen but canvas colour. This is the seam. |
| 0.100 - 0.170 | Model rises out of black. |
| 0.170 - 0.955 | The six acts. |
| 0.955 - 1.000 | Model exits: lift, chromatic spread, fade to black. |

The two ranges do not overlap. There is a 0.025 gap between the hero being
gone and the model arriving, which is what makes it a cut rather than a
crossfade. No scrim, no veil, no feather - the fade is done in the colour
grade pass (`uFade`), so at p < 0.1 the composite is literally black rather
than a translucent layer sitting over something.

## 3. The model: the Lattice

anime.js's hero works because it is *structured*. It is not a cloud of dust;
it is an ordered arrangement being deformed. The order is what makes the
deformation legible. That is the property to steal - not the lens shape.

So: the rest state is a **perfectly regular lattice**. A rectangular grid of
11,326 cells, one per held-out window, dead flat, evenly spaced, all one
colour. It reads as a manufactured object, not weather.

This is also the honest state. Act 01 says every window is one host-window
described by forty features. Before any model touches it, the corpus *is* an
undifferentiated table - a grid is a truthful picture of a table. Then the
morph carries each cell to its real embedding position, and the ordered thing
collapses into the actual manifold. Structure to structure, and the fact that
the destination is a flat dense sheet is the finding, not a failure.

### Glyph

Round gaussian blobs read as dust. The anime.js look is crisp geometry, so
the fragment shader draws a **square with a cut corner** - a chip, not a
star - antialiased with `fwidth` rather than `discard`. Per the three.js
forum thread on sharp particle edges, `discard` is precisely what produces
blocky stair-stepping; an analytic derivative-width smoothstep gives a clean
edge at any size. Alert points get a ring instead of a bloom smear.

### Palette

Monochrome steel at rest, single accent on ignition. Colour is reserved to
mean something: ground-truth family only after the morph, alarm only above
the threshold.

## 4. Removed

- `HeroMark` and the `MARK` geometry - the strip at the bottom of the hero.
- The galaxy generator, its three-population split and its stellar palette.

## 5. Gate

1. `node scripts/check-css.mjs` -> balanced
2. TS2304 + TS6133 sweep -> 0 files
3. esbuild sweep -> ok=12 fail=0
4. brace/paren balance, and `grep -c` every new identifier
