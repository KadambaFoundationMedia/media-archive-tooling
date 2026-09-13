"""Localhost FastAPI review portal application."""
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import load_config
from ..renamer.registry.registry import LocalRegistry

app = FastAPI(title="Media Archive Review Portal")

MODULE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = MODULE_DIR / "templates"
STATIC_DIR = MODULE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_registry: Optional[LocalRegistry] = None


def get_registry() -> LocalRegistry:
    global _registry
    if _registry is None:
        config = load_config()
        _registry = LocalRegistry(config.registry_path)
    return _registry


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, filter: str = "all"):
    reg = get_registry()
    all_files = reg.list_files()
    total_count = len(all_files)
    review_count = sum(1 for f in all_files if f["needs_review"])
    committed_count = sum(1 for f in all_files if f["status"] == "committed")

    if filter == "review":
        display_files = [f for f in all_files if f["needs_review"]]
    elif filter == "committed":
        display_files = [f for f in all_files if f["status"] == "committed"]
    else:
        display_files = all_files

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "files": display_files,
            "total_count": total_count,
            "review_count": review_count,
            "committed_count": committed_count,
            "current_filter": filter,
        }
    )


@app.get("/file/{tracking_id}", response_class=HTMLResponse)
def file_detail(request: Request, tracking_id: str):
    reg = get_registry()
    file_record = reg.get_file(tracking_id)
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
    reg = get_registry()
    file_record = reg.get_file(tracking_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="File not found")

    new_status = "approved" if action == "approve" else ("deferred" if action == "defer" else "pending")
    needs_review = 0 if action == "approve" else file_record["needs_review"]

    with reg._get_conn() as conn:
        conn.execute("""
        UPDATE files SET
            when_val = ?,
            what_val = ?,
            where_val = ?,
            proposed_filename = ?,
            status = ?,
            needs_review = ?
        WHERE tracking_id = ?
        """, (when_val, what_val, where_val, proposed_filename, new_status, needs_review, tracking_id))
        conn.commit()

    return RedirectResponse(url=f"/file/{tracking_id}", status_code=303)
