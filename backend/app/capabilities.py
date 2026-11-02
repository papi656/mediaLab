from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator


class PricingEntry(BaseModel):
    billable: str
    unit: str
    cost_usd: float


class Capabilities(BaseModel):
    id: str
    name: str
    description: str = ""
    input_modalities: list[str] = Field(default_factory=list)
    max_refs: int = 0
    n: dict[str, int] = Field(default_factory=lambda: {"min": 1, "max": 1})
    resolutions: list[str] = Field(default_factory=list)
    aspect_ratios: list[str] = Field(default_factory=list)
    formats: list[str] = Field(default_factory=list)
    backgrounds: list[str] = Field(default_factory=list)
    qualities: list[str] = Field(default_factory=list)
    supports_compression: bool = False
    supports_seed: bool = False
    supports_size_flag: bool = False
    passthrough_keys: list[str] = Field(default_factory=list)
    provider_tags: list[str] = Field(default_factory=list)
    endpoint_count: int = 0
    pricing: list[PricingEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sane(self) -> Capabilities:
        if self.max_refs < 0:
            raise ValueError(f"{self.id}: max_refs must be >= 0")
        if self.n.get("min", 1) < 1 or self.n.get("max", 1) < self.n.get("min", 1):
            raise ValueError(f"{self.id}: invalid n range {self.n}")
        if "image" in self.input_modalities and self.max_refs == 0:
            # Not an error — some guidance models accept an image but expose no reference slot.
            # Kept as a comment so the invariant is documented where it is checked.
            pass
        return self

    @property
    def accepts_references(self) -> bool:
        return self.max_refs > 0

    @property
    def n_max(self) -> int:
        return int(self.n.get("max", 1))

    def output_cost_per_image(self) -> float | None:
        """Deterministic per-image cost, usable for a pre-flight budget estimate.

        Token- and megapixel-billed endpoints are excluded: we cannot know those
        before the call, and pretending otherwise would make the guard lie.
        """
        costs = [
            p.cost_usd
            for p in self.pricing
            if p.billable == "output_image" and p.unit in {"image", "request"}
        ]
        return max(costs) if costs else None


class Catalogue(BaseModel):
    generated_at: str
    source: str
    models: list[Capabilities]

    @model_validator(mode="after")
    def _unique(self) -> Catalogue:
        ids = [m.id for m in self.models]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate model ids in catalogue")
        if not self.models:
            raise ValueError("catalogue is empty")
        return self

    def get(self, model_id: str) -> Capabilities | None:
        for m in self.models:
            if m.id == model_id:
                return m
        return None


def load_catalogue(path: Path) -> Catalogue:
    """Parse and validate at load time so a bad file fails at startup, not mid-request."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Catalogue.model_validate(raw)


@lru_cache(maxsize=8)
def _cached(path_str: str) -> Catalogue:
    return load_catalogue(Path(path_str))


def catalogue_for(path: Path) -> Catalogue:
    return _cached(str(path))


class RequestValidationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def validate_request(caps: Capabilities, payload: dict[str, Any]) -> list[str]:
    """Reject unsupported fields instead of silently dropping them.

    A dropped field is a user who thinks they set a seed and did not.
    """
    errors: list[str] = []

    n = payload.get("n", 1)
    if n < caps.n.get("min", 1) or n > caps.n_max:
        errors.append(f"n must be between {caps.n.get('min', 1)} and {caps.n_max} for {caps.id}")

    checks = (
        ("resolution", caps.resolutions),
        ("aspect_ratio", caps.aspect_ratios),
        ("output_format", caps.formats),
        ("background", caps.backgrounds),
        ("quality", caps.qualities),
    )
    for field, allowed in checks:
        value = payload.get(field)
        if value is None:
            continue
        if not allowed:
            errors.append(f"{field} is not supported by {caps.id}")
        elif value not in allowed:
            errors.append(f"{field}={value!r} not supported by {caps.id}")

    if payload.get("output_compression") is not None:
        if not caps.supports_compression:
            errors.append(f"output_compression is not supported by {caps.id}")
        elif not 0 <= payload["output_compression"] <= 100:
            errors.append("output_compression must be 0-100")

    if payload.get("seed") is not None and not caps.supports_seed:
        errors.append(f"seed is not supported by {caps.id}")

    refs = payload.get("reference_asset_ids") or []
    if len(refs) > caps.max_refs:
        errors.append(f"{caps.id} accepts at most {caps.max_refs} reference image(s)")

    passthrough = payload.get("passthrough") or {}
    for slug, options in passthrough.items():
        if slug not in caps.provider_tags and not any(
            tag.startswith(f"{slug}/") for tag in caps.provider_tags
        ):
            errors.append(f"unknown provider {slug!r} for {caps.id}")
            continue
        for key in options:
            if key not in caps.passthrough_keys:
                errors.append(f"passthrough key {key!r} not allowed for {slug}")
    return errors


def estimate_cost(caps: Capabilities, n: int) -> float | None:
    per_image = caps.output_cost_per_image()
    return None if per_image is None else round(per_image * n, 6)
