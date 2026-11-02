# mediaLab — v1 Design: Image Generation Service

**Status:** draft for review
**Date:** 2026-10-25
**Scope:** v1 only. Video/audio are explicitly out of scope but the data model is built to absorb them.

---

## 1. Problem

A personal service for generating and editing images through OpenRouter, deployed on a VPS behind Coolify. Reference material for the OpenRouter image API already exists in `internal-docs/openrouter-image-models.md` (61 models, 65 endpoints, per-endpoint capability matrix).

The service must:

1. Proxy all OpenRouter traffic server-side. No API key ever reaches the browser.
2. Require authentication between the frontend and the backend.
3. Treat generation as asynchronous jobs (image now, video later).
4. Support reference images wherever the chosen model supports them — including models whose reference cap is 0.
5. Persist generations and their assets.
6. Be testable end-to-end, automatically, **without** making real OpenRouter calls.

## 2. Non-goals (v1)

- No video, audio, or upscaling.
- No multi-user accounts, registration, or per-user API keys.
- No public/shareable generation pages.
- No job queue infrastructure (Redis/Celery). In-process runner only.
- No cost dashboard UI. (Per-job cost is stored; totals are available as JSON.)
- No img2img "mode" switch — reference images are a capability, not a mode (see §5).

## 3. Architecture

```
browser ──HTTPS──> FastAPI (single container)
                     ├── /api/*        JSON API, JWT httpOnly cookie
                     ├── /assets/*     served image bytes
                     ├── /            static SPA (built Vite output)
                     ├── SQLite       /data/medialab.db
                     ├── assets       /data/assets/<sha256>.<ext>
                     └── OpenRouter   server-side only, async job runner
```

**One container, one volume, no CORS.** Multi-stage Dockerfile: Node builds the SPA, Python image serves it plus the API. This removes an nginx hop, removes CORS config entirely (same origin), and is the least config to manage in Coolify. Tradeoff: a frontend change requires an image rebuild — acceptable at this scale.

**Framework:** FastAPI + Pydantic v2 + SQLAlchemy 2.0 (sync engine, `check_same_thread=False`). Uvicorn with **`--workers 1`** — required, see §9.

**Frontend:** Vite + React 19 + TypeScript + Tailwind v4 + shadcn/ui (Radix base) + TanStack Query + React Router.

## 4. Data model

Generic by design so video is additive, not a migration.

### `media_jobs`

| column | type | notes |
| --- | --- | --- |
| `id` | text PK | UUID4 hex |
| `kind` | text | `image` now; `video`, `audio` later |
| `model` | text | OpenRouter model slug |
| `provider_tag` | text? | resolved provider, if pinned |
| `prompt` | text | |
| `params` | JSON | normalized request params (see §6) |
| `status` | text | `queued` → `running` → `succeeded` \| `failed` \| `interrupted` |
| `error` | text? | human-readable; provider body in `error_detail` JSON |
| `error_detail` | JSON? | raw provider error |
| `cost_usd` | float? | from `usage.cost` when reported |
| `usage` | JSON? | raw `usage` object |
| `n_requested` | int | providers may return fewer |
| `n_received` | int? | |
| `created_at` / `started_at` / `finished_at` | datetime | UTC |

Index on `(created_at desc)` and `(status)`.

### `assets`

| column | type | notes |
| --- | --- | --- |
| `id` | text PK | UUID4 hex |
| `job_id` | text? FK | null for uploads not yet attached (allowed) |
| `role` | text | `input` \| `output` |
| `sha256` | text | content hash; identical bytes stored once |
| `content_type` | text | `image/png`, `image/svg+xml`, … |
| `filename` | text | `<sha256>.<ext>` on disk |
| `bytes` | int | |
| `width` / `height` | int? | null for SVG or if unknown |
| `source` | text | `provider` \| `upload` \| `fake` |
| `created_at` | datetime | |

### `spend_ledger`

Append-only `(id, job_id, amount_usd, created_at)` so daily/monthly caps survive job deletion and are cheap to aggregate.

No `users` table. The single operator is defined by env (§7).

## 5. Capability model (the core of the UI)

Model capabilities are a **static, typed, committed config file**, not a runtime fetch. `backend/app/data/models.json` is the single source of truth; `GET /api/models` serves it. It is generated offline from OpenRouter's `/api/v1/images/models` by `backend/scripts/gen_capabilities.py` (a promoted, simplified descendant of `internal-docs/gen-openrouter-image-models.py`).

Why static over live-fetch-and-cache:

- Tests are deterministic and need no network or fixtures.
- No TTL logic, no cache-miss states, no 503 path in the UI.
- The form's shape cannot change under the user because OpenRouter added a model mid-session.
- A model list change becomes a reviewable diff in git.

The cost is staleness — a newly released model is absent until someone runs the generator. That is the correct trade for a single-operator tool.

The file is validated against a Pydantic schema at import time; a malformed or hand-edited file fails at startup, not at request time. It carries `generated_at` and `source` metadata, surfaced in the UI as "catalogue updated <date>".

```jsonc
{
  "generated_at": "2026-10-25T10:50:00Z",
  "source": "https://openrouter.ai/api/v1/images/models",
  "models": [ { /* ModelCapabilities */ } ]
}
```

```ts
interface ModelCapabilities {
  id: string; name: string; description: string
  input_modalities: ("text" | "image" | "file")[]
  max_refs: number              // supported_parameters.input_references.max ?? 0
  n: { min: number; max: number }
  resolutions: string[]         // [] when unsupported
  aspect_ratios: string[]
  formats: string[]             // output_format
  backgrounds: string[]
  supports_compression: boolean
  supports_quality: boolean
  supports_seed: boolean
  passthrough_keys: string[]
  pricing: Record<string, unknown>
  endpoints: number
}
```

Rules:

- **The form is generated from the selected model's capabilities.** No hardcoded model list, no hardcoded dropdowns.
- **Reference images appear iff `max_refs > 0`.** Elsewhere the upload control is absent, not disabled.
- Picking a model that cannot do X removes X from the request, never sends an ignored field.
- Updating the catalogue is a developer action, not an endpoint: `python -m scripts.gen_capabilities` rewrites the file, then review and commit the diff. No `refresh` query parameter.
- `GET /api/models` is cached in process for the lifetime of the process (the file cannot change without a restart).
- Frontend types are **generated from the backend's OpenAPI schema** (`openapi-typescript` into `src/lib/api-types.ts`, refreshed by an `npm run gen:types` script) so the catalogue shape cannot drift between Python and TypeScript.

## 6. Request normalization

The frontend sends our own schema; the OpenRouter adapter translates. This is the seam that keeps video from becoming a rewrite.

```jsonc
POST /api/generations
{
  "model": "google/gemini-3-pro-image",
  "prompt": "a red panda astronaut",
  "n": 1,
  "resolution": "1K",            // omitted if unsupported
  "aspect_ratio": "16:9",
  "output_format": "png",
  "background": "auto",
  "quality": null,
  "seed": null,
  "reference_asset_ids": ["…"],  // ≤ model.max_refs, validated
  "passthrough": { "black-forest-labs": { "steps": 40 } }  // validated against allowlist
}
```

The adapter:

1. Rejects any field the model does not support (422 with a field-level message) rather than silently dropping.
2. Loads each `reference_asset_ids` file, base64-encodes as a `data:` URL, and sends `input_references`. **Base64, not URL** — the VPS may not be publicly reachable for OpenRouter to fetch.
3. Injects `provider.options[slug]` only for allowlisted keys.
4. Sets `session_id` to the job id for OpenRouter-side observability.

`GET /api/generations/{id}` returns status, error, `cost_usd`, and the output asset list.

## 7. Auth

Single operator, real auth on every endpoint.

- `POST /api/auth/login` `{password}` → `204` + `Set-Cookie: ml_session=<JWT>; HttpOnly; SameSite=Lax; Secure; Path=/`
- `GET /api/auth/me` → `{authenticated: true}`
- `POST /api/auth/logout` → clears cookie
- JWT: HS256, `sub: "operator"`, 7-day expiry, signed with `SESSION_SECRET`.
- Password: `ADMIN_PASSWORD_HASH` (bcrypt) in env. A `python -m app.hashpw` helper generates it.
- Login is rate-limited: 5 failures / 15 min / IP, in-memory.
- All `/api/*` routes except `login` and `health` require the cookie. `401` for missing/expired, `403` never used for auth.
- CSRF: `SameSite=Lax` cookie + all mutations are `POST`/`DELETE` with `Content-Type: application/json` + a required `X-Requested-With: fetch` header check. No cookie-authenticated state-changing GETs.

## 8. Job runner

`POST /api/generations` validates, inserts a `queued` row, returns `202 {id}` immediately, and schedules an `asyncio` task.

- `queued` → `running` (provider call, 120s timeout) → `succeeded` with assets, or `failed`.
- On success: for each returned image, decode `b64_json` (or download `url`), hash, dedupe-store, insert `assets`.
- Always write `cost_usd` and `usage` if present, plus a `spend_ledger` row.
- On app startup: any job left `queued`/`running` is marked `interrupted` with error `"server restarted"`. The UI offers "retry" (creates a new job with the same params).
- Cancellation: `DELETE /api/generations/{id}` sets `cancelled`; the runner checks before and after the provider call (cannot abort an in-flight HTTP request mid-body, and says so in the UI).

## 9. Constraints and honesty

- **Single worker.** In-process tasks vanish on restart with more workers. Enforced in the Dockerfile CMD and documented. When this hurts, the fix is a `jobs` table + a separate worker process, not Celery.
- **Provider URLs expire.** Output is always downloaded/copied server-side; the original URL is never the source of truth.
- **Providers may return fewer images than `n`.** `n_requested` vs `n_received` is recorded and surfaced.
- **Some models are single-image only** (31 endpoints reject `n > 1`). The capability data enforces `n.max = 1`.
- **The catalogue is a snapshot.** `models.json` goes stale by design; `generated_at` is shown in the UI. Recorded spend comes from the provider response, never from the catalogue.

## 10. Cost control

Cheap, included, no UI:

- `max_n` is driven by capabilities; `MAX_N_HARD_CAP` (default 4) is a config backstop.
- `DAILY_BUDGET_USD` (optional). Before dispatch, sum today's `spend_ledger`; if the job's *estimated* cost (from per-endpoint pricing × n) plus today's spend exceeds the cap, return `429` with the numbers. Estimated, because real cost is only known after the call.
- `GET /api/usage?period=today|month` → `{spend_usd, jobs, images}`.

## 11. UI (v1)

Three surfaces, single-column, light-first, generous whitespace.

1. **Login** — password only.
2. **Generate** — left: prompt textarea, model picker (searchable `Command`), dynamic param controls, reference dropzone (conditional). Right: result panel with status, timing, cost, per-image download, and "use as reference" (feeds the asset id back in, one click).
3. **History** — infinite-scroll grid, filter by model, click → detail; retry, reuse prompt, delete.

Theme: near-white canvas, `new-york` shadcn style, one accent colour. Dark mode is a later toggle, not designed now.

## 12. Testing

Two tiers. **No test that touches OpenRouter runs by default.**

### Fake provider

`providers/fake.py` implements the same `ImageProvider` protocol. It does three things:

1. **Records the exact outbound payload** it *would* have sent to OpenRouter.
2. Sleeps `FAKE_LATENCY_MS` (default 50) to exercise polling.
3. Emits a deterministic generated PNG (or an SVG for vector models) derived from the prompt hash.

Because it records the payload, tests assert the **exact** OpenRouter request body against golden JSON — real mapping coverage with zero network.

### Backend (`pytest`)

- **Unit:** param mapping per model family; `models.json` schema validation + a test asserting every model has sane capabilities (e.g. `max_refs >= 0`, `n.max >= n.min`, no empty `aspect_ratios` when the model supports `aspect_ratio`); auth/JWT; ref-count validation; budget math; passthrough allowlist rejection.
- **Integration:** `httpx.ASGITransport` against the app with `PROVIDER=fake` and a tmp SQLite DB. Full path per test: login → `/api/models` → upload ref → create job → poll to `succeeded` → `GET /assets/{id}` returns non-empty image bytes → `/api/usage` reflects spend.
- **Failure paths:** provider error → `failed` with detail; restart mid-job → `interrupted`; refs over cap → 422; unauthenticated → 401; budget exceeded → 429.
- **Live (opt-in):** `@pytest.mark.live`, deselected by default, `pytest -m live` runs **one** cheap generation against the cheapest model to prove the adapter still matches reality. Requires `OPENROUTER_API_KEY`.

### Frontend E2E (`Playwright`)

Drives the real stack: `uvicorn` (fake provider, tmp DB) + built SPA. Runs headless and unattended; `playwright test` is the entrypoint. Can attach to an existing Chrome over CDP (`connectOverCDP`) when a browser session is already open.

Scenarios:

1. Unauthenticated visit redirects to login; wrong password shows an error; correct password lands on Generate.
2. Text→image: pick model, submit, polling indicator, result image renders, download link works.
3. Reference flow: upload an image, model with `max_refs > 0` shows the dropzone, submit, output arrives; switch to a `max_refs = 0` model and the dropzone disappears.
4. Capability-driven form: selecting `recraft/*-vector` swaps the format control to SVG; selecting a single-image model clamps `n`.
5. History: new job appears, detail opens, retry creates a second job.
6. Failure path: `FAKE_FAIL=1` → job shows `failed` with a readable message.
7. Logout returns to login; back-button does not restore the app.

Fixtures: one deterministic PNG generated in the test setup. No network in E2E.

## 13. Deployment (Coolify)

- `Dockerfile` — multi-stage: `node:22` build → `python:3.12-slim` runtime, `uvicorn --workers 1 --host 0.0.0.0 --port 8000`.
- Persistent volume mounted at `/data` (SQLite db, assets, models cache).
- `HEALTHCHECK` → `GET /health` (no auth, checks DB writability).
- Env: `OPENROUTER_API_KEY`, `SESSION_SECRET`, `ADMIN_PASSWORD_HASH`, `PROVIDER=openrouter|fake`, `DAILY_BUDGET_USD`, `DATA_DIR` (default `/data`).
- `.env.example` committed; `.env` ignored.
- Local dev: `PROVIDER=fake` by default so nothing costs money while building.

## 14. Repo layout

```
mediaLab/
├── docs/superpowers/specs/
├── backend/
│   ├── app/{main,config,db,models,schemas,auth,capabilities,storage,jobs}.py
│   ├── app/providers/{base,openrouter,fake}.py
│   ├── app/routers/{auth,models,generations,assets,usage,health}.py
│   ├── app/data/models.json          # committed, generated capability catalogue
│   ├── scripts/gen_capabilities.py   # regenerates models.json from OpenRouter
│   ├── tests/{unit,integration,live}/
│   └── pyproject.toml
├── frontend/
│   ├── src/{components/ui,features/generate,features/history,lib,routes}
│   └── components.json
├── e2e/workflows/*.spec.ts
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

## 15. Decisions recorded

| # | Decision | Choice |
| --- | --- | --- |
| 1 | Components | shadcn/ui, installed locally via CLI v4. Cross-project sharing later via a GitHub registry (`shadcn add papi656/ui-kit/…`), not a monorepo. |
| 2 | Frontend | Vite SPA. |
| 3 | v1 scope | Text→image **and** reference-image editing, capability-gated. No video. |
| 4 | Persistence | SQLite + `/data/assets`, SQLAlchemy; generic `media_jobs`. |
| 5 | Provider | Thin `ImageProvider` protocol; OpenRouter + Fake implementations. |
| 6 | Async | Job id + polling from day one. |
| 7 | Auth | Single-operator JWT cookie. |
| 8 | Cost | Recorded per job; optional daily cap; no dashboard. |
| 9 | Deploy | Single container on Coolify, one volume. |
| 10 | Tests | pytest (unit + integration) + Playwright E2E; live calls opt-in only. |
| A | Job schema | Generic `media_jobs` with `kind` + JSON `params`, so video/audio are additive. |
| B | Model catalogue | Static typed `models.json`, generated offline, served by `GET /api/models`. No runtime fetch. |
| C | Visibility | Private only. Generation ids are UUIDs anyway, so a public read-only view is additive later. |

## 16. Open questions

1. Confirm the UI direction in §11 (light-first, generous whitespace, one accent) — or was "white / space" referring to something else?
2. Should `DELETE` of a job also delete its output files, or keep bytes for dedupe (storage is cheap, files are content-addressed)? Default: keep files, delete rows, and add a `data/gc` command later.
3. Is 7 days the right session length, or do you want "never log out" on your own machine?
