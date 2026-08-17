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


def to_base64_data_url(path: Path) -> tuple[str, str]:
    """Возвращает (data_url, base64) изображения для vision-запроса к LLM."""
    ext = path.suffix.lower()
    mime = "image/png" if ext == ".png" else "image/jpeg"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}", b64
