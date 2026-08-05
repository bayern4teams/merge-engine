# =============================================================================
# merge-engine Dockerfile (Part 1.6.C)
# =============================================================================
# The previous version of this file could not actually run the app: it
# referenced the removed Gradio UI, defaulted to `--mode both` (CLAUDE.md:
# "--mode both is broken"), exposed a port for a transport that no longer
# exists, and never ran `npm run build` — so the container would have booted
# straight into src/api/main.py's `{"status": "api-only"}` stub with no UI.
# This build has three stages: build the React frontend, install Python
# deps, then assemble the runtime image with both.
#
# Build:
#   docker build -t merge-engine:latest .
#
# Run (standalone — point it at origin/destination DBs you already have):
#   docker run -p 7860:7860 \
#     -e LLM_API_KEY=sk-xxx \
#     merge-engine:latest
#
# Or via docker/app-compose.yml, which wires this image to the project's own
# Langfuse stack and documents the expected env file.
# =============================================================================

# ── Stage 1: frontend build ─────────────────────────────────────────────────
FROM node:22-slim AS frontend-builder

WORKDIR /build/frontend

COPY src/ui/frontend/package.json src/ui/frontend/package-lock.json ./
RUN npm ci

COPY src/ui/frontend/ ./
RUN npm run build

# ── Stage 2: Python dependency build ────────────────────────────────────────
FROM python:3.11-slim AS python-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=100

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 3: runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    MERGE_ENGINE_HOST=0.0.0.0

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

COPY --from=python-builder /install /usr/local

COPY pyproject.toml ./
COPY config/config.yaml config/config.yaml
COPY src/ src/

# Do NOT copy src/ui/frontend/{src,node_modules} — only the built bundle.
# The two earlier stages are the only place npm/node exist in this image.
COPY --from=frontend-builder /build/frontend/dist/ src/ui/frontend/dist/

RUN groupadd -r mergeuser && useradd -r -g mergeuser -d /app -s /sbin/nologin mergeuser
RUN chown -R mergeuser:mergeuser /app
USER mergeuser

# 7860 = FastAPI + the built React SPA (src/api/main.py). There is no
# separate UI port or MCP-server transport to expose — everything is this
# one process on this one port (see docs/architecture/overview.md).
EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:7860/ || exit 1

# The one real run command (CLAUDE.md's "App:" line) — --mode gradio is the
# legacy flag name; it serves FastAPI + the React SPA. --mode both is broken
# and must never be the default here.
CMD ["python", "-m", "src.main", "--mode", "gradio", "--port", "7860"]
