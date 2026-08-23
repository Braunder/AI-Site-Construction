"""Кастомные шрифты: data/fonts/. Загружают админы, используют все пользователи."""

import logging
import re
from pathlib import Path

from fastapi import UploadFile

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

FONT_EXTS = {".woff2", ".woff", ".ttf", ".otf"}
MIME = {".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".otf": "font/otf"}
_SAFE_RE = re.compile(r"^[\w][\w\-. ]{0,80}$", re.UNICODE)


class FontError(ValueError):
    pass


def list_fonts() -> list[dict]:
    """Список доступных кастомных шрифтов."""
    settings.fonts_path.mkdir(parents=True, exist_ok=True)
    return sorted(
        ({"name": f.name, "size": f.stat().st_size} for f in settings.fonts_path.iterdir() if f.is_file()),
        key=lambda x: x["name"].lower(),
    )


async def save_font(file: UploadFile) -> Path:
    """Валидирует и сохраняет шрифт. Возвращает путь."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in FONT_EXTS:
        raise FontError(f"Допустимы только шрифты: {', '.join(sorted(FONT_EXTS))}")

    data = await file.read()
    if not data:
        raise FontError("Файл пуст")
    if len(data) > settings.max_font_bytes:
        raise FontError(f"Файл больше {settings.max_font_mb} МБ")

    name = Path(file.filename.replace("\\", "/")).name.strip()
    name = re.sub(r"[^\w.\- ]", "_", name)
    if not name or not _SAFE_RE.match(name):
        raise FontError("Недопустимое имя файла")

    path = settings.fonts_path / name
    path.write_bytes(data)
    logger.info("Шрифт сохранён: %s (%d байт)", path, len(data))
    return path


def delete_font(name: str) -> bool:
    target = settings.fonts_path / Path(name).name
    if target.is_file():
        target.unlink()
        return True
    return False


def font_css() -> str:
    """CSS @font-face для всех кастомных шрифтов (подключается в превью/скачивание)."""
    faces = []
    for f in list_fonts():
        p = Path(f["name"])
        family = p.stem
        mime = MIME.get(p.suffix.lower(), "font/ttf")
        url = f"/api/fonts/files/{p.name}"
        faces.append(
            f"@font-face {{ font-family: '{family}'; src: url('{url}') format('{_fmt(p.suffix)}'); }}"
        )
    return "\n".join(faces)


def _fmt(ext: str) -> str:
    return {"woff2": "woff2", "woff": "woff", ".ttf": "truetype", "otf": "opentype"}.get(
        ext.lstrip("."), "truetype"
    )
