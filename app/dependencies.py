"""Аутентификация, авторизация и лимиты генераций."""

import time
from collections import defaultdict
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request
from sqlalchemy import update
from sqlalchemy.orm import Session
from starlette.responses import RedirectResponse

from app.database import SessionLocal
from app.models import Project, User

if TYPE_CHECKING:
    pass

# Простая in-memory защита от брутфорса логина: 10 попыток за 15 минут с одного IP.
_login_attempts: dict[str, list[float]] = defaultdict(list)
_LOGIN_MAX_ATTEMPTS = 10
_LOGIN_WINDOW_SECONDS = 15 * 60


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_login_rate(request: Request) -> None:
    """Возвращает 429, если с одного IP слишком много попыток входа."""
    ip = _client_ip(request)
    now = time.time()
    attempts = [t for t in _login_attempts[ip] if now - t < _LOGIN_WINDOW_SECONDS]
    attempts.append(now)
    _login_attempts[ip] = attempts
    if len(attempts) > _LOGIN_MAX_ATTEMPTS:
        raise HTTPException(429, "Слишком много попыток входа. Попробуйте позже.")


def get_optional_user(request: Request) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    with SessionLocal() as db:
        user = db.get(User, user_id)
    return user


def require_user(request: Request) -> User:
    user = get_optional_user(request)
    if user is None:
        raise HTTPException(401, "Требуется авторизация")
    return user


def require_admin(user: User = require_user) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Доступ только для администратора")
    return user


def require_admin_dependency(request: Request) -> User:
    user = require_user(request)
    if not user.is_admin:
        raise HTTPException(403, "Доступ только для администратора")
    return user


def redirect_to_login(request: Request) -> RedirectResponse:
    return RedirectResponse(url=f"/login?next={request.url.path}", status_code=302)


def get_project_for_user(project_id: str, user: User, db: Session) -> Project:
    """Возвращает проект, если он принадлежит пользователю или пользователь — админ."""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Проект не найден")
    if not user.is_admin and project.user_id != user.id:
        raise HTTPException(403, "Нет доступа к проекту")
    return project


def has_generation_quota(user: User) -> bool:
    """Проверяет, остались ли у пользователя генерации. Админы и unlimited (-1) проходят."""
    if user.is_admin or user.generation_limit == -1:
        return True
    return user.generation_used < user.generation_limit


def consume_generation(user_id: str, db: Session) -> bool:
    """Атомарно увеличивает счётчик использованных генераций, если лимит не исчерпан.
    Возвращает True, если списание прошло успешно.
    """
    result = db.execute(
        update(User)
        .where(
            User.id == user_id,
            (User.is_admin == True) | (User.generation_limit == -1) | (User.generation_used < User.generation_limit),
        )
        .values(generation_used=User.generation_used + 1)
    )
    db.commit()
    return result.rowcount == 1


def refund_generation(user_id: str, db: Session) -> None:
    """Возвращает генерацию при ошибке фоновой задачи."""
    db.execute(
        update(User)
        .where(User.id == user_id, User.generation_used > 0)
        .values(generation_used=User.generation_used - 1)
    )
    db.commit()
