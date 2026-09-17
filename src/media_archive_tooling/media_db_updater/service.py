"""Application Service for Tool 4 (Media Database Updater).

Orchestrates:
- Building MediaDbSyncRequest from tracking_id and local registry.
- Preview (dry-run) and synchronize (commit) operations.
- Safe retries of pending and retryable sync records.
- Durable local outbox state in LocalRegistry.
"""
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..renamer.registry.registry import LocalRegistry
from .country_mapper import get_country_name_for_iso, normalize_country_name
from .engine import MediaDatabaseUpdateEngine
from .models import (
    MediaDbSyncRequest,
    MediaDbSyncResult,
    SyncOperation,
    SyncStatus,
)
from .write_adapter import (
    BaserowUnavailableError,
    BaserowWriteAdapter,
    FakeBaserowWriteAdapter,
)

logger = logging.getLogger(__name__)


class MediaDatabaseUpdaterService:
    """Service managing synchronization between local file state and Baserow Media database."""

    def __init__(
        self,
        registry: LocalRegistry,
        write_adapter: Any,
        tool2_service: Optional[Any] = None,
        renamer_service: Optional[Any] = None,
    ):
        self.registry = registry
        self.write_adapter = write_adapter
        self.tool2_service = tool2_service
        self.renamer_service = renamer_service
        self.engine = MediaDatabaseUpdateEngine(write_adapter=write_adapter, tool2_service=tool2_service)

    def build_sync_request(self, tracking_id: str) -> Optional[MediaDbSyncRequest]:
        """Construct a structured MediaDbSyncRequest from local registry state."""
        file_rec = self.registry.get_file(tracking_id)
        if not file_rec:
            return None

        # Fetch Tool 2 review
        t2_rec = self.registry.get_media_db_review(tracking_id)
        if not t2_rec and self.tool2_service:
            try:
                t2_res = self.tool2_service.review_file(tracking_id)
                if t2_res:
                    t2_rec = {
                        "decision": t2_res.decision,
                        "selected_media_row_id": t2_res.selected_media_row_id,
                        "result": t2_res.model_dump(),
                    }
            except Exception as e:
                logger.warning(f"Failed to fetch Tool 2 review for {tracking_id}: {e}")

        # Fetch Tool 3 travel review
        t3_rec = self.registry.get_travel_review(tracking_id)

        parser_res = file_rec.get("parser_result") or {}
        when_data = parser_res.get("when") or {}
        what_data = parser_res.get("what") or {}
        where_data = parser_res.get("where") or {}
        context_data = parser_res.get("context") or {}

        # Resolve country ISO vs Country Name (R-005)
        raw_country = where_data.get("country")
        raw_iso = where_data.get("country_iso2") or file_rec.get("where_val")
        country_name = None
        if raw_country:
            country_name = str(raw_country).strip()
        elif raw_iso:
            iso_name = get_country_name_for_iso(str(raw_iso).strip())
            country_name = iso_name if iso_name else str(raw_iso).strip()

        # Parent folder context
        parent_ctx = context_data.get("parent_folder") or Path(file_rec["current_path"]).parent.name

        t2_decision = t2_rec.get("decision") if t2_rec else None
        selected_row_id = t2_rec.get("selected_media_row_id") if t2_rec else None

        current_fn = file_rec.get("current_filename") or ""
        when_val = when_data.get("selected_value") or file_rec.get("when_val") or ""
        what_val = what_data.get("selected_value") or file_rec.get("what_val") or ""
        fp_str = f"{tracking_id}|{current_fn}|{when_val}|{what_val}|{country_name}|{where_data.get('place_location')}"
        req_fingerprint = hashlib.sha256(fp_str.encode("utf-8")).hexdigest()
        req_id = f"req_{tracking_id}_{int(datetime.now(timezone.utc).timestamp())}"
        table_id = str(getattr(self.write_adapter, "media_table_id", "") or "")

        return MediaDbSyncRequest(
            tracking_id=tracking_id,
            current_filename=file_rec["current_filename"],
            current_path=file_rec["current_path"],
            original_filename=file_rec.get("original_filename"),
            original_path=file_rec.get("original_path"),
            request_id=req_id,
            request_fingerprint=req_fingerprint,
            table_id=table_id,
            when_val=when_data.get("selected_value") or file_rec.get("when_val"),
            when_state=when_data.get("state"),
            when_provenance=when_data.get("provenance"),
            what_val=what_data.get("selected_value") or file_rec.get("what_val"),
            what_category=what_data.get("category"),
            what_verse=what_data.get("verse"),
            what_state=what_data.get("state"),
            what_provenance=what_data.get("provenance"),
            who_val=parser_res.get("who") or file_rec.get("who_val"),
            where_val=file_rec.get("where_val"),
            where_place=where_data.get("place_location"),
            where_country=country_name,
            where_country_iso=where_data.get("country_iso2"),
            where_state=where_data.get("state"),
            where_provenance=where_data.get("provenance"),
            parent_folder_context=parent_ctx,
            tool2_decision=t2_decision,
            selected_media_row_id=selected_row_id,
            tool3_decision=t3_rec.get("decision") if t3_rec else None,
            tool3_evidence=t3_rec.get("result") if t3_rec else None,
        )

    def preview(self, tracking_id: str, request: Optional[MediaDbSyncRequest] = None) -> MediaDbSyncResult:
        """Dry-run preview computing field diffs and actions without performing Baserow mutations."""
        return self.synchronize(tracking_id, commit=False, request=request)

    def synchronize(
        self,
        tracking_id: str,
        commit: bool = False,
        request: Optional[MediaDbSyncRequest] = None,
    ) -> MediaDbSyncResult:
        """Synchronize a local file state with Baserow Media database."""
        req = request or self.build_sync_request(tracking_id)
        if not req:
            res = MediaDbSyncResult(
                tracking_id=tracking_id,
                status=SyncStatus.FAILED_BLOCKED,
                operation=SyncOperation.BLOCKED,
                error_message=f"File {tracking_id} not found in registry",
            )
            return res

        # Check prior sync attempts
        prior_sync = self.registry.get_media_db_sync(tracking_id)
        attempt_count = (prior_sync.get("attempt_count") or 0) + 1 if prior_sync else 1

        # If committing, record durable initial status
        if commit:
            self.registry.save_media_db_sync(
                tracking_id=tracking_id,
                sync_status=SyncStatus.SYNCING.value,
                attempt_count=attempt_count,
                request_json=req.model_dump_json(),
            )

        # Execute
        result = self.engine.execute_sync(req, commit=commit)
        result.attempt_count = attempt_count
        result.last_attempt_at = datetime.now(timezone.utc).isoformat()

        # Save durable result in registry (R-008: persist previews so portal renders diff immediately)
        sync_status_val = result.status.value
        if not commit and sync_status_val == SyncStatus.SYNCING.value:
            sync_status_val = SyncStatus.PREVIEW.value

        self.registry.save_media_db_sync(
            tracking_id=tracking_id,
            sync_status=sync_status_val,
            operation_type=result.operation.value,
            media_row_id=result.media_row_id,
            attempt_count=attempt_count if commit else (prior_sync.get("attempt_count") or 0 if prior_sync else 0),
            last_attempt_at=result.last_attempt_at,
            error_message=result.error_message,
            request_json=req.model_dump_json(),
            result_json=result.model_dump_json(),
        )

        return result

    def retry_pending(self, tracking_ids: Optional[List[str]] = None) -> List[MediaDbSyncResult]:
        """Safely retries pending or retryable synchronization requests using fresh live state."""
        results: List[MediaDbSyncResult] = []
        if tracking_ids is not None:
            tids = tracking_ids
        else:
            pending = self.registry.list_pending_media_db_syncs()
            tids = [p["tracking_id"] for p in pending]

        for tid in tids:
            # Rebuild fresh request from latest state
            req = self.build_sync_request(tid)
            if req:
                res = self.synchronize(tid, commit=True, request=req)
                results.append(res)

        return results
