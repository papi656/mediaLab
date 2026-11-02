from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import text

from ..auth import COOKIE_NAME, LoginLimiter, create_session_token, verify_password
from ..schemas import HealthOut, LoginIn, MeOut

router = APIRouter(tags=["auth"])


def _is_secure(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto == "https"


@router.post("/api/auth/login", status_code=status.HTTP_204_NO_CONTENT)
def login(body: LoginIn, request: Request, response: Response) -> Response:
    settings = request.app.state.settings
    limiter: LoginLimiter = request.app.state.login_limiter
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
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid password")

    limiter.reset(key)
    token = create_session_token(settings)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.session_days * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=_is_secure(request),
        path="/",
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response) -> Response:
    response.delete_cookie(COOKIE_NAME, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/api/auth/me", response_model=MeOut)
def me(request: Request) -> MeOut:
    # Reachable without a session on purpose: the SPA asks "am I logged in?" on load.
    import jwt

    from ..auth import decode_session_token

    token = request.cookies.get(COOKIE_NAME)
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
