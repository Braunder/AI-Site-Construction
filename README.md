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

uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Открыть http://127.0.0.1:8000 — форма генерации.
Лента проектов: http://127.0.0.1:8000/projects
Админка (просмотр данных): http://127.0.0.1:8000/admin

## Настройка LLM (`.env`)

| Переменная | Назначение |
|---|---|
| `LLM_BASE_URL` | Базовый URL OpenAI-совместимого API (`.../v1`) |
| `LLM_API_KEY` | Ключ API (для локальных серверов — любая строка) |
| `LLM_MODEL` | Имя модели (для OpenAI — например `gpt-4o`) |
| `LLM_TIMEOUT` | Таймаут запроса к LLM, сек (локальные модели медленные) |
| `LLM_MAX_RETRIES` | Повторные попытки при сбое / невалидном HTML |
| `LLM_MAX_TOKENS` | Лимит токенов ответа |
| `DATABASE_URL` | Строка подключения SQLAlchemy |
| `UPLOAD_DIR` | Каталог для загруженных изображений |
| `MAX_UPLOAD_MB` | Максимальный размер изображения |
| `SECRET_KEY` | Секрет сессий (в production — заменить!) |
| `CORS_ORIGINS` | Разрешённые origins через запятую |

Для OpenAI: `LLM_BASE_URL=https://api.openai.com/v1`, `LLM_API_KEY=sk-...`,
`LLM_MODEL=gpt-4o`. Локальная модель должна принимать chat/completions;
reasoning-вывод (если есть) сервер llama.cpp складывает в `reasoning_content`,
в `content` остаётся чистый HTML.

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
