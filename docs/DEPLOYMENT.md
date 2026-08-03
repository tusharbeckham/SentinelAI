# Deploying SentinelAI

The public demo is a **Hugging Face Docker Space**: one container, one port,
serving the React console *and* the authenticated scoring API. The browser
talks to a single origin, so there is no CORS layer to misconfigure.

    browser -> :7860 sentinelai.space_server
                        |-- /            static React bundle from web/dist
                        |-- /v1/*        reverse-proxied to sentinelai.api on 127.0.0.1:8088
                        '-- /healthz     reverse-proxied

The API is **not** made public by the proxy. Every `/v1/*` route still
requires a signed JWT with the right role, and the responder stays in
dry-run mode. A demo that quietly disables its own authentication is not a
demo of a security product.

## Why there is a lockfile now (and why it matters)

`web/package.json` lists **ranges**, not versions. `three` is `^0.166.1`,
which means *any* 0.166.x or 0.16y.z release npm considers compatible. It
describes what the project will tolerate.

`web/package-lock.json` records the **exact** version and integrity hash of
every package that was actually installed, including the hundreds of
transitive dependencies you never named. It describes what was really built.

The old Space build ran `pnpm install --no-frozen-lockfile`, which resolves
those ranges fresh on every build. The consequence is worth stating plainly:
**the container built in October could contain different code than the one
built in August, with no commit in this repository to explain it.** For a
project whose claim is that every number is measured and reproducible, a
build that cannot reproduce itself undercuts the argument.

So the Space now runs `npm ci`, which:

- installs the exact tree in the lockfile, ignoring the ranges entirely,
- **refuses to run** if `package.json` and the lockfile disagree, instead of
  quietly fixing it up the way `npm install` would,
- deletes `node_modules` first, so no stale package survives a build.

npm was chosen over pnpm for a boring reason: it is what this repo already
used locally, it ships inside the `node:22-alpine` image, and it needs no
`corepack` bootstrap. `corepack enable && pnpm ...` with no `packageManager`
field pinned would have tried to download a package manager at build time,
which is one more unpinned thing in a build we just finished pinning.

### Generating it

```bash
cd web
npm install          # writes package-lock.json from package.json
npm run build        # prove the bundle actually compiles before deploying
cd ..
git add web/package-lock.json
git commit -m "build(web): commit the npm lockfile so Space builds are reproducible"
```

Run `npm install` again whenever you change a dependency, and commit the
resulting lockfile churn in the same commit as the `package.json` change.

## The `space` branch

Hugging Face requires two things at the **root** of the Space repository:

1. a `Dockerfile` (that exact name), and
2. a `README.md` beginning with a YAML frontmatter block that configures the
   Space.

Neither can live on `main` without damage: this repo's root `Dockerfile` is
the CI/pipeline image, and its `README.md` is the project write-up, not a
config file. So deployment lives on a dedicated `space` branch, which is
`main` plus exactly two overrides:

- `Dockerfile` <- a copy of `Dockerfile.space`
- `README.md`  <- the Space card (`docs/space/README-space.md`)

### First deploy

```bash
# 1. Create the Space in the HF web UI:
#      SDK = Docker, template = Blank, hardware = CPU basic (free)
#    Do NOT tick 'Add a README' - the branch below supplies it.

# 2. Point this repo at it and push the space branch to the Space's main.
git remote add hf https://huggingface.co/spaces/<your-user>/SentinelAI
git push hf space:main
```

Authentication is an HF access token with **write** scope, from
<https://huggingface.co/settings/tokens>, entered as the password when git
prompts. It is a different token from your GitHub PAT.

### Every deploy after that

```bash
git checkout space
git merge main
# README.md and Dockerfile will conflict whenever main changed them.
# The space branch's versions are always the correct ones:
git checkout --ours README.md Dockerfile
git add README.md Dockerfile
git commit --no-edit
git push hf space:main
git checkout main
```

That conflict is expected, not a mistake. It is the branch telling you the
two files it deliberately owns were touched upstream.

## What the container does at boot

1. Generates an ephemeral `SENTINELAI_JWT_SECRET` if none is configured, so
   tokens live exactly as long as the container and nobody inherits a
   hardcoded key from a public repo. Set a real secret in **Space settings
   -> Variables and secrets** only if you need tokens to survive restarts.
2. Fits the pipeline on `--days 1.0` of synthetic telemetry, which is a
   quarter of the corpus used for the published numbers. Expect roughly half
   a minute of startup on free CPU hardware.
3. Serves `web/dist` and proxies the API on port 7860.

The **console's charts do not depend on that boot fit.** They render the
committed `artifacts/*.json` baked into the image at build time, which is why
the site shows the full 4-day measured results while the live `/v1/score`
endpoint answers from the smaller boot-time model. Keeping those two separate
is deliberate: the published numbers must not silently change because a
container restarted.

## Free-tier limits worth knowing

- CPU basic is 2 vCPU / 16 GB, no GPU. The project is pure NumPy, so this is
  sufficient; nothing here needs a GPU.
- Spaces sleep after inactivity and cold-start on the next request. The first
  visitor after a nap waits for the boot fit.
- The build log is public. That is fine - the image contains no secret.

## Local verification before pushing

```bash
docker build -f Dockerfile.space -t sentinelai-space .
docker run --rm -p 7860:7860 sentinelai-space
# then open http://localhost:7860
```

Do this before the first `git push hf`. A Space build failure is slower to
read than a local one, and the local run is the only place you will notice a
blank console caused by a missing artifact.
