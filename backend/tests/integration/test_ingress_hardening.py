"""The two settings that are invisible until they are wrong: cookie shape and docs.

The production deployment is HTTPS at the Cloudflare edge and plain HTTP inside:
the tunnel connector dials the loopback-published port, so the app never sees a
TLS connection. That is exactly why `Secure` comes from configuration rather than
from `X-Forwarded-Proto`, which is a header a client can send.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.auth import cookie_name, create_session_token, read_session_token
from app.config import Settings
from app.main import create_app

from ..helpers import PASSWORD

SECURE = cookie_name(secure=True)
INSECURE = cookie_name(secure=False)


def _settings(tmp_path, **overrides) -> Settings:
    base = dict(
        _env_file=None,
        provider="fake",
        fake_latency_ms=0,
        data_dir=tmp_path / "data",
        admin_password=PASSWORD,
        admin_password_hash="",
        session_secret="test-secret-not-for-production",
        gc_interval_hours=1000,
        cookie_secure=True,
    )
    return Settings(**{**base, **overrides})


@pytest.fixture
def https_client(tmp_path):
    app = create_app(_settings(tmp_path))
    with TestClient(app, base_url="https://testserver") as client:
        client.headers["X-Requested-With"] = "fetch"
        yield client


def test_secure_cookie_is_host_prefixed_and_secure(https_client):
    response = https_client.post("/api/auth/login", json={"password": PASSWORD})
    assert response.status_code == 204
    header = response.headers["set-cookie"]
    assert f"{SECURE}=" in header
    assert "Secure" in header
    assert "HttpOnly" in header
    assert "Path=/" in header
    assert "Domain=" not in header  # __Host- cookies must not carry a Domain
    assert header.startswith(f"{SECURE}="), header


def test_secure_session_authenticates(https_client):
    assert https_client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 204
    assert https_client.get("/api/auth/me").json() == {"authenticated": True}
    assert https_client.get("/api/models").status_code == 200


def test_logout_clears_both_cookie_shapes(https_client):
    https_client.post("/api/auth/login", json={"password": PASSWORD})
    response = https_client.post("/api/auth/logout")
    cleared = response.headers.get_list("set-cookie")
    assert any(SECURE in item and "Max-Age=0" in item for item in cleared)
    assert any(INSECURE in item.split("=")[0] for item in cleared)
    assert https_client.get("/api/auth/me").json() == {"authenticated": False}


def test_a_legacy_unprefixed_cookie_still_authenticates(tmp_path):
    """Flipping COOKIE_SECURE logs nobody out mid-session."""
    settings = _settings(tmp_path)
    app = create_app(settings)
    token = create_session_token(settings)
    with TestClient(app, base_url="https://testserver") as client:
        response = client.get("/api/auth/me", headers={"cookie": f"{INSECURE}={token}"})
        assert response.json() == {"authenticated": True}


def _request(cookie_header: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"cookie", cookie_header.encode())],
            "client": ("172.17.0.1", 4444),
            "server": ("testserver", 443),
        }
    )


def test_read_session_token_prefers_the_secure_name():
    assert read_session_token(_request(f"{SECURE}=good; {INSECURE}=stale")) == "good"
    assert read_session_token(_request(f"{INSECURE}=stale; {SECURE}=good")) == "good"


def test_read_session_token_accepts_only_the_legacy_name():
    assert read_session_token(_request(f"{INSECURE}=only")) == "only"
    assert read_session_token(_request("other=1")) is None


def test_docs_are_absent_by_default(client):
    assert client.get("/api/docs").status_code == 404
    assert client.get("/api/openapi.json").status_code == 404


def test_docs_can_be_turned_on_for_type_generation(tmp_path):
    app = create_app(_settings(tmp_path, enable_docs=True))
    with TestClient(app) as client:
        assert client.get("/api/docs").status_code == 200
        assert client.get("/api/openapi.json").status_code == 200
