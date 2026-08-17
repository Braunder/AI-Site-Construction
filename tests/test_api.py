"""API-тесты с мокнутой LLM (фоновые задачи TestClient выполняет синхронно)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import llm

FAKE_HTML = (
    "<!DOCTYPE html><html><head><title>t</title><style>body{margin:0}"
    + "x" * 200
    + "</style></head><body><h1>Site</h1></body></html>"
)
FAKE_HTML_V2 = FAKE_HTML.replace("<h1>Site</h1>", "<h1>Site v2</h1>")

VALID_FORM = {
    "prompt": "Сайт для кофейни",
    "font": "Inter",
    "style": "minimalism",
    "color_primary": "#1f2937",
    "color_accent": "#3b82f6",
    "color_bg": "#ffffff",
}


@pytest.fixture
def client(monkeypatch):
    async def fake_generate(params, image_data_url=None, seed=None):
        return FAKE_HTML

    async def fake_edit(current_html, instruction):
        return FAKE_HTML_V2

    monkeypatch.setattr(llm, "generate_site", fake_generate)
    monkeypatch.setattr(llm, "regenerate_site", fake_generate)
    monkeypatch.setattr(llm, "edit_site", fake_edit)
    with TestClient(app) as c:
        yield c


def create_project(client, **overrides) -> str:
    data = {**VALID_FORM, **overrides}
    resp = client.post("/api/projects", data=data)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_index_page(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Генерация сайта" in resp.text


def test_full_cycle(client):
    # создание + генерация (фон выполняется синхронно в TestClient)
    pid = create_project(client)

    status = client.get(f"/api/projects/{pid}/status").json()
    assert status == {"status": "done", "error": None}

    # предпросмотр с sandbox-заголовком
    preview = client.get(f"/projects/{pid}/preview")
    assert preview.status_code == 200
    assert preview.headers["content-security-policy"] == "sandbox allow-scripts"
    assert FAKE_HTML in preview.text

    # страница детали
    detail = client.get(f"/projects/{pid}")
    assert detail.status_code == 200
    assert 'sandbox="allow-scripts"' in detail.text

    # чат-правка
    chat = client.post(f"/api/projects/{pid}/chat", json={"instruction": "сделай шапку темнее"})
    assert chat.status_code == 202
    assert FAKE_HTML_V2 in client.get(f"/projects/{pid}/preview").text

    # история: генерация + правка
    detail = client.get(f"/projects/{pid}")
    assert "сделай шапку темнее" in detail.text

    # скачивание
    dl = client.get(f"/api/projects/{pid}/download")
    assert dl.status_code == 200
    assert "attachment" in dl.headers["content-disposition"]
    assert dl.content.decode("utf-8") == FAKE_HTML_V2

    # лента
    feed = client.get("/projects")
    assert "Сайт для кофейни" in feed.text

    # удаление
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
    async def broken(params, image_data_url=None, seed=None):
        raise llm.LLMError("LLM недоступна")

    monkeypatch.setattr(llm, "generate_site", broken)
    pid = create_project(client)
    status = client.get(f"/api/projects/{pid}/status").json()
    assert status["status"] == "error"
    assert "недоступна" in status["error"]


def test_admin_page(client):
    assert client.get("/admin").status_code == 200
