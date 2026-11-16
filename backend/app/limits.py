"""The two resource bounds that keep one container from killing its host (D4).

Disk and RAM are shared with the VPS. The caps here are deliberately *soft and
early*: GC is the only thing that reclaims space and it runs every
`gc_interval_hours`, so work has to be refused **before** the volume is full
rather than after. The last `100 - storage_watermark_pct` percent of the disk is
headroom for SQLite's WAL, the OS, and a GC run that is mid-flight — not for users.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import HTTPException, status

if TYPE_CHECKING:
    from .config import Settings

MIB = 1024 * 1024


@dataclass(frozen=True)
class DiskUsage:
    total_bytes: int
    used_bytes: int
    free_bytes: int

    @property
    def used_pct(self) -> float:
        # A volume that reports itself as zero-sized cannot be proven to have room,
        # so treat it as full rather than as empty.
        if self.total_bytes <= 0:
            return 100.0
        return 100.0 * self.used_bytes / self.total_bytes


class StorageWatermarkExceeded(Exception):
    """The data volume is at or above its watermark, so no new bytes are accepted."""

    def __init__(self, usage: DiskUsage, limit_pct: int) -> None:
        self.usage = usage
        self.limit_pct = limit_pct
        super().__init__(
            f"storage is {usage.used_pct:.1f}% full, at or above the {limit_pct}% watermark"
        )

    def detail(self) -> dict[str, float | int | str]:
        return {
            "detail": (
                "storage is full: delete generations (or wait for the 7-day GC) to free space"
            ),
            "used_pct": round(self.usage.used_pct, 2),
            "limit_pct": self.limit_pct,
            "free_bytes": self.usage.free_bytes,
        }


def disk_usage(path: Path) -> DiskUsage:
    usage = shutil.disk_usage(path)
    return DiskUsage(usage.total, usage.used, usage.free)


def require_storage(settings: Settings) -> DiskUsage:
    """Raise when the data volume is past the watermark. Returns the reading otherwise."""
    usage = disk_usage(settings.data_dir)
    if usage.used_pct >= settings.storage_watermark_pct:
        raise StorageWatermarkExceeded(usage, settings.storage_watermark_pct)
    return usage


def enforce_storage(settings: Settings) -> None:
    """HTTP adapter: a full volume is `507 Insufficient Storage`, not a 500."""
    try:
        require_storage(settings)
    except StorageWatermarkExceeded as exc:
        raise HTTPException(status.HTTP_507_INSUFFICIENT_STORAGE, exc.detail()) from exc


def reference_bytes_exceeded(total_bytes: int, limit_bytes: int) -> dict[str, float | int | str]:
    """The 422 body for a job whose references are collectively too large.

    Kept here next to the watermark so both caps are documented in one place; the
    check itself lives where asset sizes are available (`routers/generations.py`),
    because capabilities describe a model and this is a fact about stored bytes.
    """
    return {
        "detail": "reference images are too large for one generation",
        "total_bytes": total_bytes,
        "limit_bytes": limit_bytes,
        "hint": "use fewer or smaller references; the cap is per generation",
    }
