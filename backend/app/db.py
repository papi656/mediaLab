from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import Request
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from .clock import utcnow
from .models import Base

__all__ = [
    "Base",
    "alembic_config",
    "db_session",
    "init_db",
    "live_jobs",
    "make_engine",
    "make_sessionmaker",
    "require_at_head",
    "utcnow",
]

# `backend/alembic.ini` next to `app/`, and `/app/alembic.ini` in the image.
BACKEND_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
MIGRATIONS_DIR = BACKEND_ROOT / "migrations"


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


def alembic_config() -> Config:
    """A Config that works regardless of cwd.

    The default `%(here)s` expansion inside alembic.ini covers the CLI, but tests and
    the app run from anywhere, so the script location is pinned to the package's own
    directory rather than the process working directory.
    """
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


def init_db(engine: Engine) -> None:
    """Bring the schema to `head`. Replaces `Base.metadata.create_all` (D7).

    `create_all` creates missing tables and never alters an existing one: the first
    added column would have appeared only on a fresh database, so a deployed instance
    would fail at runtime with a missing-column error instead of at startup. There is
    no create-all path left, deliberately — tests and local dev migrate too, so the
    migration that ships is the migration that is exercised on every test run.
    """
    config = alembic_config()
    with engine.connect() as connection:
        # Handing over the connection (rather than a URL) keeps one engine per
        # process and lets the caller own the file the database lives in.
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def require_at_head(engine: Engine) -> None:
    """Refuse to serve a schema this build does not know about.

    Used when migrations are delegated to the pre-deployment step: if that step was
    skipped or failed, starting anyway means queries fail one at a time against a
    schema that is a release behind, which is precisely the silent-runtime-failure
    mode D7 exists to remove.
    """
    script = ScriptDirectory.from_config(alembic_config())
    head = script.get_current_head()
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    if current != head:
        raise RuntimeError(
            "database schema is not at head "
            f"(current={current or 'empty'}, head={head}). "
            "Run `alembic upgrade head` — in production that is the Coolify "
            "pre-deployment command, and RUN_MIGRATIONS_ON_START is off."
        )


def live_jobs():
    """Every read path goes through this so a forgotten filter is impossible."""
    from .models import MediaJob

    return select(MediaJob).where(MediaJob.deleted_at.is_(None))


def db_session(request: Request) -> Iterator[Session]:
    sessionmaker_ = request.app.state.sessionmaker
    with sessionmaker_() as session:
        yield session
