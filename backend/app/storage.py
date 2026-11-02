from __future__ import annotations

import base64
import hashlib
import io
import mimetypes
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

EXT_BY_TYPE = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "image/gif": "gif",
    "image/avif": "avif",
    "image/bmp": "bmp",
    "video/mp4": "mp4",
    "video/webm": "webm",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
}

TYPE_BY_EXT = {v: k for k, v in EXT_BY_TYPE.items()}


@dataclass(frozen=True)
class StoredBytes:
    sha256: str
    filename: str
    content_type: str
    size: int
    width: int | None
    height: int | None


class UnsupportedMediaType(Exception):
    pass


def _ext(content_type: str) -> str:
    if content_type in EXT_BY_TYPE:
        return EXT_BY_TYPE[content_type]
    guessed = mimetypes.guess_extension(content_type or "") or ""
    return guessed.lstrip(".")[:8] or "bin"


def dimensions(data: bytes, content_type: str) -> tuple[int | None, int | None]:
    if content_type == "image/svg+xml":
        return None, None
    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.width, img.height
    except (UnidentifiedImageError, OSError):
        return None, None


def asset_path(assets_dir: Path, sha256: str, filename: str) -> Path:
    """Sharded so one directory never holds tens of thousands of files."""
    return assets_dir / sha256[:2] / filename


def save_bytes(assets_dir: Path, data: bytes, content_type: str) -> StoredBytes:
    if not data:
        raise UnsupportedMediaType("empty payload")
    sha256 = hashlib.sha256(data).hexdigest()
    filename = f"{sha256}.{_ext(content_type)}"
    path = asset_path(assets_dir, sha256, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():  # content-addressed: identical bytes are written once
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
    w, h = dimensions(data, content_type)
    return StoredBytes(sha256, filename, content_type, len(data), w, h)


def read_bytes(assets_dir: Path, sha256: str, filename: str) -> bytes:
    return asset_path(assets_dir, sha256, filename).read_bytes()


def unlink(assets_dir: Path, sha256: str, filename: str) -> bool:
    path = asset_path(assets_dir, sha256, filename)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def data_url(data: bytes, content_type: str) -> str:
    return f"data:{content_type};base64,{base64.b64encode(data).decode()}"


def sniff_image_type(data: bytes, declared: str | None) -> str:
    if declared and declared in EXT_BY_TYPE:
        return declared
    with Image.open(io.BytesIO(data)) as img:
        fmt = (img.format or "").lower()
    return {
        "png": "image/png",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "gif": "image/gif",
    }.get(fmt, "application/octet-stream")
