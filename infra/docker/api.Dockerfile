# syntax=docker/dockerfile:1
# Multi-stage build for the FastAPI backend.

FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies into an isolated venv we copy into the runtime stage.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt


FROM python:3.12-slim AS runtime

# Create an unprivileged user to run the app.
RUN groupadd --system app && useradd --system --gid app --create-home app

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# curl is used by the container HEALTHCHECK.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
COPY alembic.ini ./alembic.ini
COPY backend ./backend
COPY ml ./ml
COPY scripts ./scripts

# Fail the image build when the lean API runtime manifest omits a dependency
# needed by the actual FastAPI import graph. The development CI environment
# is intentionally broader than this production image.
RUN PYTHONPATH=/app/backend:/app python -c "from backend.app.main import app; assert app is not None"

USER app

EXPOSE 10000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT:-10000}/health/live" || exit 1

# Run database migrations before accepting requests. Render injects PORT at runtime.
CMD ["sh", "-c", "alembic upgrade head && if [ \"${RUN_DB_MIGRATION:-false}\" = \"true\" ]; then python scripts/migrate_render_to_neon.py; fi && if [ \"${RUN_MARKET_DATA_DRY_RUN:-false}\" = \"true\" ]; then python scripts/dry_run_market_import.py; fi && if [ \"${RUN_MARKET_DATA_IMPORT:-false}\" = \"true\" ]; then python scripts/import_market_data.py & echo MARKET_DATA_IMPORT_STARTED; fi && exec uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
