"""Тарифные планы: что доступно пользователю при генерации."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Plan:
    key: str
    title: str
    # Возможности
    reference_image: bool      # референсное изображение (vision)
    animations: bool           # анимации и интерактив в промпте
    asset_images: bool         # загрузка изображений для страницы (многофайловый режим)
    multifile: bool            # многофайловый режим вообще
    multipage: bool            # многостраничные сайты (премиум, бета)
    description: str


PLANS: dict[str, Plan] = {
    "free": Plan(
        key="free",
        title="Обычный",
        reference_image=False,
        animations=False,
        asset_images=False,
        multifile=False,
        multipage=False,
        description="Одностраничный сайт по текстовому описанию, без изображений.",
    ),
    "standard": Plan(
        key="standard",
        title="Стандартный",
        reference_image=True,
        animations=True,
        asset_images=True,
        multifile=True,
        multipage=False,
        description="Референсное изображение, анимации и интерактив, загрузка изображений для страницы.",
    ),
    "premium": Plan(
        key="premium",
        title="Премиум (Бета)",
        reference_image=True,
        animations=True,
        asset_images=True,
        multifile=True,
        multipage=True,
        description="Многостраничные сайты из нескольких HTML + файловая система. Скоро: базы данных.",
    ),
}


def get_plan(user) -> Plan:
    """План пользователя; админам — премиум."""
    if user is None:
        return PLANS["free"]
    if getattr(user, "is_admin", False):
        return PLANS["premium"]
    return PLANS.get(getattr(user, "plan", "free") or "free", PLANS["free"])


def can(plan: Plan, feature: str) -> bool:
    return getattr(plan, feature, False)
