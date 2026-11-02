from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from .db import utcnow
from .models import Asset, MediaJob, SpendLedger
from .providers import ProviderError, ProviderRequest
from .storage import read_bytes, save_bytes

log = logging.getLogger("medialab.jobs")

TERMINAL = {"succeeded", "failed", "interrupted", "cancelled"}


def new_job(
    session,  # noqa: ANN001
    *,
    model: str,
    prompt: str,
    params: dict[str, Any],
    kind: str = "image",
) -> MediaJob:
    job = MediaJob(
        kind=kind,
        model=model,
        prompt=prompt,
        params=params,
        status="queued",
        n_requested=int(params.get("n", 1)),
    )
    session.add(job)
    session.commit()
    return job


def dispatch(state, job_id: str) -> None:  # noqa: ANN001
    """Fire-and-forget onto the app's loop, from any thread.

    Sync endpoints run in a threadpool, so `asyncio.create_task` would fail there;
    `run_coroutine_threadsafe` is the correct bridge. Single-worker by design.
    """
    future = asyncio.run_coroutine_threadsafe(asyncio.to_thread(run_job, state, job_id), state.loop)
    state.futures.add(future)
    future.add_done_callback(state.futures.discard)


def run_job(state, job_id: str) -> None:  # noqa: ANN001
    settings = state.settings
    try:
        with state.sessionmaker() as session:
            job = session.get(MediaJob, job_id)
            if job is None or job.status != "queued":
                return
            job.status = "running"
            job.started_at = utcnow()
            session.commit()

            references: list[tuple[bytes, str]] = []
            for asset_id in job.params.get("reference_asset_ids") or []:
                asset = session.get(Asset, asset_id)
                if asset is None:
                    continue
                references.append(
                    (read_bytes(settings.assets_dir, asset.sha256, asset.filename),
                     asset.content_type)
                )

            request = ProviderRequest(
                model=job.model,
                prompt=job.prompt,
                params=job.params or {},
                session_id=job.id,
                references=references,
            )

        result = state.provider.generate(request)

    except ProviderError as exc:
        _fail(state, job_id, exc.message, exc.detail)
        return
    except Exception as exc:  # noqa: BLE001 - one bad job must not kill the loop
        log.exception("job %s crashed", job_id)
        _fail(state, job_id, "unexpected error while generating", {"error": repr(exc)})
        return

    try:
        with state.sessionmaker() as session:
            job = session.get(MediaJob, job_id)
            if job is None:
                return
            if job.status == "cancelled":
                return  # discard output; the user does not want it

            for image in result.images:
                stored = save_bytes(settings.assets_dir, image.data, image.content_type)
                session.add(
                    Asset(
                        job_id=job.id,
                        role="output",
                        sha256=stored.sha256,
                        filename=stored.filename,
                        content_type=stored.content_type,
                        bytes=stored.size,
                        width=stored.width,
                        height=stored.height,
                        source=image.source,
                    )
                )

            job.status = "succeeded"
            job.n_received = len(result.images)
            job.finished_at = utcnow()
            job.cost_usd = result.cost_usd
            job.usage = result.usage
            if result.cost_usd:
                session.add(SpendLedger(job_id=job.id, amount_usd=result.cost_usd))
            session.commit()
    except Exception as exc:  # noqa: BLE001
        log.exception("job %s failed while persisting output", job_id)
        _fail(state, job_id, "could not store the generated output", {"error": repr(exc)})


def _fail(state, job_id: str, message: str, detail: dict[str, Any] | None) -> None:  # noqa: ANN001
    with state.sessionmaker() as session:
        job = session.get(MediaJob, job_id)
        if job is None or job.status in TERMINAL:
            return
        job.status = "failed"
        job.error = message
        job.error_detail = detail or {}
        job.finished_at = utcnow()
        session.commit()


def mark_interrupted(state) -> int:  # noqa: ANN001
    """In-process tasks do not survive a restart. Say so instead of hanging forever."""
    with state.sessionmaker() as session:
        stale = session.scalars(
            select(MediaJob).where(MediaJob.status.in_(("queued", "running")))
        ).all()
        for job in stale:
            job.status = "interrupted"
            job.error = "server restarted while this job was running"
            job.finished_at = utcnow()
        session.commit()
        return len(stale)


def today_spend(session) -> float:  # noqa: ANN001
    start = datetime.combine(utcnow().date(), datetime.min.time())
    total = session.scalar(
        select(func.coalesce(func.sum(SpendLedger.amount_usd), 0.0)).where(
            SpendLedger.created_at >= start
        )
    )
    return float(total or 0.0)


def spend_between(session, start: datetime) -> float:  # noqa: ANN001
    total = session.scalar(
        select(func.coalesce(func.sum(SpendLedger.amount_usd), 0.0)).where(
            SpendLedger.created_at >= start
        )
    )
    return float(total or 0.0)


def period_start(period: str) -> datetime:
    now = utcnow()
    if period == "today":
        return datetime.combine(now.date(), datetime.min.time())
    if period == "month":
        return datetime(now.year, now.month, 1)
    raise ValueError(period)


def header_range(days: int) -> datetime:
    return utcnow() - timedelta(days=days)
