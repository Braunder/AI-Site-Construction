"""E2E против реальной локальной LLM. Запуск: RUN_LOCAL_E2E=1 pytest tests/test_e2e_local.py

Требуется запущенный сервер LLM (LLM_BASE_URL из .env, по умолчанию http://127.0.0.1:8080/v1).
"""

import asyncio
import base64
import os

import pytest

from app.config import get_settings
from app.services import llm
from app.services.codegen import check_generated_html

pytestmark = pytest.mark.skipif(os.environ.get("RUN_LOCAL_E2E") != "1", reason="задать RUN_LOCAL_E2E=1")

# Минимальный валидный PNG 1x1 (красный пиксель)
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

PARAMS = {
    "prompt": "Сайт-визитка для кофейни «Уют»: меню, часы работы, контакты.",
    "font": "Inter",
    "style": "minimalism",
    "color_primary": "#1f2937",
    "color_accent": "#b45309",
    "color_bg": "#ffffff",
}


def test_real_generation():
    html = asyncio.run(llm.generate_site(PARAMS))
    assert check_generated_html(html) == []


def test_real_generation_with_image(tmp_path):
    img = tmp_path / "ref.png"
    img.write_bytes(PNG_1PX)
    from app.services.images import to_base64_data_url

    data_url, _ = to_base64_data_url(img)
    html = asyncio.run(llm.generate_site(PARAMS, data_url))
    assert check_generated_html(html) == []


def test_real_edit():
    html = asyncio.run(llm.generate_site(PARAMS))
    edited = asyncio.run(llm.edit_site(html, "Добавь секцию «Отзывы» с двумя карточками"))
    assert check_generated_html(edited) == []
