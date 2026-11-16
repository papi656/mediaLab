"""A reference image must outlive the job that used it.

Regression: uploads are created with `job_id = None` and the GC sweeps orphans after
24 h, so the row outlived nothing — a retry the next day ran from the prompt alone
and still reported success.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete

from app.clock import utcnow
from app.gc import run_gc
from app.models import Asset
from app.storage import asset_path

from ..helpers import wait_for_job

GEMINI = "google/gemini-3-pro-image"


def _generate_with_reference(auth, asset_id: str, prompt: str = "match this style") -> dict:
    created = auth.post(
        "/api/generations",
        json={"model": GEMINI, "prompt": prompt, "reference_asset_ids": [asset_id]},
    )
    assert created.status_code == 202, created.text
    job = wait_for_job(auth, created.json()["id"])
    assert job["status"] == "succeeded", job["error"]
    return job


def _age_upload(app, asset_id: str, hours: int = 48) -> None:
    with app.state.sessionmaker() as session:
        asset = session.get(Asset, asset_id)
        asset.created_at = utcnow() - timedelta(hours=hours)
        session.commit()


def test_the_job_claims_the_reference_it_used(auth, app, uploaded_asset):
    job = _generate_with_reference(auth, uploaded_asset["id"])

    with app.state.sessionmaker() as session:
        assert session.get(Asset, uploaded_asset["id"]).job_id == job["id"]

    # Retention links the input to the job; the payload still only holds outputs.
    assert [asset["role"] for asset in job["assets"]] == ["output"]


def test_a_claimed_reference_is_not_swept_as_an_orphan(auth, app, uploaded_asset):
    _generate_with_reference(auth, uploaded_asset["id"])
    _age_upload(app, uploaded_asset["id"])

    counts = run_gc(app.state)

    assert counts["orphan_uploads"] == 0
    with app.state.sessionmaker() as session:
        assert session.get(Asset, uploaded_asset["id"]) is not None


def test_retrying_a_day_later_still_sends_the_reference(auth, app, uploaded_asset):
    job = _generate_with_reference(auth, uploaded_asset["id"])
    _age_upload(app, uploaded_asset["id"])
    run_gc(app.state)

    retried = auth.post(f"/api/generations/{job['id']}/retry")
    assert retried.status_code == 202, retried.text
    again = wait_for_job(auth, retried.json()["id"])

    assert again["status"] == "succeeded", again["error"]
    payload = app.state.provider.calls[-1]
    assert len(payload["input_references"]) == 1
    assert payload["input_references"][0].startswith("data:image/png;base64,")


def test_a_reference_shared_by_a_retry_is_not_deleted_with_the_original(
    auth, app, settings, uploaded_asset
):
    """Deleting the job that first used a reference must not break the retry's copy."""
    job = _generate_with_reference(auth, uploaded_asset["id"])
    retried = auth.post(f"/api/generations/{job['id']}/retry").json()
    wait_for_job(auth, retried["id"])

    auth.delete(f"/api/generations/{job['id']}")
    settings.soft_delete_retention_days = 0
    run_gc(app.state)

    with app.state.sessionmaker() as session:
        assert session.get(Asset, uploaded_asset["id"]) is not None


def test_a_retry_naming_a_vanished_reference_is_refused_before_a_job_exists(
    auth, app, uploaded_asset
):
    """The old behaviour: silently generate from the prompt alone and call it a success.

    A reference whose row is gone is knowable at request time, so it is a 422 and no
    job is created — the loudest, cheapest place to fail.
    """
    job = _generate_with_reference(auth, uploaded_asset["id"])
    calls_before = len(app.state.provider.calls)

    with app.state.sessionmaker() as session:
        session.execute(delete(Asset).where(Asset.id == uploaded_asset["id"]))
        session.commit()

    retried = auth.post(f"/api/generations/{job['id']}/retry")

    assert retried.status_code == 422
    assert "unknown reference" in retried.text
    assert len(app.state.provider.calls) == calls_before, "provider was called without its refs"


def test_a_retry_whose_reference_file_is_gone_fails_loudly(auth, app, uploaded_asset):
    """The row can outlive its file (disk damage, a manual delete). The job must fail
    visibly instead of generating from the prompt alone and reporting success."""
    job = _generate_with_reference(auth, uploaded_asset["id"])
    calls_before = len(app.state.provider.calls)

    with app.state.sessionmaker() as session:
        asset = session.get(Asset, uploaded_asset["id"])
        asset_path(app.state.settings.assets_dir, asset.sha256, asset.filename).unlink()

    retried = auth.post(f"/api/generations/{job['id']}/retry")
    assert retried.status_code == 202, retried.text
    failed = wait_for_job(auth, retried.json()["id"])

    assert failed["status"] == "failed"
    assert "reference" in failed["error"]
    assert failed["error_detail"]["missing_reference_asset_ids"] == [uploaded_asset["id"]]
    assert len(app.state.provider.calls) == calls_before, "provider was called without its refs"


def test_a_deleted_job_gives_its_reference_back_to_the_gc(auth, app, settings, uploaded_asset):
    job = _generate_with_reference(auth, uploaded_asset["id"])
    auth.delete(f"/api/generations/{job['id']}")
    settings.soft_delete_retention_days = 0

    counts = run_gc(app.state)

    assert counts["expired_jobs"] == 1
    with app.state.sessionmaker() as session:
        assert session.get(Asset, uploaded_asset["id"]) is None
