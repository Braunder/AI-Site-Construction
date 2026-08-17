import logging
import time
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqladmin import Admin
from starlette.middleware.sessions import SessionMiddleware

from app.admin import HistoryAdmin, ProjectAdmin
from app.config import BASE_DIR, get_settings
from app.database import Base, engine
from app.routers import api, pages

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
    logger.info("Приложение запущено. LLM: %s (%s)", settings.llm_model, settings.llm_base_url)
    yield


app = FastAPI(title="AI Site Construction", version="0.1.0", lifespan=lifespan)

# --- Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info("%s %s -> %s (%.0f ms)", request.method, request.url.path, response.status_code, elapsed_ms)
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Необработанная ошибка: %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка сервера"})


# --- Статика, роуты ---
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
app.include_router(pages.router)
app.include_router(api.router)

# --- Админка ---
admin = Admin(app, engine, title="AI Site Admin")
admin.add_view(ProjectAdmin)
admin.add_view(HistoryAdmin)
