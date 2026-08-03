# ---------------------------------------------------------------- build stage
# Pinned digest-free but version-pinned base: reproducible enough for a
# portfolio project, honest about the fact that a real deployment would pin the
# sha256 digest and rebuild weekly for CVE patches.
FROM python:3.13-slim AS base

# Fail fast, no stale .pyc, no pip version chatter in logs.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first so layer caching survives source edits.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY sentinelai/ ./sentinelai/
COPY tests/ ./tests/
COPY README.md ./
# tests/test_openapi_parity.py asserts the committed spec equals the generated
# one, so the in-image suite needs it. Without this the build fails with
# FileNotFoundError on /app/docs/openapi.json - a test that passes on the runner
# and only fails inside the image.
COPY docs/openapi.json ./docs/openapi.json

# The image is only published if its own test suite passes inside the image.
# A container that cannot prove its model code works is not a release artifact.
RUN python -m unittest discover -s tests -q

# --------------------------------------------------------------- runtime stage
FROM base AS runtime

# Least privilege: the scoring API never needs root, and it never writes to the
# source tree. Artifacts go to a dedicated writable volume mount.
RUN useradd --create-home --uid 10001 sentinel \
    && mkdir -p /app/artifacts \
    && chown -R sentinel:sentinel /app/artifacts
USER 10001

# SENTINELAI_JWT_SECRET is deliberately NOT baked in. serve.py exits non-zero
# when it is missing, so a misconfigured deployment fails closed instead of
# starting an unauthenticated API. Inject it from the orchestrator's secret
# store (Kubernetes Secret, Vault agent, ECS Secrets Manager).
ENV SENTINELAI_HOST=0.0.0.0 \
    SENTINELAI_PORT=8088

EXPOSE 8088

# /healthz is the only unauthenticated route, which is exactly what a liveness
# probe should be allowed to reach.
HEALTHCHECK --interval=30s --timeout=3s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8088/healthz',timeout=2).status==200 else 1)"

ENTRYPOINT ["python", "-m", "sentinelai.serve"]
CMD ["--host", "0.0.0.0", "--port", "8088", "--days", "1.5"]
