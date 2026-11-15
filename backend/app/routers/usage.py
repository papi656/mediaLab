from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..db import db_session
from ..jobs import period_start, spend_between
from ..models import Asset, MediaJob
from ..schemas import UsageOut

router = APIRouter(prefix="/api", tags=["usage"], dependencies=[Depends(require_auth)])


@router.get("/usage", response_model=UsageOut)
def usage(
    period: str = Query("today", pattern="^(today|month|all)$"),
    session: Session = Depends(db_session),
) -> UsageOut:
    if period == "all":
        start = period_start("today").replace(year=1970, month=1, day=1)
    else:
        start = period_start(period)

    jobs = (
        session.scalar(
            select(func.count())
            .select_from(MediaJob)
            .where(MediaJob.deleted_at.is_(None), MediaJob.created_at >= start)
        )
        or 0
    )
    images = (
        session.scalar(
            select(func.count())
            .select_from(Asset)
            .join(MediaJob, Asset.job_id == MediaJob.id)
            .where(
                Asset.role == "output", MediaJob.created_at >= start, MediaJob.deleted_at.is_(None)
            )
        )
        or 0
    )
    return UsageOut(
        period=period,
        spend_usd=round(spend_between(session, start), 6),
        jobs=int(jobs),
        images=int(images),
    )
