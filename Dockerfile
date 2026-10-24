# =============================================================================
# One container, one Cloud Run service: Flask API + Next.js console.
#
# Next.js listens on $PORT (Cloud Run sets it; 8080 by default) and is the only
# thing reachable from outside. Flask listens on 127.0.0.1:9090 behind it and is
# reached two ways, both in frontend/src/lib/upstream.ts:
#
#   /api/proxy/*                   the console's allow-listed BFF, which attaches
#                                  the console's credential for a signed-in user
#   /api/v1/*, /health, /metrics,  the public API, passed through with the
#   /demo                          caller's own credential and nothing added
#
# entrypoint.sh starts Flask, waits for /health, then starts Next.js.
#
# Every base image is pinned by digest, and the runtime stage installs nothing
# from a package repository: Node is copied out of the pinned node image rather
# than fetched from NodeSource, so a build cannot silently pick up a different
# binary. Dockerfile.backend is the API-only image, for running Flask alone.
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: Python dependencies
# ---------------------------------------------------------------------------
FROM python:3.11-slim@sha256:da047cb8f9d1d98e5c070f5300ba9f7274e33b8fc0e5be5ed88740aed1b95ba9 AS py-builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.lock .
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.lock

# ---------------------------------------------------------------------------
# Stage 2: Next.js build (standalone output)
# ---------------------------------------------------------------------------
FROM node:22-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c AS node-builder

WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend/ .
# Inlined into the client bundle at build time; a runtime variable cannot
# change it afterwards. See frontend/src/lib/config.ts.
ENV NEXT_PUBLIC_DEMO_MODE=false
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 3: Runtime -- Python 3.11 + the Node binary, nothing else installed
# ---------------------------------------------------------------------------
FROM python:3.11-slim@sha256:da047cb8f9d1d98e5c070f5300ba9f7274e33b8fc0e5be5ed88740aed1b95ba9 AS runtime

# The standalone server needs only the node executable. Its shared libraries
# (libstdc++, libgcc_s, glibc) are already in this Debian base -- apt itself
# links libstdc++ -- and the check below fails the build if that ever changes.
COPY --from=node-builder /usr/local/bin/node /usr/local/bin/node
RUN node --version

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src
ENV PATH="/opt/venv/bin:$PATH"
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

COPY --from=py-builder /opt/venv /opt/venv
COPY src/ src/
# hs_reference.py resolves data/hs_reference.yaml relative to the repo root and
# reads it at import with no fallback, so a missing file stops the boot.
COPY data/hs_reference.yaml data/hs_reference.yaml
# The synthetic payment corpus payment_eval.load_corpus reads for GET /api/v1/defense/scenarios and /defense/evaluation. A file left out
# is not a build error: both routes answered 500 in the image while every test, run from a checkout, passed (tests/test_dockerfile_payment_corpus.py).
COPY data/payment_fraud_corpus.json data/payment_fraud_corpus.json
# The committed evaluation reports GET /api/v1/evaluation serves (evaluation.py).
# Aggregates only -- a few tens of KB -- so the console can show what was measured
# without a second deployment of anything. A directory missing here does not fail
# the build or the boot: the endpoint serves it as empty and the console drops the
# card, so tests/test_evaluation_endpoint.py checks each one evaluation.py reads.
COPY data/benchmark_results/ data/benchmark_results/
COPY data/eval_results/ data/eval_results/
COPY data/operating_curve/ data/operating_curve/
COPY data/model_comparison/ data/model_comparison/

# Next.js standalone output
COPY --from=node-builder /app/.next/standalone ./console/
COPY --from=node-builder /app/.next/static ./console/.next/static/
COPY --from=node-builder /app/public ./console/public/

COPY entrypoint.sh /app/entrypoint.sh
# CRs stripped in case the file was checked out with CRLF endings on Windows;
# .gitattributes prevents that, this makes the image independent of it.
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh

RUN groupadd --gid 1000 appgroup && \
    useradd --uid 1000 --gid appgroup --shell /bin/bash --create-home appuser && \
    chown -R appuser:appgroup /app
USER appuser

ENV PORT=8080
EXPOSE 8080

# Through Next.js to Flask, so it reports both processes. Cloud Run ignores
# HEALTHCHECK; `docker run` and CI's container smoke test do not.
HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8080'), timeout=5)" || exit 1

CMD ["/app/entrypoint.sh"]
