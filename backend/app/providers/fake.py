from __future__ import annotations

import hashlib
import io
import time
from typing import Any

from PIL import Image, ImageDraw

from .base import ImageProvider, ProviderError, ProviderImage, ProviderRequest, ProviderResult
from .openrouter import build_request_payload

SVG_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320">'
    '<rect width="320" height="320" fill="{fill}"/>'
    '<text x="160" y="168" font-family="monospace" font-size="14" fill="#fff" '
    'text-anchor="middle">{label}</text></svg>'
)


def _digest(prompt: str, index: int) -> str:
    return hashlib.sha256(f"{prompt}:{index}".encode()).hexdigest()


def _png(prompt: str, index: int) -> bytes:
    """Deterministic from the prompt, so tests can assert on bytes."""
    h = _digest(prompt, index)
    top = tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))
    bottom = tuple(int(h[i : i + 2], 16) for i in (6, 8, 10))
    img = Image.new("RGB", (320, 320))
    px = img.load()
    assert px is not None
    for y in range(320):
        t = y / 319
        row = tuple(int(top[c] * (1 - t) + bottom[c] * t) for c in range(3))
        for x in range(320):
            px[x, y] = row
    draw = ImageDraw.Draw(img)
    draw.text((10, 10), f"fake {index} {h[:8]}", fill=(255, 255, 255))
    draw.text((10, 26), prompt[:38], fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _svg(prompt: str, index: int) -> bytes:
    h = _digest(prompt, index)
    return SVG_TEMPLATE.format(fill=f"#{h[:6]}", label=f"fake {index} {h[:8]}").encode()


class FakeProvider(ImageProvider):
    """Stand-in for OpenRouter.

    Records the exact payload it *would* have sent, so tests get real mapping
    coverage while making zero network calls.
    """

    name = "fake"

    def __init__(self, latency_ms: int = 50, fail: bool = False, cost_per_image: float = 0.01):
        self._latency_ms = latency_ms
        self._fail = fail
        self._cost_per_image = cost_per_image
        self.calls: list[dict[str, Any]] = []

    def generate(self, req: ProviderRequest) -> ProviderResult:
        payload = build_request_payload(req)  # identical code path as the real adapter
        self.calls.append(payload)

        if self._latency_ms:
            time.sleep(self._latency_ms / 1000)

        # `self._fail` (FAKE_FAIL=1) fails everything; "[fail]" in the prompt lets an
        # end-to-end test exercise the failure path without a second server.
        if self._fail or "[fail]" in req.prompt:
            raise ProviderError(
                "Fake provider failure (requested)",
                {"fake": True, "model": req.model},
            )

        n = int(req.params.get("n", 1))
        want_svg = req.params.get("output_format") == "svg"
        images = [
            ProviderImage(
                data=_svg(req.prompt, i) if want_svg else _png(req.prompt, i),
                content_type="image/svg+xml" if want_svg else "image/png",
                source="fake",
            )
            for i in range(n)
        ]
        cost = round(self._cost_per_image * n, 6)
        return ProviderResult(
            images=images,
            request_payload=payload,
            usage={"prompt_tokens": max(1, len(req.prompt) // 4), "cost": cost},
            cost_usd=cost,
        )


def make_provider(settings) -> ImageProvider:  # noqa: ANN001
    if settings.provider == "openrouter":
        from .openrouter import OpenRouterProvider

        return OpenRouterProvider(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            timeout=settings.job_timeout_seconds,
        )
    return FakeProvider(
        latency_ms=settings.fake_latency_ms,
        fail=settings.fake_fail,
    )
