from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..db import db_session
from ..models import Asset
from ..schemas import AssetOut
from ..storage import UnsupportedMediaType, asset_path, save_bytes, sniff_image_type

router = APIRouter(prefix="/api", tags=["assets"], dependencies=[Depends(require_auth)])

ALLOWED_UPLOAD_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


@router.post("/uploads", status_code=status.HTTP_201_CREATED, response_model=AssetOut)
async def upload_reference(
    request: Request,
    file: UploadFile = File(...),
    session: Session = Depends(db_session),
) -> AssetOut:
    """Reference images are stored server-side and sent to the provider as data URLs."""
    settings = request.app.state.settings
    limit = settings.max_upload_mb * 1024 * 1024

    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"upload exceeds {settings.max_upload_mb} MB",
        )
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty upload")

    try:
        content_type = sniff_image_type(data, file.content_type)
    except Exception:  # noqa: BLE001 - PIL raises a zoo of errors on bad input
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "not a readable image"
        ) from None
    if content_type not in ALLOWED_UPLOAD_TYPES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"unsupported type {content_type}"
        )

    try:
        stored = save_bytes(settings.assets_dir, data, content_type)
    except UnsupportedMediaType as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    asset = Asset(
        job_id=None,
        role="input",
        sha256=stored.sha256,
        filename=stored.filename,
        content_type=stored.content_type,
        bytes=stored.size,
        width=stored.width,
        height=stored.height,
        source="upload",
    )
    session.add(asset)
    session.commit()
    return AssetOut.from_asset(asset)


def _file(request: Request, session: Session, asset_id: str):
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    path = asset_path(request.app.state.settings.assets_dir, asset.sha256, asset.filename)
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "asset file is missing")
    return asset, path


@router.get("/assets/{asset_id}")
def get_asset(asset_id: str, request: Request, session: Session = Depends(db_session)):
    asset, path = _file(request, session, asset_id)
    return FileResponse(
        path,
        media_type=asset.content_type,
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.get("/assets/{asset_id}/download")
def download_asset(asset_id: str, request: Request, session: Session = Depends(db_session)):
    asset, path = _file(request, session, asset_id)
    ext = asset.filename.rsplit(".", 1)[-1]
    return FileResponse(
        path,
        media_type=asset.content_type,
        filename=f"medialab-{asset.id}.{ext}",
    )
