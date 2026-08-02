## What this changes

<!-- One paragraph. The body of the commit message should say *why*; this says
what a reviewer is about to read. -->

Closes #

## Why

## Type

- [ ] `feat` new capability
- [ ] `fix` corrects a defect (name the literal error text in the commit body)
- [ ] `test`
- [ ] `docs`
- [ ] `ci`
- [ ] `web`
- [ ] `data` artifacts or generator
- [ ] `deploy`

## Evidence

<!-- Numbers, not adjectives. -->

```
python -m unittest discover -s tests
```

Result:

- Tests before / after:
- If the model changed, the operating-point table before and after:

| | recall@50 | precision@50 | AP |
| --- | --- | --- | --- |
| before | | | |
| after | | | |

## Checklist

- [ ] Branch follows `CONTRIBUTING.md`: topic branch off `develop`, merged
      `--no-ff`, Conventional Commit subjects in the imperative
- [ ] No new runtime dependency, or the case for one is argued above
- [ ] Any number quoted in prose is generated, not typed by hand
- [ ] `docs/` updated if behaviour or an interface changed
- [ ] CHANGELOG entry added under an Unreleased or new version heading
- [ ] For web changes: `npm run build` passes, which runs `check-css.mjs` and
      `tsc --noEmit` before Vite

## Anything you are unsure about

<!-- Say so plainly. A PR that flags its own weak point reviews faster than one
that hides it. -->
