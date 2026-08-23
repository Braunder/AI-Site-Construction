"""Фоновые задачи генерации/правки: выполняются после ответа API, обновляют Project в БД."""

import logging
from pathlib import Path

from app.config import get_settings
from app.database import SessionLocal
from app.dependencies import refund_generation
from app.models import History, Project
from app.services import llm, sites
from app.services import fonts
from app.services.images import to_base64_data_url
from app.services.plans import get_plan

logger = logging.getLogger(__name__)


def _project_params(project: Project) -> dict:
    return {
        "prompt": project.prompt,
        "font": project.font,
        "style": project.style,
        "color_primary": project.color_primary,
        "color_accent": project.color_accent,
        "color_bg": project.color_bg,
    }


def _image_data_url(project: Project) -> str | None:
    if not project.image_path:
        return None
    path = Path(project.image_path)
    if not path.exists():
        return None
    try:
        return to_base64_data_url(path)[0]
    except OSError:
        return None


def _finish(db, project: Project, html: str, kind: str, instruction: str) -> None:
    cfg = llm.get_llm_config()
    project.current_html = html
    project.status = "done"
    project.error_message = None
    project.llm_model = cfg.model
    db.add(History(project_id=project.id, kind=kind, instruction=instruction, html=html))
    # Многофайловый режим: дублируем index.html в папку проекта
    if project.is_multifile and project.user_id:
        try:
            sites.write_index(project.user_id, project.id, html)
        except Exception:  # noqa: BLE001
            logger.warning("Не удалось записать index.html для проекта %s", project.id, exc_info=True)


def _assets_for(project: Project) -> list[dict] | None:
    """Манифест ассетов проекта + библиотечные шрифты для LLM (многофайловый режим)."""
    if not project.is_multifile or not project.user_id:
        return None
    try:
        assets = sites.assets_manifest(project.user_id, project.id)
    except Exception:  # noqa: BLE001
        assets = []
    # Кастомные шрифты из общей библиотеки админа доступны всем проектам
    for f in fonts.list_fonts():
        family = Path(f["name"]).stem
        assets.append({"path": f"fonts/{f['name']}", "kind": "font", "family": family})
    return assets or None


def _asset_images_for(project: Project) -> list[dict]:
    """Загруженные изображения проекта как vision-контент (data_url + подпись).

    Ограничено настройками vision_assets / vision_assets_limit.
    """
    if not project.is_multifile or not project.user_id:
        return []
    settings = get_settings()
    if not settings.vision_assets:
        return []
    images: list[dict] = []
    try:
        for asset in sites.list_assets(project.user_id, project.id):
            if asset["kind"] != "image":
                continue
            if len(images) >= settings.vision_assets_limit:
                logger.info(
                    "Vision-лимит %d изображений достигнут, остальные — только в манифесте",
                    settings.vision_assets_limit,
                )
                break
            path = sites.project_dir(project.user_id, project.id) / asset["name"]
            # SVG пропускаем: это векторный код, а не растр для vision
            if path.suffix.lower() == ".svg":
                continue
            try:
                data_url, _ = to_base64_data_url(path)
            except (OSError, ValueError):
                continue
            images.append({"path": asset["name"], "data_url": data_url})
    except Exception:  # noqa: BLE001
        logger.warning("Не удалось собрать изображения проекта %s", project.id, exc_info=True)
    return images


def _fail(project: Project, exc: Exception) -> None:
    logger.exception("Задача для проекта %s завершилась ошибкой", project.id)
    project.status = "error"
    project.error_message = str(exc)[:1000]


async def run_generation(project_id: str, user_id: str, regenerate: bool = False) -> None:
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if project is None:
            return
        project.status = "processing"
        db.commit()
        try:
            params = _project_params(project)
            data_url = _image_data_url(project)
            assets = _assets_for(project)
            asset_images = _asset_images_for(project)
            animations = get_plan(project.user).animations if project.user else True
            if regenerate:
                html = await llm.regenerate_site(
                    params, data_url, assets=assets, asset_images=asset_images, animations=animations
                )
            else:
                html = await llm.generate_site(
                    params, data_url, assets=assets, asset_images=asset_images, animations=animations
                )
            _finish(db, project, html, "regenerate" if regenerate else "generate", project.prompt)
        except Exception as exc:  # noqa: BLE001 — фиксируем любую ошибку в статус проекта
            _fail(project, exc)
            refund_generation(user_id, db)
        db.commit()


async def run_edit(project_id: str, user_id: str, instruction: str) -> None:
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if project is None or not project.current_html:
            return
        project.status = "processing"
        db.commit()
        try:
            html = await llm.edit_site(project.current_html, instruction)
            _finish(db, project, html, "edit", instruction)
        except Exception as exc:  # noqa: BLE001
            _fail(project, exc)
            refund_generation(user_id, db)
        db.commit()
