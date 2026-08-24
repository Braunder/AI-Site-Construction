import base64
import uuid
from pathlib import Path

from fastapi import UploadFile

from app.config import get_settings

settings = get_settings()

ALLOWED_TYPES = {
    "image/jpeg": (".jpg", b"\xff\xd8\xff"),
    "image/png": (".png", b"\x89PNG\r\n\x1a\n"),
}


class ImageValidationError(ValueError):
    pass


async def save_upload(file: UploadFile) -> Path:
    """Валидирует (JPG/PNG, до N МБ) и сохраняет загруженное изображение. Возвращает путь."""
    mime = (file.content_type or "").lower()
    if mime not in ALLOWED_TYPES:
        raise ImageValidationError("Допустимы только изображения JPG или PNG")

    data = await file.read()
    if not data:
        raise ImageValidationError("Файл пуст")
    if len(data) > settings.max_upload_bytes:
        raise ImageValidationError(f"Файл больше {settings.max_upload_mb} МБ")

    ext, magic = ALLOWED_TYPES[mime]
    if not data.startswith(magic):
        raise ImageValidationError("Содержимое файла не соответствует заявленному формату")

    path = settings.upload_path / f"{uuid.uuid4().hex}{ext}"
    path.write_bytes(data)
    return path


_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def to_base64_data_url(path: Path) -> tuple[str, str]:
    """Возвращает (data_url, base64) изображения для vision-запроса к LLM.

    WebP конвертируется в JPEG: некоторые сборки llama.cpp (mtmd) не декодируют
    WebP и падают с 'failed to decode image bytes'.
    """
    ext = path.suffix.lower()
    if ext == ".webp":
        path = _webp_to_jpeg(path)
        ext = ".jpg"
    mime = _MIME_BY_EXT.get(ext, "image/jpeg")
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}", b64


def _webp_to_jpeg(path: Path) -> Path:
    """Конвертирует WebP в JPEG рядом с оригиналом (кэшируется)."""
    jpeg_path = path.with_suffix(".vision.jpg")
    if jpeg_path.exists():
        return jpeg_path
    from PIL import Image

    with Image.open(path) as img:
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(jpeg_path, "JPEG", quality=90)
    return jpeg_path
