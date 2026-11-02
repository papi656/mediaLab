from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from .clock import utcnow
from .models import Base

__all__ = [
    "Base",
    "db_session",
    "init_db",
    "live_jobs",
    "make_engine",
    "make_sessionmaker",
    "utcnow",
]


def make_engine(url: str) -> Engine:
    engine = create_engine(
        url,
        future=True,
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    return engine


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def live_jobs():
    """Every read path goes through this so a forgotten filter is impossible."""
    from .models import MediaJob

    return select(MediaJob).where(MediaJob.deleted_at.is_(None))


def db_session(request: Request) -> Iterator[Session]:
    sessionmaker_ = request.app.state.sessionmaker
    with sessionmaker_() as session:
        yield session
