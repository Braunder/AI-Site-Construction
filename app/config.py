from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    # LLM (OpenAI-совместимый API: llama.cpp, LM Studio, OpenAI и т.д.)
    llm_base_url: str = "http://127.0.0.1:8080/v1"
    llm_api_key: str = "local"
    llm_secrets_key: str = ""
    llm_model: str = "Qwen3.5-9B.Q5_K_M.gguf"
    llm_timeout: float = 600.0
    llm_max_retries: int = 2
    llm_max_tokens: int = 16000

    # Приложение
    app_env: str = "development"
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'app.db'}"
    upload_dir: str = str(BASE_DIR / "uploads")
    secret_key: str = "change-me-in-production"
    session_cookie_secure: bool = False  # в production включить True + HTTPS
    session_cookie_httponly: bool = True
    cors_origins: str = "http://127.0.0.1:8000,http://localhost:8000"
    allowed_hosts: str = "*"
    # Railway/прокси: доверять X-Forwarded-Proto/Host (для корректных redirect и secure-cookie)
    proxy_headers: bool = False
    forwarded_allow_ips: str = "*"
    sites_dir: str = str(BASE_DIR / "data" / "sites")       # многофайловые сайты: data/sites/<user_id>/<project_id>/
    fonts_dir: str = str(BASE_DIR / "data" / "fonts")       # кастомные шрифты, загружаемые админом
    max_font_mb: int = 5
    max_upload_mb: int = 5
    max_site_files: int = 50          # максимум файлов в одном проекте
    max_project_size_mb: int = 50     # суммарный размер файлов проекта
    vision_assets: bool = True        # отправлять загруженные изображения модели как vision-контент
    vision_assets_limit: int = 6      # максимум изображений в vision-контенте за один запрос
    vision_max_px: int = 1024         # длинная сторона vision-копии (токены зависят от разрешения)

    @model_validator(mode="after")
    def _warn_on_default_secret(self):
        """Дефолтный secret_key подписывает сессии и file-токены — подделка даёт админа.

        Fail-fast: приложение не стартует без явного SECRET_KEY/SECRET_KEY_FILE.
        Для локальной разработки задайте любое непустое значение в .env.
        """
        if self.secret_key == "change-me-in-production":
            raise RuntimeError(
                "SECRET_KEY оставлен дефолтным — это позволяет подделать сессию и получить админа. "
                "Задайте собственный SECRET_KEY в .env (для разработки — любая случайная строка)."
            )
        return self

    @property
    def llm_secrets_key_value(self) -> str:
        """Ключ шифрования LLM-секретов; в production задаётся отдельным env-параметром."""
        if self.llm_secrets_key:
            return self.llm_secrets_key
        if self.secret_key == "change-me-in-production":
            # Дефолтный секрет деривирует ключ шифрования API-ключей — компрометация БД
            # означает компрометацию всех ключей провайдеров. Fail-fast вместо warning.
            raise RuntimeError(
                "LLM_SECRETS_KEY не задан, а SECRET_KEY оставлен дефолтным. "
                "Задайте LLM_SECRETS_KEY (Fernet-ключ) или собственный SECRET_KEY."
            )
        import base64
        import hashlib

        return base64.urlsafe_b64encode(hashlib.sha256(self.secret_key.encode()).digest()).decode()

    @model_validator(mode="after")
    def _normalize_sqlite_path(self):
        """Относительный sqlite-путь привязываем к корню проекта и создаём каталог."""
        prefix = "sqlite:///"
        if self.database_url.startswith(prefix):
            p = Path(self.database_url[len(prefix):])
            if not p.is_absolute():
                p = BASE_DIR / p
            p.parent.mkdir(parents=True, exist_ok=True)
            self.database_url = prefix + str(p)
        return self

    @model_validator(mode="after")
    def _normalize_postgres_url(self):
        """Railway даёт postgres:// — приводим к postgresql+psycopg:// (драйвер psycopg 3).

        Без явного dialect SQLAlchemy ищет psycopg2, которого нет в requirements.
        """
        url = self.database_url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        self.database_url = url
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_hosts_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_hosts.split(",") if o.strip()] or ["*"]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def upload_path(self) -> Path:
        p = Path(self.upload_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def sites_path(self) -> Path:
        p = Path(self.sites_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def fonts_path(self) -> Path:
        p = Path(self.fonts_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def max_font_bytes(self) -> int:
        return self.max_font_mb * 1024 * 1024

    @property
    def max_project_size_bytes(self) -> int:
        return self.max_project_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
