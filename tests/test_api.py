"""API-тесты с мокнутой LLM (фоновые задачи TestClient выполняет синхронно)."""

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Project, User
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

    feed = client.get("/projects")
    assert "Сайт для кофейни" in feed.text

    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert client.get(f"/projects/{pid}").status_code == 404


def test_validation_errors(client):
    assert client.post("/api/projects", data={**VALID_FORM, "prompt": ""}).status_code == 422
    assert client.post("/api/projects", data={**VALID_FORM, "style": "gothic"}).status_code == 422
    assert client.post("/api/projects", data={**VALID_FORM, "color_primary": "red"}).status_code == 422
    long_prompt = "а" * 5001
    assert client.post("/api/projects", data={**VALID_FORM, "prompt": long_prompt}).status_code == 422


def test_bad_image_rejected(client):
    files = {"image": ("evil.exe", b"MZ\x90\x00", "image/png")}
    resp = client.post("/api/projects", data=VALID_FORM, files=files)
    assert resp.status_code == 422


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
