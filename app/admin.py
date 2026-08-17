from sqladmin import ModelView

from app.models import History, Project


class ProjectAdmin(ModelView, model=Project):
    name = "Проект"
    name_plural = "Проекты"
    icon = "fa-solid fa-diagram-project"
    column_list = [Project.id, Project.title, Project.status, Project.style, Project.created_at]
    column_searchable_list = [Project.title, Project.prompt]
    column_sortable_list = [Project.created_at, Project.status]
    can_create = False
    can_edit = False
    page_size = 50


class HistoryAdmin(ModelView, model=History):
    name = "История"
    name_plural = "История"
    icon = "fa-solid fa-clock-rotate-left"
    column_list = [History.id, History.project_id, History.kind, History.instruction, History.created_at]
    column_sortable_list = [History.created_at]
    can_create = False
    can_edit = False
    page_size = 50
