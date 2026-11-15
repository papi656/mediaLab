# syntax=docker/dockerfile:1

# --- SPA build ---------------------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- API + static SPA --------------------------------------------------------
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    STATIC_DIR=/app/static \
    PATH=/app/.venv/bin:$PATH

RUN pip install --no-cache-dir uv==0.6.6

# Non-root from the first line that writes anything. 10001 is arbitrary but fixed:
# the host bind mount for /data is chowned to it once, before the first deploy.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app

WORKDIR /app
RUN chown app:app /app
COPY --chown=app:app backend/pyproject.toml backend/uv.lock ./
USER app
RUN uv sync --frozen --no-dev

COPY --chown=app:app backend/app ./app
# Migrations ship in the image because `alembic upgrade head` is the Coolify
# pre-deployment command (D7). Without these two COPYs the pre-deploy step fails with
# "Path doesn't exist: migrations" and no schema change can ever reach production.
COPY --chown=app:app backend/alembic.ini ./alembic.ini
COPY --chown=app:app backend/migrations ./migrations
COPY --from=web --chown=app:app /web/dist ./static

USER root
RUN mkdir -p /data && chown app:app /data
VOLUME ["/data"]
EXPOSE 8000

USER app

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"

# --workers 1 is required: jobs run in-process and would be orphaned by extra workers.
# --limit-concurrency bounds worst-case RAM (a request may hold ~20 base64 references).
# --forwarded-allow-ips is deliberately NOT on this line: uvicorn reads
# FORWARDED_ALLOW_IPS from the environment itself, and an exec-form CMD cannot
# expand a variable. app/config.py rejects "*", so the peer set is always a real
# address (the Docker gateway that NATs the loopback-published port).
CMD ["uvicorn", "app.main:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--proxy-headers", "--limit-concurrency", "8"]
