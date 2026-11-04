# mediaLab

A private image generation service. The browser talks only to this server; the server
talks to [OpenRouter](https://openrouter.ai). No API key ever reaches the client.

Image generation now, with a data model built so video and audio are additive rather
than a migration.

**Spec:** `docs/superpowers/specs/2026-10-25-medialab-image-v1-design.md`

---

## What it does

- **Text → image** across 60 OpenRouter image models.
- **Reference images** (img2img / editing / style refs) wherever the chosen model
  supports them — per-model caps from 0 to 20. This is a capability, not a mode:
  the upload control appears only for models that accept references.
- **Async jobs.** `POST` returns a job id; the UI polls. Video will reuse the same path.
- **History** with soft delete, 5-second Undo, and a background GC that reclaims disk.
- **Per-job cost** recorded from the provider's own `usage`, plus an optional daily cap.

## Quick start (no API key, no cost)

```bash
cd backend
cp .env.example .env        # PROVIDER=fake, ADMIN_PASSWORD=medialab
uv sync
uv run uvicorn app.main:create_app --factory --port 8000 --workers 1
```

```bash
cd frontend
npm install
npm run dev                 # http://localhost:5173, proxies /api to :8000
```

Log in with `medialab`. The fake provider produces real images (deterministic
gradients derived from the prompt) after ~50 ms, so the full UI is usable offline.

## Going live

```bash
# in backend/.env
PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...
ADMIN_PASSWORD_HASH=...     # uv run python -m app.hashpw
SESSION_SECRET=...          # python -c "import secrets;print(secrets.token_urlsafe(48))"
DAILY_BUDGET_USD=5          # optional
```

## Deploy (Coolify)

Single container, single volume. `Dockerfile` builds the SPA with Node and serves it
from FastAPI, so there is no CORS and no nginx.

1. Point Coolify at this repo, build with the Dockerfile.
2. Set the env vars from `backend/.env.example`.
3. Mount a persistent volume at `/data` (SQLite, images, nothing else).
4. Expose port `8000`.

`--workers 1` is not a mistake — see *Constraints*.

## Updating the model catalogue

`backend/app/data/models.json` is a committed snapshot; the server never fetches it at
runtime, so tests are deterministic and a model-list change is a reviewable diff.

```bash
cd backend
uv run python -m scripts.gen_capabilities   # rewrites app/data/models.json
git diff app/data/models.json
```

## Tests

```bash
cd backend && uv run pytest                  # 74 tests, no network
cd backend && uv run pytest -m live          # one real, cheap OpenRouter call
```

Tests use a fake provider that **records the exact payload it would have sent to
OpenRouter**, so mapping coverage is real without spending money. Live tests are
deselected by default by the `-m 'not live'` in `pyproject.toml`.

## Constraints (deliberate)

- **One worker.** Jobs run in-process; a second worker would strand them. On restart,
  unfinished jobs are marked `interrupted` rather than hanging. The fix, when needed,
  is a worker process reading the `media_jobs` table — not Celery.
- **Files are content-addressed and shared.** Two jobs producing identical bytes point
  at one file, so deletion is reference-counted by SHA-256. GC deletes rows before
  files, and never unlinks a file another job still references.
- **Provider URLs expire**, so output is always downloaded server-side.
- **Providers may return fewer images than `n`**; both numbers are stored and shown.
- **Deleted jobs linger for 7 days** (`SOFT_DELETE_RETENTION_DAYS`) so Undo is real.

## Layout

```
backend/
  app/
    main.py            app factory, SPA mount, CSRF guard, lifespan
    capabilities.py    catalogue loading + per-model request validation
    jobs.py            in-process runner, status transitions, spend
    gc.py              reference-counted retention sweep
    providers/         base protocol, openrouter adapter, fake
    routers/           auth, models, generations, assets, usage, health
    data/models.json   committed capability catalogue
  scripts/gen_capabilities.py
frontend/src/
  features/generate/   capability-driven form and result panel
  routes/              Login, Generate, History
```
