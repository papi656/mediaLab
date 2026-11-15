"""Browser flows against the real stack, driven over CDP by the local Chrome.

Run with `uv run pytest -m e2e` (build the SPA first). These are deselected by
default: they need a browser and they boot a server.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from .cdp import Chrome, by_text, q
from .conftest import PASSWORD, TIMEOUT

pytestmark = pytest.mark.e2e

ARTIFACTS = Path(__file__).parent / ".artifacts"

# The catalogue's only text-only model (max_refs = 0) — see app/data/models.json.
TEXT_ONLY = "recraft/recraft-v4.1-flash"
TEXT_ONLY_NAME = "Recraft: Recraft V4.1 Flash"
# Default selection: prefers this model because it accepts references.
DEFAULT_MODEL = "google/gemini-3-pro-image"


def _png(directory: Path) -> Path:
    """A real PNG on disk, so the upload goes through the same path a user's file does."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "reference.png"
    Image.new("RGB", (64, 64), (200, 30, 90)).save(path, format="PNG")
    return path


async def _login(page: Chrome, base_url: str) -> None:
    await page.goto(base_url)
    await page.wait_for(f"{q('#password')} !== null")
    await page.fill("password", PASSWORD)
    await page.click(by_text("button", "Sign in"))
    await page.wait_for(f"{q('#prompt')} !== null", timeout=TIMEOUT)


async def _pick_model(page: Chrome, name: str) -> None:
    await page.click(q("#model"))
    await page.wait_for("document.querySelectorAll('[role=\"option\"]').length > 0")
    await page.click(by_text('[role="option"]', name, exact=True))
    await page.wait_for_gone("document.querySelectorAll('[role=\"option\"]').length > 0")


async def _generate(page: Chrome, prompt: str) -> None:
    await page.fill("prompt", prompt)
    await page.click(by_text("button", "Generate", exact=True))
    await page.wait_for(f"{q('[data-testid="job-status"]')} !== null", timeout=TIMEOUT)
    await page.wait_for(
        """(() => {
          const badge = document.querySelector('[data-testid="job-status"]');
          if (!badge) return false;
          const status = badge.textContent.trim();
          return status !== 'queued' && status !== 'running';
        })()""",
        timeout=TIMEOUT,
    )


async def test_login_then_generate_an_image(base_url: str, tmp_path: Path) -> None:
    async with Chrome() as page:
        # Unauthenticated visit lands on the login form.
        await page.goto(base_url)
        await page.wait_for(f"{q('#password')} !== null", timeout=TIMEOUT)
        assert await page.count("#prompt") == 0

        # A wrong password is an error, not a redirect.
        await page.fill("password", "wrong-password")
        await page.click(by_text("button", "Sign in"))
        await page.wait_for(
            f"{q('[role="alert"]')}?.textContent.toLowerCase().includes('invalid password')",
            timeout=TIMEOUT,
        )
        assert await page.count("#prompt") == 0

        # The right one gets in.
        await page.fill("password", PASSWORD)
        await page.click(by_text("button", "Sign in"))
        await page.wait_for(f"{q('#prompt')} !== null", timeout=TIMEOUT)

        # The catalogue's default model is the reference-capable one.
        await page.wait_for(
            f"{q('#model')}?.textContent.toLowerCase().includes('gemini')", timeout=TIMEOUT
        )

        prompt = "a red panda astronaut, studio lighting"
        await _generate(page, prompt)

        status = await page.text(q('[data-testid="job-status"]'))
        assert status == "succeeded", await page.screenshot(ARTIFACTS / "generate.png")

        # The image really loaded: decode at natural size, and the bytes came from /assets.
        width = await page.wait_for(
            f"{q(f'img[alt="{prompt}"]')}?.naturalWidth ?? 0", timeout=TIMEOUT
        )
        assert width > 0
        src = await page.evaluate(f"{q(f'img[alt="{prompt}"]')}.src")
        assert "/assets/" in src
        served = await page.evaluate(
            f"fetch({src!r}, {{credentials: 'include'}}).then(r => r.status)"
        )
        assert served == 200


async def test_reference_control_follows_model_capabilities(base_url: str) -> None:
    async with Chrome() as page:
        await _login(page, base_url)
        await page.wait_for(
            f"{q('#model')}?.textContent.toLowerCase().includes('gemini')", timeout=TIMEOUT
        )

        # Default model accepts references -> the upload control exists.
        assert await page.visible(by_text("button", "Add", exact=True))
        assert await page.has_text("reference images")

        # A text-only model loses it entirely (and says so).
        await _pick_model(page, TEXT_ONLY_NAME)
        await page.wait_for(f"{q('body')}.textContent.includes('text only')", timeout=TIMEOUT)
        assert not await page.visible(by_text("button", "Add", exact=True))
        assert await page.count('input[type="file"]') == 0

        # Back to a reference model, and the picker can take a real file.
        await _pick_model(page, "Black Forest Labs: FLUX 3 Image")
        await page.wait_for(f"{q('input[type="file"]')} !== null", timeout=TIMEOUT)
        assert await page.visible(by_text("button", "Add", exact=True))
        assert "0 / 10" in await page.text(q("body"))

        await page.upload('input[type="file"]', _png(Path(ARTIFACTS)))
        await page.wait_for(f"{q('body')}.textContent.includes('1 / 10')", timeout=TIMEOUT)
        thumb = await page.wait_for(
            f"{q('img[alt="reference 1"]')}?.naturalWidth ?? 0", timeout=TIMEOUT
        )
        assert thumb > 0


async def test_history_delete_and_undo(base_url: str) -> None:
    async with Chrome() as page:
        await _login(page, base_url)
        prompt = "undo-me please"
        await _generate(page, prompt)
        assert await page.text(q('[data-testid="job-status"]')) == "succeeded"

        await page.goto(f"{base_url}/history")
        print(
            "DEBUG api:",
            await page.evaluate(
                "fetch('/api/generations?limit=60',{credentials:'include'}).then(r=>r.json()).then(d=>d.items.map(i=>i.prompt+'|'+i.status))"
            ),
        )
        card = q(f'[aria-label="Open generation: {prompt}"]')
        await page.wait_for(f"{card} !== null", timeout=TIMEOUT)

        await page.click(card)
        await page.wait_for(f"{q('[role="dialog"]')} !== null", timeout=TIMEOUT)
        await page.click(by_text("button", "Delete"))
        await page.wait_for_gone(f"{q('[role="dialog"]')} !== null", timeout=TIMEOUT)

        # It's gone from the grid, but Undo is offered for 5 seconds.
        await page.wait_for_gone(f"{card} !== null", timeout=TIMEOUT)
        await page.click(by_text("button", "Undo"))
        await page.wait_for(f"{card} !== null", timeout=TIMEOUT)
