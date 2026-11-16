from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..capabilities import Capabilities, estimate_cost, validate_request
from ..db import db_session, live_jobs, utcnow
from ..jobs import dispatch, new_job, today_spend
from ..limits import enforce_storage, reference_bytes_exceeded
from ..models import Asset, MediaJob
from ..schemas import GenerationIn, JobListOut, JobOut

router = APIRouter(
    prefix="/api/generations", tags=["generations"], dependencies=[Depends(require_auth)]
)

ACTIVE_STATUSES = {"queued", "running"}


def _reference_assets(session: Session, ids: list[str]) -> list[Asset]:
    assets: list[Asset] = []
    for asset_id in ids:
        asset = session.get(Asset, asset_id)
        if asset is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown reference {asset_id}"
            )
        assets.append(asset)
    return assets


def _guard_dispatch(
    state,  # noqa: ANN001 - AppState
    session: Session,
    *,
    model: str,
    params: dict[str, Any],
) -> Capabilities:
    """Everything every dispatch path must check, in one place.

    `create` and `retry` both spend money, so both go through this. Retry used to
    copy `params` straight into a new job, which skipped the capability, reference
    and daily-budget checks entirely — a cap that a button can walk around is not
    a cap.
    """
    settings = state.settings

    caps = state.catalogue.get(model)
    if caps is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown model {model!r}")

    n = int(params.get("n", 1))
    if n > settings.max_n_hard_cap:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"n is capped at {settings.max_n_hard_cap} on this server",
        )

    errors = validate_request(caps, params)
    if errors:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"detail": "invalid request for this model", "errors": errors},
        )

    references = _reference_assets(session, list(params.get("reference_asset_ids") or []))
    total_bytes = sum(asset.bytes or 0 for asset in references)
    if total_bytes > settings.max_reference_bytes:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            reference_bytes_exceeded(total_bytes, settings.max_reference_bytes),
        )

    estimate = estimate_cost(caps, n)
    if settings.daily_budget_usd is not None and estimate is not None:
        spent = today_spend(session)
        if spent + estimate > settings.daily_budget_usd:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                {
                    "detail": "daily budget would be exceeded",
                    "spent_usd": round(spent, 4),
                    "estimate_usd": estimate,
                    "limit_usd": settings.daily_budget_usd,
                },
            )
    return caps


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=JobOut)
def create_generation(
    body: GenerationIn,
    request: Request,
    session: Session = Depends(db_session),
) -> JobOut:
    state = request.app.state
    enforce_storage(state.settings)

    params = body.to_params()
    _guard_dispatch(state, session, model=body.model, params=params)

    job = new_job(session, model=body.model, prompt=body.prompt, params=params)
    dispatch(state, job.id)
    return JobOut.from_job(job)


@router.get("", response_model=JobListOut)
def list_generations(
    session: Session = Depends(db_session),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    model: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
) -> JobListOut:
    stmt = live_jobs()
    if model:
        stmt = stmt.where(MediaJob.model == model)
    if status_filter:
        stmt = stmt.where(MediaJob.status == status_filter)

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(
        stmt.order_by(MediaJob.created_at.desc(), MediaJob.id.desc()).limit(limit).offset(offset)
    ).all()
    return JobListOut(items=[JobOut.from_job(j) for j in rows], total=total)


def _live_job(session: Session, job_id: str) -> MediaJob:
    job = session.get(MediaJob, job_id)
    if job is None or job.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "generation not found")
    return job


@router.get("/{job_id}", response_model=JobOut)
def get_generation(job_id: str, session: Session = Depends(db_session)) -> JobOut:
    return JobOut.from_job(_live_job(session, job_id))


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_generation(job_id: str, session: Session = Depends(db_session)) -> Response:
    """Soft delete. Files are reclaimed later by GC, which is safe because it
    reference-counts content-addressed files before unlinking."""
    job = session.get(MediaJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "generation not found")
    if job.deleted_at is None:
        job.deleted_at = utcnow()
    if job.status in ACTIVE_STATUSES:
        job.status = "cancelled"
        job.finished_at = utcnow()
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{job_id}/restore", response_model=JobOut)
def restore_generation(job_id: str, session: Session = Depends(db_session)) -> JobOut:
    job = session.get(MediaJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "generation not found")
    job.deleted_at = None
    session.commit()
    return JobOut.from_job(job)


@router.post("/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED, response_model=JobOut)
def retry_generation(
    job_id: str, request: Request, session: Session = Depends(db_session)
) -> JobOut:
    source = session.get(MediaJob, job_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "generation not found")

    state = request.app.state
    # Retry spends money and writes bytes, so it is subject to the same caps as a
    # fresh request rather than trusted because the original passed them.
    enforce_storage(state.settings)
    params: dict[str, Any] = dict(source.params or {})
    _guard_dispatch(state, session, model=source.model, params=params)

    job = new_job(session, model=source.model, prompt=source.prompt, params=params)
    dispatch(state, job.id)
    return JobOut.from_job(job)
