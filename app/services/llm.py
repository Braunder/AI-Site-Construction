"""Клиент OpenAI-совместимого API (llama.cpp / LM Studio / OpenAI) для генерации сайтов."""

import asyncio
import json
import logging
import random
from dataclasses import dataclass

import httpx

from app.config import get_settings
from app.database import SessionLocal
from app.models import LLMProvider
from app.services.codegen import check_generated_html, extract_html
from app.services.patcher import PatchError, apply_operations

logger = logging.getLogger(__name__)
env_settings = get_settings()

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
8. Код компактный: без лишних комментариев и пустых строк.

ПРАВИЛА ВЁРСТКИ ИЗОБРАЖЕНИЙ (обязательно):
- Все <img> должны вписываться в свой контейнер: width: 100%; height: 100%; object-fit: cover; display: block.
- Задавай контейнеру фиксированную высоту (например .card-img { height: 220px; overflow: hidden; }) — картинка обрежется по краям, а не растянет макет.
- Никогда не задавай <img> ширину/высоту в пикселях больше контейнера и не используй height: auto без ограничений.
- Для адаптивности используй aspect-ratio или grid/flex с minmax, чтобы карточки были одинаковой высоты.
"""
# Расширение для многофайлового режима: ассеты пользователя + богатая интерактивность
MULTIFILE_EXTRA_PROMPT = """

РЕЖИМ ПОЛНОЦЕННОГО САЙТА (многофайловый):
9. Пользователю доступны загруженные им файлы (изображения, шрифты, стили, скрипты). Манифест файлов приведён ниже. Ссылайся на них по ОТНОСИТЕЛЬНЫМ путям из манифеста (например <img src="a1b2c3d4-photo.jpg">) — они лежат рядом с index.html.
10. Делай сайт живым и профессиональным: плавные CSS-анимации (transition/transform/keyframes), scroll-reveal эффекты, hover-состояния, интерактивные элементы на чистом JS (аккордеоны, табы, слайдеры, фильтры, счётчики, валидация форм).
11. Если по описанию нужен динамический контент (каталог, галерея, отзывы), генерируй его из встроенного JS-массива данных внутри <script> — без сервера.
12. Не выдумывай файлы, которых нет в манифесте. Если нужного файла нет — используй CSS/SVG-заглушку.
13. Шрифты из манифеста (kind: font) подключай через @font-face с путём из манифеста и указанным family:
    @font-face {{ font-family: '<family>'; src: url('<path>') format('woff2'); }}
    Затем используй font-family: '<family>' в нужных элементах."""

EDIT_SYSTEM_PROMPT = SYSTEM_PROMPT + """

ПРАВИЛА РЕДАКТИРОВАНИЯ (важно для скорости):
9. Тебе дают текущий HTML сайта и инструкцию по изменению.
10. Вноси ТОЛЬКО изменения, указанные в инструкции. Всё остальное скопируй из исходника БЕЗ ПЕРЕПИСЫВАНИЯ: сохраняй текст, классы, стили, структуру и комментарии один в один.
11. Не улучшай, не переформатируй и не сокращай то, что не касается инструкции. Это критично: чем меньше diff, тем лучше.
12. Верни ПОЛНЫЙ обновлённый HTML-документ целиком (от <!DOCTYPE html> до </html>) без markdown-обёрток и пояснений."""

# Премиум-качество для платных тарифов: уровень дорогих демо-страниц проекта
PREMIUM_QUALITY_PROMPT = """

УРОВЕНЬ КАЧЕСТВА (обязательно):
9. Сайт должен выглядеть как работа дорогого агентства, а не шаблон. Ориентиры стиля:
   — Уникальная типографическая пара с Google Fonts (дисплейный шрифт для заголовков + нейтральный для текста), крупная контрастная типографика.
   — Продуманная палитра: тёмная или светлая тема с ОДНИМ ярким акцентным цветом, никаких случайных радужных цветов.
   — Секции: hero с крупным заголовком и CTA, контентные блоки, блок цифр/фактов, контакты с финальным CTA.
10. Анимации и жизнь:
   — Появление элементов при скролле (IntersectionObserver + класс .visible, opacity/transform transition).
   — Hover-эффекты: подъём карточек, свечение акцентных элементов, плавные transition 0.2–0.35s.
   — Бегущая строка (marquee) с ключевыми офферами, если подходит тематике.
   — Sticky-навигация с backdrop-filter: blur и полупрозрачным фоном.
11. Интерактив на чистом JS: аккордеоны, табы, слайдеры, фильтры, счётчики, валидация форм — по смыслу сайта.
12. Динамический контент (каталог, галерея, отзывы) — из встроенного JS-массива данных внутри <script>, без сервера.
13. Детали дорогого вида: тонкие бордеры rgba, глубокие мягкие тени, скругления 12–20px, монохромные иконки/эмодзи вместо клипартов, много воздуха."""


@dataclass
class RuntimeLLMConfig:
    base_url: str
    api_key: str
    model: str
    timeout: float
    max_retries: int
    max_tokens: int


def get_llm_config() -> RuntimeLLMConfig:
    """Возвращает резервную конфигурацию локальной/основной LLM из env."""
    return RuntimeLLMConfig(
        base_url=env_settings.llm_base_url,
        api_key=env_settings.llm_api_key,
        model=env_settings.llm_model,
        timeout=env_settings.llm_timeout,
        max_retries=env_settings.llm_max_retries,
        max_tokens=env_settings.llm_max_tokens,
    )


def get_llm_configs() -> list[RuntimeLLMConfig]:
    """Возвращает включённые провайдеры по приоритету, либо legacy-конфиг."""
    with SessionLocal() as db:
        providers = db.query(LLMProvider).filter(LLMProvider.enabled.is_(True)).order_by(
            LLMProvider.priority.desc(), LLMProvider.id.asc()
        ).all()
    if providers:
        return [
            RuntimeLLMConfig(
                base_url=p.base_url,
                api_key=p.api_key,
                model=p.model,
                timeout=p.timeout,
                max_retries=p.max_retries,
                max_tokens=p.max_tokens,
            )
            for p in providers
        ]
    return [get_llm_config()]


class LLMError(RuntimeError):
    pass


class HTMLValidationError(LLMError):
    """LLM вернула невалидный/небезопасный HTML после всех попыток."""


def _user_prompt(params: dict, assets: list[dict] | None = None) -> str:
    style = STYLE_NAMES.get(params.get("style", ""), params.get("style", "минимализм"))
    text = (
        f"Создай одностраничный сайт по заданию.\n"
        f"Описание: {params['prompt']}\n"
        f"Стилевое направление: {style}.\n"
        f"Шрифт: {params.get('font', 'Inter')} (подключи с Google Fonts).\n"
        f"Основной цвет: {params.get('color_primary')}, акцентный: {params.get('color_accent')}, "
        f"фоновый: {params.get('color_bg')}.\n"
        f"Если приложено референсное изображение — используй его как ориентир по композиции, "
        f"цветам и настроению."
    )
    if assets:
        lines = "\n".join(f"- {a['path']} ({a['kind']})" for a in assets)
        text += (
            "\n\nДоступные файлы пользователя (вставляй указанный путь прямо в src/href):\n"
            f"{lines}\n"
            "Обязательно используй подходящие изображения в галерее/секциях сайта. "
            "Не заменяй их градиентами, placeholder-картинками или data URL."
        )
    return text


def _build_messages(
    params: dict,
    image_data_url: str | None = None,
    assets: list[dict] | None = None,
    asset_images: list[dict] | None = None,
    animations: bool = True,
) -> list[dict]:
    system = SYSTEM_PROMPT
    if not animations:
        # Free-тариф: без анимаций и интерактива, статичный аккуратный сайт
        system += "\n9. Сайт статичный: без анимаций, переходов и JS-интерактива. Только чистая адаптивная вёрстка."
    else:
        # Standard/Premium: премиум-качество уровня демо-страниц
        system += PREMIUM_QUALITY_PROMPT
    if assets:
        system += MULTIFILE_EXTRA_PROMPT
        if asset_images:
            system += (
                "\n14. Загруженные изображения приложены как картинки с подписями "
                "[файл: <путь>]. Учитывай их СОДЕРЖИМОЕ и вставляй именно указанный путь "
                "в src при выборе места размещения "
                "(фото еды — в меню, интерьер — в «О нас», портреты — в команду и т.п.)."
            )
    content: list[dict] = [{"type": "text", "text": _user_prompt(params, assets)}]
    if image_data_url:
        content.append({"type": "image_url", "image_url": {"url": image_data_url}})
    for img in asset_images or []:
        content.append({"type": "text", "text": f"[файл: {img['path']}]"})
        content.append({"type": "image_url", "image_url": {"url": img["data_url"]}})
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ]


def _build_edit_messages(current_html: str, instruction: str) -> list[dict]:
    return [
        {"role": "system", "content": EDIT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Текущий HTML сайта:\n{current_html}\n\n"
                f"Инструкция по изменению: {instruction}\n\n"
                "Верни полный HTML, изменив только то, что указано в инструкции."
            ),
        },
    ]


async def _chat_completion_with_config(
    cfg: RuntimeLLMConfig,
    messages: list[dict],
    seed: int | None = None,
    max_tokens_override: int | None = None,
) -> str:
    """Запрос к одному провайдеру с его retry."""
    payload: dict = {
        "model": cfg.model,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": max_tokens_override or cfg.max_tokens,
        "stream": False,
    }
    if seed is not None:
        payload["seed"] = seed

    url = f"{cfg.base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {cfg.api_key}"}
    last_exc: Exception | None = None

    for attempt in range(cfg.max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=cfg.timeout) as client:
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
            if attempt < cfg.max_retries:
                await asyncio.sleep(2 * (attempt + 1))
        except LLMError as exc:
            # 5xx и пустые ответы — пробуем ещё раз; 4xx — сразу наружу
            last_exc = exc
            if "HTTP 5" not in str(exc) and "пустой" not in str(exc):
                raise
            if attempt < cfg.max_retries:
                # 503 у llama.cpp — модель догружается, это может занять десятки секунд
                wait = 20 if "HTTP 503" in str(exc) else 2 * (attempt + 1)
                logger.warning("LLM %s (попытка %d), повтор через %d c", exc, attempt + 1, wait)
                await asyncio.sleep(wait)
    raise LLMError(f"Не удалось получить ответ от LLM: {last_exc}")


async def _chat_completion(
    messages: list[dict], seed: int | None = None, max_tokens_override: int | None = None
) -> str:
    """Запрос к провайдерам по приоритету с fallback при ошибке."""
    errors: list[str] = []
    for index, cfg in enumerate(get_llm_configs(), 1):
        try:
            result = await _chat_completion_with_config(cfg, messages, seed, max_tokens_override)
            if index > 1:
                logger.info("LLM fallback сработал: использован провайдер #%d (%s)", index, cfg.model)
            return result
        except (LLMError, httpx.TransportError, httpx.TimeoutException) as exc:
            errors.append(f"{cfg.model}: {exc}")
            logger.warning("Провайдер LLM #%d недоступен, пробуем следующий: %s", index, exc)
    raise LLMError("Все LLM-провайдеры недоступны: " + " | ".join(errors))


async def _generate_checked(
    messages: list[dict], seed: int | None, max_tokens_override: int | None = None
) -> str:
    """Генерация + извлечение + автопроверка; при неудаче — перегенерация (retry с новым seed)."""
    cfg = get_llm_config()
    errors: list[str] = []
    for attempt in range(cfg.max_retries + 1):
        raw = await _chat_completion(
            messages,
            seed=None if seed is None else seed + attempt,
            max_tokens_override=max_tokens_override,
        )
        html = extract_html(raw)
        if html is None:
            errors = ["не удалось извлечь HTML из ответа"]
        else:
            errors = check_generated_html(html)
            if not errors:
                return html
        logger.warning("Попытка %d: невалидный HTML: %s", attempt + 1, errors)
    raise HTMLValidationError("LLM выдала некорректный HTML: " + "; ".join(errors))


async def generate_site(
    params: dict,
    image_data_url: str | None = None,
    seed: int | None = None,
    assets: list[dict] | None = None,
    asset_images: list[dict] | None = None,
    animations: bool = True,
) -> str:
    """Генерирует сайт по параметрам формы (+ референс, ассеты, vision-изображения)."""
    return await _generate_checked(
        _build_messages(params, image_data_url, assets, asset_images, animations), seed
    )


async def regenerate_site(
    params: dict,
    image_data_url: str | None = None,
    assets: list[dict] | None = None,
    asset_images: list[dict] | None = None,
    animations: bool = True,
) -> str:
    """Полная перегенерация со случайным seed."""
    return await generate_site(
        params, image_data_url, seed=random.randint(0, 2**31 - 1),
        assets=assets, asset_images=asset_images, animations=animations,
    )


async def edit_site(current_html: str, instruction: str) -> str:
    """Правка текущего HTML по инструкции пользователя. Возвращает новый HTML.

    Стратегия (быстрый путь → fallback):
    1) Tool calling: модель декларирует точечные операции, патчер применяет их
       детерминированно (десятки токенов вместо всего документа).
    2) Если tools недоступны/неудачны — классический путь: полный документ.
    """
    try:
        return await _edit_via_tools(current_html, instruction)
    except ToolEditError as exc:
        logger.info("Tool-правка не удалась (%s); fallback на полный документ", exc)
    # ~2 символа на токен для кода + 20% запас на расширения
    budget = int(len(current_html) / 2 * 1.2) + 1024
    cfg = get_llm_config()
    return await _generate_checked(
        _build_edit_messages(current_html, instruction),
        seed=None,
        max_tokens_override=min(budget, cfg.max_tokens),
    )


# --- Tool calling для правок ---

class ToolEditError(LLMError):
    pass


EDIT_TOOLS = [
    {"type": "function", "function": {
        "name": "replace_text",
        "description": "Заменить уникальный фрагмент текста/HTML. Фрагмент должен встречаться ровно 1 раз.",
        "parameters": {"type": "object", "properties": {
            "find": {"type": "string", "description": "Точный фрагмент для замены"},
            "replace": {"type": "string", "description": "Новый фрагмент"},
        }, "required": ["find", "replace"]},
    }},
    {"type": "function", "function": {
        "name": "set_css_property",
        "description": "Изменить или добавить CSS-свойство в правиле по селектору (внутри <style>).",
        "parameters": {"type": "object", "properties": {
            "selector": {"type": "string", "description": "Селектор или его подстрока, напр. 'header'"},
            "property": {"type": "string", "description": "CSS-свойство, напр. 'background-color'"},
            "value": {"type": "string", "description": "Новое значение, напр. '#111827'"},
        }, "required": ["selector", "property", "value"]},
    }},
    {"type": "function", "function": {
        "name": "insert_before_end",
        "description": "Вставить HTML-фрагмент перед закрывающим тегом элемента.",
        "parameters": {"type": "object", "properties": {
            "selector": {"type": "string", "description": "'tag#id' или просто 'tag', напр. 'div#gallery' или 'body'"},
            "html": {"type": "string", "description": "HTML для вставки"},
        }, "required": ["selector", "html"]},
    }},
    {"type": "function", "function": {
        "name": "delete_element",
        "description": "Удалить элемент целиком (открывающий и закрывающий теги).",
        "parameters": {"type": "object", "properties": {
            "selector": {"type": "string", "description": "'tag#id' или 'tag'"},
        }, "required": ["selector"]},
    }},
    {"type": "function", "function": {
        "name": "rewrite_full",
        "description": "Полная перезапись документа. Только если точечные операции невозможны!",
        "parameters": {"type": "object", "properties": {
            "html": {"type": "string", "description": "Полный HTML-документ от <!DOCTYPE html> до </html>"},
        }, "required": ["html"]},
    }},
]

TOOLS_SYSTEM_PROMPT = (
    "Ты редактируешь существующий HTML-сайт. Тебе дан текущий документ и инструкция.\n"
    "Вызови ОДИН или НЕСКОЛЬКО инструментов подряд, чтобы выполнить инструкцию.\n"
    "Правила:\n"
    "- Предпочитай точечные инструменты (replace_text/set_css_property/insert_before_end/delete_element).\n"
    "- rewrite_full — только в крайнем случае, когда точечные правки невозможны.\n"
    "- 'find' в replace_text должен быть уникальным фрагментом (встречается ровно 1 раз).\n"
    "- Не изменяй то, что не касается инструкции."
)


def _parse_tool_calls(data: dict) -> list[dict]:
    """Извлекает операции из ответа с tool_calls."""
    message = data["choices"][0]["message"]
    calls = message.get("tool_calls") or []
    operations: list[dict] = []
    for call in calls:
        fn = call.get("function") or {}
        name = fn.get("name", "")
        raw_args = fn.get("arguments", "{}")
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError as exc:
                raise ToolEditError(f"невалидный JSON аргументов у '{name}': {exc}") from exc
        else:
            args = raw_args or {}
        operations.append({"name": name, "arguments": args})
    if not operations:
        raise ToolEditError("модель не вызвала ни одного инструмента")
    return operations


async def _chat_completion_raw(payload_extra: dict | None = None) -> dict:
    """Tool-calling запрос с fallback по включённым провайдерам."""
    errors: list[str] = []
    for index, cfg in enumerate(get_llm_configs(), 1):
        payload: dict = {
            "model": cfg.model,
            "temperature": 0.3,
            "max_tokens": cfg.max_tokens,
            "stream": False,
        }
        if payload_extra:
            payload.update(payload_extra)
        url = f"{cfg.base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {cfg.api_key}"}
        try:
            async with httpx.AsyncClient(timeout=cfg.timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                raise ToolEditError(f"LLM HTTP {resp.status_code}: {resp.text[:200]}")
            if index > 1:
                logger.info("Tool-calling fallback: использован провайдер #%d (%s)", index, cfg.model)
            return resp.json()
        except (httpx.TransportError, httpx.TimeoutException, ToolEditError) as exc:
            errors.append(f"{cfg.model}: {exc}")
            logger.warning("Tool-calling провайдер #%d недоступен: %s", index, exc)
    raise ToolEditError("Все LLM-провайдеры недоступны: " + " | ".join(errors))


async def _edit_via_tools(current_html: str, instruction: str) -> str:
    """Быстрый путь правки: tool calls + детерминированное применение операций."""
    cfg = get_llm_config()
    messages = [
        {"role": "system", "content": TOOLS_SYSTEM_PROMPT},
        {"role": "user", "content": f"Текущий HTML сайта:\n{current_html}\n\nИнструкция: {instruction}"},
    ]
    data = await _chat_completion_raw({
        "messages": messages,
        "tools": EDIT_TOOLS,
        "tool_choice": "auto",
    })
    operations = _parse_tool_calls(data)

    # Полная перезапись — проверяем как обычную генерацию
    if len(operations) == 1 and operations[0]["name"] == "rewrite_full":
        html = operations[0]["arguments"].get("html", "")
        errors = check_generated_html(html)
        if errors:
            raise ToolEditError("rewrite_full вернул невалидный HTML: " + "; ".join(errors))
        return html

    result = apply_operations(current_html, operations)
    # Вставленные фрагменты могут содержать опасный код — проверяем итог целиком
    errors = check_generated_html(result.html)
    if errors:
        raise ToolEditError("результат операций не прошёл санитайзер: " + "; ".join(errors))
    logger.info("Tool-правка применена: %s", ", ".join(result.applied))
    return result.html
