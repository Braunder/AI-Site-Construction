import os
import re
import tempfile
import uuid

# До импорта app-модулей: изолированные БД и каталог загрузок
_tmp = tempfile.mkdtemp(prefix="aisc_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["UPLOAD_DIR"] = f"{_tmp}/uploads"
os.environ["LLM_BASE_URL"] = "http://127.0.0.1:1/v1"  # недоступный адрес — LLM в тестах мокается

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import User
from app.services.auth import hash_password


def _unique_username(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations():
    """Применяет миграции Alembic к временной тестовой БД."""
    from alembic.config import Config
    from alembic.command import upgrade

    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
    upgrade(alembic_cfg, "head")


@pytest.fixture
def db_session():
    with SessionLocal() as db:
        yield db


@pytest.fixture
def test_user(db_session):
    username = _unique_username("testuser")
    password = "testpass"
    user = User(username=username, hashed_password=hash_password(password), plan="standard", generation_limit=100)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    user._plain_password = password  # для логина в фикстуре
    return user


@pytest.fixture
def admin_user(db_session):
    username = _unique_username("testadmin")
    password = "adminpass"
    user = User(username=username, hashed_password=hash_password(password), is_admin=True, generation_limit=-1)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    user._plain_password = password
    return user


def _login_client(client: TestClient, username: str, password: str) -> None:
    # Сбрасываем in-memory rate limiter, чтобы логины в тестах не блокировали друг друга
    from app.dependencies import _login_attempts
    _login_attempts.clear()

    r = client.get("/login")
    assert r.status_code == 200
    m = re.search(r'name="csrf_token" value="([^"]+)"', r.text)
    assert m, "CSRF-токен не найден на странице логина"
    login = client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": m.group(1)},
        follow_redirects=False,
    )
    assert login.status_code == 302, login.text


@pytest.fixture
def client(monkeypatch, test_user):
    from app.main import app
    from app.services import llm

    async def fake_generate(params, image_data_url=None, seed=None, assets=None, asset_images=None, animations=True):
        return (
            "<!DOCTYPE html><html><head><title>t</title><style>body{margin:0}"
            + "x" * 200
            + "</style></head><body><h1>Site</h1></body></html>"
        )

    async def fake_edit(current_html, instruction):
        return (
            "<!DOCTYPE html><html><head><title>t</title><style>body{margin:0}"
            + "x" * 200
            + "</style></head><body><h1>Site v2</h1></body></html>"
        )

    monkeypatch.setattr(llm, "generate_site", fake_generate)
    monkeypatch.setattr(llm, "regenerate_site", fake_generate)
    monkeypatch.setattr(llm, "edit_site", fake_edit)

    with TestClient(app) as c:
        _login_client(c, test_user.username, test_user._plain_password)
        yield c


@pytest.fixture
def admin_client(monkeypatch, admin_user):
    from app.main import app
    from app.services import llm

    async def fake_generate(params, image_data_url=None, seed=None, assets=None, asset_images=None, animations=True):
        return (
            "<!DOCTYPE html><html><head><title>t</title><style>body{margin:0}"
            + "x" * 200
            + "</style></head><body><h1>Site</h1></body></html>"
        )

    async def fake_edit(current_html, instruction):
        return (
            "<!DOCTYPE html><html><head><title>t</title><style>body{margin:0}"
            + "x" * 200
            + "</style></head><body><h1>Site v2</h1></body></html>"
        )

    monkeypatch.setattr(llm, "generate_site", fake_generate)
    monkeypatch.setattr(llm, "regenerate_site", fake_generate)
    monkeypatch.setattr(llm, "edit_site", fake_edit)

    with TestClient(app) as c:
        _login_client(c, admin_user.username, admin_user._plain_password)
        yield c
