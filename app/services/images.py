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

    Готовит уменьшенную JPEG-копию (кэшируется рядом с оригиналом):
    - длинная сторона <= settings.vision_max_px (по умолчанию 1024);
    - JPEG quality 80.
    Токены vision-моделей зависят от разрешения, а не от качества сжатия:
    1024px достаточно для анализа содержимого, payload падает в разы.
    WebP/GIF конвертируются в JPEG (некоторые сборки llama.cpp не декодируют WebP).
    """
    path = _vision_copy(path)
    ext = ".jpg"
    mime = _MIME_BY_EXT.get(ext, "image/jpeg")
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}", b64


def _vision_copy(path: Path) -> Path:
    """Уменьшенная JPEG-копия для vision (кэшируется как *.vision.jpg).

    При ошибке обработки возвращает оригинал — vision не должен ломать генерацию.
    """
    jpeg_path = path.with_suffix(".vision.jpg")
    if jpeg_path.exists():
        return jpeg_path
    try:
        from PIL import Image

        max_px = get_settings().vision_max_px
        with Image.open(path) as img:
            # GIF с анимацией: берём первый кадр
            if getattr(img, "is_animated", False):
                img.seek(0)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            w, h = img.size
            longest = max(w, h)
            if longest > max_px:
                scale = max_px / longest
                img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
            img.save(jpeg_path, "JPEG", quality=80, optimize=True)
        return jpeg_path
    except Exception:  # noqa: BLE001 — fallback на оригинал
        return path
