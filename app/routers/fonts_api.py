"""API кастомных шрифтов: загрузка/удаление — только админы, чтение — все."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from app.dependencies import require_admin_dependency, require_user
from app.services import fonts as fonts_store
from app.services.fonts import FontError

router = APIRouter(prefix="/api/fonts", tags=["fonts"])


@router.get("")
def get_fonts(_user=Depends(require_user)):
    """Список кастомных шрифтов (для всех авторизованных)."""
    return fonts_store.list_fonts()


@router.post("", status_code=201)
async def upload_font(file: UploadFile, _admin=Depends(require_admin_dependency)):
    """Загрузка шрифта (только админ)."""
    try:
        path = await fonts_store.save_font(file)
    except FontError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"name": path.name, "size": path.stat().st_size}


@router.delete("/{name}", status_code=204)
def remove_font(name: str, _admin=Depends(require_admin_dependency)):
    if not fonts_store.delete_font(name):
        raise HTTPException(404, "Шрифт не найден")
    return Response(status_code=204)


@router.get("/files/{name}", response_model=None)
def get_font_file(name: str, _user=Depends(require_user)):
    """Отдаёт файл шрифта для @font-face."""
    safe = Path(name).name
    target = fonts_store.settings.fonts_path / safe
    if not target.is_file():
        raise HTTPException(404, "Шрифт не найден")
    mime = fonts_store.MIME.get(target.suffix.lower(), "font/ttf")
    return FileResponse(target, media_type=mime)
