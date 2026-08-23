from sqladmin import ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request
from wtforms import SelectField

from app.database import SessionLocal
from app.models import History, LLMProvider, Project, User
from app.dependencies import check_login_rate
from app.services.auth import hash_password, verify_password


class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        try:
            check_login_rate(request)
        except Exception:
            return False
        form = await request.form()
        username = str(form.get("username", "")).strip()
        password = str(form.get("password", ""))
        if not username or not password:
            return False
        with SessionLocal() as db:
            user = db.query(User).filter(User.username == username).first()
            if user is None or not user.is_admin or not verify_password(password, user.hashed_password):
                return False
            request.session["user_id"] = user.id
            request.session["is_admin"] = True
        return True

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        user_id = request.session.get("user_id")
        is_admin = request.session.get("is_admin")
        if not user_id or not is_admin:
            return False
        with SessionLocal() as db:
            user = db.get(User, user_id)
        return user is not None and user.is_admin


class ProjectAdmin(ModelView, model=Project):
    name = "Проект"
    name_plural = "Проекты"
    icon = "fa-solid fa-diagram-project"
    column_list = [Project.id, Project.title, Project.status, Project.style, "created_at_str"]
    column_labels = {"created_at_str": "Создан"}
    column_searchable_list = [Project.title, Project.prompt]
    column_sortable_list = [Project.created_at, Project.status]
    can_create = False
    can_edit = False
    can_delete = False
    page_size = 50


class HistoryAdmin(ModelView, model=History):
    name = "История"
    name_plural = "История"
    icon = "fa-solid fa-clock-rotate-left"
    column_list = [History.id, History.project_id, History.kind, History.instruction, "created_at_str"]
    column_labels = {"created_at_str": "Создан"}
    can_create = False
    can_edit = False
    can_delete = False
    page_size = 50


class UserAdmin(ModelView, model=User):
    name = "Пользователь"
    name_plural = "Пользователи"
    icon = "fa-solid fa-user"
    column_list = [
        User.id, User.username, User.is_admin, User.plan,
        User.generation_limit, User.generation_used, "created_at_str",
    ]
    column_labels = {
        "created_at_str": "Создан",
        User.plan: "Тариф",
        User.generation_limit: "Лимит генераций",
        User.generation_used: "Использовано",
    }
    column_searchable_list = [User.username]
    column_sortable_list = [User.created_at]
    can_create = True
    can_edit = True
    can_delete = True
    column_details_list = [
        User.id, User.username, User.is_admin, User.plan,
        User.generation_limit, User.generation_used,
    ]
    column_labels = {User.hashed_password: "Пароль", "created_at_str": "Создан"}
    form_columns = [
        User.username, User.hashed_password, User.is_admin, User.plan,
        User.generation_limit,
    ]
    # Выпадающий список тарифов (sqladmin 0.20 поддерживает form_overrides)
    form_overrides = {"plan": SelectField}
    form_args = {
        "plan": {
            "choices": [
                ("free", "Обычный — одностраничник без изображений"),
                ("standard", "Стандартный — референс, анимации, изображения"),
                ("premium", "Премиум (Бета) — многостраничные сайты"),
            ],
            "coerce": str,
        }
    }

    async def on_model_change(self, data, model, is_created, request):
        """Хешируем пароль при создании/смене; если значение уже bcrypt-хеш — пропускаем."""
        password = data.get("hashed_password")
        if password and not password.startswith(("$2a$", "$2b$", "$2y$")):
            data["hashed_password"] = hash_password(password)
        elif not password and is_created:
            raise ValueError("Пароль обязателен при создании пользователя")
        # Валидация тарифа
        if data.get("plan") not in ("free", "standard", "premium"):
            data["plan"] = "free"


class LLMProviderAdmin(ModelView, model=LLMProvider):
    name = "LLM-провайдер"
    name_plural = "LLM-провайдеры"
    icon = "fa-solid fa-network-wired"
    column_list = [
        LLMProvider.name,
        LLMProvider.model,
        LLMProvider.base_url,
        LLMProvider.enabled,
        LLMProvider.priority,
        LLMProvider.updated_at,
    ]
    column_labels = {
        LLMProvider.name: "Название",
        LLMProvider.model: "Модель",
        LLMProvider.base_url: "URL API",
        LLMProvider.enabled: "Включён",
        LLMProvider.priority: "Приоритет",
    }
    column_details_list = [
        LLMProvider.id,
        LLMProvider.name,
        LLMProvider.model,
        LLMProvider.base_url,
        LLMProvider.timeout,
        LLMProvider.max_retries,
        LLMProvider.max_tokens,
        LLMProvider.enabled,
        LLMProvider.priority,
    ]
    form_columns = [
        LLMProvider.name,
        LLMProvider.model,
        LLMProvider.base_url,
        LLMProvider.api_key,
        LLMProvider.timeout,
        LLMProvider.max_retries,
        LLMProvider.max_tokens,
        LLMProvider.enabled,
        LLMProvider.priority,
    ]
    form_widget_args = {
        "api_key": {
            "type": "password",
            "autocomplete": "new-password",
            "placeholder": "Оставьте пустым, чтобы сохранить текущий ключ",
        }
    }
    can_create = True
    can_edit = True
    can_delete = True

    async def on_model_change(self, data, model, is_created, request):
        # Ключ нужен приложению в исходном виде, поэтому хеширование здесь невозможно.
        # Пустое поле при редактировании не затирает уже сохранённый ключ.
        if not data.get("api_key") and not is_created:
            data["api_key"] = model.api_key
