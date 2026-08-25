"""Многофайловое хранение сайтов: data/sites/<user_id>/<project_id>/.

Каждый проект — папка с index.html и произвольными ассетами (css/js/изображения).
Пользователь имеет своё пространство data/sites/<user_id>/.
"""

import logging
import re
import shutil
import uuid
from pathlib import Path

from fastapi import UploadFile

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Разрешённые типы файлов внутри сайта
ASSET_TYPES: dict[str, tuple[str, ...]] = {
    "image": (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico"),
    "style": (".css",),
    "script": (".js",),
    "font": (".woff2", ".woff", ".ttf"),
    "data": (".json", ".csv"),
}
ALLOWED_ASSET_EXTS = {ext for exts in ASSET_TYPES.values() for ext in exts}

MIME_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
    ".ico": "image/x-icon", ".css": "text/css", ".js": "application/javascript",
    ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf",
    ".json": "application/json", ".csv": "text/csv",
}

# Только безопасное имя файла: без путей, спецсимволов
_SAFE_NAME_RE = re.compile(r"^[\w][\w\-. ]{0,80}$", re.UNICODE)


class SiteFileError(ValueError):
    pass


def user_dir(user_id: str) -> Path:
    """Корневая папка пользователя."""
    p = settings.sites_path / _safe_component(user_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def project_dir(user_id: str, project_id: str) -> Path:
    """Папка конкретного проекта пользователя."""
    p = user_dir(user_id) / _safe_component(project_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_component(value: str) -> str:
    if not value or not _SAFE_NAME_RE.match(value):
        raise SiteFileError("Недопустимое имя пути")
    return value


def safe_filename(filename: str) -> str:
    """Нормализует имя файла: без путей, с уникальным префиксом при коллизии не заморачиваемся — caller проверяет."""
    name = Path(filename.replace("\\", "/")).name.strip()
    name = re.sub(r"[^\w.\- ]", "_", name)
    if not name or name.startswith("."):
        raise SiteFileError("Недопустимое имя файла")
    return name[:100]


async def save_asset(user_id: str, project_id: str, file: UploadFile, rename: bool = True) -> Path:
    """Сохраняет файл-ассет в папку проекта. Возвращает путь.

    rename=True добавляет короткий uuid-префикс против коллизий и перезаписи.
    """
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_ASSET_EXTS:
        raise SiteFileError(f"Тип файла {ext or '(без расширения)'} не поддерживается")

    data = await file.read()
    if not data:
        raise SiteFileError("Файл пуст")
    if len(data) > settings.max_upload_bytes:
        raise SiteFileError(f"Файл больше {settings.max_upload_mb} МБ")

    pdir = project_dir(user_id, project_id)
    _check_project_limits(pdir, incoming=len(data))

    name = safe_filename(file.filename or f"asset{ext}")
    if rename:
        name = f"{uuid.uuid4().hex[:8]}-{name}"
    path = pdir / name
    path.write_bytes(data)

    # Растровые изображения конвертируем в WebP (меньше вес, быстрее сайты)
    if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        converted = _convert_to_webp(path)
        if converted is not None:
            path.unlink(missing_ok=True)  # удаляем оригинал
            path = converted
            logger.info("Конвертировано в WebP: %s (%d байт)", path.name, path.stat().st_size)

    logger.info("Ассет сохранён: %s (%d байт)", path, len(data))
    return path


def _convert_to_webp(path: Path) -> Path | None:
    """Конвертирует изображение в WebP. Возвращает новый путь или None при ошибке."""
    try:
        from PIL import Image

        with Image.open(path) as img:
            # GIF с анимацией не трогаем — WebP-анимация сложнее, оставляем как есть
            if path.suffix.lower() == ".gif" and getattr(img, "is_animated", False):
                return None
            # RGBA сохраняем с прозрачностью, RGB — без
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            webp_path = path.with_suffix(".webp")
            img.save(webp_path, "WEBP", quality=85, method=6)
            return webp_path
    except Exception:  # noqa: BLE001 — конвертация не должна ломать загрузку
        logger.warning("Не удалось конвертировать %s в WebP", path.name, exc_info=True)
        return None


def write_index(user_id: str, project_id: str, html: str) -> Path:
    """Записывает index.html проекта."""
    pdir = project_dir(user_id, project_id)
    path = pdir / "index.html"
    path.write_text(html, encoding="utf-8")
    return path


def read_index(user_id: str, project_id: str) -> str | None:
    path = project_dir(user_id, project_id) / "index.html"
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def list_assets(user_id: str, project_id: str) -> list[dict]:
    """Список ассетов проекта (без index.html)."""
    pdir = project_dir(user_id, project_id)
    result = []
    for f in sorted(pdir.iterdir()):
        if f.is_file() and f.name != "index.html":
            kind = next((k for k, exts in ASSET_TYPES.items() if f.suffix.lower() in exts), "other")
            result.append({"name": f.name, "kind": kind, "size": f.stat().st_size})
    return result


def delete_project_dir(user_id: str, project_id: str) -> None:
    shutil.rmtree(project_dir(user_id, project_id), ignore_errors=True)


def _check_project_limits(pdir: Path, incoming: int) -> None:
    files = [f for f in pdir.iterdir() if f.is_file()]
    total = sum(f.stat().st_size for f in files) + incoming
    if len(files) + 1 > settings.max_site_files:
        raise SiteFileError(f"Максимум {settings.max_site_files} файлов на проект")
    if total > settings.max_project_size_bytes:
        raise SiteFileError(f"Суммарный размер проекта больше {settings.max_project_size_mb} МБ")


def assets_manifest(user_id: str, project_id: str) -> list[dict]:
    """Манифест для LLM с подписанными URL, доступными из preview-iframe."""
    from app.services.tokens import file_token

    return [
        {
            "path": f"/api/projects/{project_id}/files/{a['name']}?t={file_token(project_id, a['name'])}",
            "kind": a["kind"],
        }
        for a in list_assets(user_id, project_id)
    ]
