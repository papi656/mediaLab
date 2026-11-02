from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .auth import COOKIE_NAME, LoginLimiter
from .capabilities import Catalogue, catalogue_for
from .config import Settings, get_settings
from .db import init_db, make_engine, make_sessionmaker
from .gc import gc_loop, run_gc_once
from .jobs import mark_interrupted
from .providers import ImageProvider, make_provider
from .routers import assets, auth, generations, models, usage

log = logging.getLogger("medialab")

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass
class AppState:
    settings: Settings
    catalogue: Catalogue
    provider: ImageProvider
    loop: asyncio.AbstractEventLoop | None = None
    futures: set = field(default_factory=set)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.prepare_dirs()

    engine = make_engine(settings.database_url)
    init_db(engine)
    sessionmaker = make_sessionmaker(engine)
    catalogue = catalogue_for(settings.catalogue_path)
    provider = make_provider(settings)

    state = AppState(settings=settings, catalogue=catalogue, provider=provider)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.engine = engine
        app.state.sessionmaker = sessionmaker
        app.state.settings = settings
        app.state.catalogue = catalogue
        app.state.provider = provider
        app.state.login_limiter = LoginLimiter()
        state.loop = asyncio.get_running_loop()
        app.state.loop = state.loop
        app.state.futures = state.futures

        interrupted = mark_interrupted(app.state)
        if interrupted:
            log.warning("marked %s job(s) as interrupted on startup", interrupted)

        await run_gc_once(app.state)
        loop_task = asyncio.create_task(gc_loop(app.state))
        try:
            yield
        finally:
            loop_task.cancel()
            for future in list(state.futures):
                future.cancel()

    app = FastAPI(
        title="mediaLab",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    @app.middleware("http")
    async def guard_mutations(request: Request, call_next):  # noqa: ANN001
        """CSRF defence: cookie + JSON + a custom header the browser cannot add cross-site."""
        if (
            request.method in MUTATING
            and request.url.path.startswith("/api/")
            and request.cookies.get(COOKIE_NAME)
            and request.headers.get("x-requested-with") != "fetch"
        ):
            return JSONResponse(
                {"detail": "missing X-Requested-With header"}, status_code=403
            )
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    for module in (auth, models, generations, assets, usage):
        app.include_router(module.router)

    _mount_spa(app, settings.static_dir)
    return app


def _mount_spa(app: FastAPI, static_dir: Path | None) -> None:
    if not static_dir:
        return
    dist = Path(static_dir)
    index = dist / "index.html"
    if not index.exists():
        log.warning("static dir %s has no index.html; SPA not mounted", dist)
        return

    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith(("api/", "assets/")):
            return JSONResponse({"detail": "not found"}, status_code=404)
        candidate = dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)

