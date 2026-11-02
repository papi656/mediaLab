from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..auth import require_auth
from ..schemas import ModelsOut

router = APIRouter(prefix="/api", tags=["models"], dependencies=[Depends(require_auth)])


@router.get("/models", response_model=ModelsOut)
def list_models(request: Request) -> ModelsOut:
    """Served verbatim from the committed catalogue — no runtime fetch, no cache miss."""
    catalogue = request.app.state.catalogue
    return ModelsOut(
        generated_at=catalogue.generated_at,
        source=catalogue.source,
        models=[m.model_dump() for m in catalogue.models],
    )
