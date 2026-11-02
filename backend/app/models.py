from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .clock import utcnow


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class MediaJob(Base):
    """One generation request, any media kind.

    `kind` + JSON `params` are what make video/audio additive rather than a migration.
    """

    __tablename__ = "media_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(16), default="image", nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_tag: Mapped[str | None] = mapped_column(String(128))
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    error_detail: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    cost_usd: Mapped[float | None] = mapped_column(Float)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    n_requested: Mapped[int] = mapped_column(Integer, default=1)
    n_received: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)

    assets: Mapped[list[Asset]] = relationship(
        back_populates="job", cascade="save-update, merge", order_by="Asset.created_at"
    )

    __table_args__ = (
        Index("ix_jobs_created_at", "created_at"),
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_deleted_at", "deleted_at"),
    )


class Asset(Base):
    """Bytes on disk are content-addressed and shared between rows.

    Two jobs producing identical output point at the same file, so deletion must be
    reference-counted by `sha256` — never by `job_id`.
    """

    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    job_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("media_jobs.id", ondelete="SET NULL"), index=True
    )
    role: Mapped[str] = mapped_column(String(8), default="output", nullable=False)  # input|output
    sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    filename: Mapped[str] = mapped_column(String(80), nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16), default="provider", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    job: Mapped[MediaJob | None] = relationship(back_populates="assets")


class SpendLedger(Base):
    __tablename__ = "spend_ledger"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    job_id: Mapped[str | None] = mapped_column(String(32), index=True)
    amount_usd: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (Index("ix_spend_created_at", "created_at"),)
