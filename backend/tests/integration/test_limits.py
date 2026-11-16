"""D4: the caps that stop one client from filling the disk or the RAM.

Both limits are soft and early on purpose — GC is the only thing that reclaims
space, so the system must refuse work *before* the volume is full rather than
after. These tests pin the three refusal paths and, just as importantly, that a
healthy volume still accepts work.
"""

from __future__ import annotations

import pytest
from fastapi import status

from app.limits import DiskUsage, StorageWatermarkExceeded, require_storage

from ..helpers import wait_for_job

GEMINI = "google/gemini-3-pro-image"


def test_upload_over_max_upload_mb_is_413(auth):
    payload = b"\x00" * (11 * 1024 * 1024)
    response = auth.post("/api/uploads", files={"file": ("big.png", payload, "image/png")})
    assert response.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    assert "10 MB" in response.text


def test_references_over_max_reference_bytes_are_422(auth, client, app):
    """The per-file cap is 10 MB, so 5 uploads of 9 MB is under no single limit
    and still 45 MB of base64 (+33%) in memory for one provider call."""
    ids = []
    for index in range(5):
        blob = bytes([index]) * (9 * 1024 * 1024)
        response = auth.post("/api/uploads", files={"file": (f"r{index}.png", blob, "image/png")})
        assert response.status_code == status.HTTP_201_CREATED, response.text
        ids.append(response.json()["id"])

    response = auth.post(
        "/api/generations",
        json={"model": GEMINI, "prompt": "too many bytes", "reference_asset_ids": ids},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    detail = response.json()["detail"]
    assert detail["limit_bytes"] == 40 * 1024 * 1024
    assert detail["total_bytes"] > detail["limit_bytes"]
    assert client.get("/api/generations").json()["total"] == 0


def test_a_full_volume_refuses_uploads_with_507(auth, settings, monkeypatch):
    settings.storage_watermark_pct = 85
    monkeypatch.setattr(
        "app.limits.disk_usage",
        lambda _path: DiskUsage(total_bytes=100, used_bytes=90, free_bytes=10),
    )

    response = auth.post("/api/uploads", files={"file": ("x.png", b"not read", "image/png")})

    assert response.status_code == status.HTTP_507_INSUFFICIENT_STORAGE
    detail = response.json()["detail"]
    assert detail["limit_pct"] == 85
    assert detail["used_pct"] == 90.0
    assert "delete generations" in detail["detail"]


def test_a_full_volume_refuses_generations_with_507(auth, settings, monkeypatch):
    settings.storage_watermark_pct = 85
    monkeypatch.setattr(
        "app.limits.disk_usage",
        lambda _path: DiskUsage(total_bytes=100, used_bytes=99, free_bytes=1),
    )

    response = auth.post("/api/generations", json={"model": GEMINI, "prompt": "no room"})

    assert response.status_code == status.HTTP_507_INSUFFICIENT_STORAGE
    assert auth.get("/api/generations").json()["total"] == 0


def test_a_full_volume_refuses_retries_with_507(auth, app, settings, monkeypatch):
    """Retry writes output bytes too; it must not be a way around the watermark."""
    created = auth.post("/api/generations", json={"model": GEMINI, "prompt": "first"}).json()
    assert wait_for_job(auth, created["id"])["status"] == "succeeded"

    settings.storage_watermark_pct = 85
    monkeypatch.setattr(
        "app.limits.disk_usage",
        lambda _path: DiskUsage(total_bytes=100, used_bytes=99, free_bytes=1),
    )
    assert auth.post(f"/api/generations/{created['id']}/retry").status_code == (
        status.HTTP_507_INSUFFICIENT_STORAGE
    )


def test_health_still_answers_when_the_volume_is_full(client, monkeypatch):
    """A full disk must not look like a dead process to the platform's health check."""
    monkeypatch.setattr(
        "app.limits.disk_usage",
        lambda _path: DiskUsage(total_bytes=100, used_bytes=100, free_bytes=0),
    )
    assert client.get("/health").status_code == 200


def test_the_watermark_is_inclusive(settings, monkeypatch):
    """At exactly `storage_watermark_pct` the answer is no. Reading the boundary the
    other way round is how "85%" quietly becomes "until the disk is full"."""
    settings.storage_watermark_pct = 85
    monkeypatch.setattr(
        "app.limits.disk_usage",
        lambda _path: DiskUsage(total_bytes=100, used_bytes=85, free_bytes=15),
    )
    with pytest.raises(StorageWatermarkExceeded):
        require_storage(settings)

    settings.storage_watermark_pct = 86
    assert require_storage(settings).used_pct == 85.0


def test_zero_sized_volume_is_treated_as_full():
    assert DiskUsage(total_bytes=0, used_bytes=0, free_bytes=0).used_pct == 100.0


def test_a_healthy_volume_accepts_work(auth):
    created = auth.post("/api/generations", json={"model": GEMINI, "prompt": "fine"})
    assert created.status_code == status.HTTP_202_ACCEPTED
    assert wait_for_job(auth, created.json()["id"])["status"] == "succeeded"
