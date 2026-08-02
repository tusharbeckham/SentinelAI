# The hero sequence

One document, replacing seven. `HERO-3D-PLAN.md` through `-V5`,
`HERO-OBJECT-PLAN.md` and `HERO-STAGE-PLAN.md` were written in sequence as the
design was argued out, and each one contradicted the last. Keeping all seven in
the repository invited a reader to follow a plan that had already been
abandoned. What follows is the design that shipped, plus an honest record of
what was tried and rejected, because the rejections are the useful part.

## 1. What the sequence is for

The landing page has to answer one question before a visitor scrolls to the
numbers: *what does this system actually do to a network event?* Prose answers
it slowly. The scroll sequence answers it by showing a single object being
transformed six times, once per stage of the pipeline.

It is not decoration. Every act corresponds to a real artifact, and every number
on screen is read from generated JSON rather than typed into the component.

## 2. Structure

A sticky full-height stage driven by scroll progress, six acts with an entrance
and an exit transition. Act boundaries live in one array,
`ACT_BOUNDARIES = [0.17, 0.3, 0.44, 0.58, 0.73, 0.86]`.

| Act | Stage | What is shown | Source |
| --- | --- | --- | --- |
| 01 | corpus | 11,326 windows, 40 hosts, 300 s | `report.json` |
| 02 | feature space | PC1 22.5%, PC2 12.5%, 11,257 benign | `embedding.json` |
| 03 | score axis | -10.40 to +4.30 | `report.json` |
| 04 | fusion | the stacker equation, p = 0.9866 | `alert_trace.json` |
| 05 | the plane | threshold 0.6303, 49 alerts, 39 TP | `budget_sweep.json` |
| 06 | response | `h002`, `auto_contain`, chain valid | `soar_decisions.json` |

The object carries the motion. Each act gives it a **distinct verb** -- fold,
axis, fuse, cut, respond -- rather than reusing one deformation at different
amplitudes. Camera movement is not a substitute for animation; an earlier draft
leaned on zoom and read as inert.

## 3. Entrance and exit

The hero title block and the object never blend. The title holds alone, the
object enters after the first swipe in the same place, and at the end of act 06
the whole stage fades on a single driver and the explainer console takes the
same position.

The exit is one opacity on the shared parent, not N independent fades. A
previous release faded the canvas only and left the HUD chrome floating over
nothing.

## 4. Constraints that shaped it

- **Not a particle field.** Point clouds were rejected repeatedly: they read as
  noise, not as a system, and no amount of tuning fixed that.
- **Solid, not glow.** `AdditiveBlending` with `depthWrite: false` and a
  fresnel-only material cannot produce a solid form; it always reads as an
  outline. Blending and depth are decided before colour.
- **One object.** Secondary rings, halos and orbiting marks were deleted rather
  than dimmed. Every element that survives has to earn the attention.
- **No legend for invisible marks.** When a layer is hidden, its label, legend
  and readout go in the same release.
- **WebGL2.** The shaders use `dFdx`/`dFdy`; three r166 targets WebGL2 by
  default.

## 5. Where the numbers come from

`web/scripts/sync-artifacts.mjs` copies generated JSON from `artifacts/` into
`web/public/data/`, which is gitignored. `report.json`, `alerts.json` and
`soar_decisions.json` are required; the rest, including `embedding.json`, are
optional and the component degrades without them.

`embedding.json` is a 2D projection of 11,326 scored windows produced on Kaggle
by `scripts/make_embedding.py`. It is 376 KB and does not need regenerating.

## 6. Rejected directions, and why

Recorded so nobody re-proposes them:

- **A lens, as on animejs.com.** Copying the reference too closely. The
  reference was for the *quality of motion*, not the shape.
- **A galaxy.** Attempted across three releases and never became convincing.
  Volumetric rendering of that kind is not achievable within the constraints
  here, and a bad galaxy is worse than a good abstraction.
- **An SVG strip.** Too flat to carry six stages.
- **A grid floor.** Read as a stock 3D template and said nothing about the data.
- **Stacking the object below the hero section.** The object replaces the hero
  in place after the swipe; it does not appear beneath it.

## 7. Accessibility

`prefers-reduced-motion` collapses the sequence to static act frames. The
sections below the fold contain every number the animation shows, so nothing is
conveyed by motion alone.
