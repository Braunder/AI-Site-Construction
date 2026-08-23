"""Health-check LLM-провайдеров без расхода токенов.

Проверка идёт через GET /models — служебный запрос, не создающий генераций.
Опрос выполняется на сервере раз в 5 минут (фоновая задача в lifespan),
результат кэшируется и мгновенно отдаётся клиентам.
"""

import asyncio
import logging
import time

import httpx

from app.services.llm import get_llm_configs

logger = logging.getLogger(__name__)

# Кэш статуса: {"online": bool, "provider": str|None, "models": int, "checked": int, "ts": float}
_status: dict = {"online": False, "provider": None, "models": 0, "checked": 0, "ts": 0.0}
CHECK_INTERVAL_SECONDS = 5 * 60  # опрос раз в 5 минут


def get_cached_status() -> dict:
    """Последний результат серверного опроса (без обращения к LLM)."""
    return dict(_status)


async def check_llm_health(timeout: float = 5.0) -> dict:
    """Опрашивает включённые провайдеры по приоритету и обновляет кэш.

    Провайдер считается доступным, если /models отвечает 200.
    """
    configs = get_llm_configs()
    checked = 0
    result: dict = {"online": False, "provider": None, "models": 0, "checked": 0}
    for cfg in configs:
        checked += 1
        url = f"{cfg.base_url.rstrip('/')}/models"
        headers = {"Authorization": f"Bearer {cfg.api_key}"}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("data") if isinstance(data, dict) else None
                count = len(models) if isinstance(models, list) else 0
                result = {"online": True, "provider": cfg.model, "models": count, "checked": checked}
                break
            logger.debug("Health %s: HTTP %s", cfg.model, resp.status_code)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            logger.debug("Health %s недоступен: %s", cfg.model, exc)

    result["ts"] = time.time()
    _status.clear()
    _status.update(result)
    return dict(_status)


async def _health_loop() -> None:
    """Фоновый цикл: проверка сразу при старте, затем раз в 5 минут."""
    while True:
        try:
            await check_llm_health()
        except Exception:  # noqa: BLE001 — цикл не должен умирать
            logger.warning("Ошибка health-check LLM", exc_info=True)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


def start_health_task() -> asyncio.Task:
    """Создаёт фоновую задачу опроса (вызывается из lifespan)."""
    return asyncio.create_task(_health_loop())
