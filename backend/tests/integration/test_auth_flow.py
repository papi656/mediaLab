from __future__ import annotations

from app.auth import COOKIE_NAME

from ..helpers import PASSWORD

GEMINI = "google/gemini-3-pro-image"


def test_api_requires_auth(client):
    assert client.get("/api/models").status_code == 401
    assert client.get("/api/generations").status_code == 401
    assert client.get("/api/usage").status_code == 401


def test_health_is_public(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] is True
    assert body["provider"] == "fake"
    assert body["models"] > 0


def test_wrong_password_is_rejected(client):
    response = client.post("/api/auth/login", json={"password": "nope"})
    assert response.status_code == 401
    assert COOKIE_NAME not in client.cookies


def test_login_sets_httponly_cookie(client):
    response = client.post("/api/auth/login", json={"password": PASSWORD})
    assert response.status_code == 204
    cookie_header = response.headers["set-cookie"]
    assert "HttpOnly" in cookie_header
    assert "SameSite=lax" in cookie_header
    assert client.get("/api/auth/me").json() == {"authenticated": True}


def test_logout_clears_session(auth):
    assert auth.get("/api/auth/me").json() == {"authenticated": True}
    assert auth.post("/api/auth/logout").status_code == 204
    assert auth.get("/api/auth/me").json() == {"authenticated": False}
    assert auth.get("/api/models").status_code == 401


def test_login_is_rate_limited(client):
    for _ in range(5):
        assert client.post("/api/auth/login", json={"password": "bad"}).status_code == 401
    assert client.post("/api/auth/login", json={"password": "bad"}).status_code == 429
    # Even the right password is refused while blocked.
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 429


def test_mutations_require_csrf_header(auth):
    response = auth.post(
        "/api/generations",
        json={"model": GEMINI, "prompt": "x"},
        headers={"X-Requested-With": "curl"},
    )
    assert response.status_code == 403
    assert "X-Requested-With" in response.text


def test_reads_do_not_require_csrf_header(auth):
    assert auth.get("/api/models", headers={"X-Requested-With": ""}).status_code == 200


def test_unauthenticated_mutation_is_401_not_403(client):
    response = client.post("/api/generations", json={"model": GEMINI, "prompt": "x"})
    assert response.status_code == 401


def test_tampered_cookie_is_rejected(client):
    client.cookies.set(COOKIE_NAME, "not-a-jwt")
    assert client.get("/api/models").status_code == 401
