import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.types import TypeDecorator
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.config import get_settings


class EncryptedSecret(TypeDecorator):
    """Stores secrets encrypted at rest while exposing plaintext to application code."""

    impl = String(500)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return "local"
        if not isinstance(value, str):
            value = str(value)
        if not value or value.startswith("enc:"):
            return value
        from cryptography.fernet import Fernet

        token = Fernet(get_settings().llm_secrets_key_value.encode()).encrypt(value.encode()).decode()
        return f"enc:{token}"

    def process_result_value(self, value, dialect):
        if not value or not value.startswith("enc:"):
            return value or "local"
        from cryptography.fernet import Fernet, InvalidToken

        try:
            return Fernet(get_settings().llm_secrets_key_value.encode()).decrypt(value[4:].encode()).decode()
        except InvalidToken as exc:
            raise ValueError("Не удалось расшифровать ключ LLM-провайдера: проверьте LLM_SECRETS_KEY") from exc


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fmt_dt(value: datetime | None) -> str:
    """Дата-время без микросекунд: 2026-08-23 10:08:26."""
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # Тариф: free (одностраничник без изображений) / standard (референс + анимации + изображения) /
    # premium (бета: многостраничные сайты, файловая система)
    plan: Mapped[str] = mapped_column(String(20), default="free", nullable=False)
    generation_limit: Mapped[int] = mapped_column(Integer, default=0)
    generation_used: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    @property
    def created_at_str(self) -> str:
        return _fmt_dt(self.created_at)

    projects: Mapped[list["Project"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class LLMProvider(Base):
    __tablename__ = "llm_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    api_key: Mapped[str] = mapped_column(EncryptedSecret(), default="local")
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    timeout: Mapped[float] = mapped_column(Float, default=600.0)
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    max_tokens: Mapped[int] = mapped_column(Integer, default=16000)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200), default="Без названия")
    prompt: Mapped[str] = mapped_column(Text)
    font: Mapped[str] = mapped_column(String(100), default="Inter")
    style: Mapped[str] = mapped_column(String(50), default="minimalism")
    color_primary: Mapped[str] = mapped_column(String(7), default="#1f2937")
    color_accent: Mapped[str] = mapped_column(String(7), default="#3b82f6")
    color_bg: Mapped[str] = mapped_column(String(7), default="#ffffff")
    image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    current_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Многофайловый режим: сайт хранится в data/sites/<user_id>/<project_id>/ (index.html + ассеты)
    is_multifile: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    llm_model: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/processing/done/error
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    user: Mapped["User"] = relationship(back_populates="projects")
    history: Mapped[list["History"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="History.created_at"
    )

    @property
    def created_at_str(self) -> str:
        return _fmt_dt(self.created_at)


class History(Base):
    __tablename__ = "history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="generate")  # generate/edit/regenerate
    instruction: Mapped[str] = mapped_column(Text, default="")
    html: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    @property
    def created_at_str(self) -> str:
        return _fmt_dt(self.created_at)

    project: Mapped[Project] = relationship(back_populates="history")
