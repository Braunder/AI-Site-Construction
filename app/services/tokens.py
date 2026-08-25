"""Подписанные токены доступа к файлам проекта.

Превью сайта открывается в sandbox-iframe с изолированным origin, поэтому
браузер не отправляет сессионную куку на запросы картинок из сгенерированного
HTML. Файлы отдаются по URL с HMAC-подписью (project_id + filename), что
позволяет iframe загружать ассеты без куки, не открывая файлы чужих проектов.
"""

import hashlib
import hmac

from app.config import get_settings


def file_token(project_id: str, filename: str) -> str:
    """HMAC-токен для пары (project_id, filename)."""
    key = get_settings().secret_key.encode()
    msg = f"{project_id}:{filename}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:32]


def verify_file_token(project_id: str, filename: str, token: str) -> bool:
    return hmac.compare_digest(file_token(project_id, filename), token or "")
