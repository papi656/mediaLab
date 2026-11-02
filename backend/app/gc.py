from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import timedelta

from sqlalchemy import delete, func, select

from .db import utcnow
from .models import Asset, MediaJob
from .storage import unlink

log = logging.getLogger("medialab.gc")

ORPHAN_UPLOAD_HOURS = 24


def run_gc(state, dry_run: bool = False) -> dict[str, int]:  # noqa: ANN001
    """Prune soft-deleted rows and reclaim files, respecting content-addressed sharing.

    Rows are deleted before files, so an interrupted run leaves a consistent state.
    """
    settings = state.settings
    assets_dir = settings.assets_dir
    counts = {
        "expired_jobs": 0,
        "expired_assets": 0,
        "orphan_uploads": 0,
        "files_unlinked": 0,
        "orphan_files": 0,
    }
    cutoff = utcnow() - timedelta(days=settings.soft_delete_retention_days)

    with state.sessionmaker() as session:
        expired = session.scalars(
            select(MediaJob).where(
                MediaJob.deleted_at.is_not(None), MediaJob.deleted_at < cutoff
            )
        ).all()
        expired_ids = [j.id for j in expired]

        candidates: list[tuple[str, str]] = []
        if expired_ids:
            candidates += [
                (sha, fn)
                for sha, fn in session.execute(
                    select(Asset.sha256, Asset.filename).where(Asset.job_id.in_(expired_ids))
                ).all()
            ]

        orphan_cutoff = utcnow() - timedelta(hours=ORPHAN_UPLOAD_HOURS)
        orphans = session.scalars(
            select(Asset).where(
                Asset.role == "input",
                Asset.job_id.is_(None),
                Asset.created_at < orphan_cutoff,
            )
        ).all()
        candidates += [(a.sha256, a.filename) for a in orphans]

        # Decide which files are safe to unlink *before* mutating anything.
        cand_counts = Counter(sha for sha, _ in candidates)
        db_counts = dict(
            session.execute(select(Asset.sha256, func.count()).group_by(Asset.sha256)).all()
        )
        to_unlink = sorted(
            {
                (sha, fn)
                for sha, fn in candidates
                if db_counts.get(sha, 0) - cand_counts[sha] == 0
            }
        )

        counts["expired_jobs"] = len(expired_ids)
        counts["expired_assets"] = len(candidates) - len(orphans)
        counts["orphan_uploads"] = len(orphans)
        counts["files_unlinked"] = len(to_unlink)

        if not dry_run:
            if orphans:
                session.execute(delete(Asset).where(Asset.id.in_([a.id for a in orphans])))
            if expired_ids:
                session.execute(delete(Asset).where(Asset.job_id.in_(expired_ids)))
                session.execute(delete(MediaJob).where(MediaJob.id.in_(expired_ids)))
            session.commit()

            for sha, fn in to_unlink:
                unlink(assets_dir, sha, fn)

        known = set(session.scalars(select(Asset.filename)).all()) if not dry_run else None

    # Files on disk with no row at all (crash recovery, manual meddling).
    if assets_dir.exists():
        for path in assets_dir.glob("*/*"):
            if not path.is_file() or path.name.endswith(".tmp"):
                continue
            if dry_run:
                counts["orphan_files"] += 1
                continue
            if path.name not in (known or set()):
                path.unlink(missing_ok=True)
                counts["orphan_files"] += 1

    log.info("gc %s: %s", "dry-run" if dry_run else "done", counts)
    return counts


async def gc_loop(state) -> None:  # noqa: ANN001
    interval = max(60.0, state.settings.gc_interval_hours * 3600)
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(run_gc, state, False)
        except Exception:  # noqa: BLE001 - the loop must survive
            log.exception("gc run failed")


async def run_gc_once(state) -> None:  # noqa: ANN001
    try:
        await asyncio.to_thread(run_gc, state, False)
    except Exception:  # noqa: BLE001
        log.exception("startup gc failed")


def _cli() -> None:
    """`python -m app.gc [--dry-run]` for manual inspection."""
    import sys
    from types import SimpleNamespace

    from .config import get_settings
    from .db import init_db, make_engine, make_sessionmaker

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = get_settings()
    settings.prepare_dirs()
    engine = make_engine(settings.database_url)
    init_db(engine)
    state = SimpleNamespace(settings=settings, sessionmaker=make_sessionmaker(engine))
    print(run_gc(state, dry_run="--dry-run" in sys.argv))


if __name__ == "__main__":
    _cli()
