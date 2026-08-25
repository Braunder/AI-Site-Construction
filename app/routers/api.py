import re
import unicodedata
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response, FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.dependencies import consume_generation, get_project_for_user, require_user
from app.models import Project, User
from app.services.images import ImageValidationError, save_upload
from app.services import sites as sites_store
from app.services import fonts as fonts_store
from app.services import tasks
from app.services.plans import get_plan

router = APIRouter(prefix="/api/projects", tags=["api"])

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
# Только обычный пробел — не \n, \r, \t (иначе возможен prompt injection через font)
FONT_RE = re.compile(r"^[\w()\+\- ]{1,100}$", re.UNICODE)
ALLOWED_STYLES = {"minimalism", "brutalism", "corporate", "creative", "dark", "elegant"}
PROMPT_MAX_LEN = 5000
INSTRUCTION_MAX_LEN = 2000


def _validate_form_fields(prompt: str, font: str, style: str, colors: list[str]) -> None:
    if not prompt or not prompt.strip():
        raise HTTPException(422, "Описание сайта обязательно")
    if len(prompt) > PROMPT_MAX_LEN:
        raise HTTPException(422, f"Описание длиннее {PROMPT_MAX_LEN} символов")
    if not FONT_RE.match(font):
        raise HTTPException(422, "Некорректное имя шрифта (до 100 символов: буквы, цифры, пробелы, - + ( ))")
    if style not in ALLOWED_STYLES:
        raise HTTPException(422, "Неизвестное стилевое направление")
    if any(not HEX_COLOR_RE.match(c) for c in colors):
        raise HTTPException(422, "Цвет должен быть в формате #RRGGBB")


def _lock_project(project: Project, db: Session) -> None:
    """Атомарно переводит проект в processing. 409, если уже обрабатывается."""
    result = db.execute(
        update(Project)
        .where(Project.id == project.id, Project.status != "processing")
        .values(status="processing")
    )
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(409, "Проект уже обрабатывается, дождитесь завершения")


def _consume_or_429(user: User, db: Session) -> None:
    if not consume_generation(user.id, db):
        raise HTTPException(429, "Лимит генераций исчерпан")


# Серверная защита от дублей: одинаковый запрос от того же пользователя в течение 10 сек = дубль
import time as _time

_recent_creates: dict[str, tuple[dict[str, float], float]] = {}  # user_id -> ({prompt_hash: ts}, ts)
_DEDUPE_WINDOW = 10.0


def _is_duplicate_create(user_id: str, prompt: str) -> bool:
    import hashlib as _hashlib

    prompt_hash = _hashlib.sha256(prompt.encode()).hexdigest()
    now = _time.time()
    entry = _recent_creates.get(user_id, ({}, 0.0))
    prompts, _ = entry
    prompts = {h: t for h, t in prompts.items() if now - t < _DEDUPE_WINDOW}
    stale = [k for k, (_, ts) in _recent_creates.items() if now - ts > _DEDUPE_WINDOW * 2]
    for k in stale:
        _recent_creates.pop(k, None)
    if prompts.get(prompt_hash) and now - prompts[prompt_hash] < _DEDUPE_WINDOW:
        prompts[prompt_hash] = now
        _recent_creates[user_id] = (prompts, now)
        return True
    prompts[prompt_hash] = now
    _recent_creates[user_id] = (prompts, now)
    return False


@router.post("", status_code=201)
async def create_project(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
    prompt: str = Form(""),
    font: str = Form("Inter"),
    style: str = Form("minimalism"),
    color_primary: str = Form("#1f2937"),
    color_accent: str = Form("#3b82f6"),
    color_bg: str = Form("#ffffff"),
    image: UploadFile | None = File(None),
    files: list[UploadFile] = File(None),   # ассеты для многофайлового режима
    multifile: bool = Form(False),          # включить режим полноценного сайта
):
    """Создаёт проект из multipart-формы и запускает генерацию в фоне.

    Доступ определяется тарифом пользователя (plans.py):
    - free: только текстовое описание, одностраничник
    - standard: + референсное изображение, анимации, загрузка изображений для страницы
    - premium (бета): + многостраничные сайты (multipage)
    """
    _validate_form_fields(prompt, font, style, [color_primary, color_accent, color_bg])

    # Серверная защита от дублей (двойной клик/Enter): тот же промпт от того же
    # пользователя в течение 10 секунд — возвращаем существующий проект.
    if _is_duplicate_create(current_user.id, prompt.strip()):
        existing = db.scalars(
            select(Project)
            .where(Project.user_id == current_user.id, Project.prompt == prompt.strip())
            .order_by(Project.created_at.desc())
            .limit(1)
        ).first()
        if existing:
            return {"id": existing.id, "status": existing.status, "duplicate": True}

    plan = get_plan(current_user)

    # Тарифные проверки
    if image is not None and image.filename and not plan.reference_image:
        raise HTTPException(403, "Референсное изображение доступно со Стандартного тарифа")
    if multifile and not plan.multifile:
        raise HTTPException(403, "Многофайловый режим доступен со Стандартного тарифа")

    # Изображения для сайта (standard+): модель видит их содержимое (vision)
    # и размещает по смыслу. Файловая система не задействована — это не multifile.
    has_site_files = bool(files) and any(f and f.filename for f in files)
    if has_site_files and not plan.asset_images:
        raise HTTPException(403, "Загрузка изображений для сайта доступна со Стандартного тарифа")

    if has_site_files:
        image_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico"}
        for f in files:
            if f and f.filename and Path(f.filename).suffix.lower() not in image_exts:
                raise HTTPException(
                    422,
                    f"Файл «{f.filename}»: можно загружать только изображения "
                    "(JPG, PNG, GIF, WebP, SVG, ICO).",
                )

    image_path: str | None = None
    quota_consumed = False
    try:
        _consume_or_429(current_user, db)
        quota_consumed = True

        if image is not None and image.filename:
            image_path = str(await save_upload(image))
    except ImageValidationError as exc:
        if quota_consumed:
            from app.dependencies import refund_generation
            refund_generation(current_user.id, db)
        raise HTTPException(422, str(exc)) from exc

    title = " ".join(prompt.split())[:60] or "Без названия"
    project = Project(
        user_id=current_user.id,
        title=title,
        prompt=prompt.strip(),
        font=font.strip() or "Inter",
        style=style,
        color_primary=color_primary,
        color_accent=color_accent,
        color_bg=color_bg,
        image_path=image_path,
        is_multifile=bool(multifile),
        status="pending",
    )
    try:
        db.add(project)
        db.flush()

        # Сохраняем ассеты до коммита: ошибка не оставляет pending-проект.
        if has_site_files:
            for f in files:
                if not f or not f.filename:
                    continue
                await sites_store.save_asset(current_user.id, project.id, f)
        db.commit()
        db.refresh(project)
    except (sites_store.SiteFileError, OSError) as exc:
        db.rollback()
        if image_path:
            Path(image_path).unlink(missing_ok=True)
        # rmdir без mkdir: не создаём заново папку несуществующего проекта
        sites_store.remove_project_dir_if_empty(current_user.id, project.id)
        from app.dependencies import refund_generation
        refund_generation(current_user.id, db)
        raise HTTPException(422, f"Ошибка сохранения файла: {exc}") from exc

    background_tasks.add_task(tasks.run_generation, project.id, current_user.id, False)
    return {"id": project.id, "status": project.status}


class ChatRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=INSTRUCTION_MAX_LEN)

    @field_validator("instruction")
    @classmethod
    def _strip_instruction(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Инструкция не может состоять только из пробелов")
        return value


@router.get("/{project_id}/status")
def project_status(
    project_id: str,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    project = get_project_for_user(project_id, current_user, db)
    return {"status": project.status, "error": project.error_message}


@router.post("/{project_id}/chat", status_code=202)
def chat_edit(
    project_id: str,
    body: ChatRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    project = get_project_for_user(project_id, current_user, db)
    if not project.current_html:
        raise HTTPException(409, "Сначала дождитесь первичной генерации")
    # Сначала лок: иначе 409 занятого проекта сгорал после списания квоты.
    _lock_project(project, db)
    _consume_or_429(current_user, db)
    background_tasks.add_task(tasks.run_edit, project.id, current_user.id, body.instruction.strip())
    return {"status": "processing"}


@router.post("/{project_id}/regenerate", status_code=202)
def regenerate(
    project_id: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    project = get_project_for_user(project_id, current_user, db)
    if project.status == "pending":
        raise HTTPException(409, "Сначала дождитесь первичной генерации")
    # Сначала лок: иначе 409 занятого проекта сгорал после списания квоты.
    _lock_project(project, db)
    _consume_or_429(current_user, db)
    background_tasks.add_task(tasks.run_generation, project.id, current_user.id, True)
    return {"status": "processing"}


@router.delete("/{project_id}", status_code=204)
def delete_project(
    project_id: str,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    where_clause = [Project.id == project_id, Project.status != "processing"]
    if not current_user.is_admin:
        where_clause.append(Project.user_id == current_user.id)

    result = db.execute(
        delete(Project)
        .where(*where_clause)
        .returning(Project.image_path, Project.user_id, Project.is_multifile)
    )
    rows = result.all()
    db.commit()
    if not rows:
        raise HTTPException(409, "Нельзя удалять обрабатываемый проект или чужой проект")
    image_path, owner_id, is_multifile = rows[0]
    if image_path:
        Path(image_path).unlink(missing_ok=True)
    # Удаляем папку многофайлового проекта
    if is_multifile and owner_id:
        sites_store.delete_project_dir(owner_id, project_id)
    return Response(status_code=204)


# --- Ассеты многофайлового проекта ---

@router.post("/{project_id}/assets", status_code=201)
async def upload_asset(
    project_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Загружает файл (изображение/шрифт/css/js) в папку проекта.

    Файл доступен сайту по относительному пути — LLM получает манифест при генерации.
    """
    project = get_project_for_user(project_id, current_user, db)
    if not get_plan(current_user).multifile:
        raise HTTPException(403, "Загрузка файлов доступна со Стандартного тарифа")
    if not project.is_multifile:
        raise HTTPException(409, "Ассеты доступны только для многофайлового проекта")
    if not project.user_id:
        raise HTTPException(409, "Проект без владельца: загрузка файлов недоступна")
    try:
        path = await sites_store.save_asset(project.user_id, project.id, file)
    except sites_store.SiteFileError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"name": path.name, "size": path.stat().st_size}


@router.get("/{project_id}/assets")
def list_assets(
    project_id: str,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Список файлов проекта."""
    project = get_project_for_user(project_id, current_user, db)
    if not project.user_id:
        return []
    return sites_store.list_assets(project.user_id, project.id)


@router.get("/{project_id}/files/{filename}")
def get_asset(
    project_id: str,
    filename: str,
    request: Request,
    t: str = "",
    db: Session = Depends(get_db),
):
    """Отдаёт файл проекта (для iframe-превью и скачивания).

    Доступ: подписанный токен ?t=... (sandbox-iframe не передаёт куку)
    либо авторизованный владелец/админ.
    """
    from app.services.tokens import verify_file_token

    if not verify_file_token(project_id, filename, t):
        current_user = require_user(request)  # 401 без сессии
        project = get_project_for_user(project_id, current_user, db)
        if not project.user_id:
            raise HTTPException(404, "Файл не найден")

    # Шрифты из общей библиотеки: fonts/<name>
    if filename.lower().startswith("fonts/"):
        font_name = Path(filename).name
        target = fonts_store.settings.fonts_path / font_name
        if not target.is_file():
            raise HTTPException(404, "Шрифт не найден")
        media_type = fonts_store.MIME.get(target.suffix.lower(), "font/ttf")
        return FileResponse(target, media_type=media_type)

    try:
        with SessionLocal() as sdb:
            owner_id = sdb.scalar(select(Project.user_id).where(Project.id == project_id))
    except Exception:  # noqa: BLE001
        owner_id = None
    if not owner_id:
        raise HTTPException(404, "Файл не найден")
    try:
        pdir = sites_store.project_dir(owner_id, project_id)
    except sites_store.SiteFileError as exc:
        raise HTTPException(422, str(exc)) from exc
    # Защита от path traversal
    target = (pdir / Path(filename).name).resolve()
    if not str(target).startswith(str(pdir.resolve())) or not target.is_file():
        raise HTTPException(404, "Файл не найден")
    media_type = sites_store.MIME_BY_EXT.get(target.suffix.lower(), "application/octet-stream")
    response = FileResponse(target, media_type=media_type)
    # SVG/HTML исполняются браузером в origin приложения — изолируем их CSP-sandbox,
    # чтобы скрипты внутри файла не получили доступ к сессии/cookies.
    if target.suffix.lower() in {".svg", ".html", ".htm"}:
        response.headers["Content-Security-Policy"] = "sandbox"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.delete("/{project_id}/assets/{filename}", status_code=204)
def delete_asset(
    project_id: str,
    filename: str,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    project = get_project_for_user(project_id, current_user, db)
    if not project.user_id:
        return Response(status_code=204)
    try:
        pdir = sites_store.project_dir(project.user_id, project.id)
    except sites_store.SiteFileError as exc:
        raise HTTPException(422, str(exc)) from exc
    target = (pdir / Path(filename).name).resolve()
    if not (
        str(target).startswith(str(pdir.resolve()))
        and target.is_file()
        and target.name != "index.html"
    ):
        raise HTTPException(404, "Файл не найден")
    target.unlink()
    return Response(status_code=204)


@router.get("/{project_id}/download")
def download(
    project_id: str,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    project = get_project_for_user(project_id, current_user, db)
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


@router.get("/{project_id}/download-zip")
def download_zip(
    project_id: str,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Экспорт проекта в ZIP: index.html + все файлы (для многофайловых проектов)."""
    import io
    import zipfile

    project = get_project_for_user(project_id, current_user, db)
    if not project.current_html:
        raise HTTPException(409, "Сайт ещё не сгенерирован")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.html", project.current_html)
        if project.is_multifile and project.user_id:
            try:
                pdir = sites_store.project_dir(project.user_id, project.id)
                for f in sorted(pdir.iterdir()):
                    if f.is_file() and f.name != "index.html":
                        zf.write(f, f.name)
            except sites_store.SiteFileError:
                pass

    zip_name = f"{_slugify(project.title)}-{project.id[:8]}.zip"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"site-{project.id[:8]}.zip\"; "
                f"filename*=UTF-8''{quote(zip_name)}"
            )
        },
    )


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "-", text).strip("-").lower()
    return text[:50] or "site"


@router.get("")
def list_projects(
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    query = select(Project)
    if not current_user.is_admin:
        query = query.where(Project.user_id == current_user.id)
    return [
        {"id": p.id, "title": p.title, "status": p.status, "created_at": p.created_at_str}
        for p in db.scalars(query.order_by(Project.created_at.desc()))
    ]
