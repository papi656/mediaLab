#!/usr/bin/env bash
# Boots the real stack for Playwright: built SPA + FastAPI + fake provider.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export DATA_DIR="${E2E_DATA_DIR:-/tmp/medialab-e2e}"
export PROVIDER=fake
export ADMIN_PASSWORD=medialab
export ADMIN_PASSWORD_HASH=""
export SESSION_SECRET=e2e-secret-at-least-32-bytes-long-here
export STATIC_DIR="$ROOT/frontend/dist"
export FAKE_LATENCY_MS="${FAKE_LATENCY_MS:-150}"
export GC_INTERVAL_HOURS=1000
export DAILY_BUDGET_USD=""

rm -rf "$DATA_DIR"
cd "$ROOT/backend"
exec uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8011 --workers 1
