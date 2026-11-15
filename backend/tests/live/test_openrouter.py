"""Opt-in: actually calls OpenRouter. Deselected by default (`-m 'not live'`).

OPENROUTER_API_KEY=... uv run pytest -m live -s
"""

from __future__ import annotations

import os

import pytest

from app.capabilities import catalogue_for
from app.config import Settings
from app.providers.base import ProviderRequest
from app.providers.openrouter import OpenRouterProvider

pytestmark = pytest.mark.live

CHEAPEST_DETERMINISTIC = "recraft/recraft-v4.1-flash"


@pytest.fixture(scope="module")
def provider() -> OpenRouterProvider:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        pytest.skip("OPENROUTER_API_KEY not set")
    return OpenRouterProvider(api_key=key, base_url="https://openrouter.ai/api/v1", timeout=180)


def test_live_generation_still_matches_our_adapter(provider):
    """One cheap call, to prove the mapping has not drifted from reality."""
    settings = Settings(_env_file=None)
    caps = catalogue_for(settings.catalogue_path).get(CHEAPEST_DETERMINISTIC)
    assert caps is not None

    result = provider.generate(
        ProviderRequest(
            model=CHEAPEST_DETERMINISTIC,
            prompt="a single blue circle on a white background",
            params={"n": 1, "aspect_ratio": caps.aspect_ratios[0]},
            session_id="live-test",
        )
    )

    assert result.images, "provider returned no images"
    assert result.request_payload["model"] == CHEAPEST_DETERMINISTIC
    assert result.request_payload["session_id"] == "live-test"
    image = result.images[0]
    assert image.data
    assert image.content_type.startswith("image/")
    print(
        f"\nlive ok: {len(result.images)} image(s), {len(image.data)} bytes, cost={result.cost_usd}"
    )
