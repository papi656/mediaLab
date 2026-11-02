from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .models import Asset, MediaJob


class LoginIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class MeOut(BaseModel):
    authenticated: bool


class ModelOut(BaseModel):
    id: str
    name: str
    description: str
    input_modalities: list[str]
    max_refs: int
    n: dict[str, int]
    resolutions: list[str]
    aspect_ratios: list[str]
    formats: list[str]
    backgrounds: list[str]
    qualities: list[str]
    supports_compression: bool
    supports_seed: bool
    supports_size_flag: bool
    passthrough_keys: list[str]
    provider_tags: list[str]
    endpoint_count: int
    pricing: list[dict[str, Any]]


class ModelsOut(BaseModel):
    generated_at: str
    source: str
    models: list[ModelOut]


class GenerationIn(BaseModel):
    model: str
    prompt: str = Field(min_length=1, max_length=32_000)
    n: int = 1
    resolution: str | None = None
    aspect_ratio: str | None = None
    output_format: str | None = None
    background: str | None = None
    quality: str | None = None
    output_compression: int | None = None
    seed: int | None = None
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=20)
    passthrough: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def to_params(self) -> dict[str, Any]:
        data = self.model_dump(exclude={"model", "prompt"}, exclude_none=True)
        data.setdefault("n", 1)
        return data


class AssetOut(BaseModel):
    id: str
    role: str
    content_type: str
    bytes: int
    width: int | None
    height: int | None
    created_at: datetime
    url: str
    download_url: str

    @classmethod
    def from_asset(cls, asset: Asset) -> AssetOut:
        return cls(
            id=asset.id,
            role=asset.role,
            content_type=asset.content_type,
            bytes=asset.bytes,
            width=asset.width,
            height=asset.height,
            created_at=asset.created_at,
            url=f"/api/assets/{asset.id}",
            download_url=f"/api/assets/{asset.id}/download",
        )


class JobOut(BaseModel):
    id: str
    kind: str
    model: str
    provider_tag: str | None
    prompt: str
    params: dict[str, Any]
    status: str
    error: str | None
    error_detail: dict[str, Any] | None
    cost_usd: float | None
    usage: dict[str, Any] | None
    n_requested: int
    n_received: int | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    deleted_at: datetime | None
    assets: list[AssetOut]

    @classmethod
    def from_job(cls, job: MediaJob) -> JobOut:
        return cls(
            id=job.id,
            kind=job.kind,
            model=job.model,
            provider_tag=job.provider_tag,
            prompt=job.prompt,
            params=job.params or {},
            status=job.status,
            error=job.error,
            error_detail=job.error_detail,
            cost_usd=job.cost_usd,
            usage=job.usage,
            n_requested=job.n_requested,
            n_received=job.n_received,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            deleted_at=job.deleted_at,
            assets=[AssetOut.from_asset(a) for a in job.assets],
        )


class JobListOut(BaseModel):
    items: list[JobOut]
    total: int


class UsageOut(BaseModel):
    period: str
    spend_usd: float
    jobs: int
    images: int


class HealthOut(BaseModel):
    status: str
    database: bool
    provider: str
    models: int


class ErrorOut(BaseModel):
    detail: str
    errors: list[str] = Field(default_factory=list)
