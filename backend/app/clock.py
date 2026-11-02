from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Naive UTC. SQLite has no timezone type; everything downstream assumes UTC."""
    return datetime.now(UTC).replace(tzinfo=None)
