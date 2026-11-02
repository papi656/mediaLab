from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class ProviderError(Exception):
    """Provider failed. `detail` is safe to log; `message` is shown to the user."""

    def __init__(self, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


@dataclass
class ProviderRequest:
    model: str
    prompt: str
    params: dict[str, Any]
    session_id: str
    references: list[tuple[bytes, str]] = field(default_factory=list)


@dataclass
class ProviderImage:
    data: bytes
    content_type: str
    source: str = "provider"


@dataclass
class ProviderResult:
    images: list[ProviderImage]
    request_payload: dict[str, Any]
    usage: dict[str, Any] | None = None
    cost_usd: float | None = None


class ImageProvider(Protocol):
    name: str

    def generate(self, req: ProviderRequest) -> ProviderResult: ...
