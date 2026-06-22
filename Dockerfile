# =============================================================================
# merge-engine Dockerfile
# =============================================================================
# Build with:
#   docker build -t merge-engine:latest .
#
# Run with:
#   docker run -p 7860:7860 -p 8000:8000 \
#     -e OPENAI_API_KEY=sk-xxx \
#     -e CRM_DB_HOST=crm-db \
#     -e CRM_DB_USER=crm_user \
#     -e CRM_DB_PASSWORD=xxx \
#     -e CRM_DB_NAME=crm \
#     -e HR_MONGO_URI=mongodb://hr:27017/hr_system \
#     -e DATABRICKS_HOST=xxx.cloud.databricks.com \
#     -e DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/xxx \
#     -e DATABRICKS_TOKEN=dapiXXX \
#     merge-engine:latest
# =============================================================================

# ── Build stage ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=100

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY pyproject.toml requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime stage ───────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# Install runtime dependencies (libpq for psycopg2)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY pyproject.toml ./
COPY config/ config/
COPY src/ src/

# Create non-root user
RUN groupadd -r mergeuser && useradd -r -g mergeuser -d /app -s /sbin/nologin mergeuser
RUN chown -R mergeuser:mergeuser /app
USER mergeuser

# Expose ports
# 7860 = Gradio UI
# 8000 = MCP server (SSE transport)
EXPOSE 7860 8000

# Default command: start both MCP server and Gradio UI
CMD ["python", "-m", "src.main", "--mode", "both"]