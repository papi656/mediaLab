from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import text

from ..auth import (
    LoginLimiter,
    cookie_name,
    create_session_token,
    read_session_token,
    verify_password,
)
from ..schemas import HealthOut, LoginIn, MeOut

log = logging.getLogger("medialab.auth")

router = APIRouter(tags=["auth"])


@router.post("/api/auth/login", status_code=status.HTTP_204_NO_CONTENT)
def login(body: LoginIn, request: Request, response: Response) -> Response:
    settings = request.app.state.settings
    limiter: LoginLimiter = request.app.state.login_limiter
    # The proxy (cloudflared's NAT gateway, then whatever uvicorn rewrote from a
    # trusted X-Forwarded-For) is the only source of identity we have. Which is
    # why FORWARDED_ALLOW_IPS may not be "*" — see app/config.py.
    key = request.client.host if request.client else "unknown"

    if limiter.blocked(key):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "too many failed attempts; try again later"
        )

    ok = verify_password(
        body.password,
        hashed=settings.admin_password_hash,
        plaintext=settings.admin_password,
    )
    if not ok:
        limiter.record_failure(key)
        # Two addresses in one line, because they disagree in exactly one
        # situation worth noticing: the trust list does not match the proxy, so
        # every visitor is being counted as the same client.
        log.warning(
            "login failed: peer=%s cf-connecting-ip=%s",
            key,
            request.headers.get("cf-connecting-ip", "-"),
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid password")

    limiter.reset(key)
    secure = settings.resolved_cookie_secure
    response.set_cookie(
        cookie_name(secure=secure),
        create_session_token(settings),
        max_age=settings.session_days * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response) -> Response:
    # Both shapes: `__Host-` cookies cannot be deleted by a Set-Cookie that lacks
    # Secure, and a name-only delete would leave a stale session behind.
    for secure in (True, False):
        response.delete_cookie(cookie_name(secure=secure), path="/", secure=secure)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/api/auth/me", response_model=MeOut)
def me(request: Request) -> MeOut:
    # Reachable without a session on purpose: the SPA asks "am I logged in?" on load.
    import jwt

    from ..auth import decode_session_token

    token = read_session_token(request)
    if not token:
        return MeOut(authenticated=False)
    try:
        decode_session_token(token, request.app.state.settings)
    except jwt.PyJWTError:
        return MeOut(authenticated=False)
    return MeOut(authenticated=True)


@router.get("/health", response_model=HealthOut)
def health(request: Request) -> HealthOut:
    state = request.app.state
    database_ok = True
    try:
        with state.sessionmaker() as session:
            session.execute(text("select 1"))
    except Exception:  # noqa: BLE001
        database_ok = False
    return HealthOut(
        status="ok" if database_ok else "degraded",
        database=database_ok,
        provider=state.provider.name,
        models=len(state.catalogue.models),
    )
