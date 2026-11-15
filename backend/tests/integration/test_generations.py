from __future__ import annotations

from fastapi.testclient import TestClient

from app.jobs import mark_interrupted
from app.main import create_app
from app.models import MediaJob

from ..helpers import PASSWORD, wait_for_job

GEMINI = "google/gemini-3-pro-image"
NO_REFS = "recraft/recraft-v4.1-flash"
MULTI = "bytedance-seed/seedream-4.5"


def _login(client) -> None:
    client.headers["X-Requested-With"] = "fetch"
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 204


def test_model_list_exposes_capabilities(auth):
    response = auth.get("/api/models")
    assert response.status_code == 200
    body = response.json()
    assert body["generated_at"]
    by_id = {m["id"]: m for m in body["models"]}
    assert by_id[GEMINI]["max_refs"] == 14
    assert by_id[NO_REFS]["max_refs"] == 0
    assert by_id[GEMINI]["n"]["max"] == 1


def test_text_to_image_end_to_end(auth, app):
    response = auth.post("/api/generations", json={"model": GEMINI, "prompt": "a red panda"})
    assert response.status_code == 202, response.text
    created = response.json()
    assert created["status"] == "queued"

    job = wait_for_job(auth, created["id"])
    assert job["status"] == "succeeded", job["error"]
    assert job["n_received"] == 1
    assert job["cost_usd"] == 0.01
    assert job["usage"]["cost"] == 0.01

    asset = job["assets"][0]
    assert asset["width"] == 320 and asset["height"] == 320
    assert asset["content_type"] == "image/png"

    image = auth.get(asset["url"])
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content[:8] == b"\x89PNG\r\n\x1a\n"

    download = auth.get(asset["download_url"])
    assert "attachment" in download.headers["content-disposition"]


def test_provider_receives_the_exact_payload(auth, app):
    created = auth.post(
        "/api/generations",
        json={"model": GEMINI, "prompt": "a red panda", "aspect_ratio": "16:9"},
    ).json()
    wait_for_job(auth, created["id"])

    payload = app.state.provider.calls[-1]
    assert payload == {
        "model": GEMINI,
        "prompt": "a red panda",
        "n": 1,
        "session_id": created["id"],
        "aspect_ratio": "16:9",
    }


def test_reference_images_reach_the_provider_as_data_urls(auth, app, uploaded_asset):
    created = auth.post(
        "/api/generations",
        json={
            "model": GEMINI,
            "prompt": "match this style",
            "reference_asset_ids": [uploaded_asset["id"]],
        },
    )
    assert created.status_code == 202, created.text
    job = wait_for_job(auth, created.json()["id"])
    assert job["status"] == "succeeded"

    payload = app.state.provider.calls[-1]
    assert len(payload["input_references"]) == 1
    assert payload["input_references"][0].startswith("data:image/png;base64,")
    assert "reference_asset_ids" not in payload


def test_references_refused_when_model_has_no_reference_slot(auth, uploaded_asset):
    response = auth.post(
        "/api/generations",
        json={
            "model": NO_REFS,
            "prompt": "x",
            "reference_asset_ids": [uploaded_asset["id"]],
        },
    )
    assert response.status_code == 422
    errors = response.json()["detail"]["errors"]
    assert any("at most 0 reference" in e for e in errors)


def test_unknown_reference_id_is_rejected(auth):
    response = auth.post(
        "/api/generations",
        json={"model": GEMINI, "prompt": "x", "reference_asset_ids": ["deadbeef"]},
    )
    assert response.status_code == 422
    assert "unknown reference" in response.text


def test_n_above_model_max_is_rejected(auth):
    response = auth.post("/api/generations", json={"model": GEMINI, "prompt": "x", "n": 2})
    assert response.status_code == 422
    assert "n must be between" in response.text


def test_hard_cap_applies_even_when_model_allows_more(auth):
    response = auth.post("/api/generations", json={"model": MULTI, "prompt": "x", "n": 10})
    assert response.status_code == 422
    assert "capped at 4" in response.text


def test_unsupported_param_is_rejected_not_dropped(auth):
    response = auth.post("/api/generations", json={"model": NO_REFS, "prompt": "x", "seed": 7})
    assert response.status_code == 422
    assert "seed is not supported" in response.text


def test_unknown_model_is_404(auth):
    response = auth.post("/api/generations", json={"model": "nope/nope", "prompt": "x"})
    assert response.status_code == 404


def test_empty_prompt_is_rejected(auth):
    assert auth.post("/api/generations", json={"model": GEMINI, "prompt": ""}).status_code == 422


def test_svg_models_return_svg(auth):
    response = auth.post(
        "/api/generations",
        json={"model": "recraft/recraft-v4-vector", "prompt": "logo", "output_format": "svg"},
    )
    assert response.status_code == 202, response.text
    job = wait_for_job(auth, response.json()["id"])
    assert job["status"] == "succeeded"
    assert job["assets"][0]["content_type"] == "image/svg+xml"
    assert auth.get(job["assets"][0]["url"]).content.startswith(b"<svg")


def test_history_lists_and_filters(auth):
    created = auth.post("/api/generations", json={"model": GEMINI, "prompt": "one"}).json()
    wait_for_job(auth, created["id"])

    listing = auth.get("/api/generations").json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == created["id"]

    assert auth.get("/api/generations", params={"model": "other/model"}).json()["total"] == 0


def test_delete_is_soft_and_restorable(auth):
    created = auth.post("/api/generations", json={"model": GEMINI, "prompt": "bye"}).json()
    wait_for_job(auth, created["id"])

    assert auth.delete(f"/api/generations/{created['id']}").status_code == 204
    assert auth.get(f"/api/generations/{created['id']}").status_code == 404
    assert auth.get("/api/generations").json()["total"] == 0

    restored = auth.post(f"/api/generations/{created['id']}/restore")
    assert restored.status_code == 200
    assert restored.json()["id"] == created["id"]
    assert auth.get("/api/generations").json()["total"] == 1


def test_delete_twice_is_idempotent(auth):
    created = auth.post("/api/generations", json={"model": GEMINI, "prompt": "x"}).json()
    wait_for_job(auth, created["id"])
    assert auth.delete(f"/api/generations/{created['id']}").status_code == 204
    assert auth.delete(f"/api/generations/{created['id']}").status_code == 204


def test_delete_mid_flight_discards_output(settings):
    """A delete while the provider is still working must not persist images."""
    import time

    settings.fake_latency_ms = 300  # deterministic: the job is still running when we delete
    app = create_app(settings)
    with TestClient(app) as client:
        _login(client)
        created = client.post("/api/generations", json={"model": GEMINI, "prompt": "slow"}).json()
        assert client.delete(f"/api/generations/{created['id']}").status_code == 204
        assert client.get(f"/api/generations/{created['id']}").status_code == 404
        time.sleep(0.7)  # let the provider call finish and be discarded
        restored = client.post(f"/api/generations/{created['id']}/restore").json()

    assert restored["status"] == "cancelled"
    assert restored["assets"] == []
    assert restored["n_received"] is None


def test_retry_creates_a_new_job(auth):
    created = auth.post("/api/generations", json={"model": GEMINI, "prompt": "again"}).json()
    wait_for_job(auth, created["id"])

    retried = auth.post(f"/api/generations/{created['id']}/retry")
    assert retried.status_code == 202
    assert retried.json()["id"] != created["id"]
    assert retried.json()["prompt"] == "again"
    assert wait_for_job(auth, retried.json()["id"])["status"] == "succeeded"


def test_usage_reflects_recorded_spend(auth):
    created = auth.post("/api/generations", json={"model": GEMINI, "prompt": "x"}).json()
    wait_for_job(auth, created["id"])

    usage = auth.get("/api/usage", params={"period": "today"}).json()
    assert usage["jobs"] == 1
    assert usage["images"] == 1
    assert usage["spend_usd"] == 0.01


def test_provider_failure_is_reported(settings):
    settings.fake_fail = True
    app = create_app(settings)
    with TestClient(app) as client:
        _login(client)
        created = client.post("/api/generations", json={"model": GEMINI, "prompt": "boom"}).json()
        job = wait_for_job(client, created["id"])

    assert job["status"] == "failed"
    assert "Fake provider failure" in job["error"]
    assert job["error_detail"]["fake"] is True
    assert job["assets"] == []


def test_daily_budget_blocks_dispatch(settings):
    settings.daily_budget_usd = 0.001
    app = create_app(settings)
    with TestClient(app) as client:
        _login(client)
        response = client.post("/api/generations", json={"model": NO_REFS, "prompt": "expensive"})
        assert response.status_code == 429
        detail = response.json()["detail"]
        assert detail["limit_usd"] == 0.001
        assert detail["estimate_usd"] == 0.007
        assert client.get("/api/generations").json()["total"] == 0


def test_startup_marks_stale_jobs_interrupted(app, auth):
    with app.state.sessionmaker() as session:
        job = MediaJob(model=GEMINI, prompt="stale", params={"n": 1}, status="running")
        session.add(job)
        session.commit()
        job_id = job.id

    assert mark_interrupted(app.state) == 1

    body = auth.get(f"/api/generations/{job_id}").json()
    assert body["status"] == "interrupted"
    assert "restarted" in body["error"]
