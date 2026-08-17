import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(200), default="Без названия")
    prompt: Mapped[str] = mapped_column(Text)
    font: Mapped[str] = mapped_column(String(100), default="Inter")
    style: Mapped[str] = mapped_column(String(50), default="minimalism")
    color_primary: Mapped[str] = mapped_column(String(7), default="#1f2937")
    color_accent: Mapped[str] = mapped_column(String(7), default="#3b82f6")
    color_bg: Mapped[str] = mapped_column(String(7), default="#ffffff")
    image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    current_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_model: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/processing/done/error
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    history: Mapped[list["History"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="History.created_at"
    )


class History(Base):
    __tablename__ = "history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="generate")  # generate/edit/regenerate
    instruction: Mapped[str] = mapped_column(Text, default="")
    html: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    project: Mapped[Project] = relationship(back_populates="history")
