from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.clock import utcnow
from app.gc import run_gc
from app.models import Asset, MediaJob
from app.storage import asset_path

from ..helpers import wait_for_job

GEMINI = "google/gemini-3-pro-image"


def _generate(auth, prompt: str) -> dict:
    created = auth.post("/api/generations", json={"model": GEMINI, "prompt": prompt}).json()
    job = wait_for_job(auth, created["id"])
    assert job["status"] == "succeeded", job["error"]
    return job


def _asset_row(app, job_id: str) -> Asset:
    with app.state.sessionmaker() as session:
        return session.scalars(select(Asset).where(Asset.job_id == job_id)).one()


def test_identical_outputs_share_one_file(auth, app, settings):
    first = _generate(auth, "identical prompt")
    second = _generate(auth, "identical prompt")

    a = _asset_row(app, first["id"])
    b = _asset_row(app, second["id"])
    assert a.sha256 == b.sha256
    assert a.filename == b.filename
    path = asset_path(settings.assets_dir, a.sha256, a.filename)
    assert path.exists()


def test_gc_keeps_file_still_referenced_by_a_live_job(auth, app, settings):
    """The one rule that would silently corrupt data if it were wrong."""
    first = _generate(auth, "identical prompt")
    second = _generate(auth, "identical prompt")
    row = _asset_row(app, first["id"])
    path = asset_path(settings.assets_dir, row.sha256, row.filename)

    auth.delete(f"/api/generations/{first['id']}")
    settings.soft_delete_retention_days = 0
    counts = run_gc(app.state)

    assert counts["expired_jobs"] == 1
    assert counts["files_unlinked"] == 0
    assert path.exists(), "file was unlinked while another job still referenced it"

    with app.state.sessionmaker() as session:
        assert session.get(MediaJob, first["id"]) is None
        assert session.get(MediaJob, second["id"]) is not None


def test_gc_removes_file_once_last_reference_is_gone(auth, app, settings):
    first = _generate(auth, "identical prompt")
    second = _generate(auth, "identical prompt")
    row = _asset_row(app, first["id"])
    path = asset_path(settings.assets_dir, row.sha256, row.filename)

    auth.delete(f"/api/generations/{first['id']}")
    auth.delete(f"/api/generations/{second['id']}")
    settings.soft_delete_retention_days = 0

    counts = run_gc(app.state)
    assert counts["expired_jobs"] == 2
    assert counts["files_unlinked"] == 1
    assert not path.exists()


def test_restore_before_retention_window_keeps_everything(auth, app, settings):
    job = _generate(auth, "keep me")
    auth.delete(f"/api/generations/{job['id']}")

    counts = run_gc(app.state)  # default retention: 7 days
    assert counts["expired_jobs"] == 0

    assert auth.post(f"/api/generations/{job['id']}/restore").status_code == 200
    assert auth.get("/api/generations").json()["total"] == 1


def test_gc_reaps_orphaned_uploads(auth, app, settings, uploaded_asset, png_bytes):
    with app.state.sessionmaker() as session:
        asset = session.get(Asset, uploaded_asset["id"])
        assert asset is not None
        path = asset_path(settings.assets_dir, asset.sha256, asset.filename)
        asset.created_at = utcnow() - timedelta(hours=48)
        session.commit()

    assert path.exists()
    counts = run_gc(app.state)
    assert counts["orphan_uploads"] == 1
    assert counts["files_unlinked"] == 1
    assert not path.exists()
    with app.state.sessionmaker() as session:
        assert session.get(Asset, uploaded_asset["id"]) is None


def test_recent_orphan_uploads_are_left_alone(auth, app, uploaded_asset):
    counts = run_gc(app.state)
    assert counts["orphan_uploads"] == 0
    with app.state.sessionmaker() as session:
        assert session.get(Asset, uploaded_asset["id"]) is not None


def test_gc_removes_stray_files_with_no_row(auth, app, settings):
    stray = settings.assets_dir / "ff" / "ffdeadbeef.png"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"junk")

    counts = run_gc(app.state)
    assert counts["orphan_files"] == 1
    assert not stray.exists()


def test_dry_run_changes_nothing(auth, app, settings):
    job = _generate(auth, "dry run me")
    row = _asset_row(app, job["id"])
    path = asset_path(settings.assets_dir, row.sha256, row.filename)

    auth.delete(f"/api/generations/{job['id']}")
    settings.soft_delete_retention_days = 0

    counts = run_gc(app.state, dry_run=True)
    assert counts["expired_jobs"] == 1
    assert counts["files_unlinked"] == 1
    assert path.exists()
    with app.state.sessionmaker() as session:
        assert session.get(MediaJob, job["id"]) is not None


def test_gc_is_idempotent(auth, app, settings):
    job = _generate(auth, "once")
    auth.delete(f"/api/generations/{job['id']}")
    settings.soft_delete_retention_days = 0

    first = run_gc(app.state)
    second = run_gc(app.state)
    assert first["expired_jobs"] == 1
    assert second["expired_jobs"] == 0
    assert second["files_unlinked"] == 0
