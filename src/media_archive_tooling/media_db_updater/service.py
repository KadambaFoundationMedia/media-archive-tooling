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
from typing import Any, Dict, List, Optional, Union

from ..renamer.registry.registry import LocalRegistry
from .country_mapper import get_country_name_for_iso, normalize_country_name, is_valid_country_display_name
from .engine import MediaDatabaseUpdateEngine, validate_tool2_review_result
from .models import (
    AssociationApproval,
    FieldApproval,
    FieldApprovalAction,
    MediaDbSyncRequest,
    MediaDbSyncResult,
    PurgeItemResult,
    PurgeSummary,
    SyncOperation,
    SyncStatus,
    TestRowStatus,
)
from .write_adapter import (
    BaserowUnavailableError,
    BaserowWriteAdapter,
    FakeBaserowWriteAdapter,
    redact_secrets,
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

    def build_sync_request(
        self,
        tracking_id: str,
        force_refresh: bool = False,
        projected_filename: Optional[str] = None,
        projected_path: Optional[str] = None,
    ) -> Optional[MediaDbSyncRequest]:
        """Construct a structured MediaDbSyncRequest from local registry state."""
        file_rec = self.registry.get_file(tracking_id)
        if not file_rec:
            return None

        # Fetch Tool 2 review (R-031, R-036: force fresh live review on commit or retry)
        t2_rec = None
        if not force_refresh:
            t2_rec = self.registry.get_media_db_review(tracking_id)

        if force_refresh:
            if not self.tool2_service:
                t2_rec = {
                    "decision": "DATABASE_UNAVAILABLE",
                    "database_state": "DATABASE_UNAVAILABLE",
                    "selected_media_row_id": None,
                    "snapshot_timestamp": None,
                }
            else:
                try:
                    t2_res = self.tool2_service.review_file(
                        tracking_id,
                        force_refresh=True,
                        auto_enrich=False,
                    )
                    if t2_res is None:
                        t2_rec = {
                            "decision": "DATABASE_UNAVAILABLE",
                            "database_state": "DATABASE_UNAVAILABLE",
                            "selected_media_row_id": None,
                            "snapshot_timestamp": None,
                        }
                    else:
                        dec_val = t2_res.decision.value if hasattr(t2_res.decision, "value") else str(t2_res.decision or "")
                        if not dec_val or dec_val == "None":
                            dec_val = "DATABASE_UNAVAILABLE"
                        db_state = getattr(t2_res, "database_state", None)
                        t2_rec = {
                            "decision": dec_val,
                            "selected_media_row_id": getattr(t2_res, "selected_media_row_id", None),
                            "database_state": db_state,
                            "snapshot_timestamp": getattr(t2_res, "database_snapshot_at", None) or getattr(t2_res, "baserow_read_at", None),
                            "result": t2_res.model_dump() if hasattr(t2_res, "model_dump") else t2_res,
                        }
                except Exception as e:
                    logger.warning(f"Failed to fetch Tool 2 review for {tracking_id}: {e}")
                    t2_rec = {
                        "decision": "DATABASE_UNAVAILABLE",
                        "database_state": "DATABASE_UNAVAILABLE",
                        "selected_media_row_id": None,
                        "snapshot_timestamp": None,
                    }
        elif t2_rec is None and self.tool2_service:
            try:
                t2_res = self.tool2_service.review_file(
                    tracking_id,
                    force_refresh=False,
                    auto_enrich=False,
                )
                if t2_res:
                    dec_val = t2_res.decision.value if hasattr(t2_res.decision, "value") else str(t2_res.decision)
                    t2_rec = {
                        "decision": dec_val,
                        "selected_media_row_id": t2_res.selected_media_row_id,
                        "database_state": t2_res.database_state,
                        "snapshot_timestamp": t2_res.database_snapshot_at,
                        "result": t2_res.model_dump() if hasattr(t2_res, "model_dump") else t2_res,
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

        # Resolve country ISO vs Country Name (R-005, R-020)
        # Never treat an unknown two-letter code as a country display name
        raw_country = where_data.get("country")
        raw_iso = where_data.get("country_iso2") or file_rec.get("where_val")
        country_name = None
        if raw_country:
            clean_c = str(raw_country).strip()
            if len(clean_c) == 2:
                country_name = get_country_name_for_iso(clean_c)
            elif is_valid_country_display_name(clean_c) or len(clean_c) > 2:
                country_name = clean_c
        elif raw_iso:
            clean_iso = str(raw_iso).strip()
            country_name = get_country_name_for_iso(clean_iso)

        # Parent folder context
        parent_ctx = context_data.get("parent_folder") or Path(file_rec["current_path"]).parent.name

        t2_decision = None
        selected_row_id = None
        t2_timestamp = None
        live_query_timestamp = None
        t2_db_state = None

        if t2_rec:
            t2_decision = t2_rec.get("decision")
            selected_row_id = t2_rec.get("selected_media_row_id")
            t2_db_state = t2_rec.get("database_state")
            t2_timestamp = t2_rec.get("snapshot_timestamp")
            t2_res_data = t2_rec.get("result") or {}
            if isinstance(t2_res_data, dict):
                t2_timestamp = t2_timestamp or t2_res_data.get("database_snapshot_at")
                live_query_timestamp = t2_res_data.get("baserow_read_at") or t2_timestamp
                t2_db_state = t2_db_state or t2_res_data.get("database_state")

        current_path = file_rec.get("current_path") or ""
        current_fn = file_rec.get("current_filename") or ""
        orig_path = file_rec.get("original_path") or ""
        orig_fn = file_rec.get("original_filename") or ""
        latest_rename = self.registry.get_latest_rename(tracking_id)
        previous_path = (latest_rename or {}).get("from_path") or ""
        previous_fn = (latest_rename or {}).get("from_filename") or ""
        when_val = when_data.get("selected_value") or file_rec.get("when_val") or ""
        what_val = what_data.get("selected_value") or file_rec.get("what_val") or ""
        row_id_str = str(selected_row_id or "")

        # Section 16/19 fingerprinting: full committed archive state + association context (R-019)
        fp_str = f"{tracking_id}|{current_path}|{current_fn}|{orig_path}|{orig_fn}|{previous_path}|{previous_fn}|{row_id_str}|{when_val}|{what_val}|{country_name}|{where_data.get('place_location')}"
        req_fingerprint = hashlib.sha256(fp_str.encode("utf-8")).hexdigest()
        req_id = f"req_{tracking_id}_{int(datetime.now(timezone.utc).timestamp())}"
        table_id = str(getattr(self.write_adapter, "media_table_id", "") or "")

        # Carry Tool 1 evidence into structured provenance fields (R-014)
        when_prov = when_data.get("evidence") or when_data.get("provenance")
        what_prov = what_data.get("evidence") or what_data.get("provenance")
        where_prov = where_data.get("evidence") or where_data.get("provenance")

        # Retain prior field approvals, association approvals, and review notes if present
        prior_sync = self.registry.get_media_db_sync(tracking_id)
        prior_req = prior_sync.get("request") if prior_sync else None
        if isinstance(prior_req, str):
            try:
                prior_req = json.loads(prior_req)
            except Exception:
                prior_req = None

        field_approvals = {}
        association_approval = None
        reviewer_notes = None
        is_human_approved = False
        if prior_req:
            field_approvals = prior_req.get("field_approvals") or {}
            association_approval = prior_req.get("association_approval")
            reviewer_notes = prior_req.get("reviewer_notes")
            is_human_approved = bool(prior_req.get("is_human_approved", False))

        eff_current_fn = projected_filename or file_rec["current_filename"]
        eff_current_path = projected_path or file_rec["current_path"]

        # Preserve split-specific metadata from prior requests and file_splits
        split_child = self.registry.get_file_split_by_child(tracking_id)
        split_source = self.registry.get_file_split_by_source(tracking_id)
        is_singing_split = bool(split_child and split_child.get("singing_tracking_id") == tracking_id)
        is_class_split = bool(
            (split_child and split_child.get("class_tracking_id") == tracking_id)
            or (split_source and split_source.get("source_tracking_id") == tracking_id)
        )

        eff_audio_file_path = None
        if prior_req and prior_req.get("audio_file_path"):
            eff_audio_file_path = prior_req.get("audio_file_path")
        elif is_class_split and split_source:
            if file_rec.get("current_path", "").lower().endswith((".mp4", ".mov", ".mkv", ".avi")):
                eff_audio_file_path = split_source.get("class_path")

        eff_what_category = what_data.get("category")
        if prior_req and prior_req.get("what_category"):
            eff_what_category = prior_req.get("what_category")
        elif is_singing_split:
            eff_what_category = "Kirtan"

        eff_what_val = what_data.get("selected_value") or file_rec.get("what_val")
        if prior_req and prior_req.get("what_val"):
            eff_what_val = prior_req.get("what_val")

        eff_t2_decision = t2_decision
        eff_selected_row_id = selected_row_id

        if is_singing_split:
            eff_t2_decision = "NEW_MEDIA_CANDIDATE"
            eff_selected_row_id = None
        else:
            if not eff_t2_decision or eff_t2_decision == "DATABASE_UNAVAILABLE":
                if prior_req and prior_req.get("tool2_decision"):
                    eff_t2_decision = prior_req.get("tool2_decision")
                    eff_selected_row_id = eff_selected_row_id or prior_req.get("selected_media_row_id")

        # Resolve source record if tracking_id is a split child
        source_rec = None
        if split_child and split_child.get("source_tracking_id"):
            source_rec = self.registry.get_file(split_child["source_tracking_id"])

        src_parser_res = (source_rec.get("parser_result") or {}) if source_rec else {}
        src_when = src_parser_res.get("when") or {}
        src_where = src_parser_res.get("where") or {}
        src_context = src_parser_res.get("context") or {}

        # Effective when fields
        eff_when_val = when_data.get("selected_value") or file_rec.get("when_val")
        if not eff_when_val and prior_req:
            eff_when_val = prior_req.get("when_val")
        if not eff_when_val and src_when:
            eff_when_val = src_when.get("selected_value") or (source_rec.get("when_val") if source_rec else None)

        eff_when_state = when_data.get("state")
        if not eff_when_state and prior_req:
            eff_when_state = prior_req.get("when_state")
        if not eff_when_state and src_when:
            eff_when_state = src_when.get("state")

        eff_when_prov = when_prov
        if not eff_when_prov and prior_req:
            eff_when_prov = prior_req.get("when_provenance")
        if not eff_when_prov and src_when:
            eff_when_prov = src_when.get("evidence") or src_when.get("provenance")

        # Effective what fields
        eff_what_state = what_data.get("state")
        if not eff_what_state and prior_req:
            eff_what_state = prior_req.get("what_state")
        if not eff_what_state and is_singing_split:
            eff_what_state = "exact"

        eff_what_prov = what_prov
        if not eff_what_prov and prior_req:
            eff_what_prov = prior_req.get("what_provenance")

        eff_what_verse = what_data.get("verse")
        if not eff_what_verse and prior_req:
            eff_what_verse = prior_req.get("what_verse")

        # Effective where fields
        eff_where_val = file_rec.get("where_val")
        if not eff_where_val and prior_req:
            eff_where_val = prior_req.get("where_val")
        if not eff_where_val and source_rec:
            eff_where_val = source_rec.get("where_val")

        eff_where_place = where_data.get("place_location")
        if not eff_where_place and prior_req:
            eff_where_place = prior_req.get("where_place")
        if not eff_where_place and src_where:
            eff_where_place = src_where.get("place_location")

        eff_where_country = country_name
        if not eff_where_country and prior_req:
            eff_where_country = prior_req.get("where_country")
        if not eff_where_country and src_where:
            raw_c = src_where.get("country")
            raw_i = src_where.get("country_iso2")
            if raw_c:
                clean_sc = str(raw_c).strip()
                eff_where_country = get_country_name_for_iso(clean_sc) if len(clean_sc) == 2 else clean_sc
            elif raw_i:
                eff_where_country = get_country_name_for_iso(str(raw_i).strip())

        eff_where_country_iso = where_data.get("country_iso2")
        if not eff_where_country_iso and prior_req:
            eff_where_country_iso = prior_req.get("where_country_iso")
        if not eff_where_country_iso and src_where:
            eff_where_country_iso = src_where.get("country_iso2")

        eff_where_state = where_data.get("state")
        if not eff_where_state and prior_req:
            eff_where_state = prior_req.get("where_state")
        if not eff_where_state and src_where:
            eff_where_state = src_where.get("state")

        eff_where_prov = where_prov
        if not eff_where_prov and prior_req:
            eff_where_prov = prior_req.get("where_provenance")
        if not eff_where_prov and src_where:
            eff_where_prov = src_where.get("evidence") or src_where.get("provenance")

        eff_who_val = parser_res.get("who") or file_rec.get("who_val")
        if not eff_who_val and prior_req:
            eff_who_val = prior_req.get("who_val")
        if not eff_who_val and source_rec:
            eff_who_val = src_parser_res.get("who") or source_rec.get("who_val")

        eff_parent_ctx = parent_ctx
        if not eff_parent_ctx and prior_req:
            eff_parent_ctx = prior_req.get("parent_folder_context")
        if not eff_parent_ctx and src_context:
            eff_parent_ctx = src_context.get("parent_folder")

        return MediaDbSyncRequest(
            tracking_id=tracking_id,
            current_filename=eff_current_fn,
            current_path=eff_current_path,
            original_filename=file_rec.get("original_filename"),
            original_path=file_rec.get("original_path"),
            previous_filename=previous_fn or None,
            previous_path=previous_path or None,
            audio_file_path=eff_audio_file_path,
            request_id=req_id,
            request_fingerprint=req_fingerprint,
            table_id=table_id,
            when_val=eff_when_val,
            when_state=eff_when_state,
            when_provenance=eff_when_prov,
            what_val=eff_what_val,
            what_category=eff_what_category,
            what_verse=eff_what_verse,
            what_state=eff_what_state,
            what_provenance=eff_what_prov,
            who_val=eff_who_val,
            where_val=eff_where_val,
            where_place=eff_where_place,
            where_country=eff_where_country,
            where_country_iso=eff_where_country_iso,
            where_state=eff_where_state,
            where_provenance=eff_where_prov,
            parent_folder_context=eff_parent_ctx,
            tool2_decision=eff_t2_decision,
            tool2_timestamp=t2_timestamp,
            tool2_database_state=t2_db_state,
            selected_media_row_id=eff_selected_row_id,
            association_approval=association_approval,
            tool3_decision=t3_rec.get("decision") if t3_rec else None,
            tool3_evidence=t3_rec.get("result") if t3_rec else None,
            live_query_timestamp=live_query_timestamp,
            is_human_approved=is_human_approved,
            field_approvals=field_approvals,
            reviewer_notes=reviewer_notes,
        )

    def apply_field_approval(
        self,
        tracking_id: str,
        field_name: str,
        action: Union[str, FieldApprovalAction] = FieldApprovalAction.APPLY_CORRECTION,
        approved_value: Optional[Any] = None,
        reviewed_precondition_value: Optional[Any] = None,
        has_reviewed_precondition: bool = False,
        reviewer: str = "human_reviewer",
        notes: Optional[str] = None,
        commit: bool = False,
    ) -> MediaDbSyncResult:
        """Apply an explicit field-level review action with reviewed precondition (R-016, R-025, Section 18)."""
        req = self.build_sync_request(tracking_id)
        if not req:
            return MediaDbSyncResult(
                tracking_id=tracking_id,
                status=SyncStatus.FAILED_BLOCKED,
                operation=SyncOperation.BLOCKED,
                error_message=f"File {tracking_id} not found in registry",
            )

        if isinstance(action, str):
            action = FieldApprovalAction.from_value(action)

        approval = FieldApproval(
            field_name=field_name,
            action=action,
            approved_value=approved_value,
            has_reviewed_precondition=bool(has_reviewed_precondition),
            reviewed_precondition_value=reviewed_precondition_value if has_reviewed_precondition else None,
            reviewer=reviewer,
            reviewed_at=datetime.now(timezone.utc).isoformat(),
            notes=notes,
        )
        req.field_approvals[field_name] = approval

        return self.synchronize(tracking_id, commit=commit, request=req)

    def apply_association_approval(
        self,
        tracking_id: str,
        selected_media_row_id: int,
        reviewed_candidate_row_id: int,
        reviewed_precondition_filename: Optional[str] = None,
        reviewer: str = "human_reviewer",
        notes: Optional[str] = None,
        commit: bool = False,
    ) -> MediaDbSyncResult:
        """Apply an explicit human association approval to connect a file to an existing Baserow Media row (R-024)."""
        req = self.build_sync_request(tracking_id)
        if not req:
            return MediaDbSyncResult(
                tracking_id=tracking_id,
                status=SyncStatus.FAILED_BLOCKED,
                operation=SyncOperation.BLOCKED,
                error_message=f"File {tracking_id} not found in registry",
            )

        assoc = AssociationApproval(
            selected_media_row_id=selected_media_row_id,
            action=FieldApprovalAction.CHOOSE_ASSOCIATION,
            has_reviewed_precondition=True,
            reviewed_candidate_row_id=reviewed_candidate_row_id,
            reviewed_precondition_filename=reviewed_precondition_filename,
            reviewer=reviewer,
            reviewed_at=datetime.now(timezone.utc).isoformat(),
            notes=notes,
        )
        req.association_approval = assoc
        req.selected_media_row_id = selected_media_row_id

        return self.synchronize(tracking_id, commit=commit, request=req)


    def preview(
        self,
        tracking_id: str,
        request: Optional[MediaDbSyncRequest] = None,
        projected_filename: Optional[str] = None,
        projected_path: Optional[str] = None,
    ) -> MediaDbSyncResult:
        """Dry-run preview computing field diffs and actions without performing Baserow mutations."""
        req = request or self.build_sync_request(
            tracking_id,
            force_refresh=False,
            projected_filename=projected_filename,
            projected_path=projected_path,
        )
        return self.synchronize(tracking_id, commit=False, request=req)

    def synchronize(
        self,
        tracking_id: str,
        commit: bool = False,
        request: Optional[MediaDbSyncRequest] = None,
    ) -> MediaDbSyncResult:
        """Synchronize a local file state with Baserow Media database."""
        req = request or self.build_sync_request(tracking_id, force_refresh=commit)
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

        # Record in test-row ledger on verified live CREATE (alpha/beta cleanup amendment)
        if (
            commit
            and result.operation == SyncOperation.CREATE
            and result.status == SyncStatus.SYNCED
            and result.media_row_id
        ):
            table_id = result.table_id or str(getattr(self.write_adapter, "media_table_id", "") or "")
            sess_id = req.session_id or "alpha_test_session"
            marker_val = req.test_marker or f"[ALPHA-TEST-ROW session={sess_id} tracking_id={tracking_id}]"
            self.registry.record_test_created_row(
                table_id=table_id,
                row_id=result.media_row_id,
                tracking_id=tracking_id,
                request_fingerprint=req.request_fingerprint or "",
                session_id=sess_id,
                marker=marker_val,
                run_id=req.run_id,
                details={"live_row": result.live_row},
                status=TestRowStatus.CREATED.value,
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
            # Rebuild fresh request from latest live state (R-031)
            req = self.build_sync_request(tid, force_refresh=True)
            if req:
                res = self.synchronize(tid, commit=True, request=req)
                results.append(res)

        return results

    def purge_test_rows(
        self,
        dry_run: bool = False,
        session_id: Optional[str] = None,
    ) -> PurgeSummary:
        """Purge test-created Baserow rows recorded in the local test-row ledger.

        Enforces:
        - Only marker-verified rows recorded in the durable ledger are eligible.
        - Compares table ID, row ID, and remote marker in live row Notes before deletion.
        - Fails closed on table mismatch, marker mismatch, wrong row, or network error.
        - Idempotent: 404 (already absent) is marked PURGED.
        - In dry_run mode, verifies remote preconditions and reports without mutating Baserow or ledger.
        """
        summary = PurgeSummary()
        configured_table_id = str(getattr(self.write_adapter, "media_table_id", "") or "")

        all_entries = self.registry.list_test_rows(session_id=session_id)
        # Filter to actionable rows: CREATED, PURGING, or PURGE_BLOCKED
        target_statuses = {
            TestRowStatus.CREATED.value,
            TestRowStatus.PURGING.value,
            TestRowStatus.PURGE_BLOCKED.value,
        }
        actionable_entries = [e for e in all_entries if e.get("status") in target_statuses]
        summary.total_processed = len(actionable_entries)

        for entry in actionable_entries:
            row_id = entry["row_id"]
            entry_table_id = str(entry.get("table_id") or "")
            expected_marker = entry.get("marker") or ""

            # 1. Verify Table ID
            if configured_table_id and entry_table_id and entry_table_id != configured_table_id:
                reason = f"Table mismatch: ledger has table {entry_table_id}, adapter configured for {configured_table_id}"
                if not dry_run:
                    self.registry.update_test_row_status(
                        row_id=row_id,
                        status=TestRowStatus.PURGE_BLOCKED.value,
                        error_message=reason,
                    )
                summary.blocked_count += 1
                summary.results.append(PurgeItemResult(
                    row_id=row_id,
                    table_id=entry_table_id,
                    status=TestRowStatus.PURGE_BLOCKED,
                    action="BLOCKED",
                    reason=reason,
                ))
                continue

            # 2. Fetch live row from Baserow
            try:
                live_row = self.write_adapter.fetch_row_raw(row_id)
            except Exception as e:
                err_msg = redact_secrets(str(e))
                reason = f"Network or database error fetching live row {row_id}: {err_msg}"
                if not dry_run:
                    self.registry.update_test_row_status(
                        row_id=row_id,
                        status=TestRowStatus.PURGE_BLOCKED.value,
                        error_message=reason,
                    )
                summary.blocked_count += 1
                summary.results.append(PurgeItemResult(
                    row_id=row_id,
                    table_id=entry_table_id,
                    status=TestRowStatus.PURGE_BLOCKED,
                    action="BLOCKED",
                    reason=reason,
                ))
                continue

            # 3. Handle 404 (already absent)
            if live_row is None:
                if not dry_run:
                    self.registry.update_test_row_status(
                        row_id=row_id,
                        status=TestRowStatus.PURGED.value,
                        details={"outcome": "already_absent"},
                    )
                summary.already_absent_count += 1
                summary.results.append(PurgeItemResult(
                    row_id=row_id,
                    table_id=entry_table_id,
                    status=TestRowStatus.PURGED,
                    action="ALREADY_ABSENT",
                    reason="Row not found in Baserow (already absent)",
                ))
                continue

            # 4. Verify Row ID match
            live_id = live_row.get("id")
            if live_id != row_id:
                reason = f"Row ID mismatch: fetched row has ID {live_id}, expected {row_id}"
                if not dry_run:
                    self.registry.update_test_row_status(
                        row_id=row_id,
                        status=TestRowStatus.PURGE_BLOCKED.value,
                        error_message=reason,
                    )
                summary.blocked_count += 1
                summary.results.append(PurgeItemResult(
                    row_id=row_id,
                    table_id=entry_table_id,
                    status=TestRowStatus.PURGE_BLOCKED,
                    action="BLOCKED",
                    reason=reason,
                ))
                continue

            # 5. Verify Marker in Notes
            live_notes = str(live_row.get("Notes") or "")
            if not expected_marker or expected_marker not in live_notes:
                reason = f"Remote marker mismatch: expected marker '{expected_marker}' not found in live row Notes"
                if not dry_run:
                    self.registry.update_test_row_status(
                        row_id=row_id,
                        status=TestRowStatus.PURGE_BLOCKED.value,
                        error_message=reason,
                    )
                summary.blocked_count += 1
                summary.results.append(PurgeItemResult(
                    row_id=row_id,
                    table_id=entry_table_id,
                    status=TestRowStatus.PURGE_BLOCKED,
                    action="BLOCKED",
                    reason=reason,
                ))
                continue

            # 6. Execute Deletion (or report in dry-run)
            if dry_run:
                summary.deleted_count += 1
                summary.results.append(PurgeItemResult(
                    row_id=row_id,
                    table_id=entry_table_id,
                    status=TestRowStatus.CREATED,
                    action="WOULD_DELETE",
                    reason="Marker verified; row would be deleted",
                ))
            else:
                self.registry.update_test_row_status(
                    row_id=row_id,
                    status=TestRowStatus.PURGING.value,
                )
                try:
                    deleted = self.write_adapter.delete_media_row(row_id)
                    if deleted:
                        self.registry.update_test_row_status(
                            row_id=row_id,
                            status=TestRowStatus.PURGED.value,
                            details={"outcome": "deleted"},
                        )
                        summary.deleted_count += 1
                        summary.results.append(PurgeItemResult(
                            row_id=row_id,
                            table_id=entry_table_id,
                            status=TestRowStatus.PURGED,
                            action="DELETED",
                        ))
                    else:
                        # 404 between fetch and delete
                        self.registry.update_test_row_status(
                            row_id=row_id,
                            status=TestRowStatus.PURGED.value,
                            details={"outcome": "already_absent"},
                        )
                        summary.already_absent_count += 1
                        summary.results.append(PurgeItemResult(
                            row_id=row_id,
                            table_id=entry_table_id,
                            status=TestRowStatus.PURGED,
                            action="ALREADY_ABSENT",
                            reason="Row was absent during deletion call",
                        ))
                except Exception as e:
                    err_msg = redact_secrets(str(e))
                    reason = f"Error deleting row {row_id}: {err_msg}"
                    self.registry.update_test_row_status(
                        row_id=row_id,
                        status=TestRowStatus.PURGE_BLOCKED.value,
                        error_message=reason,
                    )
                    summary.blocked_count += 1
                    summary.results.append(PurgeItemResult(
                        row_id=row_id,
                        table_id=entry_table_id,
                        status=TestRowStatus.PURGE_BLOCKED,
                        action="BLOCKED",
                        reason=reason,
                    ))

        return summary
