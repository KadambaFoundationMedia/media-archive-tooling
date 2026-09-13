"""Localhost FastAPI review portal application."""
import re
from pathlib import Path
from typing import List, Optional
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
TRACKING_TOKEN_RE = re.compile(r"_ID-[0-9a-fA-F]{8}(?=\.|$)", re.IGNORECASE)
BATCH_REVIEW_ACTIONS = {"approve", "defer"}

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_service: Optional[RenamerApplicationService] = None
_review_root: Optional[Path] = None


def configure_review_context(
    registry_path: Optional[Path] = None,
    review_root: Optional[Path] = None,
) -> None:
    """Configure the portal to use the same local review registry/root as the scan."""
    global _service, _review_root
    config = load_config()
    selected_registry = Path(registry_path) if registry_path else config.registry_path
    _service = RenamerApplicationService(registry=LocalRegistry(selected_registry))
    _review_root = Path(review_root).expanduser().resolve() if review_root else None


def get_service() -> RenamerApplicationService:
    global _service
    if _service is None:
        config = load_config()
        _service = RenamerApplicationService(registry=LocalRegistry(config.registry_path))
    return _service


def get_registry() -> LocalRegistry:
    return get_service().registry


def _dashboard_record(record: dict) -> dict:
    """Return a UI view of a registry row without changing archive semantics."""
    display = dict(record)
    proposed = display.get("proposed_filename") or display.get("current_filename") or ""
    display["display_proposed_filename"] = TRACKING_TOKEN_RE.sub("", proposed)

    original_path = Path(display.get("original_path") or "")
    if _review_root:
        try:
            relative = original_path.expanduser().resolve().relative_to(_review_root)
            parent = relative.parent
            display["display_path"] = "." if str(parent) == "." else str(parent)
        except ValueError:
            display["display_path"] = str(original_path.parent)
    else:
        display["display_path"] = str(original_path.parent)

    return display


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, filter: str = "all"):
    service = get_service()
    data = service.list_files(filter_mode=filter)
    data["files"] = [_dashboard_record(record) for record in data["files"]]
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


@app.post("/batch/update")
def batch_update(
    tracking_ids: List[str] = Form(...),
    action: str = Form(...),
    filter: str = Form("review"),
):
    """Apply a safe review-state action to multiple selected review rows."""
    if action not in BATCH_REVIEW_ACTIONS:
        raise HTTPException(status_code=400, detail="Batch action must be approve or defer")

    selected = list(dict.fromkeys(tid.strip() for tid in tracking_ids if tid.strip()))
    if not selected:
        raise HTTPException(status_code=400, detail="No files selected")

    service = get_service()
    records = []
    for tracking_id in selected:
        record = service.get_file(tracking_id)
        if not record:
            raise HTTPException(status_code=404, detail=f"File {tracking_id} not found in registry")
        if not record.get("needs_review"):
            raise HTTPException(
                status_code=400,
                detail=f"File {tracking_id} is not currently in the human-review queue",
            )
        records.append(record)

    try:
        for record in records:
            service.apply_review_action(
                tracking_id=record["tracking_id"],
                action=action,
                reviewer="review_portal_batch",
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    safe_filter = filter if filter in {"all", "review", "committed"} else "review"
    return RedirectResponse(url=f"/?filter={safe_filter}", status_code=303)


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
