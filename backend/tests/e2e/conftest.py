"""Fixtures for the CDP-driven browser tests.

Boots the real stack the way production runs it: the built SPA mounted by FastAPI,
the fake provider, a throwaway data directory. Nothing here touches the network
beyond 127.0.0.1.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
PASSWORD = "medialab"
TIMEOUT = 45.0


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    if os.environ.get("E2E_BASE_URL"):
        yield os.environ["E2E_BASE_URL"].rstrip("/")
        return

    if not (FRONTEND_DIST / "index.html").exists():
        pytest.skip("frontend not built: run `npm --prefix frontend run build` first")

    port = _free_port()
    env = {
        **os.environ,
        "DATA_DIR": str(tmp_path_factory.mktemp("e2e-data")),
        "PROVIDER": "fake",
        "ADMIN_PASSWORD": PASSWORD,
        "ADMIN_PASSWORD_HASH": "",
        "SESSION_SECRET": "e2e-secret-at-least-32-bytes-long-here",
        "STATIC_DIR": str(FRONTEND_DIST),
        "FAKE_LATENCY_MS": "50",
        "GC_INTERVAL_HOURS": "1000",
        "DAILY_BUDGET_USD": "",
    }
    proc = subprocess.Popen(
        [
            "uv",
            "run",
            "uvicorn",
            "app.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workers",
            "1",
        ],
        cwd=REPO_ROOT / "backend",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 60
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                output = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
                raise RuntimeError(f"server exited early:\n{output}")
            try:
                if httpx.get(f"{url}/health", timeout=2.0).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("server did not become healthy")
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
