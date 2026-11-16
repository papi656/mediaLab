#!/usr/bin/env bash
# mediaLab restore drill (D9).
#
# Restores the latest (or a named) snapshot into a directory shaped exactly like
# the container's /data, so the drill is: restore → mount into a scratch container
# → log in → open a pre-deploy job → confirm the image bytes render.
#
# Usage:
#   sudo /opt/medialab/scripts/restore.sh /srv/medialab/restore-check
#   sudo /opt/medialab/scripts/restore.sh /srv/medialab/restore-check <snapshot-id>
#
# Then, on a scratch container (never over the live volume):
#   docker run --rm -p 127.0.0.1:8010:8000 \
#     --env-file /etc/medialab/scratch.env \
#     -v /srv/medialab/restore-check:/data <image>
#
# The database is checked with `PRAGMA integrity_check` before this script exits,
# because an unrestorable snapshot is a hypothesis, not a backup.

set -Eeuo pipefail

TARGET_DIR="${1:-}"
SNAPSHOT="${2:-latest}"
TAG="${BACKUP_TAG:-medialab}"
# Must match scripts/backup.sh. Restic stores absolute source paths.
STAGING_DIR="${STAGING_DIR:-/var/tmp/medialab-backup}"
# Derived exactly like scripts/backup.sh does, so the two cannot drift when DATA_DIR
# is overridden (a drill against a staging volume, or a different host layout).
ASSETS_SRC="${ASSETS:-${DATA_DIR:-/srv/medialab/data}/assets}"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

[[ -n "$TARGET_DIR" ]] || die "usage: restore.sh TARGET_DIR [SNAPSHOT]"
[[ -n "${RESTIC_REPOSITORY:-}" ]] || die "RESTIC_REPOSITORY is not set; source /etc/medialab/backup.env"
[[ -n "${RESTIC_PASSWORD:-}${RESTIC_PASSWORD_FILE:-}${RESTIC_PASSWORD_COMMAND:-}" ]] \
    || die "no restic password: set RESTIC_PASSWORD_FILE in /etc/medialab/backup.env"
command -v restic >/dev/null 2>&1 || die "restic is not installed"
command -v sqlite3 >/dev/null 2>&1 || die "sqlite3 is not installed"

if [[ -e "$TARGET_DIR/medialab.db" ]]; then
    die "$TARGET_DIR already has a medialab.db; pick an empty directory (never the live /data)"
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

log "restoring snapshot $SNAPSHOT into $TARGET_DIR"
restic restore "$SNAPSHOT" --tag "$TAG" --target "$work"

db="$work$STAGING_DIR/medialab.db"
assets="$work$ASSETS_SRC"
[[ -f "$db" ]] || die "snapshot has no database at $STAGING_DIR/medialab.db"
[[ -d "$assets" ]] || die "snapshot has no assets at $ASSETS_SRC"

mkdir -p "$TARGET_DIR/assets"
cp -a "$db" "$TARGET_DIR/medialab.db"
if [[ -n "$(ls -A "$assets" 2>/dev/null)" ]]; then
    cp -a "$assets/." "$TARGET_DIR/assets/"
fi

# WAL/SHM siblings from the live volume must never be copied alongside a restored
# database: SQLite would replay a WAL that belongs to a different file.
rm -f "$TARGET_DIR/medialab.db-wal" "$TARGET_DIR/medialab.db-shm"

integrity="$(sqlite3 "$TARGET_DIR/medialab.db" 'PRAGMA integrity_check;')"
[[ "$integrity" == "ok" ]] || die "restored database failed integrity_check: $integrity"

jobs_count="$(sqlite3 "$TARGET_DIR/medialab.db" 'SELECT count(*) FROM media_jobs;' 2>/dev/null || echo '?')"
asset_rows="$(sqlite3 "$TARGET_DIR/medialab.db" 'SELECT count(*) FROM assets;' 2>/dev/null || echo '?')"
asset_files="$(find "$TARGET_DIR/assets" -type f ! -name '*.tmp' | wc -l | tr -d ' ')"

log "restored: integrity=ok, job_rows=$jobs_count, asset_rows=$asset_rows, asset_files=$asset_files"
log "now mount it into a scratch container and confirm a pre-deploy image renders"
