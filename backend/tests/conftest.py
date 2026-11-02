from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.main import create_app

from .helpers import PASSWORD


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        provider="fake",
        fake_latency_ms=0,
        fake_fail=False,
        data_dir=tmp_path / "data",
        admin_password=PASSWORD,
        admin_password_hash="",
        session_secret="test-secret-not-for-production",
        gc_interval_hours=1000,
        soft_delete_retention_days=7,
        daily_budget_usd=None,
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth(client):
    """An authenticated client. Mutations carry the CSRF header."""
    client.headers["X-Requested-With"] = "fetch"
    response = client.post("/api/auth/login", json={"password": PASSWORD})
    assert response.status_code == 204, response.text
    return client


@pytest.fixture
def png_bytes() -> bytes:
    image = Image.new("RGB", (8, 8), (12, 200, 99))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def uploaded_asset(auth, png_bytes) -> dict:
    response = auth.post(
        "/api/uploads",
        files={"file": ("ref.png", png_bytes, "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()
