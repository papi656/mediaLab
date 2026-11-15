"""Alembic environment.

Two callers, one script:

* the CLI (`alembic upgrade head`), which is the Coolify pre-deployment command and
  builds its own engine from `DATA_DIR`;
* the application and the test suite, via `app.db.init_db`, which hands over an
  already-open connection. Same script either way, so the migration that runs in a
  test is byte-for-byte the migration that runs in production (D7).

`render_as_batch` is on because SQLite cannot `ALTER TABLE ... DROP COLUMN` or change
a column in place; batch mode makes every future autogenerate emit the
create-copy-swap dance instead of a statement the database rejects at deploy time.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import context

from app.config import Settings
from app.models import Base

config = context.config

# No `fileConfig(config.config_file_name)`: alembic.ini carries no logging sections, so
# the app's own logging configuration stays in charge. `basicConfig` is a no-op when a
# handler already exists (uvicorn, pytest), and gives the pre-deployment command a
# "Running upgrade ... -> <rev>" line when it runs unattended from a shell — which is
# the one place nobody is watching stderr interactively.
logging.basicConfig(level=logging.INFO, format="%(message)s")

target_metadata = Base.metadata


def _url() -> str:
    """The migration target: explicit `-x url=...`, else the app's own setting."""
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured
    return Settings().database_url


def _run(connection: sa.Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        # Fail loudly on a migration that would leave a type the DB cannot render,
        # instead of silently emitting a no-op.
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return

    engine = sa.create_engine(_url(), future=True)
    try:
        with engine.connect() as own_connection:
            _run(own_connection)
            own_connection.commit()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
