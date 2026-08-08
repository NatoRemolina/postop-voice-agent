from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(
    directory=Path(__file__).resolve().parent.parent / "web" / "templates"
)


@router.get("/", include_in_schema=False)
async def index():
    return RedirectResponse(url="/admin")


@router.get("/admin", include_in_schema=False)
async def admin_page(request: Request):
    return templates.TemplateResponse(request, "admin.html")


@router.get("/call", include_in_schema=False)
async def call_page(request: Request):
    return templates.TemplateResponse(request, "call.html")
