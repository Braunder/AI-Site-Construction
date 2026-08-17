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
    llm_model: str = "Qwen3.5-9B.Q5_K_M.gguf"
    llm_timeout: float = 600.0
    llm_max_retries: int = 2
    llm_max_tokens: int = 16000

    # Приложение
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'app.db'}"
    upload_dir: str = str(BASE_DIR / "uploads")
    max_upload_mb: int = 5
    secret_key: str = "change-me-in-production"
    cors_origins: str = "http://127.0.0.1:8000,http://localhost:8000"

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

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def upload_path(self) -> Path:
        p = Path(self.upload_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()
