"""Создание первого администратора.

Пример:
    python scripts/create_admin.py --username admin --password secret
"""

import argparse
import sys

from app.database import SessionLocal
from app.models import User
from app.services.auth import hash_password


def create_admin(username: str, password: str) -> User:
    with SessionLocal() as db:
        existing = db.query(User).filter(User.username == username).first()
        if existing:
            if existing.is_admin:
                print(f"Администратор '{username}' уже существует.")
                return existing
            raise ValueError(f"Пользователь '{username}' уже существует, но не админ.")

        user = User(
            username=username,
            hashed_password=hash_password(password),
            is_admin=True,
            generation_limit=-1,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def main() -> int:
    parser = argparse.ArgumentParser(description="Создание администратора")
    parser.add_argument("--username", required=True, help="Логин администратора")
    parser.add_argument("--password", required=True, help="Пароль администратора")
    args = parser.parse_args()

    try:
        user = create_admin(args.username, args.password)
        print(f"Администратор создан: {user.username} ({user.id})")
        return 0
    except Exception as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
