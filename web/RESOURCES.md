# Resources: what was used, how, and what was deliberately not

You sent eleven links and said "use them all in it". Seven of them are usable as
code and are used. Three cannot be dependencies of a real repo, and one is not a
UI resource at all. Pretending otherwise would be the exact thing you asked me to
avoid, so here is the honest breakdown.

## Used as code

| Resource | Licence / model | Where it lands in this repo |
| --- | --- | --- |
| [Motion](https://motion.dev/) (`motion`) | MIT, npm dependency | Every declarative animation: `ScrollReveal`, `Dock` layout pill (`layoutId`), `AnimatePresence` in the triage detail and SOAR rationale, `pathLength` line draw in `SweepChart`, all bar growth. |
| [anime.js v4](https://animejs.com/) (`animejs`) | MIT, npm dependency | `ThreatField` in `src/components/bits.tsx` - an imperative `createTimeline` stagger across 48 entity dots, which is genuinely awkward to express declaratively. Uses the v4 `createTimeline` / `utils.$` API, not the deprecated v3 `anime()` default export. |
| [React Bits](https://reactbits.dev/) | MIT, copy-in registry | `ShinyText` (wordmark), `CountUp` (every hero metric, landing exactly on the measured value), `Dock` interaction. |
| [Magic UI](https://magicui.design/) | MIT, shadcn-style registry | `BentoGrid` (hero metric grid), `Marquee` (the run-facts ticker), `AnimatedList` (alert queue and SOAR log). |
| [Vengeance UI](https://www.vengenceui.com/components/) | copy-paste registry | `GlowCard` pointer spotlight, driven by Motion values so hover never triggers a React re-render. |
| [Skiper UI](https://skiper-ui.com/) | mixed free / premium | The dock navigation interaction model. Only free patterns were used; no premium component was copied. |
| [Bklit UI](https://bklit.com/) | open source, shadcn registry | The data-viz idiom: `SweepChart`, `CompareBars`, `AttributionBars` and `ShimmeringText` follow its stat-card / chart conventions in hand-written SVG. |

**Important honesty note on "used":** these are *adaptations written from the
published patterns*, not files pulled from each registry's CLI. The build
environment had no network access, and vendoring owned source is also how these
registries are designed to be consumed - `npx shadcn add ...` copies code into
your repo rather than adding a runtime dependency. Attribution is repeated in the
header comment of `src/components/bits.tsx` and in the console footer.

## Not used, with reasons

| Resource | Why not |
| --- | --- |
| [AnimMasterLib](https://animmasterlib.dev/) | The 300-component library is behind a PRO paywall. I will not put a paid asset into a repo you intend to publish and put on a resume. |
| [MotionSites](https://motionsites.ai/) | It is a premium library of AI *prompts* for generating sites, not code. Using it would mean generating the page from a prompt, which is the "AI slop" outcome you explicitly rejected. |
| [Flames.blue](https://www.flames.blue/) | An AI app builder. Same problem, more so: the entire value of this project is that a human can defend every line in an interview. |
| [free-for.dev](https://free-for.dev/) | Not a UI library - it is a directory of free tiers. It was used as *research* for the deploy target, not as a dependency. See below. |

## Deploy targets, chosen with free-for.dev

| Option | Verdict |
| --- | --- |
| **Hugging Face Docker Space** | **Recommended.** Free, one public URL, and the only free option in this list that can run the Python scorer *and* serve the React bundle together. `Dockerfile.space` + `sentinelai/space_server.py` implement exactly this. |
| Vercel / Netlify / Cloudflare Pages | Great for the console, but static only - the authenticated scorer cannot run there. The app is built to degrade cleanly to static artifacts, so this is a valid "console only" deploy. |
| Streamlit Community Cloud | Cannot host a React SPA. Streamlit renders its own Python-defined widgets; using it would mean throwing away the front end you just asked for. Listed here because you mentioned it - it is the one option I would actively argue against. |
| Fly.io / Render free tier | Works like the Space (Docker, one port). Reasonable backup. |

## What makes this not generic

- Every number on the page is read from `artifacts/*.json` produced by
  `python -m sentinelai.pipeline`. There is no mock data anywhere; if the
  artifacts are missing the app says so instead of showing a fake dashboard.
- The failures are on screen with the same weight as the wins: 6.9% PPV at a
  realistic 1e-4 prior, 0% lateral-movement recall, 20% dns_tunnel recall, the
  hybrid losing to plain GBDT on average precision, and the `mean_pkt_size`
  shortcut labelled as a defect rather than a feature-importance flex.
- Colour is information, not decoration: green/amber/red only ever encode
  severity or pass/fail, and the budget axis is log-scaled because that is the
  shape of the real trade-off.
- Motion is subordinate to reading. Everything respects
  `prefers-reduced-motion`, `CountUp` always lands on the exact measured value,
  and there is no autoplaying hero video or gradient blob.
