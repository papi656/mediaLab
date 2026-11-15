from __future__ import annotations

import base64
from typing import Any

import httpx

from ..storage import data_url
from .base import ImageProvider, ProviderError, ProviderImage, ProviderRequest, ProviderResult

# Fields we translate 1:1. Anything not listed never leaves our schema.
PASSTHROUGH_FIELDS = (
    "aspect_ratio",
    "background",
    "output_compression",
    "output_format",
    "quality",
    "resolution",
    "seed",
)


def build_request_payload(req: ProviderRequest) -> dict[str, Any]:
    """Pure translation from our schema to OpenRouter's. Tests assert this exactly."""
    payload: dict[str, Any] = {
        "model": req.model,
        "prompt": req.prompt,
        "n": int(req.params.get("n", 1)),
        "session_id": req.session_id,
    }
    for field in PASSTHROUGH_FIELDS:
        value = req.params.get(field)
        if value is not None:
            payload[field] = value

    if req.references:
        # Base64 data URLs, not http URLs: the VPS is not necessarily reachable
        # from OpenRouter, and this keeps references self-contained.
        payload["input_references"] = [data_url(data, ctype) for data, ctype in req.references]

    options = {k: v for k, v in (req.params.get("passthrough") or {}).items() if v}
    if options:
        payload["provider"] = {"options": options}
    return payload


class OpenRouterProvider(ImageProvider):
    name = "openrouter"

    def __init__(self, api_key: str, base_url: str, timeout: float = 120.0) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def generate(self, req: ProviderRequest) -> ProviderResult:
        if not self._api_key:
            raise ProviderError(
                "OPENROUTER_API_KEY is not set on the server",
                {"hint": "set it in the environment or switch PROVIDER=fake"},
            )

        payload = build_request_payload(req)
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    f"{self._base_url}/images",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "HTTP-Referer": "https://medialab.local",
                        "X-Title": "mediaLab",
                    },
                )
        except httpx.HTTPError as exc:
            raise ProviderError("could not reach OpenRouter", {"error": str(exc)}) from exc

        if resp.status_code >= 400:
            try:
                body = resp.json()
            except ValueError:
                body = {"text": resp.text[:2000]}
            raise ProviderError(
                f"OpenRouter returned {resp.status_code}",
                {"status": resp.status_code, "body": body},
            )

        try:
            body = resp.json()
        except ValueError as exc:
            raise ProviderError("OpenRouter returned a non-JSON response") from exc

        entries = body.get("data") or []
        if not entries:
            raise ProviderError("OpenRouter returned no images", {"body": body})

        images = [self._fetch_entry(e) for e in entries]
        usage = body.get("usage") or None
        cost = None
        if isinstance(usage, dict):
            raw_cost = usage.get("cost")
            cost = float(raw_cost) if isinstance(raw_cost, int | float) else None
        return ProviderResult(images=images, request_payload=payload, usage=usage, cost_usd=cost)

    def _fetch_entry(self, entry: dict[str, Any]) -> ProviderImage:
        media_type = entry.get("media_type") or "image/png"
        b64 = entry.get("b64_json")
        if b64:
            return ProviderImage(base64.b64decode(b64), media_type)

        url = entry.get("url")
        if not url:
            raise ProviderError("image entry had neither b64_json nor url")
        # Provider URLs expire: download now, never store the URL as truth.
        try:
            with httpx.Client(timeout=self._timeout) as http:
                got = http.get(url)
                got.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError("could not download generated image", {"error": str(exc)}) from exc
        return ProviderImage(got.content, got.headers.get("content-type", media_type).split(";")[0])
