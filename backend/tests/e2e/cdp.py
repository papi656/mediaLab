"""Drive the real Chrome over the DevTools Protocol.

No Playwright, no downloaded browsers: this launches the Chrome already installed
on the machine (headless by default) with a throwaway profile and talks raw CDP to
it over a websocket. Clicks and typing go through `Input.*`, so the events are
trusted and React handles them exactly as it would for a user.

The tradeoff versus Playwright is explicit: there is no auto-waiting, no tracing
and no expect-polling framework. `wait_for` polls a JS predicate, so every
synchronisation point in a test is visible in the test.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
from websockets.asyncio.client import connect

CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)


class CDPError(RuntimeError):
    """A CDP command failed, or the page raised while evaluating."""


def find_chrome() -> str:
    for candidate in (*CHROME_CANDIDATES, shutil.which("google-chrome"), shutil.which("chromium")):
        if candidate and Path(candidate).exists():
            return str(candidate)
    raise RuntimeError("no Chrome/Chromium/Edge found; install one or set CHROME_BIN")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _js(value: str) -> str:
    """A Python string as a JS literal."""
    return json.dumps(value)


class Chrome:
    """A headless Chrome with one attached page, usable as `async with`."""

    def __init__(
        self,
        binary: str | None = None,
        *,
        headless: bool = True,
        width: int = 1440,
        height: int = 900,
        port: int | None = None,
        cdp_url: str | None = None,
    ) -> None:
        # `cdp_url` attaches to an already-running Chrome instead of launching one,
        # which is how you debug a failing flow by hand.
        self._binary = binary or (None if cdp_url else find_chrome())
        self._headless = headless
        self._width = width
        self._height = height
        self._port = port or _free_port()
        self._cdp_url = cdp_url
        self._proc: subprocess.Popen[bytes] | None = None
        self._profile: Path | None = None
        self._ws = None
        self._reader: asyncio.Task[None] | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._session: str | None = None
        self.console: list[str] = []

    # ---------------------------------------------------------------- lifecycle
    async def __aenter__(self) -> Chrome:
        if not self._cdp_url:
            await self._launch()
        await self._connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader
        if self._proc is not None:
            self._proc.terminate()
            with contextlib.suppress(Exception):
                self._proc.wait(timeout=10)
        if self._profile is not None:
            shutil.rmtree(self._profile, ignore_errors=True)

    async def _launch(self) -> None:
        self._profile = Path(tempfile.mkdtemp(prefix="medialab-e2e-chrome-"))
        args = [
            str(self._binary),
            f"--remote-debugging-port={self._port}",
            f"--user-data-dir={self._profile}",
            f"--window-size={self._width},{self._height}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-default-apps",
            "--disable-extensions",
            "--disable-sync",
            "--metrics-recording-only",
            "--mute-audio",
            "--hide-scrollbars",
            "about:blank",
        ]
        if self._headless:
            args.insert(1, "--headless=new")
        self._proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
        )
        await self._wait_for_endpoint()

    async def _wait_for_endpoint(self, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        url = f"http://127.0.0.1:{self._port}/json/version"
        async with httpx.AsyncClient(timeout=2.0) as client:
            while time.monotonic() < deadline:
                if self._proc is not None and self._proc.poll() is not None:
                    raise CDPError(f"Chrome exited immediately (code {self._proc.returncode})")
                try:
                    response = await client.get(url)
                    if response.status_code == 200:
                        self._cdp_url = response.json()["webSocketDebuggerUrl"]
                        return
                except (httpx.HTTPError, KeyError, ValueError):
                    pass
                await asyncio.sleep(0.1)
        raise CDPError("Chrome did not expose a DevTools endpoint in time")

    async def _connect(self) -> None:
        assert self._cdp_url is not None
        self._ws = await connect(self._cdp_url, max_size=64 * 1024 * 1024, open_timeout=15)
        self._reader = asyncio.create_task(self._read_loop())

        target = await self.call("Target.createTarget", {"url": "about:blank"})
        attached = await self.call(
            "Target.attachToTarget", {"targetId": target["targetId"], "flatten": True}
        )
        self._session = attached["sessionId"]

        for domain in ("Page", "Runtime", "DOM", "Network"):
            await self.call(f"{domain}.enable")
        await self.call(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": self._width,
                "height": self._height,
                "deviceScaleFactor": 1,
                "mobile": False,
            },
        )
        await self.call("Page.setLifecycleEventsEnabled", {"enabled": True})

    # ------------------------------------------------------------------ protocol
    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                message = json.loads(raw)
                if "id" in message:
                    future = self._pending.pop(message["id"], None)
                    if future is not None and not future.done():
                        future.set_result(message)
                elif message.get("method") == "Runtime.consoleAPICalled":
                    parts = [
                        str(arg.get("value", arg.get("description", "")))
                        for arg in message["params"].get("args", [])
                    ]
                    self.console.append(" ".join(parts))
                elif message.get("method") == "Runtime.exceptionThrown":
                    detail = message["params"]["exceptionDetails"]
                    self.console.append(f"EXCEPTION {detail.get('text')}")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a dead socket must not hang pending calls
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(CDPError("websocket closed"))
            self._pending.clear()

    async def call(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = 20.0
    ) -> dict[str, Any]:
        if self._ws is None:
            raise CDPError("not connected")
        self._next_id += 1
        message_id = self._next_id
        payload: dict[str, Any] = {"id": message_id, "method": method, "params": params or {}}
        if self._session is not None and not method.startswith("Target."):
            payload["sessionId"] = self._session

        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        await self._ws.send(json.dumps(payload))
        try:
            response = await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(message_id, None)
        if "error" in response:
            raise CDPError(f"{method}: {response['error'].get('message')}")
        return response.get("result", {})

    # --------------------------------------------------------------------- page
    async def goto(self, url: str, timeout: float = 20.0) -> None:
        await self.call("Page.navigate", {"url": url})
        await self.wait_for("document.readyState === 'complete'", timeout=timeout)

    async def evaluate(self, expression: str, timeout: float = 20.0) -> Any:
        result = await self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
                "userGesture": True,
            },
            timeout=timeout,
        )
        details = result.get("exceptionDetails")
        if details:
            raise CDPError(f"JS error: {details.get('text')} {details.get('exception', {})}")
        return result.get("result", {}).get("value")

    async def wait_for(self, expression: str, timeout: float = 15.0, interval: float = 0.05) -> Any:
        """Poll a JS predicate until it is truthy. Returns the final value."""
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                value = await self.evaluate(expression)
                if value:
                    return value
                last_error = None
            except CDPError as error:  # page mid-navigation
                last_error = error
            await asyncio.sleep(interval)
        suffix = f" (last error: {last_error})" if last_error else ""
        raise TimeoutError(
            f"timed out waiting for {expression}{suffix}\npage text:\n{await self.inner_text()}"
        )

    async def inner_text(self, limit: int = 800) -> str:
        """Body text, for failure messages."""
        try:
            text = await self.evaluate(f"document.body?.innerText?.slice(0, {limit}) ?? ''")
        except CDPError:
            return "<unavailable>"
        return str(text)

    async def wait_for_gone(self, expression: str, timeout: float = 15.0) -> None:
        await self.wait_for(f"!({expression})", timeout=timeout)

    # -------------------------------------------------------------------- input
    async def _centre(self, element_js: str) -> tuple[float, float]:
        box = await self.wait_for(
            f"""(() => {{
              const el = ({element_js});
              if (!el) return null;
              el.scrollIntoView({{block: 'center', inline: 'center'}});
              const rect = el.getBoundingClientRect();
              if (rect.width === 0 || rect.height === 0) return null;
              return [rect.x + rect.width / 2, rect.y + rect.height / 2];
            }})()"""
        )
        return float(box[0]), float(box[1])

    async def click(self, element_js: str, *, delay: float = 0.02) -> None:
        """A real (trusted) left click in the middle of the element.

        `element_js` is any JS expression evaluating to an element, e.g.
        `document.querySelector('#model')` or a text lookup from `by_text`.
        """
        x, y = await self._centre(element_js)
        base = {"x": x, "y": y, "button": "left", "buttons": 0, "clickCount": 1}
        await self.call(
            "Input.dispatchMouseEvent", {"type": "mouseMoved", **base, "button": "none"}
        )
        await self.call("Input.dispatchMouseEvent", {"type": "mousePressed", **base, "buttons": 1})
        await asyncio.sleep(delay)
        await self.call("Input.dispatchMouseEvent", {"type": "mouseReleased", **base})

    async def fill(self, element_id: str, text: str) -> None:
        """Set a React-controlled input/textarea the way a user would.

        React ignores a plain `el.value = ...`, so this uses the native value
        setter and dispatches `input` — the same technique Playwright uses.
        """
        ok = await self.evaluate(
            f"""(() => {{
              const el = document.getElementById({_js(element_id)});
              if (!el) return false;
              const proto = el.tagName === 'TEXTAREA'
                ? HTMLTextAreaElement.prototype
                : HTMLInputElement.prototype;
              Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, {_js(text)});
              el.dispatchEvent(new Event('input', {{ bubbles: true }}));
              return true;
            }})()"""
        )
        if not ok:
            raise CDPError(f"#{element_id} not found")

    async def type(self, element_id: str, text: str) -> None:
        """Focus the element and insert text through `Input.insertText`."""
        await self.click(f"document.getElementById({_js(element_id)})")
        await self.call("Input.insertText", {"text": text})

    async def press(self, key: str, *, modifiers: int = 0) -> None:
        for kind in ("keyDown", "keyUp"):
            await self.call(
                "Input.dispatchKeyEvent",
                {"type": kind, "key": key, "code": key, "modifiers": modifiers},
            )

    async def upload(self, selector: str, path: Path) -> None:
        """Point a hidden `<input type=file>` at a real file on disk."""
        document = await self.call("DOM.getDocument", {"depth": -1})
        node = await self.call(
            "DOM.querySelector", {"nodeId": document["root"]["nodeId"], "selector": selector}
        )
        if not node.get("nodeId"):
            raise CDPError(f"no file input matching {selector}")
        await self.call("DOM.setFileInputFiles", {"nodeId": node["nodeId"], "files": [str(path)]})

    # ------------------------------------------------------------------ queries
    async def text(self, element_js: str) -> str:
        value = await self.evaluate(f"(({element_js})?.textContent ?? '').trim()")
        return str(value or "")

    async def has_text(self, substring: str) -> bool:
        return substring.lower() in (await self.inner_text(limit=100_000)).lower()

    async def count(self, selector: str) -> int:
        return int(await self.evaluate(f"document.querySelectorAll({_js(selector)}).length") or 0)

    async def visible(self, element_js: str) -> bool:
        return bool(
            await self.evaluate(
                f"""(() => {{
                  const el = ({element_js});
                  if (!el) return false;
                  const rect = el.getBoundingClientRect();
                  return rect.width > 0 && rect.height > 0;
                }})()"""
            )
        )

    async def screenshot(self, path: Path) -> Path:
        result = await self.call("Page.captureScreenshot", {"format": "png"})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(result["data"]))
        return path


# --------------------------------------------------------------------- selectors
def by_text(tag: str, text: str, *, exact: bool = False) -> str:
    """JS expression for the first element whose text matches.

    `by_text('button', 'Sign in')` -> an expression `click()` accepts directly.
    """
    comparison = "===" if exact else ".includes("
    if exact:
        return (
            f"Array.from(document.querySelectorAll({_js(tag)}))"
            f".find(e => e.textContent.trim() === {_js(text)})"
        )
    return (
        f"Array.from(document.querySelectorAll({_js(tag)}))"
        f".find(e => e.textContent{comparison}{_js(text)}))"
    )


def q(selector: str) -> str:
    return f"document.querySelector({_js(selector)})"
