# mediaLab — VPS deployment decisions (Coolify)

**Status:** 12 decisions, 3 open questions, 6 closed questions. D1, D2, D3, D5, D6, D8,
D10 and D12 are **implemented**; D4, D7, D9 and D11's documentation are open.
**Date:** 2026-11-05 (D12 and the ingress rewrite: 2026-11-15)
**Scope:** everything standing between `main` and a real deploy on a VPS managed by
Coolify, reached through a Cloudflare Tunnel. Functional correctness is *not* in
question: the built image was run locally, and login → generate → `succeeded` worked
end-to-end.

**This file is also the record of what has already been settled.** §2 lists context that
is closed and must not be re-asked; §3 lists questions answered in review; §7 is the
order of work. If something in §2 needs to change, change it deliberately and update §1.

**Companion docs:** `docs/superpowers/specs/2026-10-25-medialab-image-v1-design.md`
(the design), `docs/vps-runbook.md` (the commands, in order), `deploy/cloudflared/config.yml`
(the ingress table), `README.md` §Deploy.

---

## 0. Verified baseline

Measured against `docker build` of the current tree, run with `PROVIDER=fake`. These
are facts, not expectations.

| Check | Result | Verdict |
| --- | --- | --- |
| `docker build .` | succeeds, 2 stages, ~5 s runtime stage | ok |
| `GET /health` | `{"status":"ok","database":true,"provider":"fake","models":60}` | ok |
| `GET /` | `200 text/html` (SPA mounted) | ok |
| login → job → poll | `queued` → `succeeded`, 1 asset, `cost_usd` recorded | ok |
| `Set-Cookie` over plain HTTP | `HttpOnly; Max-Age=604800; Path=/; SameSite=lax` | **no `Secure`** |
| 8 bad logins, rotating `X-Forwarded-For` | `401 × 8`, never `429` | **limiter bypassed** |
| 8 bad logins, one IP, no XFF | `401 × 5`, then `429` | limiter works |
| `docker compose config` | `env file /…/mediaLab/.env not found` | **fails out of the box** |
| `id` inside container | `uid=0(root)` | **root** |
| `ls -la /data` | `root root` | volume root-owned |
| `GET /api/docs`, `/api/openapi.json` | `200`, unauthenticated | **public** |
| `grep` for storage cap / quota | none exists | **unbounded disk** |
| `init_db()` | `Base.metadata.create_all` | **no migrations** |

The app is fine. The deployment story is not.

### 0.1 Re-verified after the ingress change (2026-11-15)

Measured against the tree that implements D1/D2/D3/D5/D6/D8/D10/D12. Rows above that this
change fixes are restated here rather than edited in place, so the original baseline stays
honest.

| Check | Result | Verdict |
| --- | --- | --- |
| `uv run pytest` | **105 passed, 4 deselected** | ok |
| `COOKIE_SECURE=` (empty, as shipped in `backend/.env.example`) | boots; blank means "derive from PROVIDER" | **fixed** |
| `Set-Cookie` with `COOKIE_SECURE=true` | `__Host-ml_session=…; HttpOnly; Path=/; SameSite=lax; Secure`, no `Domain` | **fixed** |
| `COOKIE_SECURE` flipped mid-session | the unprefixed legacy cookie still authenticates; logout clears both shapes | ok |
| `GET /api/docs`, `/api/openapi.json` | `404` unless `ENABLE_DOCS=1` | **fixed** |
| `FORWARDED_ALLOW_IPS` = `*`, `172.17.0.1, *`, or `" , "` | `ValueError` at startup | **fixed** |
| `PROVIDER=openrouter` without `DAILY_BUDGET_USD` | `ValueError` at startup | **fixed** |
| Container `id`, running the built image | `uid=10001(app)`, wrote `/data/probe` as 10001 | **fixed** |
| Peer address of a host process hitting a loopback-published port | the bridge **gateway** (`172.17.0.1` default, `10.77.0.1` on `10.77.0.0/16`), not `127.0.0.1` | measured — drives D2 and Q1 |
| Untrusted peer + spoofed XFF (`FORWARDED_ALLOW_IPS` unset) | `peer=172.17.0.1` — XFF ignored, limiter degrades to one bucket | safe-but-degraded |
| Trusted peer + client-prefixed XFF (`1.2.3.4, 198.51.100.7`) | `peer=198.51.100.7` — rightmost untrusted wins, exactly Cloudflare's append behaviour | **fixed** |
| `FORWARDED_ALLOW_IPS='*'` in the built image | exits non-zero with the never-`*` message | **fixed** |
| `openrouter` with loopback-only trust | logs the shared-bucket warning at startup | ok |
| SPA served from the image | `GET /` → `200 text/html` with `/api/docs` → `404` | ok |
| Tunnel path end-to-end on the VPS | runbook §6, **not yet run** | pending |

---

## 1. Decision log

| # | Decision | Status |
| --- | --- | --- |
| D1 | Ingress is the Cloudflare Tunnel; the container port is published on **loopback only** | ✅ done |
| D2 | `FORWARDED_ALLOW_IPS` names one peer (the Docker gateway), never `*`; rejected at startup | ✅ done |
| D3 | Cookies are always `Secure` (+ `__Host-` prefix); never inferred from a spoofable header | ✅ done |
| D4 | Bound the two resources that can kill the VPS: disk and RAM | decided |
| D5 | A spend cap is mandatory when `PROVIDER=openrouter` | ✅ done |
| D6 | Container runs as non-root (uid 10001); `/data` is a host bind mount, pre-chowned | ✅ done in the image; VPS steps in runbook §4 |
| D7 | Alembic from day one, run as a Coolify pre-deploy command (not at container start) | **not implemented** |
| D8 | Production config lives in Coolify env vars; `docker-compose.yml` is local-dev only | ✅ done |
| D9 | Nightly consistent SQLite backup + asset sync, off-host, with a restore drill | **not implemented** |
| D10 | `/api/docs` and `/api/openapi.json` are off unless `ENABLE_DOCS=1` | ✅ done |
| D11 | Single replica, `--workers 1`; deploys interrupt in-flight jobs (accepted v1 cost) | decided, runbook §4 |
| D12 | Ingress is a **Cloudflare Tunnel** (host connector, ingress table in git), not Traefik | ✅ done |
| Q1 | The Docker **gateway** address in front of the container (feeds `FORWARDED_ALLOW_IPS`) | **open** (needs the VPS) |
| Q2 | Backup target and retention (B2 / S3 / other) | **open** |
| Q3 | `DAILY_BUDGET_USD` value | **open** |
| Q4 | Cloudflare proxy mode (proxied vs DNS-only) | **closed** — a tunnel forces proxied; see D12 |

---

### D1 — Nothing on the VPS answers the internet except the tunnel — ✅ done

**Superseded in shape by D12.** The ingress is a tunnel connector on the host, not
Traefik, so "*Ports Exposes* only" became a **loopback publish**: `127.0.0.1:8000:8000`.
A connector running on the host cannot reach a port that exists only on a Docker
network, so `expose:` would put the app out of reach.

**Problem (unchanged).** Publishing `8000:8000` on a VPS makes the app answer plaintext
in parallel with whatever TLS front-end exists — the login password and the session
token one `curl` away — and lets every proxy-trust assumption in D2/D3 be sidestepped by
talking to the origin directly.

**Decision.** `docker-compose.yml` binds the published port to `127.0.0.1`. In Coolify the
app has *Ports Mappings* `127.0.0.1:8000:8000` and **no domain**, so no Traefik router and
no ACME certificate exist for it. The only listener the internet sees is Cloudflare's.

**Consequences.** No nginx, no CORS, no certificate code in the repo. TLS terminates
outside the VPS, so the app sees plain HTTP and must not infer `Secure` from the scheme
(D3). Because the last hop is host → Docker NAT, the container's peer is the bridge
gateway rather than `127.0.0.1` (D2, Q1).

**Verify.** From off-host, `nc -z <vps-ip> 80`, `443` and `8000` all fail (runbook §6.2);
`curl -sSI https://<domain>/health` returns `200` with `strict-transport-security`.

---

### D2 — Trust exactly one peer: the Docker gateway in front of the container — ✅ done

**Problem.** The image ran with `--forwarded-allow-ips "*"`, which trusts
`X-Forwarded-For` from *anyone who can reach the port*. Measured: rotating XFF defeated
the login limiter completely (`401 × 8`, no `429`).

**Decision.** uvicorn reads `FORWARDED_ALLOW_IPS` from the environment itself (verified in
the pinned uvicorn 0.54's `config.py`), so the Dockerfile passes no flag and needs no
shell expansion. `Settings` declares the same variable — one name, not two — and refuses
to boot with `*`, an empty value, or a list containing `*`.

The value is **one address**: the Docker gateway that NATs the connector's loopback
connection. Measured, so the runbook can be exact: a host process hitting a
loopback-published port appears inside the container as the network's gateway —
`172.17.0.1` on the default bridge, `10.77.0.1` for a network built on `10.77.0.0/16`.
Not `127.0.0.1`, which is the intuition that would quietly break the limiter.

**Why `X-Forwarded-For` and not `CF-Connecting-IP`.** Cloudflare *appends* the visitor
address to XFF, so uvicorn's right-to-left walk lands on the visitor even when the client
prefixed an XFF of its own. A second trusted header would be a second thing to get wrong
and no more accurate.

**Consequences.** uvicorn substitutes the XFF-derived client only when the peer matches,
so the limiter keys on the visitor. A wrong value degrades to one shared bucket —
detectable, not dangerous — and the app says so twice: a startup warning when
`PROVIDER != fake` and the trust list is loopback-only (what an unset variable looks like
inside a container), and a `login failed: peer=… cf-connecting-ip=…` line where the two
addresses disagreeing is the tell.

**Verify.** Runbook §6.5: rotating `X-Forwarded-For` still yields `429` by the sixth
attempt. Unit tests: `*`, `172.17.0.1, *` and `" , "` are all `ValueError` at startup.

---

### D3 — Cookies are `Secure` by configuration, not by sniffing headers — ✅ done

**Problem.** `_is_secure()` read `x-forwarded-proto` and fell back to
`request.url.scheme`. Two ways to get it wrong: a proxy that forgets the header yields a
cookie without `Secure` (measured locally), and a trusted-but-spoofable header yields
`Secure` on a plaintext connection — the worst of both. Behind a tunnel the app sees
plain HTTP *by construction*, so the header path was guaranteed to be wrong.

**Decision.** `COOKIE_SECURE: bool | None = None` in `Settings`, resolved by
`resolved_cookie_secure`: explicit value wins, otherwise red for anything that is not
`PROVIDER=fake`. So local HTTP dev keeps working with no flag and production cannot opt
out by accident. The cookie name is `__Host-ml_session` when secure — a `__Host-` cookie
cannot be set without `Secure`, cannot carry a `Domain`, and cannot be overwritten by a
sibling subdomain. `_is_secure()` is deleted.

Two details that are easy to get wrong and are now tested:

- **Reading** accepts either name, secure first (`read_session_token`), so flipping the
flag does not log anyone out mid-session.
- **Logout** deletes both names with `Secure` set — a `__Host-` cookie cannot be cleared
  by a `Set-Cookie` that omits `Secure`, and a name-only delete would leave a stale
  session behind.

**Consequences.** A `__Host-` cookie on a non-TLS deployment is silently rejected and
login appears broken. That is a loud failure with a precise cause, and the correct trade
for a service whose only credential is one password. `SESSION_SECRET` rotation already
logs everyone out; no extra invalidation needed.

**Verify.** `tests/integration/test_ingress_hardening.py` asserts the prefix, `Secure`,
`HttpOnly`, `Path=/`, the absence of `Domain`, the legacy-cookie fallback and the
double-name logout. On the VPS: runbook §6.3.

---

### D4 — Bound disk and RAM, because both are shared with the host

**Problem.** Nothing limits total bytes. `MAX_UPLOAD_MB=20` is per file, up to 20
references per request, and every reference is read fully into memory and base64-encoded
(+33%) before the provider call: a single request can peak near 500 MB. There is no cap
on job count, and soft-deleted jobs keep their files for 7 days. On a 2 GB VPS with a
40 GB disk, one leaked session is enough to OOM the box and then fill the volume —
taking SQLite's WAL with it.

**Decision.** Three limits, all in `Settings`, all enforced server-side:

| Setting | Value | Enforced in |
| --- | --- | --- |
| `MAX_UPLOAD_MB` | `10` (was 20) | `routers/assets.py`, already — **still 20 in code and in `backend/.env.example`; only the runbook sets 10** |
| `MAX_REFERENCE_BYTES` | `40 MiB` total across a job's references | `capabilities.validate_request` |
| `STORAGE_WATERMARK_PCT` | `85` — refuse new uploads/generations above it | `routers/assets.py`, `routers/generations.py` via `shutil.disk_usage(data_dir)` |
| `--limit-concurrency` | `8` | Dockerfile `CMD` |

The watermark, not a hard quota: GC is the only thing that reclaims space, and it runs
every 6 h, so the system must stop accepting work *before* the disk is full rather than
after. The error is a `507 Insufficient Storage` with the current and limit values.

**Consequences.** A 40 MiB reference set is still generous for img2img. Refusing at 85%
means the last 15% of the disk is headroom for WAL growth, GC, and the OS — not for
users. Concurrency 8 bounds worst-case RAM near 1 GB instead of unbounded.

**Not decided here:** moving references to streaming/temp files, or output to object
storage. Both are real fixes for a *multi-operator* service; this is a single-operator
one, and the caps above are cheaper than the rewrite.

**Verify.** `POST /api/uploads` with a 15 MB file → `413`. Fill `/data` past 85% in a
scratch container → uploads and generations return `507`, health still `200`.

---

### D5 — A spend cap is mandatory for a real deployment

**Problem.** `DAILY_BUDGET_USD` defaults to `None` (no cap), and the startup validator
only demands a secret, a password, and an API key. The first real deployment is therefore
one stuck retry loop away from an open-ended OpenRouter bill. The 7-day-session cookie has
no revocation, so a stolen laptop is a stolen credit card.

**Decision.** Extend the `PROVIDER != fake` startup check: refuse to boot with
`DAILY_BUDGET_USD` unset. Keep the existing per-request `estimate_cost` gate unchanged
(it only applies to models with deterministic pricing — the cap is a backstop, not an
accounting system).

**Consequences.** One more mandatory env var, and the error message must show the
`curl /api/usage` call that reports today's spend. Q3 chooses the number.

**Verify.** Boot with `PROVIDER=openrouter` and no budget → process exits non-zero with
the budget in the error list.

---

### D6 — Non-root container, `/data` as a pre-chowned host bind mount

**Problem.** Measured: `uid=0(root)` and `/data` owned by root. Root in a container is
root on the volume, and a root-owned volume makes the backup story (D9) require `sudo`
on the host for every read.

**Decision.** `useradd -u 10001 -m app`, `chown` the app and `/data` at build time,
`USER app` for the runtime stage. In Coolify, `/data` is *Persistent Storage* pointing at
a **host bind mount** (`/srv/medialab/data`) that is `chown 10001:10001` once, before the
first deploy. A Docker-managed named volume is created root-owned and the container would
then be unable to write — that is the failure mode to avoid, and the reason this is a
decision rather than a detail.

**Consequences.** Images and DB on the host filesystem are trivially inspectable and
backup-able. The bind mount survives `docker compose down -v`, which a named volume does
not.

**Verify.** Six steps, run in order at §7 step 3. Each has one unambiguous outcome; the
result decides whether the container ever needs to run as root.

1. **Pre-chown the host dir, before first deploy.**
   ```bash
   sudo mkdir -p /srv/medialab/data
   sudo chown 10001:10001 /srv/medialab/data
   ls -ln /srv/medialab/data        # expect: drwxr-xr-x 10001 10001
   ```
   Bind mounts preserve host uid/gid, so this is the ownership the container will see.

2. **In Coolify, add Persistent Storage** — source `/srv/medialab/data`, destination
   `/data`, type **bind** (not volume). Deploy.

3. **Check Coolify did not override `USER`.**
   ```bash
   docker exec <container> id       # expect: uid=10001(app)
   ```
   If this says `uid=0(root)`, Coolify is running the image as root and the no-root
   shape is impossible without a Coolify setting — that is the fork.

4. **Check the container can actually write.**
   ```bash
   docker exec <container> touch /data/probe
   docker exec <container> ls -ln /data/probe   # expect: 10001 10001
   ```
   `Permission denied` here means the host dir was not chowned, or Coolify created it
   root-owned itself.

5. **Redeploy (not just restart) and repeat step 4.** This is the step people skip — it
   proves Coolify does not reset ownership on every deploy. If it does, the no-root shape
   is a recurring manual chore and the root-then-drop entrypoint starts looking cheaper.

6. **After the entrypoint has run Alembic:**
   ```bash
   docker exec <container> ls -ln /data   # expect: medialab.db owned 10001
   ```
   This confirms `alembic upgrade head` as uid 10001 could create the DB — the actual
   thing D6 and D7 have to do together.

**Reading the result.** Steps 3, 4, 5 and 6 all pass → no-root shape, no `su-exec`, no
root moment. Any of 3 or 5 fails → root-then-drop entrypoint, or a Coolify config fix,
and this decision is revisited.

**Current assumption.** D6 is built as the no-root shape (`USER 10001`, no root
entrypoint) and these six steps confirm it at §7 step 3. They cannot run before there is
a VPS shell and a live deploy.

---

### D7 — Alembic from the first deploy

**Problem.** `init_db()` is `Base.metadata.create_all(engine)` — it creates missing
tables and *never* alters existing ones. Since `kind` + JSON `params` were designed for
video later, the second feature *will* add a column, and on a database that already holds
data it will fail silently at runtime rather than loudly at startup.

**Decision.** Add Alembic before the first production row exists: `alembic.ini`,
`migrations/` with an initial revision generated from the current models, and `init_db()`
reduced to `alembic upgrade head`.

In production the migration runs as a **Coolify pre-deployment command**, not from the
container entrypoint. The command is `alembic upgrade head`, executed against the new
image with `/data` mounted, while the *old* container is still serving. If it fails, the
deploy aborts and the old container keeps running — no outage. The entrypoint does **not**
migrate: having both would re-introduce the outage the pre-deploy step exists to avoid.

D11's single replica means there is no concurrent-migration race, so no lock is needed.

**Consequences.** `uv sync` gains `alembic`. Local dev gets `uv run alembic revision
--autogenerate -m "…"` and `uv run alembic upgrade head`. The blank-database path must keep
working for tests, which is why `conftest.py` moves from `create_all` to `upgrade head` as
well — otherwise tests stop exercising the migration you actually ship.

**Migrations must be backward-compatible for the duration of a deploy.** Because the
pre-deploy step mutates the schema while the old container is still running, the old code
briefly serves against the new schema. Every migration is therefore expand-then-contract:
add columns/tables and backfill first, switch the code over in a later release, and drop
the old column only once no running image references it. A rename or a drop in the same
deploy that adds its replacement will break the old container mid-deploy.

**The pre-Alembic database is disposable.** `backend/data/medialab.db` is deleted rather
than stamped: the VPS starts from an empty `/data` and `upgrade head` builds it from
nothing. There is no `stamp head` path and no need for the initial revision to reproduce
`create_all` byte-for-byte. §8's note about that file being useful as a migration fixture
is superseded.

**Verify.** On a scratch deploy: `alembic upgrade head` then `alembic current` matches
`alembic heads`. Then push a deliberately failing revision and redeploy — the pre-deploy
step exits non-zero, Coolify aborts, and `curl https://<domain>/health` still returns
`200` from the **old** container. That last check is what (b) was chosen for; if the site
is down, Coolify is running pre-deploy after stopping the old container and the ordering
assumption needs re-checking before relying on it.

---

### D8 — Production config comes from Coolify, not the repo

**Problem.** `docker-compose.yml` reads `env_file: .env` at the repo root, while the
documented template is `backend/.env` — so `docker compose config` fails on a clean
clone (verified). That is a deployment trap, and an `env_file` is the wrong source of
truth for production anyway: it invites committing a secret.

**Decision.** Two distinct paths, documented as such:

- **Coolify (production):** Dockerfile build pack, env vars entered in the Coolify UI,
  applied to *Preview = off*. No `.env` in the image, no `.env` in the repo.
- **Local:** `docker compose up` uses `backend/.env`; `env_file` points there and the
  file is already gitignored. `docker-compose.yml` also stops publishing the port (D1).

Coolify can read `docker-compose.yml` as its build pack, but does not need to: a single
service with one volume is exactly what the Dockerfile pack models, and the compose file
then stays a developer convenience.

**Consequences.** Two files describe one container; that drift is deliberate and small.
Any new setting must be added in *two* places (`.env.example` and the Coolify UI), which
the deploy checklist enforces.

**Verify.** `docker compose config` succeeds on a clean clone after `cp backend/.env.example
backend/.env`. `docker run --env-file backend/.env` boots; no `.env` exists in the image
(`docker run --rm --entrypoint ls <img> -a /app`).

---

### D9 — Off-host backups, taken consistently

**Problem.** All state is one SQLite file (WAL mode) plus content-addressed assets on one
volume on one VPS. There is no backup, and a `cp medialab.db` on a live WAL database
produces a torn copy.

**Decision.** A host-side script (`scripts/backup.sh`, cron via Coolify's scheduled task
or systemd timer):

1. `sqlite3 /srv/medialab/data/medialab.db ".backup '/tmp/medialab.db'"` — the online
   backup API, WAL-safe.
2. `restic` (or `rclone`) ships `/tmp/medialab.db` plus `assets/` to off-host storage.
   Assets are immutable and content-addressed, so incremental dedupe is essentially free.

Retention: 7 daily, 4 weekly, 3 monthly. The assets directory is the expensive part —
restoring the DB alone restores history, not images, so a restore drill must check both.

**Consequences.** A backup that has never been restored is a hypothesis. The drill is
part of the definition of done: restore into a scratch container, log in, open a
pre-deploy job, and confirm the image bytes render.

**Verify.** `bash scripts/backup.sh` → snapshot listed; restore into a scratch container
and diff a known asset's `sha256` against the source.

---

### D10 — Documentation endpoints off in production

**Problem.** `GET /api/docs` and `/api/openapi.json` return `200` unauthenticated
(verified). On a private, single-operator service that is a free map of every endpoint,
including the ones with no UI behind them.

**Decision.** `ENABLE_DOCS: bool = False`; when false, `docs_url=None` and
`openapi_url=None`. The frontend's generated types still come from a local run with the
flag on (`npm run gen:types`), so the type pipeline is unaffected.

**Consequences.** `GET /api/docs` → `404` in production. If the route disappears while
the SPA catch-all exists, that is fine — `/api/*` 404s before the mount.

**Verify.** `curl -o /dev/null -w '%{http_code}' https://<domain>/api/docs` → `404`.

---

### D11 — One replica, `--workers 1`, deploys interrupt jobs

**Problem.** Jobs run in-process: `dispatch()` schedules onto the app's own event loop,
and `mark_interrupted()` marks `queued`/`running` rows as `interrupted` on boot. Any
redeploy or restart therefore kills in-flight generations, and a second replica would
strand jobs that the other replica cannot see.

**Decision.** Accepted for v1, made explicit instead of discovered:

- Coolify autoscaling and multiple replicas stay **off**. `--workers 1` stays.
- A deploy that lands mid-generation is a known, visible outcome: the job shows
  `interrupted` with "server restarted while this job was running", and the existing
  **Retry** button is the recovery path.
- Coolify's zero-downtime/rolling option is turned off, so the old container stops before
  the new one starts — one interruption instead of two overlapping ones.

The escape hatch, when this becomes annoying, is a worker process polling `media_jobs` —
still not Celery, still SQLite. Not in v1.

**Verify.** Start a generation, redeploy, confirm the job reads `interrupted` and Retry
produces a new `succeeded` job.

---

### D12 — Ingress is a Cloudflare Tunnel, not Traefik — ✅ done

**Problem.** D1/D2 assumed Coolify's Traefik + Let's Encrypt was the ingress. That shape
requires the VPS to accept inbound 80/443, publishes the VPS IP, needs ACME (blocked until
DNS existed), and — the deciding factor — leaves TLS and routing split across two
configurations (Coolify labels and the app) with no second factor anywhere in front of a
single shared password.

**Decision.** A `cloudflared` connector runs as a **host systemd service**. Each app is
published on its own **loopback** port; the connector maps a hostname to it.

- **One connector, one tunnel, N hostnames.** Adding a service is one `ingress` rule plus
  one DNS route, not another cert, proxy or port.
- **The ingress table is in git** (`deploy/cloudflared/config.yml`) in locally-managed
  mode, for the same reason the model catalogue is a committed JSON: a route change is a
  reviewable diff and a bad one is one `git revert` away. The dashboard-managed (token)
  mode is the alternative; pick one, because two sources of truth for ingress is the
  failure this document exists to prevent.
- **No inbound port on the VPS at all**, so D1's verification becomes trivial and D2's
  spoof surface shrinks to "who can reach loopback".
- **No Traefik router and no Coolify domain** for this app. Traefik would either need TLS
  it no longer needs, or it would 301-redirect the tunnel's plain-HTTP request into a
  loop. That is the single most common way this setup breaks.
- **Cloudflare Access is the optional upgrade**, not part of v1: it puts MFA and instant
  revocation in front of a service whose only credential has neither, without a code
  change. Recommended once the hostname is live (runbook §5).

**Consequences.**

- The connector becomes a component on the critical path: it must restart (`unless-stopped`
  via the systemd unit), and "container running but tunnel disconnected" is a distinct
  failure that `/health` cannot see — check `journalctl -u cloudflared` for
  `Registered tunnel connection`.
- The tunnel's credentials file (`/etc/cloudflared/<UUID>.json`) is a bearer secret for
  **every** hostname on that tunnel. `chmod 600`, and record it beside the OpenRouter key.
- A second proxy now sits in the path (edge → connector → app). Nothing in the app may
  assume a header Cloudflare rewrites; the only trusted peer is the local hop (D2).
- Cloudflare's Free plan caps request bodies at 100 MB even through a tunnel — irrelevant
  at `MAX_UPLOAD_MB=10`, a real constraint if D4's caps are ever raised.
- Proxied is forced: a tunnel hostname cannot be DNS-only, which closes Q4.

**Verify.** Runbook §6: `200` from `https://<domain>/health` through the edge;
`nc -z <vps-ip> 80|443|8000` all fail from off-host; `journalctl -u cloudflared` shows
registered connections; `docker exec <c> id` is `uid=10001`.

---

## 2. Clarified context — do not re-ask

Settled before or during this review. Reopening any of these is a different conversation
than "host it on a VPS".

### Deployment

- **Target is one VPS managed by Coolify, fronted by a Cloudflare Tunnel.** TLS ends at
  the Cloudflare edge; the connector runs on the host and dials the app's loopback port.
  Not Traefik + Let's Encrypt (D12 replaced it), not bare compose behind a hand-rolled
  Caddy, not Kubernetes, not a PaaS.
- **Issues are tackled one at a time**, in §7's order, and an issue closes when its
  verification has been *run*, not when its code is written.
- **Deployed at a subdomain, at the root path.** The SPA calls root-relative `/api/...`
  (`frontend/src/lib/api.ts`), so a path-prefixed deploy (`https://host/medialab`) is
  unsupported and would need changes in Vite, the router, and the cookie path.

### Product scope

- **Exactly one operator.** No registration, no accounts, no per-user keys, no roles.
- **Private service.** No public or shareable generation pages, no gallery, no anonymous
  reads. Every `/api/*` route except `login`, `me`, `health` requires the cookie.
- **Image only.** Video and audio are out of scope. The schema absorbs them later
  (`kind` + JSON `params`); nothing else is built for them.

### Architecture (load-bearing, from the v1 design)

- **One container, one origin, no CORS, no nginx.** FastAPI serves the built SPA.
- **In-process jobs.** `dispatch()` schedules onto the app's own event loop: no Redis, no
  Celery, no broker. The escape hatch, if it is ever needed, is a worker polling
  `media_jobs` — still SQLite. `--workers 1` is not a default to tune; extra workers
  strand jobs.
- **SQLite + WAL**, sync SQLAlchemy 2.0, `check_same_thread=False`, one engine. Postgres
  is not on the table.
- **The model catalogue is a committed static JSON** (60 models), generated offline by
  `python -m scripts.gen_capabilities`. Never fetched at runtime, no TTL, no refresh
  endpoint. Staleness is the accepted trade; updating is a reviewable git diff.
- **Capabilities drive the UI.** A field appears iff the model supports it; unsupported
  fields are rejected with 422, never silently dropped.
- **References are base64 `data:` URLs**, because the VPS may not be publicly reachable
  for OpenRouter to fetch. Output is always downloaded server-side because provider
  URLs expire.
- **Storage is content-addressed** by sha256, sharded `<sha[:2]>/`, identical bytes
  stored once, deletion reference-counted, GC deletes rows before files.
- **Soft delete is 7 days** with a real Undo; GC also runs once at startup. Uploads start
  unowned and are swept after 24 h unless a job claims them. That startup GC walks the
  entire assets directory, so boot time grows with asset count — relevant to the
  Dockerfile's 10 s health-check `start-period`.
- **Money is recorded, not estimated.** `cost_usd` comes from the provider's `usage`
  into an append-only `spend_ledger`. The budget gate only works for models with
  deterministic pricing — it is a backstop, not accounting. Two consequences to know
  before trusting it: the window resets at **UTC** midnight (not local), and the check is
  read-then-spend, so two jobs started in the same second can both pass it.
- **`MAX_N_HARD_CAP=4` overrides the catalogue.** A model that advertises `n: 1..8` still
  gets 422 above 4; the cap is a server cost limit, not a capability.
- **`PROVIDER=fake` is the default** and must never be the production value. It produces
  real, deterministic images with no network in ~50 ms, which is why staging and the
  whole test suite run without spending.
- **Provider passthrough is allowlisted per model**; the client cannot send arbitrary
  provider options.

### Auth, including its honest limits

- One shared password → one HS256 JWT cookie, 7 days, stateless.
- **No revocation.** Logout clears the browser cookie; a copied token stays valid until
  `exp`. Rotating `SESSION_SECRET` is the only global logout.
- CSRF is `SameSite=Lax` plus a required `X-Requested-With: fetch` header on mutations.
  There are no CSRF tokens and none are planned.
- The login limiter is in-memory, per-IP, 5 failures / 15 min, cleared by a restart.
  Deliberately not persisted.

### Tests

- `addopts = -m 'not live and not e2e'`. The fake provider records the exact payload it
  would have sent, so request mapping is covered without spending money.
- `-m live` hits OpenRouter for real and is opt-in.
- The E2E layer is a CDP driver, not Playwright (see §8).

## 3. Answers given in this review

| Question | Answer |
| --- | --- |
| Which front-end sits in front of the VPS? | **A Cloudflare Tunnel (D12), with Coolify deploying the container.** Traefik + Let's Encrypt was the earlier answer and was replaced: the tunnel needs no inbound port and no certificate on the VPS. Compose-behind-hand-rolled-Caddy and bare metal remain ruled out. |
| Is anything functionally broken? | **No.** Build, boot, login, generation and polling were all verified locally (§0). Only hosting, hardening and ops are open. |
| What actually blocks hosting? | D1–D11 in §1, worked in §7's order. |
| Where does this live? | This file; `README.md` §Deploy links to it. |
| Is the VPS/Coolify greenfield? | **No.** Coolify is already installed on the VPS. §7 step 3 is *configuration*, not installation, so no provisioning runbook is needed and §7's order stands. |
| What is the hostname? | The domain is **`papi656.com`**. The app subdomain is **not yet created** — it will be added in Cloudflare. Assumed `medialab.papi656.com` until told otherwise. §6 Q1 (Coolify CIDR) stays open until there is a VPS shell. |
| Is DNS live? | **Not yet.** The record is created by `cloudflared tunnel route dns` (runbook §2) rather than in the dashboard, so it is one command once the tunnel exists. Nothing depends on ACME any more, so the old "blocked on a certificate" constraint is gone; the deploy is blocked only on the VPS work in runbook §1–§4. |

Anything else that "comes up" during the work and is already covered by §2 is not an open
question — it is a constraint. If a §2 item genuinely needs to change, change it, update
the corresponding §1 row, and say so here rather than re-litigating it.

## 4. Required changes

| # | Files | Change |
| --- | --- | --- |
| D1 ✅ | `docker-compose.yml`, runbook §4 | `ports:` bound to `127.0.0.1`; no Coolify domain |
| D2 ✅ | `Dockerfile`, `config.py`, `main.py` | `FORWARDED_ALLOW_IPS` (uvicorn's own env var), `*` rejected, startup warning when loopback-only |
| D3 ✅ | `config.py`, `auth.py`, `routers/auth.py`, `tests/conftest.py` | `COOKIE_SECURE` + `__Host-`, dual-name read/delete, `_is_secure()` gone |
| D4 ◐ | `config.py`, `capabilities.py`, `routers/{assets,generations}.py` | only `--limit-concurrency 8` landed; watermark + reference-bytes cap **missing** |
| D5 ✅ | `config.py` | `DAILY_BUDGET_USD` mandatory for `openrouter` |
| D6 ✅ | `Dockerfile`, runbook §4 | `USER 10001` via `COPY --chown`, `/data` chowned, loopback publish |
| D7 ✗ | `alembic.ini`, `migrations/`, `db.py`, `tests/conftest.py`, `pyproject.toml` | Alembic, `upgrade head` — **not started** |
| D8 ✅ | `docker-compose.yml`, `.env.example` | local-dev compose reads `backend/.env`; Coolify owns prod env |
| D9 ✗ | `scripts/backup.sh` | backup + restore drill — **not started** |
| D10 ✅ | `main.py`, `config.py` | `ENABLE_DOCS` |
| D11 ◐ | `Dockerfile`, runbook §4 | replica/zero-downtime off documented; interruption test still pending |
| D12 ✅ | `deploy/cloudflared/config.yml`, `docs/vps-runbook.md` | host connector, ingress table in git |

Tests that now hold these decisions down (105 pass, 4 deselected):

- `test_config.py`: openrouter without a budget → `ValueError`; `FORWARDED_ALLOW_IPS` of
  `*`, `172.17.0.1, *`, or `" , "` → `ValueError`; cookie security follows the provider.
- `test_ingress_hardening.py`: `__Host-ml_session=` + `Secure` + `HttpOnly` + `Path=/` and
  no `Domain`; legacy unprefixed cookie still authenticates; logout clears both shapes;
  `/api/docs` is `404` unless `ENABLE_DOCS=1`.
- `test_assets.py`: 15 MB upload → `413`; 41 MiB of references → `422`; watermark → `507`
  (**still to write**, with D4).

## 5. Production environment

Set in the Coolify UI, nowhere else. Values marked ⚠ are mandatory for `openrouter`.

```
PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...              ⚠
SESSION_SECRET=<48 bytes url-safe>        ⚠  (must not be the repo default)
SESSION_DAYS=7
ADMIN_PASSWORD_HASH=<bcrypt>              ⚠  (uv run python -m app.hashpw)
DAILY_BUDGET_USD=<Q3>                     ⚠  (D5)
DATA_DIR=/data
STATIC_DIR=/app/static
MAX_UPLOAD_MB=10
MAX_REFERENCE_BYTES=41943040              (D4, not implemented yet)
STORAGE_WATERMARK_PCT=85                 (D4, not implemented yet)
FORWARDED_ALLOW_IPS=<Q1 gateway>          ⚠  (D2 — one address, never '*')
COOKIE_SECURE=true
ENABLE_DOCS=0
MAX_N_HARD_CAP=4
JOB_TIMEOUT_SECONDS=120
SOFT_DELETE_RETENTION_DAYS=7
GC_INTERVAL_HOURS=6
```

`ADMIN_PASSWORD` stays supported for local dev only and is unset in production, so the
bcrypt hash is the only credential.

## 6. Open questions

- **Q1 — the Docker gateway in front of the container.** Needed for
  `FORWARDED_ALLOW_IPS`. Read it on the VPS with
  `docker inspect <container> --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{$v.Gateway}}{{"\n"}}{{end}}'`
  and put the gateway of the network that fronts the published port in. One address is
  enough; the whole subnet is not needed. Re-read it if the Docker network is ever
  recreated.
- **Q2 — backup target.** restic to Backblaze B2 is the default assumption (cheap,
  append-only via application keys). Needs a bucket and a key before D9 can be closed.
- **Q3 — `DAILY_BUDGET_USD`.** A number, chosen with knowledge of the models actually
  used: `curl /api/usage?period=month` after a week of local use gives the honest figure.
- ~~**Q4 — Cloudflare proxy mode (proxied vs DNS-only).**~~ **Closed by D12.** A tunnel
  hostname is always proxied — the record points at the tunnel, not at the VPS, so there
  is no DNS-only shape to choose. Consequences keep the old analysis's second bullet:
  TLS ends at the edge, the Free plan caps request bodies at 100 MB (irrelevant at
  `MAX_UPLOAD_MB=10`), and the origin is plain HTTP, which is exactly why D3 stopped
  reading `X-Forwarded-Proto`.
- **Q5 — Cloudflare Access in front of the hostname?** Recommended but undecided: it adds
  MFA and instant revocation to a service whose only credential is one shared password
  with no logout-everywhere. It also means two logins unless the app's own password is
  kept deliberately as the second layer. Needs a policy email/provider choice, not code.

## 7. Deploy sequence

Ordered so that each step's verification is meaningful before the next step runs.

1. ✅ **Landed**: D1, D2, D3, D5, D6, D8, D10, D12. Verified by `uv run pytest` (103
   pass) and a `docker build` — not yet by a deploy.
2. **Connector up** (runbook §1–§3): binary installed, tunnel created, DNS record routed,
   `journalctl -u cloudflared` shows registered connections. No app required yet — the
   ingress can be proven with `http_status:404`.
3. **First deploy, `PROVIDER=fake`** (runbook §4): loopback port mapping, **no domain**,
   bind mount pre-chowned to 10001, env vars incl. `FORWARDED_ALLOW_IPS` = the gateway.
   Then run runbook §6.1–§6.6. The rotating-XFF check (§6.5) is the one that proves D2.
4. **D7 (Alembic)** — must land *before* any production row exists; `create_all` will not
   alter a table, and the next feature adds a column.
5. **D4 (caps)** — verify the `413`/`507` paths against the live container.
6. **D9 (backup)** — once there is state worth keeping, then run the restore drill. A
   backup that has never been restored is a hypothesis.
7. **Flip to `PROVIDER=openrouter`** (runbook §7), one cheap generation, then D11's
   interruption test: start a job, redeploy, confirm `interrupted` + Retry works.
8. Optional: **Cloudflare Access** (Q5), then re-run §6.3 — the cookie should still be
   `__Host-` and `Secure`, with Access in front rather than instead.

Until step 7 the deployment is a `fake`-provider staging environment that costs nothing.

## 8. Repo state at the time of writing

Recorded so that a later reader does not mistake work-in-progress for a decision.

Updated 2026-11-15, after the ingress rewrite.

- **Implemented and reviewed here**: `FORWARDED_ALLOW_IPS` + `*` rejection, cookie
  hardening (`COOKIE_BASE`/`cookie_name`/`read_session_token`), `ENABLE_DOCS`, the
  mandatory budget, the non-root runtime stage, the loopback compose bind,
  `deploy/cloudflared/config.yml` and `docs/vps-runbook.md`.
- **Committed as the deployable baseline**: everything above, the `e2e` marker in
  `backend/pyproject.toml`, the `backend/tests/e2e/` CDP suite, this document, the runbook
  and `deploy/cloudflared/config.yml`. `main` now builds the image described in §0/§0.1 —
  before that, deploying `main` shipped the root/`*`-trusted/published-port container.
  (This bullet used to say "uncommitted"; it was the most important item here, because a
  Coolify build reads git and nothing else.)
- **Blank optional env values are now "unset"**, not a parse error (`config.py`'s
  `_blank_is_none`, pinned by `test_a_blank_optional_value_means_unset` and
  `test_the_documented_env_template_boots`). `COOKIE_SECURE=` as shipped in
  `.env.example` exited the container on a bool-parsing error before; the README's first
  command (`cp .env.example .env`) now produces a config that boots, and a blank field in
  the Coolify UI is harmless. Any future non-string optional field must join that
  validator.
- **`init_db()` is still `Base.metadata.create_all`** — D7 has not started, so there is no
  migration path yet and `backend/data/medialab.db` (gitignored) is still the disposable
  pre-Alembic file.
- **`docker compose config` now succeeds** on a clean clone once
  `cp backend/.env.example backend/.env` exists, because `env_file` points at
  `backend/.env` (D8).
- **`MAX_UPLOAD_MB` is still 20 in `config.py` and `.env.example`** while runbook §4 and
  §5 say 10 — deliberate drift only until D4 lands; the env var is the one that matters at
  runtime, so the deployed container gets 10 whichever way round it is.
- **Formatting is a no-op gate right now**: `uv run ruff check` passes, and the tree is
  `ruff format`-clean as of this baseline, so a future CI step can enforce both.
- **The container publishes on loopback**, so a local `docker compose up` is reachable at
  `http://127.0.0.1:8000` and from nowhere else. That is the production shape too.
- The catalogue's `generated_at` is still `2026-11-02`. No action; noted so a
  stale-catalogue report is not mistaken for a bug.

## 9. Change log for this document

| Date | Change |
| --- | --- |
| 2026-11-05 | Created. Baseline verified against a real build; D1–D11 recorded; §2 context and §3 answers added after review, so the same questions are not asked twice. |
| 2026-11-05 | D6: six-step no-root confirmation added. D7: migrations moved from the container entrypoint to a Coolify pre-deploy command, so a failed migration aborts the deploy instead of causing downtime; migrations are now required to be backward-compatible (expand-then-contract). Pre-Alembic local DB declared disposable — no `stamp head` path. |
| 2026-11-16 | Baseline made deployable: blank optional env values (notably the `COOKIE_SECURE=` in `.env.example`, which exited the built container on a bool-parsing error) are treated as unset, pinned by two tests; the hardening work D1/D2/D3/D5/D6/D8/D10/D12, the runbook, the ingress table and the e2e suite were committed, so `main` is what the image is built from; `ruff format` applied tree-wide (12 files, mechanical). §0.1 and §8 corrected — they claimed `.env.example` said `MAX_UPLOAD_MB=10`, which it never did. |
| 2026-11-15 | **Ingress changed to a Cloudflare Tunnel (D12).** D1 became a loopback publish instead of `expose:`; D2's trust target became the Docker gateway named by uvicorn's own `FORWARDED_ALLOW_IPS` (measured: `172.17.0.1` default, `10.77.0.1` on `10.77.0.0/16`) and `*` is now a startup error; D3 stopped sniffing `X-Forwarded-Proto` entirely, since behind a tunnel the app is always plain HTTP; D5, D6, D8, D10 implemented; Q4 closed by construction, Q1 reworded, Q5 (Access) opened. Runbook added. D4/D7/D9 still open — §7's order changed accordingly. |
