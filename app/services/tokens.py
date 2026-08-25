"""Подписанные токены доступа к файлам проекта.

Превью сайта открывается в sandbox-iframe с изолированным origin, поэтому
браузер не отправляет сессионную куку на запросы картинок из сгенерированного
HTML. Файлы отдаются по URL с HMAC-подписью (project_id + filename), что
позволяет iframe загружать ассеты без куки, не открывая файлы чужих проектов.
Токен содержит timestamp выпуска и проверяется на TTL (по умолчанию 7 дней),
поэтому утёкшая ссылка перестаёт работать по истечении срока.
"""

import hashlib
import hmac
import time

from app.config import get_settings

TOKEN_TTL_SECONDS = 60 * 60  # 1 час: утёкшая ссылка быстро теряет силу


def file_token(project_id: str, filename: str, issued_at: int | None = None) -> str:
    """HMAC-токен с меткой времени для пары (project_id, filename)."""
    key = get_settings().secret_key.encode()
    ts = int(issued_at if issued_at is not None else time.time())
    msg = f"{ts}:{project_id}:{filename}".encode()
    sig = hmac.new(key, msg, hashlib.sha256).hexdigest()[:32]
    return f"{ts}-{sig}"


def verify_file_token(project_id: str, filename: str, token: str) -> bool:
    """Проверяет подпись и срок действия токена."""
    value = token or ""
    ts_str, sep, sig = value.partition("-")
    if not sep or not ts_str.isdigit():
        return False
    try:
        issued_at = int(ts_str)
    except ValueError:
        return False
    if time.time() - issued_at > TOKEN_TTL_SECONDS:
        return False
    expected = file_token(project_id, filename, issued_at).partition("-")[2]
    return hmac.compare_digest(expected, sig)
