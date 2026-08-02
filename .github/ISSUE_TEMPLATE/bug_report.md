---
name: Bug report
about: Something behaves differently from what the README or the docs claim
title: ""
labels: bug
assignees: ""
---

## What happened

<!-- The literal error text, not a paraphrase. Paste the whole traceback inside
a fenced block. A summary loses the one line that identifies the cause. -->

```
paste here
```

## What you expected

<!-- Quote the README or doc line that led you to expect it, if there is one.
If the docs are what is wrong, say so; that is still a bug. -->

## Reproduction

1.
2.
3.

## Environment

- OS and version:
- Python version (`python -V`):
- Commit SHA (`git rev-parse --short HEAD`):
- Installed via zip or clone:

## Checks

- [ ] `python -m unittest discover -s tests` was run; result pasted below
- [ ] The failure reproduces on a clean checkout
- [ ] `web/public/data` is populated, if the issue is in the console

```
test output here
```

## Notes

<!-- Anything you already ruled out. Negative results save real time. -->
