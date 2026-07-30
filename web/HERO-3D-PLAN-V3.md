# Hero 3D — Plan v3: "The Aperture"

Supersedes v2 (particle grid). v2 was a *diagram* of a network. It rendered as
drifting dots, and the reader's verdict was correct: "pixel circles".

## The concept

A lens does exactly what this pipeline does: it takes scattered rays arriving
from everywhere and converges them onto a single point. 563,619 flows in, one
alert out. So the hero is not a picture of a network — it is a **precision
optical instrument**, seen from inside the barrel.

And the payoff: **the iris is the alert budget.** Stopping the aperture down is
literally what raising the threshold does — fewer rays admitted, the survivors
brighter and more certain. The central metaphor of the whole project is a
mechanism the reader can watch move.

## Research log

| Source | Taken |
| --- | --- |
| animejs.com landing (Julian Garnier's "Learn" course rebuilds it in Three.js) | The read: one hero *object*, machined and lit, not a field of particles. Scroll drives one continuous mechanism. |
| Codrops — transparent glass & plastic in Three.js | `MeshPhysicalMaterial` + `transmission`; `thickness` is the property that sells glass, `ior` 1.46 for optical crown. |
| three.js `PMREMGenerator` + `RoomEnvironment` | Metal and glass need something to reflect. A procedural room env is the single biggest quality jump — zero assets, zero requests. |
| three.js `ExtrudeGeometry` / `Shape` | Iris blades as extruded curved shapes with a bevel; bevel is what catches the key light along the blade edge. |
| Volumetric beam port (TrentSterling), Codrops light rays | Rejected raymarching. Rays are instanced streak geometry authored above the bloom threshold — the bloom pass does the volumetrics. |
| v2 finding (drcmda, three.js forum) | Kept: bloom threshold 1.0 = free selective bloom. Still one composer, still `OutputPass` last. |

## Six acts, one mechanism

| # | Act | The instrument does |
| --- | --- | --- |
| 01 | Telemetry | Rays stream in from the dark, wide and unsorted. Iris wide open. |
| 02 | Features | Rays band into six coloured groups — the six feature bands. |
| 03 | Legs | Three lens elements light in turn: isolation forest, gradient boosting, graph. |
| 04 | Fusion | The elements stack into one optic; every ray bends to one focal point. |
| 05 | Threshold | The iris stops down to the budget. Most rays die at the blade plane. |
| 06 | Response | The survivor ignites the sensor; the containment field closes. |

## Bloom contract (unchanged from v2)

Threshold `1.0`. Above it = evidence, below it = instrument.

- **Below 1.0 (never blooms):** housing, iris blades, bezels, sensor grid.
- **Above 1.0 (blooms):** rays x2.0, focal core x2.8, lens emissive rims x1.5,
  containment field x2.4.

The machined parts stay matte metal so the light has something to be brighter
than. If everything glows, nothing does.

## Acceptance checklist

1. No text is ever duplicated in the DOM for an effect.
2. The title owns the first screen; the canvas fades up only after scroll.
3. The canvas fades out before the section ends, into the Explain handoff.
4. Native scroll only — no wheel hijacking.
5. `OutputPass` is the last pass.
6. Resize moves renderer, composer and bloom together.
7. Reduced motion gets the static title, no WebGL.
8. Every figure in the act cards matches an artifacts/ value.
9. One draw call for all rays.
