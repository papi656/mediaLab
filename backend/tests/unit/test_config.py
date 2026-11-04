"""A real deployment must not boot with the values committed to the repository."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import DEV_SESSION_SECRET, Settings

GOOD_SECRET = "s" * 48


def _settings(**overrides) -> Settings:
    base = {
        "provider": "openrouter",
        "openrouter_api_key": "sk-or-test",
        "session_secret": GOOD_SECRET,
        "admin_password_hash": "$2b$12$notarealhash",
        "admin_password": "",
    }
    return Settings(_env_file=None, **{**base, **overrides})


def test_a_sane_live_config_boots():
    assert _settings().provider == "openrouter"


def test_fake_provider_keeps_the_permissive_defaults():
    """Tests and local dev must not need real secrets."""
    settings = Settings(_env_file=None, provider="fake", session_secret=DEV_SESSION_SECRET)
    assert settings.session_secret == DEV_SESSION_SECRET


def test_the_public_default_secret_is_refused():
    with pytest.raises(ValidationError) as caught:
        _settings(session_secret=DEV_SESSION_SECRET)
    assert "SESSION_SECRET is still the public default" in str(caught.value)


def test_a_short_secret_is_refused():
    with pytest.raises(ValidationError) as caught:
        _settings(session_secret="short")
    assert "at least 32" in str(caught.value)


def test_no_password_at_all_is_refused():
    with pytest.raises(ValidationError) as caught:
        _settings(admin_password_hash="", admin_password="")
    assert "ADMIN_PASSWORD_HASH" in str(caught.value)


def test_openrouter_without_a_key_is_refused():
    with pytest.raises(ValidationError) as caught:
        _settings(openrouter_api_key="")
    assert "OPENROUTER_API_KEY" in str(caught.value)


def test_every_problem_is_reported_at_once():
    """One restart should not reveal a second misconfiguration."""
    with pytest.raises(ValidationError) as caught:
        _settings(session_secret="", openrouter_api_key="", admin_password_hash="")
    message = str(caught.value)
    assert "SESSION_SECRET" in message
    assert "ADMIN_PASSWORD_HASH" in message
    assert "OPENROUTER_API_KEY" in message
