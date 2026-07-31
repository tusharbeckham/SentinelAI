# Hero Galaxy Plan (v2.3.0)

## The conflict in the brief, and how it is resolved

You asked for the hero to be **in the shape of a galaxy**. The points in the
hero are not decoration: `x` and `z` are the UMAP projection of the 40-feature
windows, and `y` is the model log-odds. The real data is a thin sheet, because
11,257 of 11,326 windows are benign and look alike. That flatness is the actual
finding. Bending those points into spiral arms would mean drawing window
positions the model never produced.

So the galaxy is not a lie laid over the data. It is a **second state**, and the
hero morphs between the two:

| Scroll | State | Meaning |
| --- | --- | --- |
| 0.00 (at rest, before any scroll) | Full spiral galaxy | The opening image. Formed, still, slowly turning. |
| 0.015 - 0.19 | Morph | Each star flies to its true position in the embedding. |
| 0.19 - 0.955 | The real corpus | Every act reads honest coordinates. |
| 0.955 - 1.0 | Outro | Field exhales and hands off to the page. |

The morph is the argument: *this is what it looks like when you make it pretty,
and this is what it actually is.* Nothing is faked in the state that carries
figures.

## Galaxy construction (research-grounded)

Built from the standard disc-galaxy decomposition (NASA SVS Milky Way Anatomy;
Freeman and Bland-Hawthorn on disc/bulge/halo structure) rather than a swirl
of noise.

### Components

| Component | Share | Distribution |
| --- | --- | --- |
| Bulge | 16% | `r = rnd()^2.2 * 5.0`, spheroidal, flattened to 0.72 in y |
| Halo | 4% | `r = 12 + sqrt(rnd()) * 26`, near-spherical, sparse |
| Disc | 80% | Exponential radial profile on two logarithmic arms |

### Arms

Grand-design two-arm logarithmic spiral, the Lin-Shu density-wave form:

```
r(theta) = a * e^(b * theta),  b = tan(pitch)
```

- **Pitch angle 14 degrees** (`b = 0.2493`). Measured Milky Way pitch sits near
  12 degrees; 14 opens the arms just enough to read at this camera distance.
- Arms offset by `2*pi / 2`.
- Radial profile `r = -4.6 * ln(1 - u)`, the exponential disc, truncated at 26.
- Perpendicular scatter **grows with radius** (`0.22 + 0.055r`), which is what
  keeps arms crisp in the core and frayed at the rim instead of looking like a
  drawn line.
- Scatter uses a **sum of three uniforms** as a cheap gaussian. Uniform scatter
  gives arms hard edges; real ones fall off smoothly.
- Disc thickness `(0.55 + 0.02r) * 0.9`, again gaussian. Thin, and thickening
  outward.

### Colour

Stellar populations, not one hue:

- Core: warm amber `#ffcf9b` - old population II.
- Arms: blue-white `#9fc4ff` - young hot stars form in the density wave.
- Ramp by radius over `r = 3 .. 19`.
- 3% of disc stars become HII regions `#ff8fb0`, the pink knots along the arms
  that make real galaxy photographs read as real.

Colour lerps to the ground-truth family palette across the morph, so the moment
the data appears, colour starts carrying meaning instead of beauty.

## Hero chrome changes

- **Corner bracket lines removed** entirely.
- **Act dot rail** hidden at rest, fades in only once the morph starts.
- **All three blend layers deleted**: `.title-scrim`, `.canvas-feather` mask and
  `.handoff-veil`. Nothing is dimmed or feathered over the scene any more.
- **No entrance animation on the title.** The galaxy is fully formed on load.

## Verification gate

1. `node scripts/check-css.mjs`
2. esbuild sweep, expect `ok=12 fail=0`
3. tsc filtered to TS2304, expect 0 undefined identifiers
4. brace / paren / bracket balance
