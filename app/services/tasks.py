"""Фоновые задачи генерации/правки: выполняются после ответа API, обновляют Project в БД."""

import logging
from pathlib import Path

from app.database import SessionLocal
from app.models import History, Project
from app.services import llm
from app.services.images import to_base64_data_url

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


def _finish(db: SessionLocal, project: Project, html: str, kind: str, instruction: str) -> None:
    project.current_html = html
    project.status = "done"
    project.error_message = None
    db.add(History(project_id=project.id, kind=kind, instruction=instruction, html=html))


def _fail(project: Project, exc: Exception) -> None:
    logger.exception("Задача для проекта %s завершилась ошибкой", project.id)
    project.status = "error"
    project.error_message = str(exc)[:1000]


async def run_generation(project_id: str, regenerate: bool = False) -> None:
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if project is None:
            return
        project.status = "processing"
        db.commit()
        try:
            params = _project_params(project)
            data_url = _image_data_url(project)
            if regenerate:
                html = await llm.regenerate_site(params, data_url)
            else:
                html = await llm.generate_site(params, data_url)
            _finish(db, project, html, "regenerate" if regenerate else "generate", project.prompt)
        except Exception as exc:  # noqa: BLE001 — фиксируем любую ошибку в статус проекта
            _fail(project, exc)
        db.commit()


async def run_edit(project_id: str, instruction: str) -> None:
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
        db.commit()
