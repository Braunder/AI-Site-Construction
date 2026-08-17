"""Клиент OpenAI-совместимого API (llama.cpp / LM Studio / OpenAI) для генерации сайтов."""

import asyncio
import logging
import random

import httpx

from app.config import get_settings
from app.services.codegen import check_generated_html, extract_html

logger = logging.getLogger(__name__)
settings = get_settings()

STYLE_NAMES = {
    "minimalism": "минимализм",
    "brutalism": "брутализм",
    "corporate": "корпоративный",
    "creative": "креативный/яркий",
    "dark": "тёмная тема",
    "elegant": "элегантный/премиум",
}

SYSTEM_PROMPT = """Ты — профессиональный веб-дизайнер и фронтенд-разработчик. Создаёшь красивые, современные, адаптивные одностраничные сайты.

ЖЁСТКИЕ ПРАВИЛА ВЫВОДА:
1. Ответ — ТОЛЬКО один полный HTML-документ. Без пояснений, без markdown-обёрток.
2. Документ начинается с <!DOCTYPE html> и заканчивается </html>.
3. Весь CSS — внутри <style> в <head>, весь JS — внутри <script> перед </body>. Никаких внешних файлов.
4. ЗАПРЕЩЕНО: теги <iframe>, <object>, <embed>, внешние картинки с посторонних сайтов, ссылки вида javascript:. Вместо растровых картинок используй CSS-градиенты, SVG-иконки и фигуры.
5. Можно подключать только Google Fonts и Bootstrap/Tailwind с CDN.
6. Сайт должен быть адаптивным (mobile-first) и визуально завершённым: шапка, основные секции, подвал.
7. Тексты — осмысленные, на языке описания заказчика (по умолчанию русский), без lorem ipsum.
8. Код компактный: без лишних комментариев и пустых строк."""

EDIT_SYSTEM_PROMPT = SYSTEM_PROMPT + """

9. Тебе дают текущий HTML сайта и инструкцию по изменению. Верни ПОЛНЫЙ обновлённый HTML-документ целиком с учётом инструкции, сохранив всё остальное."""


class LLMError(RuntimeError):
    pass


class HTMLValidationError(LLMError):
    """LLM вернула невалидный/небезопасный HTML после всех попыток."""


def _user_prompt(params: dict) -> str:
    style = STYLE_NAMES.get(params.get("style", ""), params.get("style", "минимализм"))
    return (
        f"Создай одностраничный сайт по заданию.\n"
        f"Описание: {params['prompt']}\n"
        f"Стилевое направление: {style}.\n"
        f"Шрифт: {params.get('font', 'Inter')} (подключи с Google Fonts).\n"
        f"Основной цвет: {params.get('color_primary')}, акцентный: {params.get('color_accent')}, "
        f"фоновый: {params.get('color_bg')}.\n"
        f"Если приложено референсное изображение — используй его как ориентир по композиции, "
        f"цветам и настроению."
    )


def _build_messages(params: dict, image_data_url: str | None) -> list[dict]:
    content: list[dict] = [{"type": "text", "text": _user_prompt(params)}]
    if image_data_url:
        content.append({"type": "image_url", "image_url": {"url": image_data_url}})
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


def _build_edit_messages(current_html: str, instruction: str) -> list[dict]:
    return [
        {"role": "system", "content": EDIT_SYSTEM_PROMPT},
        {"role": "user", "content": f"Текущий HTML сайта:\n{current_html}\n\nИнструкция по изменению: {instruction}"},
    ]


async def _chat_completion(messages: list[dict], seed: int | None = None) -> str:
    """Один вызов chat/completions с retry при сетевых ошибках и 5xx."""
    payload: dict = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": settings.llm_max_tokens,
        "stream": False,
    }
    if seed is not None:
        payload["seed"] = seed

    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
    last_exc: Exception | None = None

    for attempt in range(settings.llm_max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code >= 500:
                raise LLMError(f"LLM HTTP {resp.status_code}: {resp.text[:300]}")
            if resp.status_code != 200:
                raise LLMError(f"LLM HTTP {resp.status_code}: {resp.text[:300]}") from None
            data = resp.json()
            content = data["choices"][0]["message"].get("content") or ""
            finish = data["choices"][0].get("finish_reason")
            if finish == "length":
                logger.warning("LLM обрезала ответ по max_tokens")
            if not content.strip():
                raise LLMError("LLM вернула пустой ответ")
            return content
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            last_exc = exc
            logger.warning("LLM недоступна (попытка %d): %s", attempt + 1, exc)
            if attempt < settings.llm_max_retries:
                await asyncio.sleep(2 * (attempt + 1))
        except LLMError as exc:
            # 5xx и пустые ответы — пробуем ещё раз; 4xx — сразу наружу
            last_exc = exc
            if "HTTP 5" not in str(exc) and "пустой" not in str(exc):
                raise
            if attempt < settings.llm_max_retries:
                await asyncio.sleep(2 * (attempt + 1))
    raise LLMError(f"Не удалось получить ответ от LLM: {last_exc}")


async def _generate_checked(messages: list[dict], seed: int | None) -> str:
    """Генерация + извлечение + автопроверка; при неудаче — перегенерация (retry с новым seed)."""
    errors: list[str] = []
    for attempt in range(settings.llm_max_retries + 1):
        raw = await _chat_completion(messages, seed=None if seed is None else seed + attempt)
        html = extract_html(raw)
        if html is None:
            errors = ["не удалось извлечь HTML из ответа"]
        else:
            errors = check_generated_html(html)
            if not errors:
                return html
        logger.warning("Попытка %d: невалидный HTML: %s", attempt + 1, errors)
    raise HTMLValidationError("LLM выдала некорректный HTML: " + "; ".join(errors))


async def generate_site(params: dict, image_data_url: str | None = None, seed: int | None = None) -> str:
    """Генерирует сайт по параметрам формы (+ опциональному референсу). Возвращает HTML."""
    return await _generate_checked(_build_messages(params, image_data_url), seed)


async def regenerate_site(params: dict, image_data_url: str | None = None) -> str:
    """Полная перегенерация со случайным seed."""
    return await generate_site(params, image_data_url, seed=random.randint(0, 2**31 - 1))


async def edit_site(current_html: str, instruction: str) -> str:
    """Правка текущего HTML по инструкции пользователя. Возвращает новый HTML."""
    return await _generate_checked(_build_edit_messages(current_html, instruction), seed=None)
