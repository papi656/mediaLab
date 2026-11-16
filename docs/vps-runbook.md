# mediaLab — VPS runbook (Cloudflare Tunnel + Coolify)

**Status:** unrun. Every command below is written to be copy-pasteable, but nothing in
this file has been executed against the VPS yet. Verification steps say what the output
*must* be; if it differs, the step failed.

**Shape:** one tunnel connector on the host, one ingress rule per service, each app
published on `127.0.0.1:<port>` only.

```
visitor ──TLS──▶ Cloudflare edge ──tunnel──▶ cloudflared (host, systemd)
                                              │ http://127.0.0.1:8000
                                              ▼
                                      docker NAT (peer = bridge gateway)
                                              ▼
                                     mediaLab (uid 10001, /data bind mount)
```

Nothing listens on the VPS's public 80/443. There is no Traefik domain for this app and
no Let's Encrypt certificate — the tunnel is the ingress, and it has no inbound port.

---

## 1. Prerequisites

- VPS shell, root or sudo.
- `papi656.com` on Cloudflare, and the zone's nameservers already delegated.
- Coolify installed and reachable.
- ~10 minutes; no downtime concerns because nothing is serving yet.

Install the connector on the **host**, not in a container:

```bash
# Debian/Ubuntu, x86-64. Pick the matching .deb from
# https://github.com/cloudflare/cloudflared/releases if the arch differs.
curl -fL -o /tmp/cloudflared.deb \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i /tmp/cloudflared.deb
cloudflared --version
```

Why the host and not a container: the connector must survive Coolify deploys and Docker
restarts (it is the thing that *serves* the app), and it must be the only client of the
app's loopback port. A container that shares the app's network namespace would also work
and would let `FORWARDED_ALLOW_IPS` stay `127.0.0.1`, but it couples the tunnel's life to
one app's stack — the wrong shape once a second service exists.

## 2. Create the tunnel and the DNS record

```bash
cloudflared tunnel login                 # browser: authorise papi656.com, writes ~/.cloudflared/cert.pem
cloudflared tunnel create medialab       # prints the UUID; writes ~/.cloudflared/<UUID>.json
cloudflared tunnel list
cloudflared tunnel route dns medialab medialab.papi656.com
```

`route dns` creates a **proxied** CNAME to `<UUID>.cfargotunnel.com`. Proxied is mandatory
— the record points at the tunnel, not at the VPS, so the VPS IP is never published.
That closes the old Q4 (proxied vs DNS-only): with a tunnel there is no DNS-only option.

## 3. Install the config as a service

```bash
UUID=<the-uuid-from-step-2>
sudo install -d -m 755 /etc/cloudflared
sudo install -m 600 ~/.cloudflared/$UUID.json /etc/cloudflared/$UUID.json
sudo cp deploy/cloudflared/config.yml /etc/cloudflared/config.yml
sudo sed -i "s/REPLACE_WITH_TUNNEL_UUID/$UUID/g" /etc/cloudflared/config.yml
sudo cloudflared service install          # reads /etc/cloudflared/config.yml
sudo systemctl enable --now cloudflared
sudo journalctl -u cloudflared -n 30 --no-pager | grep -i "Registered tunnel connection"
```

Expect **four** `Registered tunnel connection` lines (one per edge). Fewer than four is
still working; zero means it is not.

The ingress table lives in `deploy/cloudflared/config.yml` and is copied here. Editing it
on the host without copying it back to the repo is how the two drift — change the repo,
then re-copy and `sudo systemctl restart cloudflared`.

## 4. Coolify: the app container

Create the app from this repo, **Dockerfile build pack**, and set:

| Setting | Value | Why |
| --- | --- | --- |
| Ports Exposes | `8000` | container port, for the Docker network only |
| Ports Mappings | `127.0.0.1:8000:8000` | the connector dials loopback; the internet cannot reach it |
| Domains | **none** | a Coolify domain would re-create a second ingress and a TLS redirect loop |
| Pre-deployment Command | `alembic upgrade head` | the schema is migrated against the **new** image with `/data` mounted, while the old container still serves, so a failing migration aborts the deploy instead of causing an outage (D7) |
| Persistent Storage | bind `/srv/medialab/data` → `/data` | survives `docker compose down -v` |
| Preview Deployments | off | a preview would claim the same loopback port |
| Autoscaling / replicas | off | in-process jobs, `--workers 1` |

Pre-chown the host directory once, before the first deploy (the container runs as uid
10001 and cannot write a root-owned bind mount):

```bash
sudo mkdir -p /srv/medialab/data
sudo chown 10001:10001 /srv/medialab/data
ls -ln /srv/medialab/data          # expect: drwxr-xr-x 10001 10001
```

On Docker Desktop (macOS/Windows) the same step appears unnecessary because the file
sharing layer remaps ownership. That is a Desktop property, not a property of the image —
on Linux this chown is what stops `Permission denied` on `/data`.

### Environment variables

Set these in the Coolify UI. `backend/.env.example` is the annotated list; production
must not ship a `.env` file.

```
PROVIDER=fake                       # first deploy only; flip at step 7
SESSION_SECRET=<48 url-safe bytes>
ADMIN_PASSWORD_HASH=<bcrypt>
DAILY_BUDGET_USD=<number>           # mandatory when PROVIDER=openrouter
DATA_DIR=/data
STATIC_DIR=/app/static
RUN_MIGRATIONS_ON_START=0          # the pre-deployment command migrates; with 0 the
                                  #   container refuses to start on a stale schema
FORWARDED_ALLOW_IPS=<gateway>       # next section
COOKIE_SECURE=true
ENABLE_DOCS=0
MAX_UPLOAD_MB=10                    # D4: per-file
MAX_REFERENCE_BYTES=41943040        # D4: 40 MiB across one job's references
STORAGE_WATERMARK_PCT=85            # D4: 507 for new work above this fill level
MAX_N_HARD_CAP=4
JOB_TIMEOUT_SECONDS=120              # provider HTTP timeout; raise it for slow models
SOFT_DELETE_RETENTION_DAYS=7
GC_INTERVAL_HOURS=6
```

`MAX_UPLOAD_MB`, `MAX_REFERENCE_BYTES` and `STORAGE_WATERMARK_PCT` are the D4 caps.
A blank value for any of them means "use the default" (the same rule as the optional
fields), so a half-filled Coolify form still boots.

### Find `FORWARDED_ALLOW_IPS`

The connector runs on the host, so its connection to `127.0.0.1:8000` is NATed by Docker,
and the container sees the **bridge gateway** as the peer — not `127.0.0.1`. Measured:
`172.17.0.1` on the default bridge, `10.77.0.1` for a network with subnet `10.77.0.0/16`.

```bash
docker inspect <medialab-container> --format \
  '{{range $name, $net := .NetworkSettings.Networks}}{{$name}} gateway={{$net.Gateway}}{{"\n"}}{{end}}'
```

Put that gateway (the one whose network also fronts the published port — normally
Coolify's shared network) in `FORWARDED_ALLOW_IPS`. One address, never `*`: uvicorn trusts
`X-Forwarded-For` only from peers in this list, and the login rate limiter keys on the
resulting address.

If it is wrong, the symptom is precise: every visitor shares one rate-limit bucket, and
the failure log shows `peer=` and `cf-connecting-ip=` disagreeing.

## 5. Cloudflare dashboard, the three settings that matter

| Setting | Value | Why |
| --- | --- | --- |
| SSL/TLS → Edge Certificates → **Always Use HTTPS** | on | the session cookie is `Secure` + `__Host-`; plain HTTP must never be served |
| Speed → **Rocket Loader** | off | it rewrites scripts and breaks Vite SPAs |
| Caching → Cache Rules: `medialab.papi656.com/api/*` | **Bypass cache** | responses carry `Cache-Control: no-store`; do not rely on the edge to honour it |

Optional, and the strongest upgrade available: put **Cloudflare Access** in front of the
hostname (one-time PIN or Google, policy limited to your address). The app's single
password has no revocation and no second factor; Access adds both without touching code.
Keep the app's password either way.

## 6. Verification

Run in this order. Each line has one acceptable result.

```bash
# 6.1 the site is up through the tunnel
curl -sS https://medialab.papi656.com/health
# -> {"status":"ok","database":true,"provider":"fake","models":60}

# 6.2 nothing is published on the VPS itself (run from a DIFFERENT machine)
nc -z -w2 <vps-ip> 80    ; echo "80=$?"     # nonzero
nc -z -w2 <vps-ip> 443   ; echo "443=$?"    # nonzero
nc -z -w2 <vps-ip> 8000  ; echo "8000=$?"   # nonzero

# 6.3 the cookie shape: __Host- prefix, Secure, HttpOnly, no Domain
curl -si -X POST https://medialab.papi656.com/api/auth/login \
  -H 'Content-Type: application/json' -H 'X-Requested-With: fetch' \
  -d '{"password":"<password>"}' | grep -i '^set-cookie'
# -> set-cookie: __Host-ml_session=...; HttpOnly; Max-Age=604800; Path=/; SameSite=lax; Secure

# 6.4 the API map is not public
curl -o /dev/null -w '%{http_code}\n' https://medialab.papi656.com/api/docs       # 404
curl -o /dev/null -w '%{http_code}\n' https://medialab.papi656.com/api/openapi.json  # 404

# 6.5 rotating X-Forwarded-For must NOT bypass the limiter (this is D2's whole point)
for i in $(seq 1 7); do curl -s -o /dev/null -w '%{http_code} ' \
  -X POST https://medialab.papi656.com/api/auth/login \
  -H 'Content-Type: application/json' -H 'X-Requested-With: fetch' \
  -H "X-Forwarded-For: 10.9.9.$i" -d '{"password":"x"}'; done; echo
# -> 401 401 401 401 401 429 429

# 6.6 the container is not root and can write its volume
docker exec <container> id                                  # uid=10001(app)
docker exec <container> sh -c 'touch /data/probe && ls -ln /data/probe'   # 10001 10001
docker exec <container> ls -ln /data | head                 # medialab.db owned by 10001

# 6.7 the pre-deploy migration actually ran, and the container agreed with it
docker exec <container> alembic current                     # 50a3ad53309a (head)
docker exec <container> alembic heads                       # same revision

# 6.8 the D4 caps are the ones in production, and the 413 path is live
docker exec <container> python -c \
  'from app.config import get_settings as g; s=g(); print(s.max_upload_mb, s.max_reference_bytes, s.storage_watermark_pct)'
# -> 10 41943040 85
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://medialab.papi656.com/api/uploads \
  -H "Cookie: __Host-ml_session=<from 6.3>" -H 'X-Requested-With: fetch' \
  -F 'file=@/tmp/eleven-mb.png'                              # -> 413

# 6.9 the backup has run and the repository is not empty
RESTIC_REPOSITORY=... RESTIC_PASSWORD_FILE=... restic snapshots --tag medialab --latest 1
# -> one snapshot, listing medialab.db and the assets directory
```

`6.7` is what proves the pre-deployment command is wired up. If the two differ, the
container would have refused to start (`RUN_MIGRATIONS_ON_START=0`), so a running
container whose `alembic current` is behind head is impossible — which is the point.

`6.5` blocking you out for 15 minutes is expected — it counts your own attempts against
whatever address Cloudflare reports. Wait, or restart the container to clear the in-memory
limiter.

## 7. Flip to the real provider

```bash
# Coolify env: PROVIDER=openrouter, OPENROUTER_API_KEY=sk-or-..., DAILY_BUDGET_USD=<number>
# Redeploy, then:
curl -sS -X POST https://medialab.papi656.com/api/auth/login ...   # (as in 6.3)
# generate one cheap image in the UI, confirm the job reaches `succeeded`,
curl -sS https://medialab.papi656.com/api/usage?period=day          # cost recorded, not estimated
```

The server refuses to boot with `PROVIDER=openrouter` and no `DAILY_BUDGET_USD`, or with
`FORWARDED_ALLOW_IPS=*`. That is intentional; a failed deploy is cheaper than a wrong one.

## 8. Backups (D9)

All state is one SQLite file plus content-addressed assets on one volume. `cp` on a
live WAL database produces a torn copy, so `scripts/backup.sh` uses SQLite's online
backup API, checks the copy with `PRAGMA integrity_check`, and ships it with the assets
in one restic snapshot. Assets are immutable and content-addressed, so dedupe makes the
daily snapshot cheap after the first.

### 8.1 One-time setup

```bash
# On the host. restic is a single static binary.
sudo apt-get install -y restic sqlite3

# Credentials + repository password (a bearer secret: chmod 600, never commit).
sudo install -d -m 700 /etc/medialab
sudo install -m 600 deploy/medialab-backup.env.example /etc/medialab/backup.env
sudo sh -c 'openssl rand -base64 32 > /etc/medialab/restic-password && chmod 600 /etc/medialab/restic-password'
sudo editor /etc/medialab/backup.env      # B2 bucket, application key, password file

# The scripts the units call. Kept outside /srv/medialab/data so a restore never
# overwrites the code that performs it.
sudo install -d -m 755 /opt/medialab/scripts
sudo install -m 755 scripts/backup.sh scripts/restore.sh /opt/medialab/scripts/

# Initialise the repository once (fails harmlessly if it already exists).
sudo sh -c 'set -a; . /etc/medialab/backup.env; set +a; restic init'

# Prove it works before trusting a timer with it.
sudo /opt/medialab/scripts/backup.sh
sudo systemctl daemon-reload
sudo cp deploy/systemd/medialab-backup*.{service,timer} /etc/systemd/system/
sudo systemctl enable --now medialab-backup.timer medialab-backup-prune.timer
systemctl list-timers 'medialab-*'
```

Retention is **7 daily / 4 weekly / 3 monthly**, applied by `restic forget` on every
run. `prune` (which rewrites pack files and costs object-store transactions) runs
weekly from its own unit, `medialab-backup-prune.service`.

> The restic password is the only thing that can decrypt the repository. Store a copy
> off this VPS (password manager). Losing it loses every backup; there is no reset.

### 8.2 The restore drill — part of the definition of done

A backup that has never been restored is a hypothesis.

```bash
# 1. Restore the latest snapshot into a directory shaped like /data.
sudo /opt/medialab/scripts/restore.sh /srv/medialab/restore-check
# -> restored: integrity=ok, job_rows=…, asset_rows=…, asset_files=…

# 2. Mount it into a scratch container on a spare loopback port. Never over the
#    live volume, never on 8000 (the connector dials that port).
docker run --rm -d --name medialab-restore \
  -p 127.0.0.1:8010:8000 \
  -e PROVIDER=fake -e DATA_DIR=/data -e STATIC_DIR=/app/static \
  -e ADMIN_PASSWORD=drill -e RUN_MIGRATIONS_ON_START=0 \
  -v /srv/medialab/restore-check:/data <image>

# 3. Log in, open a job that existed before the backup, and confirm the image bytes
#    render. History without images is not a restore.
open http://127.0.0.1:8010  # or: docker exec medialab-restore ls -ln /data/assets | head

# 4. Compare a known asset against the live volume.
docker exec medialab-restore sh -c 'sha256sum /data/assets/ab/*.png' \
  && docker exec <live-container> sh -c 'sha256sum /data/assets/ab/*.png'

docker rm -f medialab-restore
sudo rm -rf /srv/medialab/restore-check
```

Record the date of the last successful drill here in the repo's decision log. Monthly is
enough for a single-operator service; re-run it after any restic or schema change.

## 9. Still missing before this is a finished deployment

| Decision | State |
| --- | --- |
| D4 — disk/RAM caps | **implemented**; `MAX_UPLOAD_MB`, `MAX_REFERENCE_BYTES`, `STORAGE_WATERMARK_PCT` (`507`) and `--limit-concurrency 8`; run §6.8 against the live container |
| D7 — Alembic migrations | **implemented**; only the failed-migration/redeploy check is unrun, and it needs a VPS (§6.7 proves the happy path) |
| D9 — off-host backup + restore drill | **implemented**; the drill in §8.2 still has to be run on the VPS once the bucket exists |
| D11 — deploy interrupts in-flight jobs | accepted; the job shows `interrupted` and Retry is the path |

`docker compose down -v` on the local stack is not a backup.

### 9.1 Adding a migration locally

```bash
cd backend
uv run alembic revision --autogenerate -m "add thing"   # review the generated file
uv run alembic upgrade head                             # what the pre-deploy step runs
uv run alembic check                                    # must print "No new upgrade operations detected"
uv run pytest                                           # test_models_and_migrations_do_not_drift guards this
```

Every migration must be **expand-then-contract**: the pre-deploy step mutates the schema
while the *old* container is still serving, so a rename or a drop in the same deploy that
adds its replacement breaks the running container mid-deploy. Add the column, backfill,
switch the code in a later release, drop the old column last.

## 10. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Cloudflare `1033` / `502` | connector down, or `service:` port wrong | `journalctl -u cloudflared -n 50`; check `127.0.0.1:8000` answers on the host |
| `curl` 301 → same URL | a Coolify domain / Traefik router still exists for the app | remove the Coolify domain; the tunnel is the only ingress |
| Login "works", reload says `authenticated:false` | `COOKIE_SECURE=false` (cookie not `Secure`, dropped or reused across schemes) | set `COOKIE_SECURE=true`; it must match how the browser sees the site |
| All visitors look like one client | `FORWARDED_ALLOW_IPS` is the old value or loopback | re-read the gateway (§4) and redeploy |
| `docker exec <c> id` says `uid=0` | Coolify overrode `USER` | the no-root fork in D6; either fix Coolify's setting or accept a root-then-drop entrypoint |
| `Permission denied` writing `/data` | bind mount not chowned to 10001 | `sudo chown -R 10001:10001 /srv/medialab/data` |
| Container exits 1: `database schema is not at head` | `RUN_MIGRATIONS_ON_START=0` and the pre-deployment command did not run or failed | fix the Pre-deployment Command (`alembic upgrade head`), redeploy; the container is deliberately refusing rather than serving a schema it does not know |
| `413` on upload | file over `MAX_UPLOAD_MB` (10) | expected; shrink the reference |
| `422` with `limit_bytes` on a generation | this job's references total more than `MAX_REFERENCE_BYTES` (40 MiB) | use fewer/smaller references; the cap bounds the base64 (+33%) memory a single call can hold |
| `507` on upload/generate | the volume is at `STORAGE_WATERMARK_PCT` (85%) | delete generations or wait for GC; check with `df -h /srv/medialab/data`. Do not raise the watermark to "fix" it — the last 15% is the WAL/GC headroom |
| `medialab-backup.service` fails | restic credentials, network, or the repository was never `init`-ed | `journalctl -u medialab-backup -n 50`; test by hand with `sudo /opt/medialab/scripts/backup.sh` |

## 11. Adding a second service later

1. Pick an unused loopback port (`8001`).
2. Publish the new app as `127.0.0.1:8001:<port>` in Coolify, no domain.
3. Add one `ingress` rule to `deploy/cloudflared/config.yml` **above** the `http_status:404`
   catch-all, copy it to `/etc/cloudflared/config.yml`, `sudo systemctl restart cloudflared`.
4. `cloudflared tunnel route dns medialab something.papi656.com`.

One connector, one tunnel, N hostnames. The tunnel token / credentials file is a bearer
secret for every hostname on it — treat `/etc/cloudflared/<UUID>.json` like the OpenRouter
key, and keep `chmod 600`.
