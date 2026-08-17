from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.database import get_db
from app.models import Project
from app.services.llm import STYLE_NAMES

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request, "index.html", {"styles": STYLE_NAMES, "fonts": FONT_CHOICES, "presets": PRESETS}
    )


@router.get("/projects", response_class=HTMLResponse)
def projects_feed(request: Request, db: Session = Depends(get_db)):
    projects = db.scalars(select(Project).order_by(Project.created_at.desc())).all()
    return templates.TemplateResponse(request, "projects.html", {"projects": projects})


@router.get("/projects/{project_id}", response_class=HTMLResponse)
def project_detail(project_id: str, request: Request, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Проект не найден")
    return templates.TemplateResponse(request, "project_detail.html", {"project": project})


@router.get("/projects/{project_id}/preview", response_class=HTMLResponse)
def project_preview(project_id: str, db: Session = Depends(get_db)):
    """Отдаёт сгенерированный HTML для sandbox-iframe (изолированный origin, скрипты разрешены)."""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Проект не найден")
    if not project.current_html:
        return HTMLResponse("<p style='font-family:sans-serif'>Сайт ещё не сгенерирован.</p>", status_code=200)
    return HTMLResponse(
        content=project.current_html,
        headers={"Content-Security-Policy": "sandbox allow-scripts"},
    )


FONT_CHOICES = [
    "Inter",
    "Roboto",
    "Montserrat",
    "Open Sans",
    "Playfair Display",
    "Oswald",
    "PT Sans",
    "system-ui (без подключения)",
]

PRESETS = {
    "landing": {
        "label": "Лендинг",
        "prompt": "Лендинг для продукта/услуги: герой-блок с заголовком и кнопкой, секция преимуществ (3-4 карточки), блок «Как это работает», отзывы клиентов, призыв к действию и контакты в подвале.",
    },
    "portfolio": {
        "label": "Портфолио",
        "prompt": "Сайт-портфолио специалиста: обо мне, навыки, галерея проектов (карточки с CSS-градиентными обложками), опыт работы, форма обратной связи, ссылки на соцсети.",
    },
    "business_card": {
        "label": "Визитка",
        "prompt": "Сайт-визитка компании: кто мы, чем занимаемся, ключевые цифры, услуги списком, контактная информация с картой-заглушкой, аккуратный минималистичный футер.",
    },
}
