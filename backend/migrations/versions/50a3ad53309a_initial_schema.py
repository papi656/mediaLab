"""initial schema

The pre-Alembic database is disposable (docs/vps-deployment-decisions.md, D7), so this
revision is generated from the models rather than reconstructed from whatever
`create_all` had produced: the VPS starts on an empty `/data` and reaches this state by
running the migration, not by being stamped. There is no `stamp head` path.

Generated with:

    DATA_DIR=$(mktemp -d) uv run alembic revision --autogenerate -m "initial schema"

Tables are created in dependency order (`media_jobs` before `assets`, which carries the
`job_id` foreign key). Indexes use batch operations, matching `render_as_batch=True` in
`migrations/env.py`, because SQLite cannot drop or alter an index in place.

Revision ID: 50a3ad53309a
Revises:
Create Date: 2026-11-15 23:45:05.868876

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "50a3ad53309a"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "media_jobs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("provider_tag", sa.String(length=128), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_detail", sa.JSON(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("n_requested", sa.Integer(), nullable=False),
        sa.Column("n_received", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("media_jobs", schema=None) as batch_op:
        batch_op.create_index("ix_jobs_created_at", ["created_at"], unique=False)
        batch_op.create_index("ix_jobs_deleted_at", ["deleted_at"], unique=False)
        batch_op.create_index("ix_jobs_status", ["status"], unique=False)

    op.create_table(
        "spend_ledger",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=True),
        sa.Column("amount_usd", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("spend_ledger", schema=None) as batch_op:
        batch_op.create_index("ix_spend_created_at", ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_spend_ledger_job_id"), ["job_id"], unique=False)

    op.create_table(
        "assets",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=True),
        sa.Column("role", sa.String(length=8), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=80), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["media_jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_assets_job_id"), ["job_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_assets_sha256"), ["sha256"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_assets_sha256"))
        batch_op.drop_index(batch_op.f("ix_assets_job_id"))
    op.drop_table("assets")

    with op.batch_alter_table("spend_ledger", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_spend_ledger_job_id"))
        batch_op.drop_index("ix_spend_created_at")
    op.drop_table("spend_ledger")

    with op.batch_alter_table("media_jobs", schema=None) as batch_op:
        batch_op.drop_index("ix_jobs_status")
        batch_op.drop_index("ix_jobs_deleted_at")
        batch_op.drop_index("ix_jobs_created_at")
    op.drop_table("media_jobs")
