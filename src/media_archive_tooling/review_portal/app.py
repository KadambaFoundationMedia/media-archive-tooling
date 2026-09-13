"""Localhost FastAPI review portal application."""
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import load_config
from ..renamer.registry.registry import LocalRegistry
from ..renamer.service import RenamerApplicationService

app = FastAPI(title="Media Archive Review Portal")

MODULE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = MODULE_DIR / "templates"
STATIC_DIR = MODULE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_service: Optional[RenamerApplicationService] = None


def get_service() -> RenamerApplicationService:
    global _service
    if _service is None:
        config = load_config()
        registry = LocalRegistry(config.registry_path)
        _service = RenamerApplicationService(registry=registry)
    return _service


def get_registry() -> LocalRegistry:
    return get_service().registry


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, filter: str = "all"):
    service = get_service()
    data = service.list_files(filter_mode=filter)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=data
    )


@app.get("/file/{tracking_id}", response_class=HTMLResponse)
def file_detail(request: Request, tracking_id: str):
    service = get_service()
    file_record = service.get_file(tracking_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="File not found in registry")

    return templates.TemplateResponse(
        request=request,
        name="detail.html",
        context={
            "file": file_record,
        }
    )


@app.post("/file/{tracking_id}/update")
def update_file(
    tracking_id: str,
    action: str = Form(...),
    when_val: str = Form(""),
    what_val: str = Form(""),
    where_val: str = Form(""),
    proposed_filename: str = Form(""),
):
    service = get_service()
    try:
        service.apply_review_action(
            tracking_id=tracking_id,
            action=action,
            when_val=when_val,
            what_val=what_val,
            where_val=where_val,
            custom_proposed_filename=proposed_filename,
            reviewer="review_portal",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return RedirectResponse(url=f"/file/{tracking_id}", status_code=303)
