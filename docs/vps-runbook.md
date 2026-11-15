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
FORWARDED_ALLOW_IPS=<gateway>       # next section
COOKIE_SECURE=true
ENABLE_DOCS=0
MAX_UPLOAD_MB=10
MAX_N_HARD_CAP=4
JOB_TIMEOUT_SECONDS=120
SOFT_DELETE_RETENTION_DAYS=7
GC_INTERVAL_HOURS=6
```

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
```

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

## 8. Still missing before this is a finished deployment

| Decision | State |
| --- | --- |
| D4 — disk/RAM caps (watermark `507`, reference-bytes cap) | **not implemented**; only `MAX_UPLOAD_MB` and `--limit-concurrency 8` exist |
| D7 — Alembic migrations | **not implemented**; `init_db()` is still `create_all`, so the first schema change after go-live will fail at runtime instead of at startup |
| D9 — off-host backup + restore drill | **not implemented**; `/data` has no copy off the VPS |
| D11 — deploy interrupts in-flight jobs | accepted; the job shows `interrupted` and Retry is the path |

Do D7 before the first *production* row exists. `docker compose down -v` on the local
stack is not a backup.

## 9. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Cloudflare `1033` / `502` | connector down, or `service:` port wrong | `journalctl -u cloudflared -n 50`; check `127.0.0.1:8000` answers on the host |
| `curl` 301 → same URL | a Coolify domain / Traefik router still exists for the app | remove the Coolify domain; the tunnel is the only ingress |
| Login "works", reload says `authenticated:false` | `COOKIE_SECURE=false` (cookie not `Secure`, dropped or reused across schemes) | set `COOKIE_SECURE=true`; it must match how the browser sees the site |
| All visitors look like one client | `FORWARDED_ALLOW_IPS` is the old value or loopback | re-read the gateway (§4) and redeploy |
| `docker exec <c> id` says `uid=0` | Coolify overrode `USER` | the no-root fork in D6; either fix Coolify's setting or accept a root-then-drop entrypoint |
| `Permission denied` writing `/data` | bind mount not chowned to 10001 | `sudo chown -R 10001:10001 /srv/medialab/data` |

## 10. Adding a second service later

1. Pick an unused loopback port (`8001`).
2. Publish the new app as `127.0.0.1:8001:<port>` in Coolify, no domain.
3. Add one `ingress` rule to `deploy/cloudflared/config.yml` **above** the `http_status:404`
   catch-all, copy it to `/etc/cloudflared/config.yml`, `sudo systemctl restart cloudflared`.
4. `cloudflared tunnel route dns medialab something.papi656.com`.

One connector, one tunnel, N hostnames. The tunnel token / credentials file is a bearer
secret for every hostname on it — treat `/etc/cloudflared/<UUID>.json` like the OpenRouter
key, and keep `chmod 600`.
