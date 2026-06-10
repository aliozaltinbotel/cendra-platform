# ══════════════════════════════════════════════════════════
# Brain Engine — Production Dockerfile
# Multi-stage build, non-root user, optimized layers
# ══════════════════════════════════════════════════════════

# ── Stage 1: Dependencies ──
FROM python:3.11-slim AS deps

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir \
    torch --extra-index-url https://download.pytorch.org/whl/cpu \
    -r requirements.txt

# ── Stage 2: Production ──
FROM python:3.11-slim AS production

WORKDIR /app

# System deps (runtime only)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

# Non-root user
RUN groupadd -r brain && useradd -r -g brain -d /app brain

# Copy installed packages from deps stage
COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Copy application code
COPY brain_engine/ brain_engine/
COPY api_server/ api_server/
COPY config/ config/
# Stage 2 out-of-process worker (run as `python -m workers.bootstrap_worker`).
# Shipped in the same image as the API; without this COPY the worker
# Deployment crash-loops with ModuleNotFoundError: No module named 'workers'.
COPY workers/ workers/

# Foundation Layer ground truth — read at startup by foundation_registry,
# scenario_matcher, intelligent_classifier_factory and the rule_creation
# workflow.  WORKDIR is /app (line 23) so callers that resolve the file
# as <repo_root>/<filename> land on /app/<filename>.  The Proactive
# companion MD is deliberately omitted; all Proactive work
# (FL-02 / FL-08-11) is parked per the foundation roadmap.
COPY Cendra_Brain_Engine_Hospitality_Scenario_Pattern_Learning_Foundation.md .

# Create data directories
RUN mkdir -p data/photos/before data/photos/after data/sessions data/vector_db && \
    chown -R brain:brain /app

# Switch to non-root
USER brain

# Environment
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV ENVIRONMENT=production
ENV LOG_LEVEL=info
ENV WORKERS=4

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

CMD ["sh", "-c", "uvicorn api_server.server:app --host 0.0.0.0 --port 8000 --workers ${WORKERS}"]
