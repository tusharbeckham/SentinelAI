# Contributing / branching model

This repository uses a three-tier model. Nothing lands on `main` except through
`develop`, and nothing lands on `develop` except through a topic branch.

```
main        protected, release-only, tagged      v0.1.0-detection -> v0.2.0-platform -> v1.0.0
  ^
  | merge --no-ff at milestones
develop     integration branch, always green
  ^
  | merge --no-ff per reviewed topic
topic       feat/* fix/* test/* ci/* web/* data/* deploy/* docs/*
```

Every merge uses `--no-ff` on purpose. A fast-forward merge erases the fact that
a group of commits belonged to one piece of work; with `--no-ff` the branch stays
visible in `git log --graph` forever, which is what makes the history reviewable
after the branch is deleted.

## Branch prefixes

| Prefix    | Use for                                        | Example |
|-----------|------------------------------------------------|---------|
| `feat/`   | New capability                                 | `feat/graph-lateral-movement` |
| `fix/`    | Repair of observed, reproducible behaviour     | `fix/drift-panel-crash` |
| `test/`   | Coverage with no behaviour change              | `test/platform-coverage` |
| `ci/`     | Pipeline, scanning, automation                 | `ci/docker-and-actions` |
| `web/`    | Front-end console                              | `web/console-sections` |
| `data/`   | Measured artifacts                             | `data/measured-artifacts` |
| `deploy/` | Hosting and runtime packaging                  | `deploy/hf-docker-space` |
| `docs/`   | Documentation only                             | `docs/readme-and-resources` |

One concern per branch. If a branch needs the word "and" to describe it, it is
probably two branches.

## Commit messages

Conventional Commits: `type(scope): summary in the imperative mood`.

The subject says *what*; the body says *why*, and why the obvious alternative was
not chosen. A commit body that restates the diff is noise. A fix commit must name
the observed failure, ideally with the literal error text, so the next person
searching for that error finds the commit that fixed it.

## Daily loop

```bash
git checkout develop
git pull --ff-only origin develop      # never create a merge on pull
git checkout -b feat/my-thing

# ... work, in small commits ...

python -m unittest discover -s tests   # 46 tests must pass
cd web && npm run build && cd ..       # tsc --noEmit then vite build

git push -u origin feat/my-thing       # then open a PR into develop
```

`pull --ff-only` is deliberate: it fails loudly instead of silently creating a
merge commit that reverses the direction of history. If it fails, rebase:

```bash
git fetch origin
git rebase origin/develop
```

Rebase your own unpushed topic branch freely. Never rebase `develop` or `main`,
and never force-push a branch someone else has pulled.

## Definition of done

Before a topic branch is merged:

1. `python -m unittest discover -s tests` passes.
2. `cd web && npm run build` passes, which runs `tsc --noEmit` first.
3. New numbers in docs are traceable to `artifacts/report.json`, not remembered.
4. Known defects are documented, not omitted. This project reports graph
   AP 0.039 and near-zero zero-day recall at budget on purpose. Removing an
   inconvenient result is the one change that will be rejected outright.

## Releasing

```bash
git checkout main
git merge --no-ff develop -m "Release vX.Y.Z: <summary>"
git tag -a vX.Y.Z -m "<summary>"
git push origin main --follow-tags
```
