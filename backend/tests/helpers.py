from __future__ import annotations

import time
from typing import Any

PASSWORD = "test-password"
TERMINAL = {"succeeded", "failed", "interrupted", "cancelled"}


def wait_for_job(client, job_id: str, timeout: float = 10.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/generations/{job_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] in TERMINAL:
            return last
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish; last={last}")


PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)
