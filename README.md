# AI Site Construction (MVP)

Веб-сервис для генерации одностраничных сайтов с помощью LLM: заполняете форму
(описание, стиль, цвета, шрифт, референс-картинка) — получаете готовый HTML-сайт
с предпросмотром, чатом для правок и скачиванием.

Стек: FastAPI, SQLAlchemy + Alembic, SQLAdmin, Jinja2 + Bootstrap 5, httpx.

## Требования

- Python 3.11+
- Доступ к OpenAI-совместимому API: OpenAI, либо локальная LLM
  (llama.cpp `llama-server`, LM Studio и т.п.). Для работы с референсными
  изображениями модель должна быть мультимодальной.

## Установка и запуск

```bash
python -m venv .venv
# Windows (Git Bash):
source .venv/Scripts/activate
# Linux/macOS: source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env   # и отредактировать под себя

# миграции БД (SQLite; таблицы также создаются автоматически при старте)
alembic upgrade head

# создание первого администратора (обязательно перед входом в /admin)
PYTHONPATH=. python scripts/create_admin.py --username admin --password <пароль>

uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Открыть http://127.0.0.1:8000/login — форма входа.  
После авторизации доступны генерация (`/`), лента проектов (`/projects`) и скачивание.  
Админка: http://127.0.0.1:8000/admin — только для пользователей с `is_admin=True`.

### Пользователи и лимиты

- Администратор создаёт учётные записи через `/admin` → **Пользователи**.
- У каждого пользователя есть лимит генераций (`generation_limit`). Каждый запрос к LLM
  (создание сайта, перегенерация, чат-правка) уменьшает счётчик на 1.
- `generation_limit = -1` означает безлимитный доступ (по умолчанию для администратора).
- Пользователь видит только свои проекты; администратор видит все.

### LLM-провайдеры

В `/admin` → **LLM-провайдеры** администратор может добавить несколько
OpenAI-совместимых LLM, включать и выключать их, задавать приоритет, URL, токен,
модель, таймаут, retries и max_tokens. Значения применяются на лету.
При ошибке включённого провайдера сервис автоматически пробует следующий.
Если провайдеры не добавлены, используется локальная/резервная LLM из `.env`.

## Настройка LLM (`.env`)

| Переменная | Назначение |
|---|---|
| `LLM_BASE_URL` | Базовый URL OpenAI-совместимого API (`.../v1`) |
| `LLM_API_KEY` | Ключ API (для локальных серверов — любая строка) |
| `LLM_SECRETS_KEY` | Отдельный Fernet-ключ для шифрования ключей провайдеров в БД; в production обязателен |
| `LLM_MODEL` | Имя модели (для OpenAI — например `gpt-4o`) |
| `LLM_TIMEOUT` | Таймаут запроса к LLM, сек (локальные модели медленные) |
| `LLM_MAX_RETRIES` | Повторные попытки при сбое / невалидном HTML |
| `LLM_MAX_TOKENS` | Лимит токенов ответа |
| `DATABASE_URL` | Строка подключения SQLAlchemy |
| `UPLOAD_DIR` | Каталог для загруженных изображений |
| `MAX_UPLOAD_MB` | Максимальный размер изображения |
| `SECRET_KEY` | Секрет сессий (в production — заменить!) |
| `SESSION_COOKIE_SECURE` | `True` — cookie только по HTTPS (в production включить) |
| `CORS_ORIGINS` | Разрешённые origins через запятую |

Для OpenAI: `LLM_BASE_URL=https://api.openai.com/v1`, `LLM_API_KEY=sk-...`,
`LLM_MODEL=gpt-4o`. Локальная модель должна принимать chat/completions;
reasoning-вывод (если есть) сервер llama.cpp складывает в `reasoning_content`,
в `content` остаётся чистый HTML.

`LLM_SECRETS_KEY` должен быть стабильным секретом формата Fernet. Не меняйте его
после сохранения провайдеров без предварительной ротации ключей.

## Как это работает

1. Форма (`/`) отправляет multipart-запрос на `POST /api/projects`; изображение
   валидируется (JPG/PNG, ≤ 5 МБ, проверка magic bytes) и сохраняется в `uploads/`.
2. Генерация выполняется в фоне; фронт опрашивает `GET /api/projects/{id}/status`.
3. Ответ LLM проходит автопроверку (`app/services/codegen.py`): извлечение HTML
   из markdown-блоков, структура (DOCTYPE/html/head/body), запрещённые теги и
   внешние ресурсы (iframe/object/embed, javascript:-ссылки, скрипты с чужих
   хостов — разрешены только CDN из белого списка). При неудаче — автоматическая
   перегенерация с новым seed.
4. Предпросмотр отдаётся с `Content-Security-Policy: sandbox allow-scripts` и
   рендерится в `<iframe sandbox="allow-scripts">` — изоляция от основного приложения.
5. Чат-правки: `POST /api/projects/{id}/chat` — текущий HTML + инструкция уходят
   в LLM, новая версия сохраняется в `History`.
6. Экспорт: `GET /api/projects/{id}/download` — файл в UTF-8 с корректным
   `Content-Disposition` (RFC 5987).

## Тесты

```bash
pytest                          # юнит/API-тесты (LLM замокана)
RUN_LOCAL_E2E=1 pytest tests/test_e2e_local.py   # E2E против локальной LLM
```

## Деплой

Деплой на VPS/облако и выбор production-модели — по согласованию с заказчиком.
Минимум для production: сменить `SECRET_KEY`, указать боевые `LLM_*`,
запускать uvicorn за reverse-proxy (nginx) с HTTPS.

### Деплой на Railway

Проект готов к Railway из коробки: `Procfile`, `railway.json`, `runtime.txt`.

1. **Создайте проект**: [railway.app](https://railway.app) → New Project → Deploy from GitHub repo.
2. **Добавьте PostgreSQL**: New → Database → PostgreSQL (Railway сам выставит `DATABASE_URL`).
3. **Variables** — обязательно задайте:
   | Переменная | Значение |
   |---|---|
   | `SECRET_KEY` | длинная случайная строка (`python -c "import secrets; print(secrets.token_urlsafe(48))"`) |
   | `SESSION_COOKIE_SECURE` | `true` |
   | `PROXY_HEADERS` | `true` |
   | `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | боевые значения (или настройте провайдеров в админке после деплоя) |
   | `CORS_ORIGINS` | `https://ваш-домен.up.railway.app` |
4. **Deploy** — Railway соберёт через Nixpacks, применит миграции не нужно: таблицы создаются при старте, а `alembic upgrade head` можно выполнить один раз из Railway Shell.
5. **Админ**: выполните в Railway Shell:
   ```bash
   python scripts/create_admin.py --username admin --password <надёжный пароль>
   ```
6. **Домен**: Settings → Networking → Generate Domain.

Важно: файловая система Railway эфемерна — SQLite-файл и папки `uploads/`, `data/sites/`, `data/fonts/` сбрасываются при редеплое. Для продакшена используйте PostgreSQL (п.2) и внешнее хранилище для файлов (S3-совместимое), либо Volume (Settings → Volumes, монтировать в `/app/data`).
