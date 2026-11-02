from __future__ import annotations

from datetime import timedelta

import jwt
import pytest

from app.auth import (
    ALGORITHM,
    LoginLimiter,
    create_session_token,
    decode_session_token,
    hash_password,
    verify_password,
)
from app.clock import utcnow
from app.config import Settings


def _settings() -> Settings:
    return Settings(_env_file=None, session_secret="unit-secret", session_days=7)


def test_bcrypt_roundtrip():
    hashed = hash_password("correct horse")
    assert hashed.startswith("$2")
    assert verify_password("correct horse", hashed=hashed, plaintext="")
    assert not verify_password("wrong", hashed=hashed, plaintext="")


def test_plaintext_fallback_for_dev():
    assert verify_password("dev", hashed="", plaintext="dev")
    assert not verify_password("nope", hashed="", plaintext="dev")


def test_hash_wins_over_plaintext():
    hashed = hash_password("real")
    assert verify_password("real", hashed=hashed, plaintext="different")
    assert not verify_password("different", hashed=hashed, plaintext="different")


def test_no_credentials_configured_denies_everything():
    assert not verify_password("anything", hashed="", plaintext="")


def test_bcrypt_ignores_bytes_past_72():
    long_a = "a" * 80
    long_b = "a" * 72 + "b" * 8
    hashed = hash_password(long_a)
    assert verify_password(long_b, hashed=hashed, plaintext="")


def test_token_roundtrip():
    settings = _settings()
    token = create_session_token(settings)
    assert decode_session_token(token, settings)["sub"] == "operator"


def test_expired_token_is_rejected():
    settings = _settings()
    token = jwt.encode(
        {"sub": "operator", "exp": utcnow() - timedelta(seconds=1)},
        settings.session_secret,
        algorithm=ALGORITHM,
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_session_token(token, settings)


def test_token_signed_with_other_secret_is_rejected():
    token = create_session_token(Settings(_env_file=None, session_secret="other"))
    with pytest.raises(jwt.InvalidSignatureError):
        decode_session_token(token, _settings())


def test_limiter_blocks_after_max_attempts():
    limiter = LoginLimiter(max_attempts=3, window_seconds=60)
    assert not limiter.blocked("ip")
    for _ in range(3):
        limiter.record_failure("ip")
    assert limiter.blocked("ip")
    assert not limiter.blocked("other-ip")


def test_limiter_reset_clears_failures():
    limiter = LoginLimiter(max_attempts=1, window_seconds=60)
    limiter.record_failure("ip")
    assert limiter.blocked("ip")
    limiter.reset("ip")
    assert not limiter.blocked("ip")


def test_limiter_window_expires():
    limiter = LoginLimiter(max_attempts=1, window_seconds=0.01)
    limiter.record_failure("ip")
    import time

    time.sleep(0.02)
    assert not limiter.blocked("ip")
