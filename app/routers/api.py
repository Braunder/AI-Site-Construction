import re
import unicodedata
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Project
from app.services.images import ImageValidationError, save_upload
from app.services import tasks

router = APIRouter(prefix="/api/projects", tags=["api"])

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
ALLOWED_STYLES = {"minimalism", "brutalism", "corporate", "creative", "dark", "elegant"}
PROMPT_MAX_LEN = 5000
INSTRUCTION_MAX_LEN = 2000


def _validate_form_fields(prompt: str, style: str, colors: list[str]) -> None:
    if not prompt or not prompt.strip():
        raise HTTPException(422, "Описание сайта обязательно")
    if len(prompt) > PROMPT_MAX_LEN:
        raise HTTPException(422, f"Описание длиннее {PROMPT_MAX_LEN} символов")
    if style not in ALLOWED_STYLES:
        raise HTTPException(422, "Неизвестное стилевое направление")
    if any(not HEX_COLOR_RE.match(c) for c in colors):
        raise HTTPException(422, "Цвет должен быть в формате #RRGGBB")


@router.post("", status_code=201)
async def create_project(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    prompt: str = Form(""),
    font: str = Form("Inter"),
    style: str = Form("minimalism"),
    color_primary: str = Form("#1f2937"),
    color_accent: str = Form("#3b82f6"),
    color_bg: str = Form("#ffffff"),
    image: UploadFile | None = File(None),
):
    """Создаёт проект из multipart-формы и запускает генерацию в фоне."""
    _validate_form_fields(prompt, style, [color_primary, color_accent, color_bg])

    image_path: str | None = None
    if image is not None and image.filename:
        try:
            image_path = str(await save_upload(image))
        except ImageValidationError as exc:
            raise HTTPException(422, str(exc)) from exc

    title = " ".join(prompt.split())[:60] or "Без названия"
    project = Project(
        title=title,
        prompt=prompt.strip(),
        font=font.strip() or "Inter",
        style=style,
        color_primary=color_primary,
        color_accent=color_accent,
        color_bg=color_bg,
        image_path=image_path,
        status="pending",
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    background_tasks.add_task(tasks.run_generation, project.id)
    return {"id": project.id, "status": project.status}


class ChatRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=INSTRUCTION_MAX_LEN)


def _get_project_or_404(project_id: str, db: Session) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Проект не найден")
    return project


@router.get("/{project_id}/status")
def project_status(project_id: str, db: Session = Depends(get_db)):
    project = _get_project_or_404(project_id, db)
    return {"status": project.status, "error": project.error_message}


@router.post("/{project_id}/chat", status_code=202)
def chat_edit(project_id: str, body: ChatRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    project = _get_project_or_404(project_id, db)
    if project.status == "processing":
        raise HTTPException(409, "Проект уже обрабатывается, дождитесь завершения")
    if not project.current_html:
        raise HTTPException(409, "Сначала дождитесь первичной генерации")
    background_tasks.add_task(tasks.run_edit, project.id, body.instruction.strip())
    return {"status": "processing"}


@router.post("/{project_id}/regenerate", status_code=202)
def regenerate(project_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    project = _get_project_or_404(project_id, db)
    if project.status == "processing":
        raise HTTPException(409, "Проект уже обрабатывается, дождитесь завершения")
    background_tasks.add_task(tasks.run_generation, project.id, True)
    return {"status": "processing"}


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: str, db: Session = Depends(get_db)):
    project = _get_project_or_404(project_id, db)
    if project.image_path:
        Path(project.image_path).unlink(missing_ok=True)
    db.delete(project)
    db.commit()
    return Response(status_code=204)


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "-", text).strip("-").lower()
    return text[:50] or "site"


@router.get("/{project_id}/download")
def download(project_id: str, db: Session = Depends(get_db)):
    project = _get_project_or_404(project_id, db)
    if not project.current_html:
        raise HTTPException(409, "Сайт ещё не сгенерирован")
    filename = f"{_slugify(project.title)}-{project.id[:8]}.html"
    return Response(
        content=project.current_html.encode("utf-8"),
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"site-{project.id[:8]}.html\"; "
                f"filename*=UTF-8''{quote(filename)}"
            )
        },
    )


@router.get("")
def list_projects(db: Session = Depends(get_db)):
    return [
        {"id": p.id, "title": p.title, "status": p.status, "created_at": p.created_at.isoformat()}
        for p in db.scalars(select(Project).order_by(Project.created_at.desc()))
    ]
