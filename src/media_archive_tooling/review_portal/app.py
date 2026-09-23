"""Localhost FastAPI review portal application."""
import json
import re
from pathlib import Path
from typing import Any, List, Optional
from urllib.parse import urlencode

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import load_config
from ..renamer.commit_service import RenameCommitService
from ..renamer.models import RenameMode
from ..renamer.registry.registry import LocalRegistry
from ..renamer.service import RenamerApplicationService

app = FastAPI(title="Media Archive Review Portal")

MODULE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = MODULE_DIR / "templates"
STATIC_DIR = MODULE_DIR / "static"
TRACKING_TOKEN_RE = re.compile(r"_ID-[0-9a-fA-F]{8}(?=\.|$)", re.IGNORECASE)
BATCH_ACTIONS = {"approve", "defer", "commit", "approve_commit"}

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_service: Optional[RenamerApplicationService] = None
_commit_service: Optional[RenameCommitService] = None
_review_root: Optional[Path] = None
_media_db_service: Optional[Any] = None
_media_db_provider: Optional[Any] = None
_media_db_updater_service: Optional[Any] = None


def configure_review_context(
    registry_path: Optional[Path] = None,
    review_root: Optional[Path] = None,
    media_db_service: Optional[Any] = None,
    media_db_provider: Optional[Any] = None,
    media_db_updater_service: Optional[Any] = None,
    registry: Optional[LocalRegistry] = None,
) -> None:
    """Configure the portal to use the same local review registry/root as the scan."""
    global _service, _commit_service, _review_root, _media_db_service, _media_db_provider, _media_db_updater_service
    config = load_config()
    if registry is None:
        selected_registry = Path(registry_path) if registry_path else config.registry_path
        registry = LocalRegistry(selected_registry)
    _service = RenamerApplicationService(registry=registry)
    _media_db_service = media_db_service
    _media_db_provider = media_db_provider
    _media_db_updater_service = media_db_updater_service or get_media_db_updater_service()
    _commit_service = RenameCommitService(
        registry=registry,
        mode=RenameMode.INITIAL,
        media_db_updater_service=_media_db_updater_service,
    )
    _review_root = Path(review_root).expanduser().resolve() if review_root else None


def get_service() -> RenamerApplicationService:
    global _service
    if _service is None:
        config = load_config()
        _service = RenamerApplicationService(registry=LocalRegistry(config.registry_path))
    return _service


def get_commit_service() -> RenameCommitService:
    global _commit_service
    if _commit_service is None:
        _commit_service = RenameCommitService(
            registry=get_service().registry,
            mode=RenameMode.INITIAL,
            media_db_updater_service=get_media_db_updater_service(),
        )
    return _commit_service


def get_registry() -> LocalRegistry:
    return get_service().registry


def get_media_db_service() -> Any:
    """Return configured MediaDatabaseReviewService with support for test dependency injection."""
    global _media_db_service
    if _media_db_service is not None:
        return _media_db_service

    from ..media_db_reviewer.baserow_provider import BaserowSnapshotProvider
    from ..media_db_reviewer.service import MediaDatabaseReviewService

    config = load_config()
    registry = get_registry()
    provider = _media_db_provider
    if provider is None:
        provider = BaserowSnapshotProvider(
            api_url=config.baserow_api_url,
            api_token=config.baserow_api_token,
            media_table_id=config.baserow_media_table_id,
            category_table_id=config.baserow_category_table_id,
            travel_schedule_table_id=config.baserow_travel_schedule_table_id,
            snapshot_path=config.baserow_snapshot_path,
        )
    return MediaDatabaseReviewService(registry=registry, provider=provider)


def get_media_db_updater_service() -> Any:
    """Return configured MediaDatabaseUpdaterService with support for test dependency injection."""
    global _media_db_updater_service
    if _media_db_updater_service is not None:
        return _media_db_updater_service

    from ..cli import create_media_db_updater_service

    config = load_config()
    registry = get_registry()
    tool2_svc = get_media_db_service()
    return create_media_db_updater_service(
        registry=registry,
        config=config,
        tool2_service=tool2_svc,
    )


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
def dashboard(
    request: Request,
    filter: str = "evaluation",
    batch_message: str = "",
    batch_error: str = "",
):
    from ..orchestrator.service import check_and_enforce_fingerprint

    service = get_service()
    registry = service.registry
    updater = get_media_db_updater_service()

    with registry.acquire_lock():
        ok, blocked_msg = check_and_enforce_fingerprint(registry, updater)

    if not ok:
        data = {
            "files": [],
            "total_count": 0,
            "review_count": 0,
            "committed_count": 0,
            "current_filter": filter,
            "batch_message": "",
            "batch_error": "",
            "purge_blocked": True,
            "blocked_reason": blocked_msg or "Cleanup blocked",
            "fresh_slate": False,
        }
        return templates.TemplateResponse(request=request, name="index.html", context=data)

    data = service.list_files(filter_mode=filter)
    data["files"] = [_dashboard_record(record) for record in data["files"]]
    data["batch_message"] = batch_message
    data["batch_error"] = batch_error
    data["purge_blocked"] = False
    data["blocked_reason"] = ""
    data["fresh_slate"] = (data.get("total_count", 0) == 0)
    return templates.TemplateResponse(request=request, name="index.html", context=data)


def _resolve_audio_path(registry: LocalRegistry, tracking_id: str) -> Optional[Path]:
    """Find the playable audio/video file for a tracking ID."""
    # 1. Check video audio derivative table
    deriv = registry.get_video_audio_derivative(tracking_id)
    if deriv and deriv.get("derived_path"):
        p = Path(deriv["derived_path"]).resolve()
        if p.exists():
            return p

    # 2. Check content review table
    content_rev = registry.get_content_review(tracking_id)
    if content_rev and content_rev.get("derived_audio_path"):
        p = Path(content_rev["derived_audio_path"]).resolve()
        if p.exists():
            return p

    # 3. Check registered media file
    file_rec = registry.get_file(tracking_id)
    if file_rec:
        for key in ("current_path", "original_path", "proposed_path"):
            val = file_rec.get(key)
            if val:
                p = Path(val).resolve()
                if p.exists():
                    return p
    return None


@app.get("/audio/{tracking_id}")
def stream_audio(tracking_id: str):
    """Stream playable audio for a tracking ID."""
    service = get_service()
    registry = service.registry
    audio_path = _resolve_audio_path(registry, tracking_id)
    if not audio_path or not audio_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found for tracking ID")

    suffix = audio_path.suffix.lower()
    media_types = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
        ".opus": "audio/opus",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
    }
    media_type = media_types.get(suffix, "audio/mpeg")
    return FileResponse(
        path=str(audio_path),
        media_type=media_type,
        filename=audio_path.name,
    )


@app.get("/file/{tracking_id}", response_class=HTMLResponse)
def file_detail(request: Request, tracking_id: str):
    service = get_service()
    registry = service.registry
    if registry.get_metadata("purge_blocked"):
        raise HTTPException(
            status_code=503,
            detail=f"Review portal is blocked: {registry.get_metadata('purge_blocked')}. Run './run-media-archive.sh --purge' to retry.",
        )
    file_record = service.get_file(tracking_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="File not found in registry")
    media_db_review = service.registry.get_media_db_review(tracking_id)
    travel_review = service.registry.get_travel_review(tracking_id)
    media_db_sync = service.registry.get_media_db_sync(tracking_id)
    content_review = service.registry.get_content_review(tracking_id)
    audio_path = _resolve_audio_path(service.registry, tracking_id)
    has_audio = audio_path is not None and audio_path.exists()
    return templates.TemplateResponse(
        request=request,
        name="detail.html",
        context={
            "file": file_record,
            "media_db_review": media_db_review,
            "travel_review": travel_review,
            "media_db_sync": media_db_sync,
            "content_review": content_review,
            "has_audio": has_audio,
        },
    )


@app.post("/file/{tracking_id}/content-review-action")
def content_review_action(
    tracking_id: str,
    classification: Optional[str] = Form(None),
    mantra_type: Optional[str] = Form(None),
    coarse_boundary: Optional[str] = Form(None),
    notes: Optional[str] = Form(""),
):
    from ..content_discoverer.service import ContentDiscovererService
    service = get_service()
    discoverer = ContentDiscovererService(registry=service.registry)
    try:
        discoverer.apply_human_decision(
            tracking_id=tracking_id,
            classification=classification,
            mantra_type=mantra_type,
            coarse_boundary=coarse_boundary,
            reviewer="review_portal",
            notes=notes or "",
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return RedirectResponse(url=f"/file/{tracking_id}", status_code=303)


@app.post("/file/{tracking_id}/media-db-action")
def media_db_action(
    tracking_id: str,
    action: str = Form(...),
    media_row_id: Optional[int] = Form(None),
    notes: str = Form(""),
):
    service = get_media_db_service()
    try:
        service.apply_human_decision(
            tracking_id=tracking_id,
            action=action,
            media_row_id=media_row_id,
            notes=notes,
            reviewer="review_portal",
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return RedirectResponse(url=f"/file/{tracking_id}", status_code=303)


@app.post("/file/{tracking_id}/media-db-sync")
def media_db_sync(
    tracking_id: str,
    action: str = Form("preview"),
):
    updater = get_media_db_updater_service()
    commit = (action in ("commit", "retry"))
    try:
        updater.synchronize(tracking_id, commit=commit)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return RedirectResponse(url=f"/file/{tracking_id}", status_code=303)


@app.post("/file/{tracking_id}/media-db-field-approval")
def media_db_field_approval(
    tracking_id: str,
    field_name: str = Form(...),
    action: Optional[str] = Form(None),
    approved_value: Optional[str] = Form(None),
    reviewed_precondition_value: Optional[str] = Form(None),
    reviewed_precondition_json: Optional[str] = Form(None),
    has_reviewed_precondition: Optional[bool] = Form(None),
    notes: Optional[str] = Form(None),
    commit: bool = Form(False),
):
    from ..media_db_updater.models import FieldApprovalAction
    updater = get_media_db_updater_service()
    if not action or not str(action).strip():
        raise HTTPException(status_code=400, detail="Missing required field approval action")
    try:
        approval_action = FieldApprovalAction.from_value(action)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid field approval action: {action}")

    # Determine precondition value and flag
    pre_val = None
    has_pre = False

    if has_reviewed_precondition is not None:
        has_pre = bool(has_reviewed_precondition)
    else:
        has_pre = bool(reviewed_precondition_json is not None or reviewed_precondition_value is not None)

    if reviewed_precondition_json is not None:
        try:
            pre_val = json.loads(reviewed_precondition_json)
        except Exception:
            pre_val = reviewed_precondition_json
    elif reviewed_precondition_value is not None:
        raw_val = reviewed_precondition_value.strip()
        if (raw_val.startswith("[") and raw_val.endswith("]")) or (raw_val.startswith("{") and raw_val.endswith("}")) or raw_val == "null":
            try:
                pre_val = json.loads(raw_val)
            except Exception:
                pre_val = reviewed_precondition_value
        else:
            pre_val = reviewed_precondition_value

    try:
        updater.apply_field_approval(
            tracking_id=tracking_id,
            field_name=field_name,
            action=approval_action,
            approved_value=approved_value,
            reviewed_precondition_value=pre_val,
            has_reviewed_precondition=has_pre,
            notes=notes,
            commit=commit,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return RedirectResponse(url=f"/file/{tracking_id}", status_code=303)


@app.post("/file/{tracking_id}/media-db-association")
def media_db_association(
    tracking_id: str,
    selected_media_row_id: int = Form(...),
    reviewed_candidate_row_id: int = Form(...),
    reviewed_precondition_filename: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    commit: bool = Form(False),
):
    updater = get_media_db_updater_service()
    try:
        updater.apply_association_approval(
            tracking_id=tracking_id,
            selected_media_row_id=selected_media_row_id,
            reviewed_candidate_row_id=reviewed_candidate_row_id,
            reviewed_precondition_filename=reviewed_precondition_filename,
            reviewer="review_portal",
            notes=notes,
            commit=commit,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return RedirectResponse(url=f"/file/{tracking_id}", status_code=303)


def _batch_redirect(filter_mode: str, message: str = "", error: str = "") -> RedirectResponse:
    safe_filter = filter_mode if filter_mode in {"all", "review", "committed"} else "review"
    params = {"filter": safe_filter}
    if message:
        params["batch_message"] = message
    if error:
        params["batch_error"] = error
    return RedirectResponse(url=f"/?{urlencode(params)}", status_code=303)


@app.post("/batch/update")
def batch_update(
    tracking_ids: List[str] = Form(...),
    action: str = Form(...),
    filter: str = Form("review"),
):
    """Apply review and/or filesystem actions to multiple selected rows."""
    if action not in BATCH_ACTIONS:
        raise HTTPException(status_code=400, detail="Batch action must be approve, defer, commit, or approve_commit")

    selected = list(dict.fromkeys(tid.strip() for tid in tracking_ids if tid.strip()))
    if not selected:
        raise HTTPException(status_code=400, detail="No files selected")

    service = get_service()
    commit_service = get_commit_service() if action in {"commit", "approve_commit"} else None
    records = []
    for tracking_id in selected:
        record = service.get_file(tracking_id)
        if not record:
            raise HTTPException(status_code=404, detail=f"File {tracking_id} not found in registry")
        records.append(record)

    if action in {"approve", "defer", "approve_commit"}:
        non_review = [record for record in records if not record.get("needs_review")]
        if non_review:
            raise HTTPException(status_code=400, detail="Approve/defer actions only apply to files currently requiring human review")
    elif action == "commit":
        blocked = [record for record in records if record.get("needs_review")]
        if blocked:
            raise HTTPException(status_code=400, detail="Commit requires all selected files to have their human-review blockers resolved first")

    succeeded = 0
    failures = []
    for record in records:
        try:
            tracking_id = record["tracking_id"]
            if action == "approve":
                service.apply_review_action(tracking_id=tracking_id, action="approve", reviewer="review_portal_batch")
            elif action == "defer":
                service.apply_review_action(tracking_id=tracking_id, action="defer", reviewer="review_portal_batch")
            elif action == "commit":
                assert commit_service is not None
                commit_service.commit_file(tracking_id=tracking_id, reviewer="review_portal_batch")
            elif action == "approve_commit":
                assert commit_service is not None
                service.apply_review_action(tracking_id=tracking_id, action="approve", reviewer="review_portal_batch")
                commit_service.commit_file(tracking_id=tracking_id, reviewer="review_portal_batch")
            succeeded += 1
        except ValueError as exc:
            failures.append(f"{record.get('original_filename') or record['tracking_id']}: {exc}")

    action_label = {"approve": "approved", "defer": "deferred", "commit": "committed", "approve_commit": "approved and committed"}[action]
    message = f"{succeeded} file{'s' if succeeded != 1 else ''} {action_label}."
    error = ""
    if failures:
        preview = "; ".join(failures[:3])
        if len(failures) > 3:
            preview += f"; and {len(failures) - 3} more"
        error = f"{len(failures)} file{'s' if len(failures) != 1 else ''} failed: {preview}"
    return _batch_redirect(filter, message=message, error=error)


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
