"""A real deployment must not boot with the values committed to the repository."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import DEV_SESSION_SECRET, Settings

GOOD_SECRET = "s" * 48
ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


def _settings(**overrides) -> Settings:
    base = {
        "provider": "openrouter",
        "openrouter_api_key": "sk-or-test",
        "session_secret": GOOD_SECRET,
        "admin_password_hash": "$2b$12$notarealhash",
        "admin_password": "",
        "daily_budget_usd": 5.0,
        "forwarded_allow_ips": "172.17.0.1",
        "run_migrations_on_start": False,
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


def test_boot_time_migrations_are_refused_in_a_real_deployment():
    """D7: the pre-deploy step migrates; boot-time migration re-adds the outage.

    Dev and tests keep the default (nothing else migrates for them), so this is a
    production-shape check like the wildcard-XFF and open-ended-budget ones.
    """
    with pytest.raises(ValidationError) as caught:
        _settings(run_migrations_on_start=True)
    assert "RUN_MIGRATIONS_ON_START" in str(caught.value)

    assert Settings(_env_file=None, provider="fake").run_migrations_on_start is True


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


def test_openrouter_without_a_budget_is_refused():
    """An open-ended OpenRouter bill is the failure this backstop exists for."""
    with pytest.raises(ValidationError) as caught:
        _settings(daily_budget_usd=None)
    assert "DAILY_BUDGET_USD" in str(caught.value)


def test_the_fake_provider_still_needs_no_budget():
    assert Settings(_env_file=None, provider="fake", daily_budget_usd=None).provider == "fake"


def test_every_problem_is_reported_at_once():
    """One restart should not reveal a second misconfiguration."""
    with pytest.raises(ValidationError) as caught:
        _settings(
            session_secret="",
            openrouter_api_key="",
            admin_password_hash="",
            daily_budget_usd=None,
        )
    message = str(caught.value)
    assert "SESSION_SECRET" in message
    assert "ADMIN_PASSWORD_HASH" in message
    assert "OPENROUTER_API_KEY" in message
    assert "DAILY_BUDGET_USD" in message


def test_wildcard_proxy_trust_is_refused():
    """'*' is how X-Forwarded-For becomes a choose-your-own-client-IP header."""
    with pytest.raises(ValidationError) as caught:
        _settings(forwarded_allow_ips="*")
    assert "never" in str(caught.value)


def test_wildcard_hidden_in_a_list_is_refused():
    with pytest.raises(ValidationError):
        _settings(forwarded_allow_ips="172.17.0.1, *")


def test_empty_proxy_trust_is_refused():
    with pytest.raises(ValidationError) as caught:
        _settings(forwarded_allow_ips=" , ")
    assert "FORWARDED_ALLOW_IPS" in str(caught.value)


def test_loopback_trust_is_detected():
    assert _settings(forwarded_allow_ips="127.0.0.1,::1").trusts_only_loopback
    assert not _settings(forwarded_allow_ips="172.17.0.1").trusts_only_loopback


def test_secure_cookies_follow_the_provider_unless_overridden():
    assert _settings().resolved_cookie_secure is True
    assert Settings(_env_file=None, provider="fake").resolved_cookie_secure is False
    assert Settings(_env_file=None, provider="fake", cookie_secure=True).resolved_cookie_secure
    assert not Settings(_env_file=None, provider="fake", cookie_secure=False).resolved_cookie_secure


def test_docs_are_off_unless_asked_for():
    assert _settings().enable_docs is False
    assert _settings(enable_docs=True).enable_docs is True


def test_a_blank_optional_value_means_unset():
    """A blank field in a deploy UI is an absent field, not a parse error.

    Reproduced in the built image before this test existed: `COOKIE_SECURE=` (the
    empty value in `.env.example`) made pydantic raise a bool-parsing error, so the
    container exited before uvicorn logged anything.
    """
    settings = Settings(
        _env_file=None,
        provider="fake",
        cookie_secure="",
        daily_budget_usd="",
        static_dir="",
    )
    assert settings.cookie_secure is None
    assert settings.daily_budget_usd is None
    assert settings.static_dir is None
    # ...which means the provider-derived default applies, not a hard-coded False.
    assert settings.resolved_cookie_secure is False


def test_the_documented_env_template_boots(monkeypatch):
    """`cp backend/.env.example backend/.env` is step one of the README."""
    for name in ("PROVIDER", "COOKIE_SECURE", "STATIC_DIR", "DAILY_BUDGET_USD", "DATA_DIR"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=ENV_EXAMPLE)

    assert settings.provider == "fake"
    assert settings.cookie_secure is None
    assert settings.static_dir is None
    assert settings.daily_budget_usd is None
    assert settings.forwarded_allow_ips == "127.0.0.1,::1"
