#!/usr/bin/env bash
# mediaLab off-host backup (D9).
#
# All state is one SQLite file in WAL mode plus content-addressed assets on one
# volume on one VPS. `cp medialab.db` on a live WAL database produces a torn copy,
# so this uses the SQLite online backup API and then ships the sealed copy plus the
# assets with restic. Assets are immutable and content-addressed, so incremental
# dedupe is essentially free.
#
# Usage:      sudo /opt/medialab/scripts/backup.sh
# Env file:   /etc/medialab/backup.env  (see deploy/medialab-backup.env.example)
# Timer:      deploy/systemd/medialab-backup.{service,timer}
#
# Retention: 7 daily / 4 weekly / 3 monthly snapshots, pruned each run.
#
# A backup that has never been restored is a hypothesis. Run scripts/restore.sh
# (see docs/vps-runbook.md §8) before calling D9 done.

set -Eeuo pipefail

DATA_DIR="${DATA_DIR:-/srv/medialab/data}"
DB="${DB:-$DATA_DIR/medialab.db}"
ASSETS="${ASSETS:-$DATA_DIR/assets}"
TAG="${BACKUP_TAG:-medialab}"
# A fixed staging path, not mktemp: restic records absolute paths, and a stable one
# keeps `restore.sh` predictable and keeps the DB's chunks deduping across runs.
STAGING_DIR="${STAGING_DIR:-/var/tmp/medialab-backup}"
LOCK_FILE="${LOCK_FILE:-/run/lock/medialab-backup.lock}"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

cleanup() {
    rm -rf "$STAGING_DIR"
}
trap cleanup EXIT

[[ -n "${RESTIC_REPOSITORY:-}" ]] || die "RESTIC_REPOSITORY is not set; source /etc/medialab/backup.env"
[[ -n "${RESTIC_PASSWORD:-}${RESTIC_PASSWORD_FILE:-}${RESTIC_PASSWORD_COMMAND:-}" ]] \
    || die "no restic password: set RESTIC_PASSWORD_FILE in /etc/medialab/backup.env"
command -v sqlite3 >/dev/null 2>&1 || die "sqlite3 is not installed"
command -v restic >/dev/null 2>&1 || die "restic is not installed"
[[ -f "$DB" ]] || die "no database at $DB"
[[ -d "$ASSETS" ]] || die "no assets directory at $ASSETS"

# Overlapping runs would race on the staging directory and on restic's lock.
if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK_FILE" || die "cannot open lock file $LOCK_FILE"
    flock -n 9 || die "another backup is already running"
fi

log "backing up $DB and $ASSETS (tag=$TAG)"

rm -rf "$STAGING_DIR"
mkdir -p "$STAGING_DIR"

# 1. WAL-safe copy through the online backup API. Never `cp` a live WAL database.
sqlite3 "$DB" ".backup '$STAGING_DIR/medialab.db'" \
    || die "sqlite3 .backup failed; refusing to ship a possibly torn copy"

# 2. Prove the copy is readable before it leaves the host. A snapshot of a corrupt
#    database is worse than no snapshot, because it looks like a backup.
integrity="$(sqlite3 "$STAGING_DIR/medialab.db" 'PRAGMA integrity_check;')"
[[ "$integrity" == "ok" ]] || die "restored copy failed integrity_check: $integrity"

jobs_count="$(sqlite3 "$STAGING_DIR/medialab.db" 'SELECT count(*) FROM media_jobs;' 2>/dev/null || echo '?')"
log "database copy verified (integrity=ok, jobs=$jobs_count, $(du -sh "$STAGING_DIR/medialab.db" | cut -f1))"

# 3. Ship it. One snapshot holds both the database and the assets, so a restore of
#    "latest" always returns a consistent pair — restoring the DB alone restores
#    history without the images.
restic backup \
    --tag "$TAG" \
    --host "$(hostname -s)" \
    "$STAGING_DIR/medialab.db" \
    "$ASSETS"

# 4. Retention. `forget` is cheap; `--prune` rewrites packs and costs B2/S3
#    transactions, which is why it is a separate, weekly-only step by default.
#    Without pruning, deleted snapshots keep their data forever.
restic forget \
    --tag "$TAG" \
    --keep-daily 7 \
    --keep-weekly 4 \
    --keep-monthly 3

if [[ "${PRUNE:-0}" == "1" ]]; then
    log "pruning unreferenced data (weekly)"
    # `prune` has no --tag filter: it rewrites the whole repository. On this host
    # the repository holds one tag, so that is the same thing; a second app sharing
    # this repo would need its own repository instead.
    restic prune
fi

# 5. Report what is actually in the repository, so a silent no-op is visible.
latest="$(restic snapshots --tag "$TAG" --latest 1 --json | grep -o '"short_id":"[^"]*"' | head -1 | cut -d'"' -f4 || true)"
[[ -n "$latest" ]] || die "no snapshot found after backup; check restic output above"
log "ok: latest snapshot ${latest} (retention 7d/4w/3m${PRUNE:+, prune=${PRUNE}})"
