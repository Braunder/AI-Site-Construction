import logging
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqladmin import Admin
from starlette.middleware.proxy_headers import ProxyHeadersMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.admin import AdminAuth, HistoryAdmin, LLMProviderAdmin, ProjectAdmin, UserAdmin
from app.config import BASE_DIR, get_settings
from app.database import Base, engine
from app.dependencies import get_optional_user
from app.routers import api, pages
from app.routers import fonts_api

settings = get_settings()


def setup_logging() -> None:
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    file_handler = RotatingFileHandler(log_dir / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.handlers = [console, file_handler]


setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # SQLite-файл и таблицы создаются автоматически; миграции — через alembic (см. README)
    Base.metadata.create_all(engine)
    settings.upload_path.mkdir(parents=True, exist_ok=True)
    # Задачи, прерванные прошлым завершением сервера, помечаем ошибкой
    # и возвращаем пользователям списанные за них квоты.
    from sqlalchemy import select, update

    from app.dependencies import refund_generation
    from app.database import SessionLocal
    from app.models import Project

    with engine.begin() as conn:
        stale = conn.execute(
            update(Project)
            .where(Project.status.in_(["pending", "processing"]))
            .values(status="error", error_message="Генерация прервана перезапуском сервера")
        )
    if stale.rowcount:
        logger.warning("Помечено ошибкой прерванных задач: %d", stale.rowcount)
        # Возвращаем квоты владельцам прерванных проектов (по одной на проект).
        with SessionLocal() as db:
            owner_ids = db.scalars(
                select(Project.user_id).where(
                    Project.error_message == "Генерация прервана перезапуском сервера"
                )
            ).all()
            # По одной квоте за КАЖДЫЙ прерванный проект, а не один refund на пользователя.
            from collections import Counter

            for uid, count in Counter(u for u in owner_ids if u).items():
                for _ in range(count):
                    refund_generation(uid, db)
            logger.info("Возвращено квот: %d (пользователей: %d)", sum(Counter(u for u in owner_ids if u).values()), len(set(owner_ids)))
    logger.info("Приложение запущено. LLM: %s (%s)", settings.llm_model, settings.llm_base_url)
    # Фоновый health-check LLM раз в 5 минут (без расхода токенов)
    from app.services.llm_health import start_health_task

    health_task = start_health_task()
    yield
    health_task.cancel()


app = FastAPI(title="AI Site Construction", version="0.1.0", lifespan=lifespan)

# --- Middleware ---
if settings.proxy_headers:
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=settings.allowed_hosts_list,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    https_only=settings.session_cookie_secure,
    same_site="lax",
)


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    response = await call_next(request)
    logger.info("%s %s -> %s", request.method, request.url.path, response.status_code)
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Необработанная ошибка: %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка сервера"})


# --- Статика, роуты ---
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
app.include_router(pages.router)
app.include_router(api.router)
app.include_router(fonts_api.router)

# --- Админка ---
admin = Admin(app, engine, title="AI Site Admin", authentication_backend=AdminAuth(settings.secret_key))
admin.add_view(ProjectAdmin)
admin.add_view(HistoryAdmin)
admin.add_view(UserAdmin)
admin.add_view(LLMProviderAdmin)


# --- Кнопки + тёмная тема для админки (sqladmin/Tabler) ---
_ADMIN_HOME_SNIPPET = """
<div style="position: fixed; top: 12px; right: 16px; z-index: 9999; display: flex; gap: 8px;">
  <a href="/" style="display:inline-block;padding:8px 18px;background:#6366f1;color:#fff;border-radius:10px;text-decoration:none;font-size:14px;box-shadow:0 4px 18px rgba(99,102,241,.4);">🏠 На сайт</a>
  <a href="/projects" style="display:inline-block;padding:8px 18px;background:#1c2130;color:#e6e9f0;border:1px solid rgba(148,163,184,.28);border-radius:10px;text-decoration:none;font-size:14px;">📁 Кабинет</a>
</div>
<style>
/* Тёмная премиум-тема для sqladmin (Tabler) */
html, body { background: #0b0d12 !important; color: #e6e9f0 !important; }
/* Всё, что Tabler красит в белый — перекрываем */
[class*="bg-white"], [class*="bg-light"], .page-body, .page-content,
.container, .container-fluid, .row, .col, [class*="col-"],
.panel, .box, .tabs-content, .tab-pane, .form-fieldset, fieldset,
.form-footer, .card-body, .card, .datagrid, .content, .page {
  background: transparent !important;
}
.page, .page-body { background: #0b0d12 !important; }
.navbar, .navbar-vertical, .card, .modal-content,
.tabler .card, .card-body, .datagrid, .form-horizontal,
.form-body, .form-group { background: #161a23 !important; color: #e6e9f0 !important; }
.navbar-vertical.navbar-light { background: #11141b !important; border-right: 1px solid rgba(148,163,184,.14) !important; }
.card { border: 1px solid rgba(148,163,184,.14) !important; border-radius: 14px !important;
        box-shadow: 0 10px 30px rgba(0,0,0,.4) !important; }
.card-header, .card-footer { background: #11141b !important; border-color: rgba(148,163,184,.14) !important; }
h1, h2, h3, h4, h5, h6, .page-title, .card-title, label, th, td, strong, span, p, a, li, div {
  color: #e6e9f0 !important;
}
.text-muted { color: #8b93a7 !important; }
.form-control, .form-select, .form-control-plaintext {
  background: #11141b !important; border: 1px solid rgba(148,163,184,.2) !important;
  color: #e6e9f0 !important; border-radius: 10px !important;
}
.bg-white, .bg-light, .form-control-label, .settings, .settings-form,
.form-wrapper, .form-container, .card-body.bg-light {
    background: #161a23 !important;
    color: #e6e9f0 !important;
}
/* Кнопки Cancel/Save — тёмные, без белых плашек */
.btn { background: #1c2130 !important; color: #e6e9f0 !important; border: 1px solid rgba(148,163,184,.25) !important; }
.btn:hover { background: #262d40 !important; }
.btn-primary { background: #6366f1 !important; border-color: #6366f1 !important; color: #fff !important; }
.btn-primary:hover { background: #4f46e5 !important; }
/* Чекбокс «Включён» */
.form-check-input { background-color: #1c2130 !important; border-color: rgba(148,163,184,.35) !important; }
.form-check-input:checked { background-color: #6366f1 !important; }
.form-control::placeholder { color: #6f7a91 !important; }
.form-control:focus { border-color: #6366f1 !important; box-shadow: 0 0 0 .2rem rgba(99,102,241,.18) !important; }
.btn-primary { background: #6366f1 !important; border-color: #6366f1 !important; box-shadow: 0 4px 16px rgba(99,102,241,.35) !important; }
.btn-secondary, .btn { border-radius: 10px !important; }
.btn-outline-secondary { color: #8b93a7 !important; border-color: rgba(148,163,184,.3) !important; }
.datagrid table, .table { color: #e6e9f0 !important; background: #161a23 !important; }
.datagrid thead, .datagrid thead tr, .datagrid thead th,
.table thead, .table thead tr, .table thead th {
    background: #11141b !important;
    color: #aeb7ca !important;
    border-color: rgba(148,163,184,.14) !important;
}
.datagrid tbody tr, .table tbody tr { background: #161a23 !important; }
.datagrid tbody td, .table tbody td { color: #e6e9f0 !important; border-color: rgba(148,163,184,.14) !important; }
.datagrid tbody tr:hover td, .table tbody tr:hover td { background: #1c2130 !important; }
.form-label, .form-check-label, .input-group-text { color: #aeb7ca !important; }
.form-label, label, .form-control-label, .form-check-label { color: #c3cbdb !important; }
.col-form-label, .control-label { color: #c3cbdb !important; }
.form-text, .help-block, .text-muted { color: #8b93a7 !important; }
.form-control::placeholder { color: #6f7a91 !important; }
.dropdown-menu { background: #161a23 !important; border-color: rgba(148,163,184,.24) !important; }
.dropdown-item { color: #e6e9f0 !important; }
.dropdown-item:hover { background: #1c2130 !important; color: #fff !important; }
.pagination .page-item.disabled .page-link { background: #11141b !important; color: #6f7a91 !important; }
.table-responsive { background: #161a23 !important; }
.pagination .page-link { background: #161a23 !important; border-color: rgba(148,163,184,.2) !important; color: #e6e9f0 !important; }
.badge { border-radius: 8px !important; }
.empty { background: transparent !important; }
</style>
<script>
/* Автозагрузка моделей OpenAI-совместимого провайдера */
(function () {
    function initModelLoader() {
        var urlInput = document.querySelector('input[name="base_url"]');
        var keyInput = document.querySelector('input[name="api_key"]');
        var modelInput = document.querySelector('input[name="model"]');
        if (!urlInput || !modelInput) return;

        var timer;
        var status = document.createElement('small');
        status.style.cssText = 'display:block;margin-top:6px;color:#8b93a7;';
        modelInput.parentElement.appendChild(status);

        async function loadModels() {
            var baseUrl = urlInput.value.trim();
            while (baseUrl.endsWith('/')) baseUrl = baseUrl.slice(0, -1);
            if (!baseUrl) return;
            status.textContent = 'Загрузка списка моделей…';
            try {
                var headers = {};
                if (keyInput && keyInput.value.trim()) {
                    headers.Authorization = 'Bearer ' + keyInput.value.trim();
                }
                var response = await fetch(baseUrl + '/models', { headers: headers });
                if (!response.ok) throw new Error('HTTP ' + response.status);
                var payload = await response.json();
                var models = Array.isArray(payload.data)
                    ? payload.data.map(function (item) { return item.id; }).filter(Boolean)
                    : [];
                if (!models.length) throw new Error('список пуст');

                var current = modelInput.value;
                var select = document.createElement('select');
                select.name = modelInput.name;
                select.id = modelInput.id;
                select.className = modelInput.className;
                models.forEach(function (name) {
                    var option = document.createElement('option');
                    option.value = name;
                    option.textContent = name;
                    option.selected = name === current;
                    select.appendChild(option);
                });
                if (current && !models.includes(current)) {
                    var custom = document.createElement('option');
                    custom.value = current;
                    custom.textContent = current + ' (текущее)';
                    custom.selected = true;
                    select.insertBefore(custom, select.firstChild);
                }
                modelInput.replaceWith(select);
                modelInput = select;
                status.textContent = 'Доступно моделей: ' + models.length;
                status.style.color = '#34d399';
            } catch (error) {
                status.textContent = 'Не удалось получить /models (' + error.message + '). Модель можно указать вручную.';
                status.style.color = '#fbbf24';
            }
        }

        urlInput.addEventListener('input', function () {
            clearTimeout(timer);
            timer = setTimeout(loadModels, 700);
        });
        urlInput.addEventListener('blur', loadModels);
        if (urlInput.value.trim()) loadModels();
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initModelLoader);
    else initModelLoader();
})();
</script>
"""


@app.middleware("http")
async def admin_nav_middleware(request: Request, call_next):
    """Добавляет кнопки «На сайт»/«Кабинет» в шапку всех страниц /admin."""
    path = request.url.path
    if path == "/admin" or path.startswith("/admin/"):
        response = await call_next(request)
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type:
            body = b""
            async for chunk in response.body_iterator:
                body += chunk
            # Вставляем кнопки после открывающего <body ...>
            snippet = _ADMIN_HOME_SNIPPET.encode("utf-8")
            lower = body.lower()
            idx = lower.find(b"<body")
            if idx != -1:
                close = lower.find(b">", idx)
                body = body[: close + 1] + snippet + body[close + 1 :]
            headers = dict(response.headers)
            # Пересчитываем Content-Length: тело изменилось, иначе uvicorn падает
            headers["content-length"] = str(len(body))
            return Response(
                content=body,
                status_code=response.status_code,
                headers=headers,
                media_type="text/html",
            )
        return response
    return await call_next(request)
