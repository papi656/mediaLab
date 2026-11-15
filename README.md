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

With `PROVIDER=openrouter` the server refuses to boot if `SESSION_SECRET` is still the
value in this repo, is shorter than 32 bytes, if no password and no API key are set, if
`DAILY_BUDGET_USD` is unset, or if `FORWARDED_ALLOW_IPS` contains `*`. Those are startup
errors, not first-request errors.

## Deploy (Cloudflare Tunnel → Coolify)

Single container, single volume, one ingress. `Dockerfile` builds the SPA with Node and
serves it from FastAPI, so there is no CORS and no nginx. The container is reached only
through a Cloudflare Tunnel: **no inbound port on the VPS, no Let's Encrypt, no Traefik
router for this app.**

Run `docs/vps-runbook.md` in order. The short version:

1. `cloudflared` connector on the host (systemd), one tunnel, ingress table in
   `deploy/cloudflared/config.yml` → `http://127.0.0.1:8000`.
2. In Coolify: Dockerfile build pack, *Ports Mappings* `127.0.0.1:8000:8000`, **no
domain**, bind mount `/srv/medialab/data:/data` pre-chowned to `10001:10001`, and
*Pre-deployment Command* `alembic upgrade head` — the schema is migrated against the new
image while the old container keeps serving, so a failing migration aborts the deploy
instead of causing an outage.
3. Env vars from `backend/.env.example`, set in the Coolify UI — not a committed `.env`.
   `FORWARDED_ALLOW_IPS` must be the Docker gateway in front of the container (never `*`),
   `RUN_MIGRATIONS_ON_START=0` because the pre-deployment step migrates, and
   `DAILY_BUDGET_USD` is mandatory once `PROVIDER=openrouter`.
4. Verify with runbook §6 — especially the rotating-`X-Forwarded-For` check, which is the
   one that proves the login limiter is not bypassable.

`--workers 1` is not a mistake — see *Constraints*. Deploys interrupt in-flight jobs;
that is accepted, and Retry is the recovery path.

**Read `docs/vps-deployment-decisions.md` before the first deploy.** It records the ingress,
proxy-trust, cookie, disk-cap, migration and backup decisions — and, at the time of
writing, that D4 (disk/RAM caps), D7 (Alembic) and D9 (backups) are **not implemented
yet**.

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
cd backend && uv run pytest                  # 112 tests, no network
cd backend && uv run pytest -m live          # one real, cheap OpenRouter call
```

Tests use a fake provider that **records the exact payload it would have sent to
OpenRouter**, so mapping coverage is real without spending money. Live tests are
deselected by default by `-m 'not live and not e2e'` in `pyproject.toml`; the e2e
suite needs a built frontend and a real Chrome (§`backend/tests/e2e`).

## Constraints (deliberate)

- **One worker.** Jobs run in-process; a second worker would strand them. On restart,
  unfinished jobs are marked `interrupted` rather than hanging. The fix, when needed,
  is a worker process reading the `media_jobs` table — not Celery.
- **Files are content-addressed and shared.** Two jobs producing identical bytes point
  at one file, so deletion is reference-counted by SHA-256. GC deletes rows before
  files, and never unlinks a file another job still references.
- **A reference image belongs to the job that last used it.** Uploads start unowned and
  the GC sweeps unowned ones after 24 h, so creating a job links its references to it.
  A retry that finds a reference gone fails loudly instead of quietly generating from
  the prompt alone.
- **Provider URLs expire**, so output is always downloaded server-side.
- **Providers may return fewer images than `n`**; both numbers are stored and shown.
- **Deleted jobs linger for 7 days** (`SOFT_DELETE_RETENTION_DAYS`) so Undo is real.
- **The schema is migrations, not `create_all`.** Alembic, one revision, and tests that
  fail if the models drift from it. Migrations must be expand-then-contract: the
  pre-deploy step changes the schema while the previous container is still serving.

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
  alembic.ini          migration config (no URL: DATA_DIR owns it)
  migrations/          one revision, "initial schema"; `alembic upgrade head` is the pre-deploy step
  scripts/gen_capabilities.py
frontend/src/
  features/generate/   capability-driven form and result panel
  routes/              Login, Generate, History
```
