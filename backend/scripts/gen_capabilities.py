#!/usr/bin/env python
"""Regenerate app/data/models.json from the live OpenRouter images API.

This is the only thing in the project that needs network access at build time.
Run it when you want new models, then review the diff and commit it:

    uv run python -m scripts.gen_capabilities
"""

from __future__ import annotations

import concurrent.futures
import json
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MODELS_URL = "https://openrouter.ai/api/v1/images/models"
OUT = Path(__file__).resolve().parent.parent / "app" / "data" / "models.json"


def get(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "medialab-capabilities/1.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())


def _values(sp: dict[str, Any], key: str) -> list[str]:
    node = sp.get(key) or {}
    if node.get("type") != "enum":
        return []
    return [str(v) for v in node.get("values") or []]


def _max(sp: dict[str, Any], key: str, default: int) -> int:
    node = sp.get(key) or {}
    if node.get("type") != "range":
        return default
    return int(node.get("max", default))


def normalize(model: dict[str, Any], endpoints: list[dict[str, Any]]) -> dict[str, Any]:
    sp = model.get("supported_parameters") or {}

    # Per-endpoint capabilities are the truth; the model-level union can advertise
    # a slot no single endpoint actually accepts. Intersect where it matters.
    passthrough: set[str] = set()
    pricing: list[dict[str, Any]] = []
    provider_tags: list[str] = []
    for ep in endpoints:
        passthrough |= set(ep.get("allowed_passthrough_parameters") or [])
        tag = ep.get("provider_tag") or ep.get("provider_slug")
        if tag:
            provider_tags.append(tag)
        for p in ep.get("pricing") or []:
            entry = {
                "billable": p.get("billable"),
                "unit": p.get("unit"),
                "cost_usd": p.get("cost_usd"),
            }
            has_all = entry["billable"] and entry["unit"] and entry["cost_usd"] is not None
            if has_all and entry not in pricing:
                pricing.append(entry)

    n_range = sp.get("n") or {}
    return {
        "id": model["id"],
        "name": model.get("name") or model["id"],
        "description": (model.get("description") or "").strip(),
        "input_modalities": (model.get("architecture") or {}).get("input_modalities") or [],
        "max_refs": _max(sp, "input_references", 0),
        "n": {
            "min": int(n_range.get("min", 1)),
            "max": int(n_range.get("max", 1)),
        },
        "resolutions": _values(sp, "resolution"),
        "aspect_ratios": _values(sp, "aspect_ratio"),
        "formats": _values(sp, "output_format"),
        "backgrounds": _values(sp, "background"),
        "qualities": _values(sp, "quality"),
        "supports_compression": (sp.get("output_compression") or {}).get("type") == "range",
        "supports_seed": (sp.get("seed") or {}).get("type") == "boolean",
        "supports_size_flag": (sp.get("size") or {}).get("type") == "boolean",
        "passthrough_keys": sorted(passthrough),
        "provider_tags": sorted(set(provider_tags)),
        "endpoint_count": len(endpoints),
        "pricing": pricing,
    }


def main() -> None:
    models = get(MODELS_URL)["data"]

    def fetch(model_id: str):
        try:
            return model_id, get(f"{MODELS_URL}/{model_id}/endpoints").get("endpoints") or []
        except Exception:  # noqa: BLE001 - a dead model must not abort the whole run
            return model_id, []

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        endpoints = dict(ex.map(fetch, [m["id"] for m in models]))

    live = [m for m in models if endpoints.get(m["id"])]
    payload = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": MODELS_URL,
        "models": [normalize(m, endpoints[m["id"]]) for m in sorted(live, key=lambda x: x["id"])],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with_refs = sum(1 for m in payload["models"] if m["max_refs"] > 0)
    print(
        f"wrote {OUT.relative_to(OUT.parents[2])}: "
        f"{len(payload['models'])} live models "
        f"({len(models) - len(live)} skipped without endpoints), "
        f"{with_refs} accept reference images"
    )


if __name__ == "__main__":
    main()
