import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.database import get_db
from app.dependencies import (
    check_login_rate,
    get_optional_user,
    get_project_for_user,
    redirect_to_login,
    require_user,
)
from app.models import Project
from app.services import fonts as fonts_store
from app.services.plans import get_plan
from app.services.auth import verify_password
from app.services.llm import STYLE_NAMES

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")


def _template_context(request: Request, user, **extra) -> dict:
    ctx = {"request": request, "user": user}
    ctx.update(extra)
    return ctx


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = ""):
    if get_optional_user(request):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    csrf_token = secrets.token_urlsafe(32)
    request.session["csrf_token"] = csrf_token
    return templates.TemplateResponse(
        request, "login.html", {"csrf_token": csrf_token, "error": error}
    )


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    csrf_token: str = Form(""),
):
    check_login_rate(request)
    if request.session.get("csrf_token") != csrf_token:
        return RedirectResponse(url="/login?error=Недействительная+сессия", status_code=status.HTTP_302_FOUND)

    from app.database import SessionLocal
    from app.models import User

    with SessionLocal() as db:
        user = db.query(User).filter(User.username == username).first()
        if user is None or not verify_password(password, user.hashed_password):
            return RedirectResponse(url="/login?error=Неверный+логин+или+пароль", status_code=status.HTTP_302_FOUND)

        request.session["user_id"] = user.id
        request.session["is_admin"] = user.is_admin

    # После входа — в личный кабинет (для всех, включая админа)
    return RedirectResponse(url="/projects", status_code=status.HTTP_302_FOUND)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    """Публичная главная страница."""
    user = get_optional_user(request)
    return templates.TemplateResponse(request, "index.html", _template_context(request, user))


@router.get("/api/llm-status")
async def llm_status():
    """Публичный статус LLM для индикатора «AI online».

    Отдаёт кэш серверного опроса (раз в 5 минут), без обращения к LLM.
    """
    from app.services.llm_health import get_cached_status

    return get_cached_status()


@router.get("/demos", response_class=HTMLResponse)
def demos_page(request: Request):
    """Отдельная страница с демонстрациями сайтов."""
    user = get_optional_user(request)
    return templates.TemplateResponse(request, "demos.html", _template_context(request, user))


@router.get("/fonts", response_class=HTMLResponse)
def fonts_page(request: Request):
    """Страница библиотеки кастомных шрифтов (загрузка — только админ)."""
    user = get_optional_user(request)
    if user is None:
        return redirect_to_login(request)
    return templates.TemplateResponse(
        request, "fonts.html", _template_context(request, user, fonts=fonts_store.list_fonts())
    )


@router.get("/generate", response_class=HTMLResponse)
def generate_page(request: Request):
    """Форма генерации доступна только авторизованным пользователям."""
    user = get_optional_user(request)
    if user is None:
        return redirect_to_login(request)
    # Кастомные шрифты из библиотеки (data/fonts) добавляются к стандартным
    custom = [f["name"].rsplit(".", 1)[0] for f in fonts_store.list_fonts()]
    all_fonts = FONT_CHOICES + [c for c in custom if c not in FONT_CHOICES]
    plan = get_plan(user)
    return templates.TemplateResponse(
        request,
        "generate.html",
        _template_context(
            request, user, styles=STYLE_NAMES, fonts=all_fonts, presets=PRESETS,
            plan=plan,
        ),
    )


@router.get("/projects", response_class=HTMLResponse)
def projects_feed(request: Request, db: Session = Depends(get_db)):
    """Личный кабинет пользователя со списком его проектов."""
    user = get_optional_user(request)
    if user is None:
        return redirect_to_login(request)
    query = select(Project)
    if not user.is_admin:
        query = query.where(Project.user_id == user.id)
    projects = db.scalars(query.order_by(Project.created_at.desc())).all()
    plan = get_plan(user)
    return templates.TemplateResponse(
        request, "projects.html", _template_context(request, user, projects=projects, plan=plan)
    )


@router.get("/projects/{project_id}", response_class=HTMLResponse)
def project_detail(project_id: str, request: Request, db: Session = Depends(get_db)):
    user = get_optional_user(request)
    if user is None:
        return redirect_to_login(request)
    project = get_project_for_user(project_id, user, db)
    return templates.TemplateResponse(
        request, "project_detail.html", _template_context(request, user, project=project)
    )


@router.get("/projects/{project_id}/preview", response_class=HTMLResponse)
def project_preview(project_id: str, request: Request, db: Session = Depends(get_db)):
    """Отдаёт сгенерированный HTML для sandbox-iframe (изолированный origin, скрипты разрешены)."""
    user = require_user(request)
    project = get_project_for_user(project_id, user, db)
    if not project.current_html:
        return HTMLResponse("<p style='font-family:sans-serif'>Сайт ещё не сгенерирован.</p>", status_code=200)
    return HTMLResponse(
        content=project.current_html,
        headers={
            "Content-Security-Policy": "sandbox allow-scripts",
            "X-Content-Type-Options": "nosniff",
        },
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
