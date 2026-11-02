from __future__ import annotations

import hmac
import time
from datetime import timedelta

import bcrypt
import jwt
from fastapi import HTTPException, Request, status

from .config import Settings
from .db import utcnow

COOKIE_NAME = "ml_session"
ALGORITHM = "HS256"
MAX_PASSWORD_BYTES = 72  # bcrypt truncates beyond this


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:MAX_PASSWORD_BYTES], bcrypt.gensalt()).decode()


def verify_password(password: str, *, hashed: str, plaintext: str) -> bool:
    raw = password.encode()[:MAX_PASSWORD_BYTES]
    if hashed:
        try:
            return bcrypt.checkpw(raw, hashed.encode())
        except ValueError:
            return False
    if plaintext:
        return hmac.compare_digest(password, plaintext)
    return False


def create_session_token(settings: Settings) -> str:
    now = utcnow()
    payload = {
        "sub": "operator",
        "iat": now,
        "exp": now + timedelta(days=settings.session_days),
    }
    return jwt.encode(payload, settings.session_secret, algorithm=ALGORITHM)


def decode_session_token(token: str, settings: Settings) -> dict:
    return jwt.decode(token, settings.session_secret, algorithms=[ALGORITHM])


def require_auth(request: Request) -> None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
    try:
        decode_session_token(token, request.app.state.settings)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session expired") from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid session") from exc


class LoginLimiter:
    """In-memory, per-IP. A restart clears it; that is acceptable for one operator."""

    def __init__(self, max_attempts: int = 5, window_seconds: float = 900.0) -> None:
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._failures: dict[str, list[float]] = {}

    def _prune(self, key: str, now: float) -> list[float]:
        recent = [t for t in self._failures.get(key, []) if now - t < self.window]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def blocked(self, key: str) -> bool:
        now = time.monotonic()
        return len(self._prune(key, now)) >= self.max_attempts

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        self._prune(key, now)
        self._failures.setdefault(key, []).append(now)

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)

    def clear(self) -> None:
        self._failures.clear()
