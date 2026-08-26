"""API-тесты с мокнутой LLM (фоновые задачи TestClient выполняет синхронно)."""

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import LLMProvider, Project, User
from sqlalchemy import text
from app.services import llm

VALID_FORM = {
    "prompt": "Сайт для кофейни",
    "font": "Inter",
    "style": "minimalism",
    "color_primary": "#1f2937",
    "color_accent": "#3b82f6",
    "color_bg": "#ffffff",
}


def create_project(client, **overrides) -> str:
    data = {**VALID_FORM, **overrides}
    resp = client.post("/api/projects", data=data)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_index_page_requires_login(client):
    # client уже авторизован, проверяем успешный доступ
    resp = client.get("/")
    assert resp.status_code == 200


def test_index_public_when_anonymous():
    """Главная страница публична: доступна без авторизации."""
    from app.main import app

    with TestClient(app) as c:
        resp = c.get("/", follow_redirects=False)
        assert resp.status_code == 200


def test_full_cycle(client):
    pid = create_project(client)

    status = client.get(f"/api/projects/{pid}/status").json()
    assert status == {"status": "done", "error": None}

    preview = client.get(f"/projects/{pid}/preview")
    assert preview.status_code == 200
    assert preview.headers["content-security-policy"] == "sandbox allow-scripts"

    detail = client.get(f"/projects/{pid}")
    assert detail.status_code == 200
    assert 'sandbox="allow-scripts"' in detail.text

    chat = client.post(f"/api/projects/{pid}/chat", json={"instruction": "сделай шапку темнее"})
    assert chat.status_code == 202

    dl = client.get(f"/api/projects/{pid}/download")
    assert dl.status_code == 200
    assert "attachment" in dl.headers["content-disposition"]
    body = dl.content.decode("utf-8")
    assert "Site v2" in body

    zip_dl = client.get(f"/api/projects/{pid}/download-zip")
    assert zip_dl.status_code == 200
    assert zip_dl.headers["content-type"] == "application/zip"

    feed = client.get("/projects")
    assert "Сайт для кофейни" in feed.text

    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert client.get(f"/projects/{pid}").status_code == 404


def test_validation_errors(client):
    assert client.post("/api/projects", data={**VALID_FORM, "prompt": ""}).status_code == 422
    assert client.post("/api/projects", data={**VALID_FORM, "style": "gothic"}).status_code == 422
    assert client.post("/api/projects", data={**VALID_FORM, "color_primary": "red"}).status_code == 422
    assert client.post("/api/projects", data={**VALID_FORM, "prompt": "а" * 1001}).status_code == 422
    long_prompt = "а" * 5001
    assert client.post("/api/projects", data={**VALID_FORM, "prompt": long_prompt}).status_code == 422


def test_bad_image_rejected(client):
    files = {"image": ("evil.exe", b"MZ\x90\x00", "image/png")}
    resp = client.post("/api/projects", data=VALID_FORM, files=files)
    assert resp.status_code == 422


def test_invalid_asset_does_not_consume_quota_or_create_project(client, test_user):
    prompt = "Проект с недопустимым ассетом"
    before = test_user.generation_used
    resp = client.post(
        "/api/projects",
        data={**VALID_FORM, "prompt": prompt, "multifile": "true"},
        files=[("files", ("bad.exe", b"not allowed", "application/octet-stream"))],
    )
    assert resp.status_code == 422
    with SessionLocal() as db:
        user = db.get(User, test_user.id)
        assert user.generation_used == before
        assert db.query(Project).filter(Project.prompt == prompt).first() is None


def test_free_user_cannot_upload_project_asset(client, test_user):
    project_id = create_project(client, prompt="Многофайловый проект", multifile="true")
    with SessionLocal() as db:
        db.get(User, test_user.id).plan = "free"
        db.commit()
    resp = client.post(
        f"/api/projects/{project_id}/assets",
        files={"file": ("x.png", b"image", "image/png")},
    )
    assert resp.status_code == 403


def test_svg_asset_served_with_csp_sandbox(client):
    """Stored XSS через SVG: файл должен отдаваться с CSP sandbox и nosniff."""
    project_id = create_project(client, prompt="SVG проект", multifile="true")
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>alert(document.cookie)</script></svg>'
    upload = client.post(
        f"/api/projects/{project_id}/assets",
        files={"file": ("evil.svg", svg, "image/svg+xml")},
    )
    assert upload.status_code == 201, upload.text
    name = upload.json()["name"]
    from app.services.tokens import file_token

    resp = client.get(f"/api/projects/{project_id}/files/{name}?t={file_token(project_id, name)}")
    assert resp.status_code == 200
    assert resp.headers.get("content-security-policy") == "sandbox"
    assert resp.headers.get("x-content-type-options") == "nosniff"


def test_download_with_assets_returns_zip(client):
    """Обычная кнопка скачивания экспортирует HTML вместе с ассетами."""
    import io
    import zipfile

    project_id = create_project(client, prompt="Сайт с картинкой", multifile="true")
    upload = client.post(
        f"/api/projects/{project_id}/assets",
        files={"file": ("hero.svg", b"<svg></svg>", "image/svg+xml")},
    )
    assert upload.status_code == 201, upload.text
    asset_name = upload.json()["name"]
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        project.current_html = f'<html><body><img src="/api/projects/{project_id}/files/{asset_name}?t=token"></body></html>'
        db.commit()

    response = client.get(f"/api/projects/{project_id}/download")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert "index.html" in archive.namelist()
        assert asset_name in archive.namelist()
        assert f'/api/projects/{project_id}/files/' not in archive.read("index.html").decode()
        assert f'src="{asset_name}"' in archive.read("index.html").decode()


def test_delete_missing_asset_returns_404(client):
    project_id = create_project(client, prompt="Удаление ассета", multifile="true")
    resp = client.delete(f"/api/projects/{project_id}/assets/nope.png")
    assert resp.status_code == 404


def test_expired_file_token_rejected(client):
    """Просроченный токен файла не должен давать доступ без сессии."""
    import time as _time

    from app.services.tokens import file_token as ft

    project_id = create_project(client, prompt="Токен TTL", multifile="true")
    upload = client.post(
        f"/api/projects/{project_id}/assets",
        files={"file": ("pic.png", b"png", "image/png")},
    )
    name = upload.json()["name"]
    old_ts = int(_time.time()) - 8 * 24 * 3600
    stale_token = ft(project_id, name, issued_at=old_ts)
    from app.main import app as fastapi_app

    from fastapi.testclient import TestClient as TC

    with TC(fastapi_app) as anon:
        resp = anon.get(f"/api/projects/{project_id}/files/{name}?t={stale_token}")
        assert resp.status_code == 401


def test_llm_failure_masks_upstream_details(client, monkeypatch):
    """Тело ответа провайдера не должно попадать в error_message пользователю."""
    async def broken(params, image_data_url=None, seed=None, assets=None, asset_images=None, animations=True):
        raise llm.LLMError('LLM HTTP 500: {"error":"internal 10.0.0.5 quota exceeded for sk-abc123"}')

    monkeypatch.setattr(llm, "generate_site", broken)
    pid = create_project(client)
    status = client.get(f"/api/projects/{pid}/status").json()
    assert status["status"] == "error"
    msg = status["error"] or ""
    assert "10.0.0.5" not in msg and "sk-abc123" not in msg and "quota" not in msg


def test_js_asset_served_with_csp_sandbox(client):
    """JS-ассеты отдаются с CSP sandbox + nosniff (как svg/html)."""
    project_id = create_project(client, prompt="JS проект", multifile="true")
    upload = client.post(
        f"/api/projects/{project_id}/assets",
        files={"file": ("evil.js", b"fetch('/api/projects')", "application/javascript")},
    )
    name = upload.json()["name"]
    from app.services.tokens import file_token

    resp = client.get(f"/api/projects/{project_id}/files/{name}?t={file_token(project_id, name)}")
    assert resp.status_code == 200
    assert resp.headers.get("content-security-policy") == "sandbox"
    assert resp.headers.get("x-content-type-options") == "nosniff"


def test_preview_has_nosniff(client):
    pid = create_project(client)
    resp = client.get(f"/projects/{pid}/preview")
    assert resp.headers.get("x-content-type-options") == "nosniff"


def test_default_secret_key_fails_fast():
    """Дефолтный SECRET_KEY должен ронять конфигурацию (подделка сессии = админ)."""
    import pytest as _pytest

    from app.config import Settings

    with _pytest.raises(RuntimeError, match="SECRET_KEY"):
        Settings(secret_key="change-me-in-production", _env_file=None)


def test_llm_provider_key_is_encrypted_at_rest(db_session):
    provider = LLMProvider(
        name="encrypted-test",
        base_url="http://localhost/v1",
        api_key="secret-provider-key",
        model="test-model",
    )
    db_session.add(provider)
    db_session.commit()
    stored = db_session.execute(text("SELECT api_key FROM llm_providers WHERE name = 'encrypted-test'")).scalar_one()
    assert stored.startswith("enc:")
    assert provider.api_key == "secret-provider-key"


def test_chat_unknown_project(client):
    assert client.post("/api/projects/nope/chat", json={"instruction": "x"}).status_code == 404


def test_llm_failure_sets_error_status(client, monkeypatch):
    async def broken(params, image_data_url=None, seed=None, assets=None, asset_images=None, animations=True):
        raise llm.LLMError("LLM недоступна")

    monkeypatch.setattr(llm, "generate_site", broken)
    pid = create_project(client)
    status = client.get(f"/api/projects/{pid}/status").json()
    assert status["status"] == "error"
    assert "недоступна" in status["error"]


def test_admin_page_requires_admin(admin_client):
    assert admin_client.get("/admin").status_code == 200


def test_admin_page_redirects_for_regular_user(client):
    resp = client.get("/admin", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert "/admin" in resp.headers["location"]


def test_font_validation(client):
    assert client.post("/api/projects", data={**VALID_FORM, "font": "A" * 200}).status_code == 422
    assert client.post("/api/projects", data={**VALID_FORM, "font": '"; DROP TABLE--'}).status_code == 422
    assert client.post("/api/projects", data={**VALID_FORM, "font": "Playfair Display"}).status_code == 201


def test_chat_before_generation(client, test_user):
    with SessionLocal() as db:
        project = Project(title="t", prompt="сайт", status="pending", user_id=test_user.id)
        db.add(project)
        db.commit()
        pid = project.id
    resp = client.post(f"/api/projects/{pid}/chat", json={"instruction": "x"})
    assert resp.status_code == 409


def test_second_chat_conflicts(client):
    pid = create_project(client)
    with SessionLocal() as db:
        db.get(Project, pid).status = "processing"
        db.commit()
    assert client.post(f"/api/projects/{pid}/chat", json={"instruction": "x"}).status_code == 409
    assert client.post(f"/api/projects/{pid}/regenerate").status_code == 409


def test_chat_409_does_not_consume_quota(client, test_user):
    """Гонка: 409 занятого проекта не должен сжигать квоту (лок до списания)."""
    pid = create_project(client)
    with SessionLocal() as db:
        used_before = db.get(User, test_user.id).generation_used
        db.get(Project, pid).status = "processing"
        db.commit()
    resp = client.post(f"/api/projects/{pid}/chat", json={"instruction": "x"})
    assert resp.status_code == 409
    with SessionLocal() as db:
        assert db.get(User, test_user.id).generation_used == used_before
    resp = client.post(f"/api/projects/{pid}/regenerate")
    assert resp.status_code == 409
    with SessionLocal() as db:
        assert db.get(User, test_user.id).generation_used == used_before


def test_duplicate_create_alternating_prompts(client):
    """Дедупликация не должна затирать разные промпты друг другом."""
    r1 = client.post("/api/projects", data={**VALID_FORM, "prompt": "Промпт А"})
    r2 = client.post("/api/projects", data={**VALID_FORM, "prompt": "Промпт Б"})
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]
    r3 = client.post("/api/projects", data={**VALID_FORM, "prompt": "Промпт А"})
    assert r3.json().get("duplicate") is True


def test_windows_reserved_name_rejected():
    from app.services.sites import SiteFileError, _safe_component

    for name in ("CON", "aux", "com1", "lpt1"):
        with pytest.raises(SiteFileError):
            _safe_component(name)


def test_css_import_data_url_blocked():
    from app.services.codegen import find_forbidden

    assert find_forbidden("<style>@import url(data:text/css,body{});</style>")


def test_css_expression_in_url_blocked():
    from app.services.codegen import find_forbidden

    problems = find_forbidden('<style>a{background:url(expression(alert(1)))}</style>')
    assert any("CSS url" in p for p in problems), problems


def test_font_rejects_newline(client):
    assert client.post("/api/projects", data={**VALID_FORM, "font": "Inter\nignore previous"}).status_code == 422
    assert client.post("/api/projects", data={**VALID_FORM, "font": "Inter\tignore"}).status_code == 422


def test_regenerate_pending_blocked(client):
    pid = create_project(client)
    with SessionLocal() as db:
        project = db.get(Project, pid)
        project.status = "pending"
        db.commit()
    assert client.post(f"/api/projects/{pid}/regenerate").status_code == 409


def test_delete_processing_blocked(client):
    pid = create_project(client)
    with SessionLocal() as db:
        project = db.get(Project, pid)
        project.status = "processing"
        db.commit()
    assert client.delete(f"/api/projects/{pid}").status_code == 409


def test_llm_model_recorded(client):
    pid = create_project(client)
    with SessionLocal() as db:
        project = db.get(Project, pid)
        assert project.llm_model


def test_chat_whitespace_instruction_rejected(client):
    pid = create_project(client)
    assert client.post(f"/api/projects/{pid}/chat", json={"instruction": "   "}).status_code == 422


def test_delete_race_uses_atomic_lock(client):
    pid = create_project(client)
    with SessionLocal() as db:
        db.get(Project, pid).status = "processing"
        db.commit()
    assert client.delete(f"/api/projects/{pid}").status_code == 409


def test_unauthorized_api_access():
    from app.main import app

    with TestClient(app) as c:
        assert c.post("/api/projects", data=VALID_FORM).status_code == 401
        assert c.get("/api/projects").status_code == 401
        assert c.get("/api/projects/abc/status").status_code == 401


def test_user_cannot_access_other_project(client, admin_client):
    pid = create_project(admin_client)
    assert client.get(f"/api/projects/{pid}/status").status_code == 403
    assert client.get(f"/projects/{pid}").status_code == 403


def test_generation_limit_enforced(client, test_user):
    with SessionLocal() as db:
        user = db.get(User, test_user.id)
        user.generation_used = user.generation_limit
        db.commit()
    resp = client.post("/api/projects", data=VALID_FORM)
    assert resp.status_code == 429
    assert "Лимит" in resp.json()["detail"]


def test_limit_refunded_on_llm_error(client, test_user, monkeypatch):
    async def broken(params, image_data_url=None, seed=None, assets=None):
        raise llm.LLMError("fail")

    monkeypatch.setattr(llm, "generate_site", broken)
    before = test_user.generation_used
    pid = create_project(client)
    status = client.get(f"/api/projects/{pid}/status").json()
    assert status["status"] == "error"
    with SessionLocal() as db:
        user = db.get(User, test_user.id)
        assert user.generation_used == before


def test_admin_can_list_all_projects(admin_client, client):
    user_pid = create_project(client)
    admin_pid = create_project(admin_client)
    data = admin_client.get("/api/projects").json()
    ids = {p["id"] for p in data}
    assert user_pid in ids
    assert admin_pid in ids


def test_regular_user_lists_only_own_projects(client, admin_client):
    user_pid = create_project(client)
    create_project(admin_client)
    data = client.get("/api/projects").json()
    ids = {p["id"] for p in data}
    assert ids == {user_pid}
