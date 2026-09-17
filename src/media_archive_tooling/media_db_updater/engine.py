"""Core Synchronization Engine for Tool 4 (Media Database Updater).

Enforces:
- Tool 2 is the create-vs-update gate.
- Mandatory pre-write revalidation (both pre-create and pre-update race guards).
- Minimal PATCH-style writes; never full-row replacement.
- Preservation of collaborator edits and unrelated online fields.
- Field merge policies for Title, Date, Category, Tag, Language, Statuses, Timestamps, Notes.
- Strict select-option rules (only Country and Place, location may create options).
"""
from datetime import datetime, timezone
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from ..common.ascii_latin import to_ascii_latin
from ..renamer.parser.what import SB_REGEX, BG_REGEX, CC_REGEX
from .country_mapper import are_countries_equivalent
from .models import (
    FieldAction,
    FieldApproval,
    FieldApprovalAction,
    FieldDiff,
    MediaDbSyncRequest,
    MediaDbSyncResult,
    SyncOperation,
    SyncStatus,
)
from ..media_db_reviewer.models import MediaDatabaseReviewResult, ReviewDecision
from .write_adapter import (
    AmbiguousOptionError,
    BaserowSchemaError,
    BaserowUnavailableError,
    BaserowWriteError,
    FakeBaserowWriteAdapter,
    TaxonomyForbiddenError,
    _normalize_option_text,
    index_fields_by_name,
    redact_secrets,
    validate_field_schema,
)

logger = logging.getLogger(__name__)


def validate_tool2_review_result(
    rev: Any, tracking_id: str
) -> Tuple[Optional[MediaDatabaseReviewResult], Optional[str]]:
    """Strictly validate that Tool 2 returned a typed, valid MediaDatabaseReviewResult.

    Enforces R-023 and R-033:
    - Object must be an instance of MediaDatabaseReviewResult (or validatable via model).
    - tracking_id must match.
    - live_read_complete must be True.
    - snapshot_complete must be True.
    - baserow_check_complete must be True.
    - database_state must be in ('LIVE_CURRENT', 'LIVE_COMPLETE').
    - baserow_read_at (or database_snapshot_at) must be a non-empty string.
    - decision must not be empty or DATABASE_UNAVAILABLE.
    """
    if rev is None:
        return None, "Tool 2 returned no review result (None)"

    result_model: Optional[MediaDatabaseReviewResult] = None
    if isinstance(rev, MediaDatabaseReviewResult):
        result_model = rev
    elif isinstance(rev, dict):
        try:
            result_model = MediaDatabaseReviewResult.model_validate(rev)
        except Exception as e:
            return None, f"Tool 2 review dictionary failed MediaDatabaseReviewResult contract validation: {e}"
    else:
        if hasattr(rev, "model_dump"):
            try:
                result_model = MediaDatabaseReviewResult.model_validate(rev.model_dump())
            except Exception as e:
                return None, f"Tool 2 result object failed MediaDatabaseReviewResult contract: {e}"
        else:
            return None, f"Tool 2 result is of type {type(rev).__name__}, not MediaDatabaseReviewResult"

    if result_model.tracking_id != tracking_id:
        return None, f"Tool 2 result tracking_id '{result_model.tracking_id}' does not match expected '{tracking_id}'"

    if result_model.live_read_complete is not True:
        return None, "Tool 2 live_read_complete is not True"

    if result_model.snapshot_complete is not True:
        return None, "Tool 2 snapshot_complete is not True"

    if result_model.baserow_check_complete is not True:
        return None, "Tool 2 baserow_check_complete is not True"

    if result_model.database_state not in ("LIVE_CURRENT", "LIVE_COMPLETE"):
        return None, f"Tool 2 check incomplete or non-live (database_state '{result_model.database_state}' not in ('LIVE_CURRENT', 'LIVE_COMPLETE'))"

    read_ts = (result_model.baserow_read_at or result_model.database_snapshot_at or "").strip()
    if not read_ts:
        return None, "Tool 2 live-read timestamp is missing or empty"

    dec_val = result_model.decision.value if hasattr(result_model.decision, "value") else str(result_model.decision)
    if not dec_val or dec_val.upper() == "DATABASE_UNAVAILABLE":
        return None, f"Tool 2 decision is '{dec_val}'"

    return result_model, None

# Section 7 positive eligibility: automatic semantic writes require exact/strong (R-014)
TRUSTED_SEMANTIC_STATES = {"exact", "strong"}


def is_semantic_state_eligible(state: Optional[str]) -> bool:
    """Positive eligibility check for automatic semantic database writes (R-014).
    Only 'exact' or 'strong' states authorize automatic writes.
    Missing, empty, provisional, ambiguous, unresolved, unexpected fail closed.
    """
    if not state or not isinstance(state, str):
        return False
    return state.strip().lower() in TRUSTED_SEMANTIC_STATES


# Fields that Tool 4 considers unrelated to local archive file metadata
UNRELATED_ONLINE_FIELDS = {
    "Youtube",
    "Youtube descr",
    "Audio link",
    "Thumb image",
    "Transcriber",
    "Alt. Links",
    "Article Link",
    "Themes",
    "Transcript Archive link",
    "Media Archive link",
}

# Category aliases and abbreviations for flexible matching
CATEGORY_ALIASES: Dict[str, str] = {
    "bg": "bhagavad gita",
    "b.g.": "bhagavad gita",
    "bhagavad gita": "bhagavad gita",
    "bhagavad-gita": "bhagavad gita",
    "sb": "srimad bhagavatam",
    "s.b.": "srimad bhagavatam",
    "srimad bhagavatam": "srimad bhagavatam",
    "srimad-bhagavatam": "srimad bhagavatam",
    "cc": "caitanya caritamrta",
    "c.c.": "caitanya caritamrta",
    "caitanya-caritamrta": "caitanya caritamrta",
    "caitanya caritamrta": "caitanya caritamrta",
}


def _normalize_category_key(text: Optional[str]) -> str:
    """Normalize category string for comparison, resolving hyphens, spaces, and abbreviations."""
    if not text:
        return ""
    norm = to_ascii_latin(str(text)).strip().lower()
    norm = re.sub(r"[\s\-_]+", " ", norm).strip()
    return CATEGORY_ALIASES.get(norm, norm)


def _normalize_title_text(t: Optional[str]) -> str:
    """Normalize title for equivalent title comparison, collapsing hyphens and spaces."""
    if not t:
        return ""
    ascii_t = to_ascii_latin(str(t)).lower()
    return re.sub(r"[\s\-_]+", " ", ascii_t).strip()


def _is_complete_date(val: Optional[str]) -> bool:
    """True if val is an exact real calendar date YYYY-MM-DD."""
    if not val or not isinstance(val, str):
        return False
    clean = val.strip().replace("/", "-")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", clean):
        return False
    if clean.endswith("-DD") or clean.endswith("-00"):
        return False
    try:
        datetime.strptime(clean, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _extract_scripture_verse(what_val: Optional[str], what_verse: Optional[str]) -> Optional[str]:
    """Extract scripture verse representation (e.g. '1.3.4' or '1.18' or 'Adi 1.1')."""
    if what_verse and what_verse.strip():
        parts = what_verse.strip().split(".")
        return ".".join(str(int(p)) if p.isdigit() else p for p in parts)
    if not what_val:
        return None
    val = str(what_val).strip()
    # Check SB
    m = SB_REGEX.search(val)
    if m:
        canto, ch, v, v_end = m.groups()
        c_n = str(int(canto))
        ch_n = str(int(ch))
        v_n = str(int(v))
        return f"{c_n}.{ch_n}.{v_n}-{int(v_end)}" if v_end else f"{c_n}.{ch_n}.{v_n}"
    # Check BG
    m = BG_REGEX.search(val)
    if m:
        ch, v, v_end = m.groups()
        ch_n = str(int(ch))
        v_n = str(int(v))
        return f"{ch_n}.{v_n}-{int(v_end)}" if v_end else f"{ch_n}.{v_n}"
    # Check CC
    m = CC_REGEX.search(val)
    if m:
        lila, ch, v, v_end = m.groups()
        prefix = f"{lila} " if lila else ""
        ch_n = str(int(ch))
        v_n = str(int(v))
        return f"{prefix}{ch_n}.{v_n}-{int(v_end)}" if v_end else f"{prefix}{ch_n}.{v_n}"
    return None


def _resolve_title(request: MediaDbSyncRequest, for_create: bool = True) -> Optional[str]:
    """Resolve Title based on Section 11 priority:
    1. Explicit usable title in committed filename / structured WHAT title evidence.
    2. Parent folder context (excluding year/country/format folders).
    3. Current filename fallback (only for create).
    """
    # 1. Structured WHAT / title evidence
    if request.what_val:
        wv = request.what_val.strip()
        pure_scripture = False
        for rx in (SB_REGEX, BG_REGEX, CC_REGEX):
            m = rx.search(wv)
            if m:
                rem = rx.sub("", wv).strip(" -_")
                if not rem or len(rem) < 3:
                    pure_scripture = True
                break
        if not pure_scripture:
            return wv

    # 2. Parent folder context
    p_ctx = request.parent_folder_context
    if p_ctx and p_ctx.strip():
        clean_p = p_ctx.strip()
        is_year = bool(re.match(r"^\d{4}$", clean_p))
        is_technical = clean_p.lower() in ("mp3", "audio", "video", "edited", "archive", "incoming", "raw")
        if not is_year and not is_technical and len(clean_p) > 2:
            return clean_p

    # 3. Fallback: filename stem only for create
    if for_create:
        fn = request.current_filename
        if fn:
            stem = Path(fn).stem
            return stem or fn
        return "Untitled Archive File"

    return None


def merge_notes(
    existing_notes: Optional[str],
    incomplete_date: Optional[str] = None,
    full_date_resolved: bool = False,
) -> str:
    """Merge notes idempotently in accordance with Section 13:
    - 'Added from archive' begins Notes exactly once when archive linkage is established.
    - Incomplete recording date marker is idempotent.
    - Full date removes/replaces only the incomplete-date marker, preserving all human text.
    - Any existing occurrences of 'Added from archive' anywhere in notes are deduplicated.
    """
    marker_prefix = "Incomplete recording date:"
    lines: List[str] = []
    if existing_notes:
        for l in existing_notes.splitlines():
            clean_l = l.strip()
            # Remove any occurrences of "Added from archive" to ensure deduplication
            if clean_l == "Added from archive":
                continue
            lines.append(l)

    # 1. Clean / update incomplete date marker lines
    if full_date_resolved:
        lines = [l for l in lines if not l.strip().startswith(marker_prefix)]
    elif incomplete_date:
        marker_line = f"Incomplete recording date: {incomplete_date}"
        lines = [l for l in lines if not l.strip().startswith(marker_prefix)]
        lines.append(marker_line)

    # 2. Build final notes with 'Added from archive' prepended exactly once
    remaining = "\n".join(lines).strip()
    if remaining:
        return f"Added from archive\n{remaining}"
    return "Added from archive"



class MediaDatabaseUpdateEngine:
    """Engine implementing Tool 4 business rules, race guards, and merge policies."""

    def __init__(self, write_adapter: Any, tool2_service: Optional[Any] = None):
        self.write_adapter = write_adapter
        self.tool2_service = tool2_service

    def _enrich_result(self, result: MediaDbSyncResult, request: MediaDbSyncRequest) -> MediaDbSyncResult:
        """Enrich result with request identity, table identity, and Section 17 audit contract."""
        result.request_id = request.request_id
        result.request_fingerprint = request.request_fingerprint
        result.table_id = request.table_id

        # Build field-specific approvals audit summary with redacted secrets (R-019)
        approvals_audit: Dict[str, Any] = {}
        for k in request.field_approvals:
            appr = request.get_approval(k)
            if appr:
                approvals_audit[k] = {
                    "action": appr.action.value,
                    "approved_value": redact_secrets(appr.approved_value),
                    "has_reviewed_precondition": appr.has_reviewed_precondition,
                    "reviewed_precondition_value": redact_secrets(appr.reviewed_precondition_value),
                    "reviewer": appr.reviewer,
                    "reviewed_at": appr.reviewed_at,
                    "notes": redact_secrets(appr.notes) if appr.notes else None,
                }

        # Never substitute result time for an unknown live-read time (R-026)
        live_ts = request.live_query_timestamp or "UNAVAILABLE"
        tool2_ts = request.tool2_timestamp or "UNAVAILABLE"
        tool2_db_state = request.tool2_database_state or "UNAVAILABLE"

        result.audit_provenance = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tracking_id": request.tracking_id,
            "request_id": request.request_id,
            "rules_version": "v1.0",
            "operation_type": result.operation.value,
            "table_id": request.table_id,
            "media_row_id": result.media_row_id,
            "live_read_timestamp": live_ts,
            "tool2_snapshot_timestamp": tool2_ts,
            "tool2_database_state": tool2_db_state,
            "tool2_decision": request.tool2_decision,
            "when_state": request.when_state,
            "what_state": request.what_state,
            "where_state": request.where_state,
            "is_human_approved": request.is_human_approved,
            "field_approvals_count": len(request.field_approvals),
            "field_approvals": approvals_audit,
            "fields_modified": result.fields_modified,
            "fields_preserved": result.fields_preserved,
        }
        if result.error_message:
            result.error_message = redact_secrets(result.error_message)
        result.diagnostic_notes = [redact_secrets(note) for note in result.diagnostic_notes]
        result.conflicts = [redact_secrets(c) for c in result.conflicts]
        return result

    def _match_select_option(
        self,
        options: List[Dict[str, Any]],
        value: str,
        is_category: bool = False,
    ) -> Tuple[Optional[str], bool]:
        """Find matching select option by normalized text. Returns (matched_value, is_ambiguous)."""
        if is_category:
            target_key = _normalize_category_key(value)
            matches = [opt["value"] for opt in options if _normalize_category_key(opt.get("value", "")) == target_key]
        else:
            target = _normalize_option_text(value)
            matches = [opt["value"] for opt in options if _normalize_option_text(opt.get("value", "")) == target]
        if len(matches) == 1:
            return matches[0], False
        if len(matches) > 1:
            return None, True
        return None, False

    def plan_and_revalidate(
        self,
        request: MediaDbSyncRequest,
        live_fields: List[Dict[str, Any]],
        live_row: Optional[Dict[str, Any]] = None,
    ) -> MediaDbSyncResult:
        """Compute the intended sync operation, field diffs, and validation status."""
        # 1. Tool 2 Gate Evaluation
        t2_decision = (request.tool2_decision or "").upper()

        if t2_decision == "DATABASE_UNAVAILABLE":
            return self._enrich_result(MediaDbSyncResult(
                tracking_id=request.tracking_id,
                status=SyncStatus.DATABASE_UNAVAILABLE,
                operation=SyncOperation.BLOCKED,
                review_required=True,
                diagnostic_notes=["Tool 2 reported DATABASE_UNAVAILABLE; mutation blocked"],
            ), request)

        # Map field schema rejecting normalized duplicate column names (R-017)
        try:
            fields_by_name = index_fields_by_name(live_fields)
        except BaserowSchemaError as e:
            return self._enrich_result(MediaDbSyncResult(
                tracking_id=request.tracking_id,
                status=SyncStatus.FAILED_BLOCKED,
                operation=SyncOperation.BLOCKED,
                review_required=True,
                error_message=str(e),
                diagnostic_notes=[f"Deterministic schema error: {e}"],
            ), request)

        # Handle CREATE
        if t2_decision == "NEW_MEDIA_CANDIDATE":
            res = self._plan_create(request, fields_by_name)
            return self._enrich_result(res, request)

        # Handle UPDATE (R-024)
        # Note: request.is_human_approved NEVER authorizes semantic writes or bypasses association checks (R-016)
        # Default-deny every decision other than current EXISTING_MEDIA_MATCH or a separately validated explicit association decision
        assoc_approval = request.get_association_approval()
        is_match = (t2_decision == "EXISTING_MEDIA_MATCH")
        is_valid_assoc = (assoc_approval is not None)

        if is_match or is_valid_assoc:
            target_id = assoc_approval.selected_media_row_id if is_valid_assoc else request.selected_media_row_id
            if not target_id:
                return self._enrich_result(MediaDbSyncResult(
                    tracking_id=request.tracking_id,
                    status=SyncStatus.FAILED_BLOCKED,
                    operation=SyncOperation.BLOCKED,
                    diagnostic_notes=["Existing media match missing selected_media_row_id"],
                ), request)
            if live_row is None:
                return self._enrich_result(MediaDbSyncResult(
                    tracking_id=request.tracking_id,
                    status=SyncStatus.FAILED_BLOCKED,
                    operation=SyncOperation.BLOCKED,
                    media_row_id=target_id,
                    diagnostic_notes=[f"Target Media row {target_id} not found in database"],
                ), request)

            # Precondition revalidation for association approval (R-024, R-033)
            if is_valid_assoc and assoc_approval:
                if assoc_approval.reviewed_candidate_row_id != live_row.get("id"):
                    return self._enrich_result(MediaDbSyncResult(
                        tracking_id=request.tracking_id,
                        status=SyncStatus.REVIEW_REQUIRED,
                        operation=SyncOperation.CONFLICT,
                        review_required=True,
                        conflicts=["ASSOCIATION_PRECONDITION_FAILED"],
                        diagnostic_notes=[
                            f"Reviewed candidate row ID {assoc_approval.reviewed_candidate_row_id} does not match live row ID {live_row.get('id')}"
                        ],
                    ), request)
                if not assoc_approval.reviewed_precondition_filename:
                    return self._enrich_result(MediaDbSyncResult(
                        tracking_id=request.tracking_id,
                        status=SyncStatus.REVIEW_REQUIRED,
                        operation=SyncOperation.CONFLICT,
                        review_required=True,
                        conflicts=["ASSOCIATION_PRECONDITION_FAILED"],
                        diagnostic_notes=[
                            "Association approval missing reviewed_precondition_filename fingerprint"
                        ],
                    ), request)
                live_fn = str(live_row.get("Filename") or "").strip()
                precond_fn = str(assoc_approval.reviewed_precondition_filename or "").strip()
                if precond_fn != live_fn:
                    return self._enrich_result(MediaDbSyncResult(
                        tracking_id=request.tracking_id,
                        status=SyncStatus.REVIEW_REQUIRED,
                        operation=SyncOperation.CONFLICT,
                        review_required=True,
                        conflicts=["ASSOCIATION_PRECONDITION_FAILED"],
                        diagnostic_notes=[
                            f"Association precondition filename '{precond_fn}' does not match live row Filename '{live_fn}'"
                        ],
                    ), request)

            res = self._plan_update(request, fields_by_name, live_row)
            return self._enrich_result(res, request)

        # Non-match Tool 2 decisions requiring human review
        if t2_decision in (
            "PROBABLE_EXISTING_MEDIA",
            "MULTIPLE_CANDIDATES",
            "CONFLICT_WITH_EXISTING",
            "INSUFFICIENT_EVIDENCE",
        ):
            return self._enrich_result(MediaDbSyncResult(
                tracking_id=request.tracking_id,
                status=SyncStatus.REVIEW_REQUIRED,
                operation=SyncOperation.BLOCKED,
                review_required=True,
                diagnostic_notes=[f"Tool 2 decision {t2_decision} requires human association review; automatic mutation disallowed"],
            ), request)

        # Any unrecognized, missing, or bogus state
        return self._enrich_result(MediaDbSyncResult(
            tracking_id=request.tracking_id,
            status=SyncStatus.REVIEW_REQUIRED,
            operation=SyncOperation.BLOCKED,
            review_required=True,
            diagnostic_notes=[f"Unrecognized or unassociated Tool 2 decision '{request.tool2_decision}'"],
        ), request)


    def _plan_create(
        self,
        request: MediaDbSyncRequest,
        fields_by_name: Dict[str, Dict[str, Any]],
    ) -> MediaDbSyncResult:
        diffs: List[FieldDiff] = []
        conflicts: List[str] = []
        diag_notes: List[str] = []
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # 1. Title
        title_val = _resolve_title(request)
        appr_title = request.get_approval("Title")
        if appr_title:
            if not appr_title.has_reviewed_precondition:
                conflicts.append("Title approval missing required reviewed precondition value")
            elif appr_title.action == FieldApprovalAction.DEFER:
                conflicts.append("Title review action is DEFER; manual resolution required")
            elif appr_title.approved_value:
                title_val = appr_title.approved_value
        elif request.what_val and title_val == request.what_val:
            if not is_semantic_state_eligible(request.what_state):
                title_val = _resolve_title(MediaDbSyncRequest(
                    tracking_id=request.tracking_id,
                    current_filename=request.current_filename,
                    current_path=request.current_path,
                    parent_folder_context=request.parent_folder_context,
                ))
                diag_notes.append(f"WHAT state '{request.what_state}' not positively eligible ('exact'/'strong'); excluded from authoritative Title; used fallback")

        if "title" in fields_by_name:
            diffs.append(FieldDiff(field_name="Title", old_value=None, new_value=title_val, action=FieldAction.SET))
        else:
            return MediaDbSyncResult(
                tracking_id=request.tracking_id,
                status=SyncStatus.FAILED_BLOCKED,
                operation=SyncOperation.BLOCKED,
                review_required=True,
                error_message="Missing field in schema: Title",
                diagnostic_notes=["Intended field 'Title' missing from schema"],
            )

        # 2. Date / Incomplete Date
        date_written: Optional[str] = None
        incomplete_marker: Optional[str] = None
        appr_date = request.get_approval("Date")
        if appr_date:
            if not appr_date.has_reviewed_precondition:
                conflicts.append("Date approval missing required reviewed precondition value")
            elif appr_date.action == FieldApprovalAction.DEFER:
                conflicts.append("Date review action is DEFER; manual resolution required")
            elif appr_date.approved_value:
                if _is_complete_date(appr_date.approved_value):
                    date_written = appr_date.approved_value.strip().replace("/", "-")
                    if "date" in fields_by_name:
                        diffs.append(FieldDiff(field_name="Date", old_value=None, new_value=date_written, action=FieldAction.SET))
                    else:
                        return MediaDbSyncResult(
                            tracking_id=request.tracking_id,
                            status=SyncStatus.FAILED_BLOCKED,
                            operation=SyncOperation.BLOCKED,
                            review_required=True,
                            error_message="Missing field in schema: Date",
                            diagnostic_notes=["Intended field 'Date' missing from schema"],
                        )
                else:
                    incomplete_marker = str(appr_date.approved_value).strip()
                    if "date" in fields_by_name:
                        diffs.append(FieldDiff(
                            field_name="Date",
                            old_value=None,
                            new_value=None,
                            action=FieldAction.PRESERVED,
                            details=f"Incomplete date stored in Notes: {incomplete_marker}",
                        ))
                    else:
                        return MediaDbSyncResult(
                            tracking_id=request.tracking_id,
                            status=SyncStatus.FAILED_BLOCKED,
                            operation=SyncOperation.BLOCKED,
                            review_required=True,
                            error_message="Missing field in schema: Date",
                            diagnostic_notes=["Intended field 'Date' missing from schema"],
                        )
        elif not is_semantic_state_eligible(request.when_state):
            diag_notes.append(f"WHEN state '{request.when_state}' not positively eligible ('exact'/'strong'); excluded from authoritative Date field")
        elif _is_complete_date(request.when_val):
            date_written = request.when_val.strip().replace("/", "-")
            if "date" in fields_by_name:
                diffs.append(FieldDiff(field_name="Date", old_value=None, new_value=date_written, action=FieldAction.SET))
            else:
                return MediaDbSyncResult(
                    tracking_id=request.tracking_id,
                    status=SyncStatus.FAILED_BLOCKED,
                    operation=SyncOperation.BLOCKED,
                    review_required=True,
                    error_message="Missing field in schema: Date",
                    diagnostic_notes=["Intended field 'Date' missing from schema"],
                )
        elif request.when_val and request.when_val.strip():
            incomplete_marker = request.when_val.strip()
            if "date" in fields_by_name:
                diffs.append(FieldDiff(
                    field_name="Date",
                    old_value=None,
                    new_value=None,
                    action=FieldAction.PRESERVED,
                    details=f"Incomplete date stored in Notes: {incomplete_marker}",
                ))

        # 3. Category
        appr_cat = request.get_approval("Category")
        cat_fld = fields_by_name.get("category")
        target_cat = None
        if appr_cat:
            if not appr_cat.has_reviewed_precondition:
                conflicts.append("Category approval missing required reviewed precondition value")
            elif appr_cat.action == FieldApprovalAction.DEFER:
                conflicts.append("Category review action is DEFER; manual resolution required")
            else:
                target_cat = appr_cat.approved_value or request.what_category
        elif not is_semantic_state_eligible(request.what_state):
            diag_notes.append(f"WHAT state '{request.what_state}' not positively eligible ('exact'/'strong'); excluded from Category field")
        else:
            target_cat = request.what_category

        if cat_fld and target_cat:
            matched_cat, ambig = self._match_select_option(cat_fld.get("select_options", []), target_cat, is_category=True)
            if matched_cat:
                diffs.append(FieldDiff(field_name="Category", old_value=None, new_value=matched_cat, action=FieldAction.SET))
            elif ambig:
                conflicts.append(f"Ambiguous Category option for '{target_cat}'")
            else:
                conflicts.append(f"Category option '{target_cat}' not found in live schema (creation disallowed)")

        # 4. Tag (Scripture verse)
        verse = _extract_scripture_verse(request.what_val, request.what_verse)
        appr_tag = request.get_approval("Tag")
        tag_fld = fields_by_name.get("tag")
        target_verse = None
        if appr_tag:
            if not appr_tag.has_reviewed_precondition:
                conflicts.append("Tag approval missing required reviewed precondition value")
            elif appr_tag.action == FieldApprovalAction.DEFER:
                conflicts.append("Tag review action is DEFER; manual resolution required")
            else:
                target_verse = appr_tag.approved_value or verse
        elif not is_semantic_state_eligible(request.what_state):
            diag_notes.append(f"WHAT state '{request.what_state}' not positively eligible ('exact'/'strong'); excluded from Tag field")
        else:
            target_verse = verse

        if tag_fld and target_verse:
            if tag_fld.get("type") == "multiple_select":
                matched_tag, _ = self._match_select_option(tag_fld.get("select_options", []), target_verse)
                if matched_tag:
                    diffs.append(FieldDiff(field_name="Tag", old_value=None, new_value=[matched_tag], action=FieldAction.SET))
                else:
                    conflicts.append(f"Scripture Tag option '{target_verse}' not found in live schema (creation disallowed)")
            else:
                diffs.append(FieldDiff(field_name="Tag", old_value=None, new_value=target_verse, action=FieldAction.SET))


        # 5. Language (defaults to English)
        lang_fld = fields_by_name.get("language")
        if lang_fld:
            matched_lang, _ = self._match_select_option(lang_fld.get("select_options", []), "English")
            if matched_lang:
                diffs.append(FieldDiff(field_name="Language", old_value=None, new_value=matched_lang, action=FieldAction.SET))
            else:
                conflicts.append("Required Language option 'English' not found in live schema")

        # 6. Statuses: Status Media, Status thumb, Status Transcript (default to Not-started)
        for stat_name in ("Status Media", "Status thumb", "Status Transcript"):
            fld = fields_by_name.get(stat_name.lower())
            if fld:
                matched_stat, _ = self._match_select_option(fld.get("select_options", []), "Not-started")
                if matched_stat:
                    diffs.append(FieldDiff(field_name=stat_name, old_value=None, new_value=matched_stat, action=FieldAction.SET))
                else:
                    conflicts.append(f"Required status option 'Not-started' for '{stat_name}' not found in live schema")

        # 7. Filename & media_archive_path
        if "filename" in fields_by_name:
            diffs.append(FieldDiff(field_name="Filename", old_value=None, new_value=request.current_filename, action=FieldAction.SET))
        else:
            return MediaDbSyncResult(
                tracking_id=request.tracking_id,
                status=SyncStatus.FAILED_BLOCKED,
                operation=SyncOperation.BLOCKED,
                review_required=True,
                error_message="Missing field in schema: Filename",
                diagnostic_notes=["Intended field 'Filename' missing from schema"],
            )
        if "media_archive_path" in fields_by_name:
            diffs.append(FieldDiff(field_name="media_archive_path", old_value=None, new_value=request.current_path, action=FieldAction.SET))
        else:
            return MediaDbSyncResult(
                tracking_id=request.tracking_id,
                status=SyncStatus.FAILED_BLOCKED,
                operation=SyncOperation.BLOCKED,
                review_required=True,
                error_message="Missing field in schema: media_archive_path",
                diagnostic_notes=["Intended field 'media_archive_path' missing from schema"],
            )

        # 8. Notes
        notes_val = merge_notes(existing_notes=None, incomplete_date=incomplete_marker, full_date_resolved=bool(date_written))
        if "notes" in fields_by_name:
            diffs.append(FieldDiff(field_name="Notes", old_value=None, new_value=notes_val, action=FieldAction.SET))

        # Q-001: Media Archive link must remain empty on new rows; incoming proposals are blocked
        appr_mal = request.get_approval("Media Archive link") or request.get_approval("media_archive_link")
        if appr_mal and appr_mal.approved_value:
            conflicts.append("Media Archive link cannot be populated on creation; value must remain empty")
            diffs.append(FieldDiff(
                field_name="Media Archive link",
                old_value=None,
                new_value=appr_mal.approved_value,
                action=FieldAction.CONFLICT,
                details="Media Archive link cannot be populated on creation",
            ))

        # 9. Dates/Timestamps
        for d_fld in ("Created_on", "Last modified by", "Last modified", "imported_on"):
            if d_fld.lower() in fields_by_name:
                diffs.append(FieldDiff(field_name=d_fld, old_value=None, new_value=today, action=FieldAction.SET))

        # 10. Country & Place, location
        appr_country = request.get_approval("Country")
        appr_place = request.get_approval("Place, location")
        target_country = None
        target_place = None

        if appr_country:
            if not appr_country.has_reviewed_precondition:
                conflicts.append("Country approval missing required reviewed precondition value")
            elif appr_country.action == FieldApprovalAction.DEFER:
                conflicts.append("Country review action is DEFER; manual resolution required")
            else:
                target_country = appr_country.approved_value or request.where_country
        elif is_semantic_state_eligible(request.where_state):
            target_country = request.where_country

        if appr_place:
            if not appr_place.has_reviewed_precondition:
                conflicts.append("Place, location approval missing required reviewed precondition value")
            elif appr_place.action == FieldApprovalAction.DEFER:
                conflicts.append("Place, location review action is DEFER; manual resolution required")
            else:
                target_place = appr_place.approved_value or request.where_place
        elif is_semantic_state_eligible(request.where_state):
            target_place = request.where_place

        if not is_semantic_state_eligible(request.where_state) and not appr_country and not appr_place:
            diag_notes.append(f"WHERE state '{request.where_state}' not positively eligible ('exact'/'strong'); excluded from location fields")

        if target_country:
            if "country" in fields_by_name:
                diffs.append(FieldDiff(field_name="Country", old_value=None, new_value=target_country, action=FieldAction.SET))
            else:
                return MediaDbSyncResult(
                    tracking_id=request.tracking_id,
                    status=SyncStatus.FAILED_BLOCKED,
                    operation=SyncOperation.BLOCKED,
                    review_required=True,
                    error_message="Missing field in schema: Country",
                    diagnostic_notes=["Intended field 'Country' missing from schema"],
                )
        if target_place:
            place_fld = fields_by_name.get("place, location") or fields_by_name.get("place_location") or fields_by_name.get("location")
            if place_fld:
                diffs.append(FieldDiff(field_name="Place, location", old_value=None, new_value=target_place, action=FieldAction.SET))
            else:
                return MediaDbSyncResult(
                    tracking_id=request.tracking_id,
                    status=SyncStatus.FAILED_BLOCKED,
                    operation=SyncOperation.BLOCKED,
                    review_required=True,
                    error_message="Missing field in schema: Place, location",
                    diagnostic_notes=["Intended field 'Place, location' missing from schema"],
                )


        # Preserved fields on create (unrelated fields)
        preserved = list(UNRELATED_ONLINE_FIELDS)

        has_conflicts = len(conflicts) > 0
        return MediaDbSyncResult(
            tracking_id=request.tracking_id,
            status=SyncStatus.REVIEW_REQUIRED if has_conflicts else SyncStatus.SYNCING,
            operation=SyncOperation.CREATE,
            field_diffs=diffs,
            fields_preserved=preserved,
            fields_modified=[d.field_name for d in diffs if d.action == FieldAction.SET],
            conflicts=conflicts,
            diagnostic_notes=diag_notes,
            review_required=has_conflicts,
        )

    def _plan_update(
        self,
        request: MediaDbSyncRequest,
        fields_by_name: Dict[str, Dict[str, Any]],
        live_row: Dict[str, Any],
    ) -> MediaDbSyncResult:
        diffs: List[FieldDiff] = []
        conflicts: List[str] = []
        diag_notes: List[str] = []
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row_id = live_row["id"]

        def _get_text(field_key: str) -> Optional[str]:
            v = live_row.get(field_key)
            if v is None:
                for k, val in live_row.items():
                    if k.strip().lower() == field_key.strip().lower():
                        v = val
                        break
            if v is None:
                return None
            if isinstance(v, dict) and "value" in v:
                return str(v["value"]).strip()
            if isinstance(v, list) and v and isinstance(v[0], dict) and "value" in v[0]:
                return str(v[0]["value"]).strip()
            s = str(v).strip()
            return s if s else None

        # 1. Filename & media_archive_path (Archive linkage)
        curr_fn = _get_text("Filename")
        curr_path = _get_text("media_archive_path")

        path_needs_update = False
        fn_needs_update = False

        if not curr_path:
            path_needs_update = True
        elif curr_path == request.current_path:
            diffs.append(FieldDiff(field_name="media_archive_path", old_value=curr_path, new_value=curr_path, action=FieldAction.PRESERVED))
        elif curr_path == request.original_path or curr_fn == request.original_filename:
            path_needs_update = True
        else:
            conflicts.append(f"media_archive_path collision: row has '{curr_path}', incoming file is '{request.current_path}'")
            diffs.append(FieldDiff(
                field_name="media_archive_path",
                old_value=curr_path,
                new_value=request.current_path,
                action=FieldAction.CONFLICT,
                details="Unproven archive representation conflict",
            ))

        if not curr_fn:
            fn_needs_update = True
        elif curr_fn == request.current_filename:
            diffs.append(FieldDiff(field_name="Filename", old_value=curr_fn, new_value=curr_fn, action=FieldAction.PRESERVED))
        elif curr_fn == request.original_filename or curr_path == request.original_path:
            fn_needs_update = True
        else:
            diffs.append(FieldDiff(field_name="Filename", old_value=curr_fn, new_value=request.current_filename, action=FieldAction.PRESERVED))

        if path_needs_update and "media_archive_path" in fields_by_name:
            diffs.append(FieldDiff(field_name="media_archive_path", old_value=curr_path, new_value=request.current_path, action=FieldAction.SET))
        if fn_needs_update and "filename" in fields_by_name:
            diffs.append(FieldDiff(field_name="Filename", old_value=curr_fn, new_value=request.current_filename, action=FieldAction.SET))

        # Q-001: Media Archive link must be preserved on existing rows; cannot be modified by archive tooling
        curr_link = _get_text("Media Archive link") or _get_text("media_archive_link")
        appr_link = request.get_approval("Media Archive link") or request.get_approval("media_archive_link")
        if appr_link and appr_link.approved_value and appr_link.approved_value != curr_link:
            conflicts.append("Media Archive link modification is unsupported; existing database value must be preserved")
            diffs.append(FieldDiff(
                field_name="Media Archive link",
                old_value=curr_link,
                new_value=appr_link.approved_value,
                action=FieldAction.CONFLICT,
                details="Media Archive link cannot be modified by archive tooling",
            ))
        elif curr_link is not None or "media archive link" in fields_by_name or "media_archive_link" in fields_by_name:
            diffs.append(FieldDiff(
                field_name="Media Archive link",
                old_value=curr_link,
                new_value=curr_link,
                action=FieldAction.PRESERVED,
            ))

        # 2. Semantic Fields: Date, Title, Category, Place, Country
        # Date
        curr_date = _get_text("Date")
        date_needs_update = False
        new_date_val: Optional[str] = None
        date_is_incomplete = False

        appr_date = request.get_approval("Date")
        if appr_date:
            if not appr_date.has_reviewed_precondition:
                conflicts.append("Date approval missing required reviewed precondition value")
                diffs.append(FieldDiff(field_name="Date", old_value=curr_date, new_value=appr_date.approved_value, action=FieldAction.CONFLICT))
            else:
                expected_pre = appr_date.reviewed_precondition_value
                pre_matches = (not curr_date and not expected_pre) or (str(curr_date or "").strip() == str(expected_pre or "").strip())
                if pre_matches:
                    if appr_date.action == FieldApprovalAction.KEEP_DATABASE:
                        diffs.append(FieldDiff(field_name="Date", old_value=curr_date, new_value=curr_date, action=FieldAction.PRESERVED))
                    elif appr_date.action == FieldApprovalAction.DEFER:
                        conflicts.append("Date review action is DEFER; manual resolution required")
                        diffs.append(FieldDiff(field_name="Date", old_value=curr_date, new_value=curr_date, action=FieldAction.CONFLICT))
                    else:
                        date_needs_update = True
                        new_date_val = appr_date.approved_value
                else:
                    conflicts.append(f"Date approval precondition failed: DB has '{curr_date}', expected '{expected_pre}'")
                    diffs.append(FieldDiff(field_name="Date", old_value=curr_date, new_value=appr_date.approved_value, action=FieldAction.CONFLICT))
        elif not is_semantic_state_eligible(request.when_state):
            diag_notes.append(f"WHEN state '{request.when_state}' not positively eligible ('exact'/'strong'); excluded from Date field")
            if curr_date:
                diffs.append(FieldDiff(field_name="Date", old_value=curr_date, new_value=curr_date, action=FieldAction.PRESERVED))
        else:
            if _is_complete_date(request.when_val):
                clean_incoming_date = request.when_val.strip().replace("/", "-")
                if not curr_date:
                    date_needs_update = True
                    new_date_val = clean_incoming_date
                elif curr_date == clean_incoming_date:
                    diffs.append(FieldDiff(field_name="Date", old_value=curr_date, new_value=curr_date, action=FieldAction.PRESERVED))
                else:
                    conflicts.append(f"Date conflict: DB has '{curr_date}', incoming is '{clean_incoming_date}'")
                    diffs.append(FieldDiff(field_name="Date", old_value=curr_date, new_value=clean_incoming_date, action=FieldAction.CONFLICT))
            elif request.when_val and request.when_val.strip():
                date_is_incomplete = True
                if curr_date:
                    diffs.append(FieldDiff(field_name="Date", old_value=curr_date, new_value=curr_date, action=FieldAction.PRESERVED))

        if date_needs_update and new_date_val and "date" in fields_by_name:
            diffs.append(FieldDiff(field_name="Date", old_value=curr_date, new_value=new_date_val, action=FieldAction.SET))

        # Title
        curr_title = _get_text("Title")
        appr_title = request.get_approval("Title")
        if appr_title:
            if not appr_title.has_reviewed_precondition:
                conflicts.append("Title approval missing required reviewed precondition value")
                diffs.append(FieldDiff(field_name="Title", old_value=curr_title, new_value=appr_title.approved_value, action=FieldAction.CONFLICT))
            else:
                expected_pre = appr_title.reviewed_precondition_value
                pre_matches = (not curr_title and not expected_pre) or (_normalize_title_text(curr_title) == _normalize_title_text(expected_pre))
                if pre_matches:
                    if appr_title.action == FieldApprovalAction.KEEP_DATABASE:
                        diffs.append(FieldDiff(field_name="Title", old_value=curr_title, new_value=curr_title, action=FieldAction.PRESERVED))
                    elif appr_title.action == FieldApprovalAction.DEFER:
                        conflicts.append("Title review action is DEFER; manual resolution required")
                        diffs.append(FieldDiff(field_name="Title", old_value=curr_title, new_value=curr_title, action=FieldAction.CONFLICT))
                    else:
                        diffs.append(FieldDiff(field_name="Title", old_value=curr_title, new_value=appr_title.approved_value, action=FieldAction.SET))
                else:
                    conflicts.append(f"Title approval precondition failed: DB has '{curr_title}', expected '{expected_pre}'")
                    diffs.append(FieldDiff(field_name="Title", old_value=curr_title, new_value=appr_title.approved_value, action=FieldAction.CONFLICT))
        else:
            title_val = _resolve_title(request, for_create=False)
            if request.what_val and title_val == request.what_val and not is_semantic_state_eligible(request.what_state):
                title_val = None
                diag_notes.append(f"WHAT state '{request.what_state}' not positively eligible ('exact'/'strong'); excluded from Title field")

            if title_val:
                if not curr_title:
                    if "title" in fields_by_name:
                        diffs.append(FieldDiff(field_name="Title", old_value=None, new_value=title_val, action=FieldAction.SET))
                elif _normalize_title_text(curr_title) == _normalize_title_text(title_val):
                    diffs.append(FieldDiff(field_name="Title", old_value=curr_title, new_value=curr_title, action=FieldAction.PRESERVED))
                else:
                    conflicts.append(f"Title conflict: DB has '{curr_title}', incoming resolved '{title_val}'")
                    diffs.append(FieldDiff(field_name="Title", old_value=curr_title, new_value=title_val, action=FieldAction.CONFLICT))
            else:
                if curr_title:
                    diffs.append(FieldDiff(field_name="Title", old_value=curr_title, new_value=curr_title, action=FieldAction.PRESERVED))

        # Category
        curr_cat = _get_text("Category")
        appr_cat = request.get_approval("Category")
        if appr_cat:
            if not appr_cat.has_reviewed_precondition:
                conflicts.append("Category approval missing required reviewed precondition value")
                diffs.append(FieldDiff(field_name="Category", old_value=curr_cat, new_value=appr_cat.approved_value, action=FieldAction.CONFLICT))
            else:
                expected_pre = appr_cat.reviewed_precondition_value
                pre_matches = (not curr_cat and not expected_pre) or (_normalize_category_key(curr_cat) == _normalize_category_key(expected_pre))
                if pre_matches:
                    if appr_cat.action == FieldApprovalAction.KEEP_DATABASE:
                        diffs.append(FieldDiff(field_name="Category", old_value=curr_cat, new_value=curr_cat, action=FieldAction.PRESERVED))
                    elif appr_cat.action == FieldApprovalAction.DEFER:
                        conflicts.append("Category review action is DEFER; manual resolution required")
                        diffs.append(FieldDiff(field_name="Category", old_value=curr_cat, new_value=curr_cat, action=FieldAction.CONFLICT))
                    else:
                        target_cat = appr_cat.approved_value or request.what_category
                        cat_fld = fields_by_name.get("category")
                        if cat_fld and target_cat:
                            matched_cat, ambig = self._match_select_option(cat_fld.get("select_options", []), target_cat, is_category=True)
                            if matched_cat:
                                diffs.append(FieldDiff(field_name="Category", old_value=curr_cat, new_value=matched_cat, action=FieldAction.SET))
                            elif ambig:
                                conflicts.append(f"Ambiguous Category option for '{target_cat}'")
                            else:
                                conflicts.append(f"Category option '{target_cat}' not found in live schema")
                else:
                    conflicts.append(f"Category approval precondition failed: DB has '{curr_cat}', expected '{expected_pre}'")
                    diffs.append(FieldDiff(field_name="Category", old_value=curr_cat, new_value=appr_cat.approved_value, action=FieldAction.CONFLICT))
        elif not is_semantic_state_eligible(request.what_state):
            diag_notes.append(f"WHAT state '{request.what_state}' not positively eligible ('exact'/'strong'); excluded from Category field")
            if curr_cat:
                diffs.append(FieldDiff(field_name="Category", old_value=curr_cat, new_value=curr_cat, action=FieldAction.PRESERVED))
        elif request.what_category and "category" in fields_by_name:
            cat_fld = fields_by_name["category"]
            matched_cat, ambig = self._match_select_option(cat_fld.get("select_options", []), request.what_category, is_category=True)
            if matched_cat:
                if not curr_cat:
                    diffs.append(FieldDiff(field_name="Category", old_value=None, new_value=matched_cat, action=FieldAction.SET))
                elif _normalize_category_key(curr_cat) == _normalize_category_key(matched_cat):
                    diffs.append(FieldDiff(field_name="Category", old_value=curr_cat, new_value=curr_cat, action=FieldAction.PRESERVED))
                else:
                    conflicts.append(f"Category conflict: DB has '{curr_cat}', incoming is '{matched_cat}'")
                    diffs.append(FieldDiff(field_name="Category", old_value=curr_cat, new_value=matched_cat, action=FieldAction.CONFLICT))
            elif ambig:
                conflicts.append(f"Ambiguous Category option for '{request.what_category}'")
            else:
                conflicts.append(f"Category option '{request.what_category}' not found in live schema")
        else:
            if curr_cat:
                diffs.append(FieldDiff(field_name="Category", old_value=curr_cat, new_value=curr_cat, action=FieldAction.PRESERVED))

        # Country & Place, location
        appr_country = request.get_approval("Country")
        curr_country = _get_text("Country")
        if appr_country:
            if not appr_country.has_reviewed_precondition:
                conflicts.append("Country approval missing required reviewed precondition value")
                diffs.append(FieldDiff(field_name="Country", old_value=curr_country, new_value=appr_country.approved_value, action=FieldAction.CONFLICT))
            else:
                expected_pre = appr_country.reviewed_precondition_value
                pre_matches = (not curr_country and not expected_pre) or are_countries_equivalent(curr_country, expected_pre)
                if pre_matches:
                    if appr_country.action == FieldApprovalAction.KEEP_DATABASE:
                        diffs.append(FieldDiff(field_name="Country", old_value=curr_country, new_value=curr_country, action=FieldAction.PRESERVED))
                    elif appr_country.action == FieldApprovalAction.DEFER:
                        conflicts.append("Country review action is DEFER; manual resolution required")
                        diffs.append(FieldDiff(field_name="Country", old_value=curr_country, new_value=curr_country, action=FieldAction.CONFLICT))
                    else:
                        target_c = appr_country.approved_value or request.where_country
                        diffs.append(FieldDiff(field_name="Country", old_value=curr_country, new_value=target_c, action=FieldAction.SET))
                else:
                    conflicts.append(f"Country approval precondition failed: DB has '{curr_country}', expected '{expected_pre}'")
                    diffs.append(FieldDiff(field_name="Country", old_value=curr_country, new_value=appr_country.approved_value, action=FieldAction.CONFLICT))
        elif not is_semantic_state_eligible(request.where_state):
            diag_notes.append(f"WHERE state '{request.where_state}' not positively eligible ('exact'/'strong'); excluded from Country field")
            if curr_country:
                diffs.append(FieldDiff(field_name="Country", old_value=curr_country, new_value=curr_country, action=FieldAction.PRESERVED))
        elif request.where_country and "country" in fields_by_name:
            if not curr_country:
                diffs.append(FieldDiff(field_name="Country", old_value=None, new_value=request.where_country, action=FieldAction.SET))
            elif are_countries_equivalent(curr_country, request.where_country):
                diffs.append(FieldDiff(field_name="Country", old_value=curr_country, new_value=curr_country, action=FieldAction.PRESERVED))
            else:
                conflicts.append(f"Country conflict: DB has '{curr_country}', incoming is '{request.where_country}'")
                diffs.append(FieldDiff(field_name="Country", old_value=curr_country, new_value=request.where_country, action=FieldAction.CONFLICT))
        else:
            if curr_country:
                diffs.append(FieldDiff(field_name="Country", old_value=curr_country, new_value=curr_country, action=FieldAction.PRESERVED))

        appr_place = request.get_approval("Place, location")
        curr_place = _get_text("Place, location")
        if appr_place:
            if not appr_place.has_reviewed_precondition:
                conflicts.append("Place, location approval missing required reviewed precondition value")
                diffs.append(FieldDiff(field_name="Place, location", old_value=curr_place, new_value=appr_place.approved_value, action=FieldAction.CONFLICT))
            else:
                expected_pre = appr_place.reviewed_precondition_value
                pre_matches = (not curr_place and not expected_pre) or (_normalize_option_text(curr_place) == _normalize_option_text(expected_pre))
                if pre_matches:
                    if appr_place.action == FieldApprovalAction.KEEP_DATABASE:
                        diffs.append(FieldDiff(field_name="Place, location", old_value=curr_place, new_value=curr_place, action=FieldAction.PRESERVED))
                    elif appr_place.action == FieldApprovalAction.DEFER:
                        conflicts.append("Place, location review action is DEFER; manual resolution required")
                        diffs.append(FieldDiff(field_name="Place, location", old_value=curr_place, new_value=curr_place, action=FieldAction.CONFLICT))
                    else:
                        target_p = appr_place.approved_value or request.where_place
                        diffs.append(FieldDiff(field_name="Place, location", old_value=curr_place, new_value=target_p, action=FieldAction.SET))
                else:
                    conflicts.append(f"Place approval precondition failed: DB has '{curr_place}', expected '{expected_pre}'")
                    diffs.append(FieldDiff(field_name="Place, location", old_value=curr_place, new_value=appr_place.approved_value, action=FieldAction.CONFLICT))
        elif not is_semantic_state_eligible(request.where_state):
            diag_notes.append(f"WHERE state '{request.where_state}' not positively eligible ('exact'/'strong'); excluded from Place, location field")
            if curr_place:
                diffs.append(FieldDiff(field_name="Place, location", old_value=curr_place, new_value=curr_place, action=FieldAction.PRESERVED))
        elif request.where_place and "place, location" in fields_by_name:
            if not curr_place:
                diffs.append(FieldDiff(field_name="Place, location", old_value=None, new_value=request.where_place, action=FieldAction.SET))
            elif _normalize_option_text(curr_place) == _normalize_option_text(request.where_place):
                diffs.append(FieldDiff(field_name="Place, location", old_value=curr_place, new_value=curr_place, action=FieldAction.PRESERVED))
            else:
                conflicts.append(f"Place conflict: DB has '{curr_place}', incoming is '{request.where_place}'")
                diffs.append(FieldDiff(field_name="Place, location", old_value=curr_place, new_value=request.where_place, action=FieldAction.CONFLICT))
        else:
            if curr_place:
                diffs.append(FieldDiff(field_name="Place, location", old_value=curr_place, new_value=curr_place, action=FieldAction.PRESERVED))

        # Tag (Additive / union)
        verse = _extract_scripture_verse(request.what_val, request.what_verse)
        curr_tags_raw = live_row.get("Tag")
        curr_tags: List[str] = []
        if isinstance(curr_tags_raw, list):
            for t in curr_tags_raw:
                if isinstance(t, dict) and "value" in t:
                    curr_tags.append(str(t["value"]))
                elif isinstance(t, str):
                    curr_tags.append(t)
        elif isinstance(curr_tags_raw, str) and curr_tags_raw.strip():
            curr_tags.append(curr_tags_raw.strip())

        appr_tag = request.get_approval("Tag")
        if appr_tag:
            if not appr_tag.has_reviewed_precondition:
                conflicts.append("Tag approval missing required reviewed precondition value")
                diffs.append(FieldDiff(field_name="Tag", old_value=curr_tags, new_value=appr_tag.approved_value, action=FieldAction.CONFLICT))
            else:
                expected_pre = appr_tag.reviewed_precondition_value
                pre_matches = (not curr_tags and not expected_pre) or (curr_tags == expected_pre) or (isinstance(expected_pre, str) and expected_pre in curr_tags)
                if pre_matches:
                    if appr_tag.action == FieldApprovalAction.KEEP_DATABASE:
                        diffs.append(FieldDiff(field_name="Tag", old_value=curr_tags, new_value=curr_tags, action=FieldAction.PRESERVED))
                    elif appr_tag.action == FieldApprovalAction.DEFER:
                        conflicts.append("Tag review action is DEFER; manual resolution required")
                        diffs.append(FieldDiff(field_name="Tag", old_value=curr_tags, new_value=curr_tags, action=FieldAction.CONFLICT))
                    else:
                        t_val = appr_tag.approved_value or verse
                        tag_fld = fields_by_name.get("tag")
                        if tag_fld:
                            if tag_fld.get("type") == "multiple_select":
                                matched_verse_opt, _ = self._match_select_option(tag_fld.get("select_options", []), t_val)
                                if matched_verse_opt:
                                    new_tags = list(curr_tags) + ([matched_verse_opt] if matched_verse_opt not in curr_tags else [])
                                    diffs.append(FieldDiff(field_name="Tag", old_value=curr_tags, new_value=new_tags, action=FieldAction.SET))
                                else:
                                    conflicts.append(f"Scripture Tag option '{t_val}' not found in live schema")
                            else:
                                diffs.append(FieldDiff(field_name="Tag", old_value=curr_tags, new_value=[t_val], action=FieldAction.SET))
                else:
                    conflicts.append(f"Tag approval precondition failed: DB has '{curr_tags}', expected '{expected_pre}'")
                    diffs.append(FieldDiff(field_name="Tag", old_value=curr_tags, new_value=appr_tag.approved_value, action=FieldAction.CONFLICT))
        elif not is_semantic_state_eligible(request.what_state):
            diag_notes.append(f"WHAT state '{request.what_state}' not positively eligible ('exact'/'strong'); excluded from Tag field")
            if curr_tags:
                diffs.append(FieldDiff(field_name="Tag", old_value=curr_tags, new_value=curr_tags, action=FieldAction.PRESERVED))
        elif verse and "tag" in fields_by_name:
            tag_fld = fields_by_name["tag"]
            if tag_fld.get("type") == "multiple_select":
                matched_verse_opt, _ = self._match_select_option(tag_fld.get("select_options", []), verse)
                if matched_verse_opt:
                    if matched_verse_opt not in curr_tags:
                        new_tags = list(curr_tags) + [matched_verse_opt]
                        diffs.append(FieldDiff(field_name="Tag", old_value=curr_tags, new_value=new_tags, action=FieldAction.SET))
                    else:
                        diffs.append(FieldDiff(field_name="Tag", old_value=curr_tags, new_value=curr_tags, action=FieldAction.PRESERVED))
                else:
                    conflicts.append(f"Scripture Tag option '{verse}' not found in live schema")
            else:
                if verse not in curr_tags:
                    new_tags = list(curr_tags) + [verse]
                    diffs.append(FieldDiff(field_name="Tag", old_value=curr_tags, new_value=new_tags, action=FieldAction.SET))
        else:
            if curr_tags:
                diffs.append(FieldDiff(field_name="Tag", old_value=curr_tags, new_value=curr_tags, action=FieldAction.PRESERVED))


        # Notes Merge
        curr_notes = _get_text("Notes")
        inc_date_arg = request.when_val.strip() if date_is_incomplete else None
        merged_notes = merge_notes(
            existing_notes=curr_notes,
            incomplete_date=inc_date_arg,
            full_date_resolved=bool(curr_date or date_needs_update),
        )
        if "notes" in fields_by_name:
            if curr_notes != merged_notes:
                diffs.append(FieldDiff(field_name="Notes", old_value=curr_notes, new_value=merged_notes, action=FieldAction.SET))
            else:
                diffs.append(FieldDiff(field_name="Notes", old_value=curr_notes, new_value=curr_notes, action=FieldAction.PRESERVED))

        # Modifications check: if any fields modified, update Last modified and Last modified by
        modified_diffs = [d for d in diffs if d.action == FieldAction.SET]
        if modified_diffs:
            if "last modified by" in fields_by_name:
                diffs.append(FieldDiff(field_name="Last modified by", old_value=_get_text("Last modified by"), new_value=today, action=FieldAction.SET))
            if "last modified" in fields_by_name:
                diffs.append(FieldDiff(field_name="Last modified", old_value=_get_text("Last modified"), new_value=today, action=FieldAction.SET))

        # Always preserve unrelated online fields, statuses, language, import date
        preserved_fields = list(UNRELATED_ONLINE_FIELDS) + [
            "Status Media", "Status thumb", "Status Transcript", "Language", "Created_on", "imported_on"
        ]

        has_conflicts = len(conflicts) > 0
        status = SyncStatus.REVIEW_REQUIRED if has_conflicts else SyncStatus.SYNCING
        op = SyncOperation.UPDATE if modified_diffs else SyncOperation.NOOP

        return MediaDbSyncResult(
            tracking_id=request.tracking_id,
            status=SyncStatus.SYNCED if op == SyncOperation.NOOP else status,
            operation=op,
            media_row_id=row_id,
            precondition_row_snapshot=live_row,
            field_diffs=diffs,
            fields_preserved=preserved_fields,
            fields_modified=[d.field_name for d in modified_diffs],
            conflicts=conflicts,
            diagnostic_notes=diag_notes,
            review_required=has_conflicts,
        )

    def execute_sync(
        self,
        request: MediaDbSyncRequest,
        commit: bool = False,
    ) -> MediaDbSyncResult:
        """Execute synchronization plan with live revalidations and safe commit."""
        # 1. Fetch live fields schema
        try:
            live_fields = self.write_adapter.fetch_table_fields()
        except BaserowUnavailableError as e:
            return self._enrich_result(MediaDbSyncResult(
                tracking_id=request.tracking_id,
                status=SyncStatus.DATABASE_UNAVAILABLE,
                operation=SyncOperation.BLOCKED,
                review_required=True,
                diagnostic_notes=[f"Failed to fetch live table schema: {e}"],
            ), request)
        except Exception as e:
            return self._enrich_result(MediaDbSyncResult(
                tracking_id=request.tracking_id,
                status=SyncStatus.FAILED_RETRYABLE,
                operation=SyncOperation.BLOCKED,
                review_required=True,
                diagnostic_notes=[f"Unexpected error fetching table schema: {e}"],
            ), request)

        # 2. If target row specified, fetch it live
        live_row: Optional[Dict[str, Any]] = None
        if request.selected_media_row_id:
            try:
                live_row = self.write_adapter.fetch_row_raw(request.selected_media_row_id)
            except BaserowUnavailableError as e:
                return self._enrich_result(MediaDbSyncResult(
                    tracking_id=request.tracking_id,
                    status=SyncStatus.DATABASE_UNAVAILABLE,
                    operation=SyncOperation.BLOCKED,
                    media_row_id=request.selected_media_row_id,
                    review_required=True,
                    diagnostic_notes=[f"Failed to fetch live target row {request.selected_media_row_id}: {e}"],
                ), request)
            except Exception as e:
                return self._enrich_result(MediaDbSyncResult(
                    tracking_id=request.tracking_id,
                    status=SyncStatus.FAILED_RETRYABLE,
                    operation=SyncOperation.BLOCKED,
                    media_row_id=request.selected_media_row_id,
                    review_required=True,
                    diagnostic_notes=[f"Unexpected error fetching target row: {e}"],
                ), request)

        # 3. Compute plan & diffs
        plan = self.plan_and_revalidate(request, live_fields, live_row)

        if plan.status in (SyncStatus.DATABASE_UNAVAILABLE, SyncStatus.REVIEW_REQUIRED, SyncStatus.FAILED_BLOCKED):
            return self._enrich_result(plan, request)

        if plan.operation == SyncOperation.NOOP:
            return self._enrich_result(plan, request)

        # If preview / dry-run mode, return plan without mutating database
        if not commit:
            return self._enrich_result(plan, request)

        # 4. Commit Mutations with Pre-Write Guards
        if plan.operation == SyncOperation.CREATE:
            res = self._commit_create(request, plan)
            return self._enrich_result(res, request)
        elif plan.operation == SyncOperation.UPDATE:
            res = self._commit_update(request, plan, live_row)
            return self._enrich_result(res, request)

        return self._enrich_result(plan, request)

    def _commit_create(
        self,
        request: MediaDbSyncRequest,
        plan: MediaDbSyncResult,
    ) -> MediaDbSyncResult:
        """Execute CREATE with pre-create candidate revalidation race guard."""
        # 1. Hard precondition: pre-create candidate revalidation via Tool 2 service (R-001)
        if self.tool2_service is None:
            return MediaDbSyncResult(
                tracking_id=request.tracking_id,
                status=SyncStatus.DATABASE_UNAVAILABLE,
                operation=SyncOperation.BLOCKED,
                review_required=True,
                diagnostic_notes=["Tool 2 service is not configured; cannot revalidate pre-create candidate safely"],
            )

        try:
            fresh_rev = self.tool2_service.review_file(request.tracking_id, force_refresh=True)
        except Exception as e:
            logger.warning(f"Pre-create live check encountered error: {e}")
            return MediaDbSyncResult(
                tracking_id=request.tracking_id,
                status=SyncStatus.DATABASE_UNAVAILABLE,
                operation=SyncOperation.BLOCKED,
                review_required=True,
                error_message=redact_secrets(str(e)),
                diagnostic_notes=[redact_secrets(f"Pre-create live check failed with exception: {e}")],
            )

        valid_rev, err_msg = validate_tool2_review_result(fresh_rev, request.tracking_id)
        if not valid_rev:
            return MediaDbSyncResult(
                tracking_id=request.tracking_id,
                status=SyncStatus.DATABASE_UNAVAILABLE,
                operation=SyncOperation.BLOCKED,
                review_required=True,
                diagnostic_notes=[f"Pre-create race guard: {err_msg}"],
            )

        dec_str = valid_rev.decision.value if hasattr(valid_rev.decision, "value") else str(valid_rev.decision)
        if dec_str != "NEW_MEDIA_CANDIDATE":
            return MediaDbSyncResult(
                tracking_id=request.tracking_id,
                status=SyncStatus.REVIEW_REQUIRED,
                operation=SyncOperation.CONFLICT,
                review_required=True,
                conflicts=["COLLABORATOR_NEW_ROW_CREATED"],
                diagnostic_notes=[
                    f"Pre-create race guard: live Tool 2 check changed from NEW_MEDIA_CANDIDATE to {dec_str}"
                ],
            )

        # Update request metadata with fresh live Tool 2 audit evidence
        read_ts = (valid_rev.baserow_read_at or valid_rev.database_snapshot_at or "").strip()
        request.tool2_timestamp = valid_rev.database_snapshot_at or read_ts
        request.live_query_timestamp = read_ts
        request.tool2_database_state = valid_rev.database_state
        request.tool2_decision = dec_str

        # 2. Re-fetch live table fields to build payload with valid field existence (R-006, R-017)
        try:
            live_fields = self.write_adapter.fetch_table_fields()
        except BaserowUnavailableError as e:
            return MediaDbSyncResult(
                tracking_id=request.tracking_id,
                status=SyncStatus.DATABASE_UNAVAILABLE,
                operation=SyncOperation.BLOCKED,
                review_required=True,
                diagnostic_notes=[f"Failed to fetch live schema in commit_create: {e}"],
            )
        except Exception as e:
            return MediaDbSyncResult(
                tracking_id=request.tracking_id,
                status=SyncStatus.FAILED_RETRYABLE,
                operation=SyncOperation.BLOCKED,
                review_required=True,
                diagnostic_notes=[f"Failed to fetch live schema in commit_create: {e}"],
            )

        try:
            fields_by_name = index_fields_by_name(live_fields)
        except BaserowSchemaError as e:
            return MediaDbSyncResult(
                tracking_id=request.tracking_id,
                status=SyncStatus.FAILED_BLOCKED,
                operation=SyncOperation.BLOCKED,
                review_required=True,
                error_message=str(e),
                diagnostic_notes=[f"Ambiguous or duplicate field in live schema: {e}"],
            )

        # Pre-validate EVERY planned SET field against the live schema snapshot BEFORE any option creation or DB write (R-017)
        payload: Dict[str, Any] = {}
        for diff in plan.field_diffs:
            if diff.action == FieldAction.SET:
                norm_name = diff.field_name.strip().lower()
                if norm_name not in fields_by_name:
                    return MediaDbSyncResult(
                        tracking_id=request.tracking_id,
                        status=SyncStatus.FAILED_BLOCKED,
                        operation=SyncOperation.BLOCKED,
                        review_required=True,
                        error_message=f"Intended field '{diff.field_name}' not found in live table schema",
                        diagnostic_notes=[f"Schema mismatch: field '{diff.field_name}' missing from live table"],
                    )
                f_def = fields_by_name[norm_name]
                if f_def.get("read_only"):
                    return MediaDbSyncResult(
                        tracking_id=request.tracking_id,
                        status=SyncStatus.FAILED_BLOCKED,
                        operation=SyncOperation.BLOCKED,
                        review_required=True,
                        error_message=f"Field '{diff.field_name}' is read-only",
                        diagnostic_notes=[f"Schema error: field '{diff.field_name}' is read-only"],
                    )
                try:
                    validate_field_schema(f_def["name"], diff.new_value, f_def)
                except BaserowSchemaError as e:
                    return MediaDbSyncResult(
                        tracking_id=request.tracking_id,
                        status=SyncStatus.FAILED_BLOCKED,
                        operation=SyncOperation.BLOCKED,
                        review_required=True,
                        error_message=str(e),
                        diagnostic_notes=[f"Deterministic schema validation error on field '{diff.field_name}': {e}"],
                    )
                payload[f_def["name"]] = diff.new_value

        # 3. Ensure Country / Place, location select options only after full payload schema validation
        for fld_name in ("Country", "Place, location"):
            val = payload.get(fld_name)
            if val and isinstance(val, str):
                try:
                    canonical_opt = self.write_adapter.ensure_select_option(fld_name, val)
                    payload[fld_name] = canonical_opt
                except AmbiguousOptionError as e:
                    return MediaDbSyncResult(
                        tracking_id=request.tracking_id,
                        status=SyncStatus.REVIEW_REQUIRED,
                        operation=SyncOperation.CONFLICT,
                        review_required=True,
                        conflicts=[str(e)],
                        diagnostic_notes=[f"Ambiguous select option in {fld_name}: {e}"],
                    )
                except (BaserowSchemaError, TaxonomyForbiddenError) as e:
                    return MediaDbSyncResult(
                        tracking_id=request.tracking_id,
                        status=SyncStatus.FAILED_BLOCKED,
                        operation=SyncOperation.BLOCKED,
                        review_required=True,
                        error_message=str(e),
                        diagnostic_notes=[f"Schema error ensuring select option for {fld_name}: {e}"],
                    )
                except Exception as e:
                    return MediaDbSyncResult(
                        tracking_id=request.tracking_id,
                        status=SyncStatus.FAILED_RETRYABLE,
                        operation=SyncOperation.CONFLICT,
                        review_required=True,
                        diagnostic_notes=[f"Failed to ensure select option for {fld_name}: {e}"],
                    )

        # 4. Perform write
        try:
            created_row = self.write_adapter.create_row(payload)
            new_id = created_row.get("id")
            plan.media_row_id = new_id
            plan.status = SyncStatus.SYNCED
            return plan
        except BaserowUnavailableError as e:
            return self.reconcile_uncertain_create(request, plan, error_message=str(e))
        except Exception as e:
            plan.status = SyncStatus.FAILED_RETRYABLE
            plan.error_message = redact_secrets(str(e))
            return plan

    def _commit_update(
        self,
        request: MediaDbSyncRequest,
        plan: MediaDbSyncResult,
        initial_live_row: Optional[Dict[str, Any]],
    ) -> MediaDbSyncResult:
        """Execute UPDATE with pre-update relevant-field race guard and minimal PATCH."""
        row_id = plan.media_row_id
        if not row_id:
            plan.status = SyncStatus.FAILED_BLOCKED
            return plan

        # R-031: Fresh Tool 2 write gate before update mutation
        if self.tool2_service is not None:
            try:
                fresh_rev = self.tool2_service.review_file(request.tracking_id, force_refresh=True)
            except Exception as e:
                logger.warning(f"Pre-update Tool 2 live check encountered error: {e}")
                return self._enrich_result(MediaDbSyncResult(
                    tracking_id=request.tracking_id,
                    status=SyncStatus.DATABASE_UNAVAILABLE,
                    operation=SyncOperation.BLOCKED,
                    media_row_id=row_id,
                    review_required=True,
                    error_message=redact_secrets(str(e)),
                    diagnostic_notes=[redact_secrets(f"Pre-update Tool 2 live check failed with exception: {e}")],
                ), request)

            valid_rev, err_msg = validate_tool2_review_result(fresh_rev, request.tracking_id)
            if not valid_rev:
                return self._enrich_result(MediaDbSyncResult(
                    tracking_id=request.tracking_id,
                    status=SyncStatus.DATABASE_UNAVAILABLE,
                    operation=SyncOperation.BLOCKED,
                    media_row_id=row_id,
                    review_required=True,
                    diagnostic_notes=[f"Pre-update Tool 2 gate contract failure: {err_msg}"],
                ), request)

            # Update request metadata with fresh live Tool 2 audit evidence
            read_ts = (valid_rev.baserow_read_at or valid_rev.database_snapshot_at or "").strip()
            request.tool2_timestamp = valid_rev.database_snapshot_at or read_ts
            request.live_query_timestamp = read_ts
            request.tool2_database_state = valid_rev.database_state
            t2_decision_val = valid_rev.decision.value if hasattr(valid_rev.decision, "value") else str(valid_rev.decision)
            request.tool2_decision = t2_decision_val

            # Validate decision gate for update (R-031)
            assoc_approval = request.get_association_approval()
            if assoc_approval is None:
                # Without association approval, fresh review MUST be EXISTING_MEDIA_MATCH with exact same row_id
                if t2_decision_val != "EXISTING_MEDIA_MATCH":
                    return self._enrich_result(MediaDbSyncResult(
                        tracking_id=request.tracking_id,
                        status=SyncStatus.REVIEW_REQUIRED,
                        operation=SyncOperation.CONFLICT,
                        media_row_id=row_id,
                        review_required=True,
                        conflicts=["TOOL2_DECISION_CHANGED"],
                        diagnostic_notes=[
                            f"Pre-update Tool 2 gate: live decision changed from EXISTING_MEDIA_MATCH to {t2_decision_val}; update blocked"
                        ],
                    ), request)
                if valid_rev.selected_media_row_id != row_id:
                    return self._enrich_result(MediaDbSyncResult(
                        tracking_id=request.tracking_id,
                        status=SyncStatus.REVIEW_REQUIRED,
                        operation=SyncOperation.CONFLICT,
                        media_row_id=row_id,
                        review_required=True,
                        conflicts=["TOOL2_SELECTED_ROW_CHANGED"],
                        diagnostic_notes=[
                            f"Pre-update Tool 2 gate: live selected row ID changed from {row_id} to {valid_rev.selected_media_row_id}; update blocked"
                        ],
                    ), request)

        # 1. Fetch fresh live row immediately before write
        try:
            fresh_row = self.write_adapter.fetch_row_raw(row_id)
        except Exception as e:
            plan.status = SyncStatus.FAILED_RETRYABLE
            plan.error_message = redact_secrets(f"Failed to re-fetch live row before update: {e}")
            return plan

        if not fresh_row:
            plan.status = SyncStatus.FAILED_BLOCKED
            plan.error_message = f"Target row {row_id} no longer exists"
            return plan

        # R-033: Association approval precondition revalidation against live row
        assoc_approval = request.get_association_approval()
        if assoc_approval is not None:
            if assoc_approval.reviewed_candidate_row_id != fresh_row.get("id"):
                return self._enrich_result(MediaDbSyncResult(
                    tracking_id=request.tracking_id,
                    status=SyncStatus.REVIEW_REQUIRED,
                    operation=SyncOperation.CONFLICT,
                    media_row_id=row_id,
                    review_required=True,
                    conflicts=["ASSOCIATION_PRECONDITION_FAILED"],
                    diagnostic_notes=[
                        f"Reviewed candidate row ID {assoc_approval.reviewed_candidate_row_id} does not match live row ID {fresh_row.get('id')}"
                    ],
                ), request)
            if not assoc_approval.reviewed_precondition_filename:
                return self._enrich_result(MediaDbSyncResult(
                    tracking_id=request.tracking_id,
                    status=SyncStatus.REVIEW_REQUIRED,
                    operation=SyncOperation.CONFLICT,
                    media_row_id=row_id,
                    review_required=True,
                    conflicts=["ASSOCIATION_PRECONDITION_FAILED"],
                    diagnostic_notes=[
                        "Association approval missing reviewed_precondition_filename fingerprint"
                    ],
                ), request)
            live_filename = str(fresh_row.get("Filename") or "").strip()
            precond_filename = str(assoc_approval.reviewed_precondition_filename or "").strip()
            if precond_filename != live_filename:
                return self._enrich_result(MediaDbSyncResult(
                    tracking_id=request.tracking_id,
                    status=SyncStatus.REVIEW_REQUIRED,
                    operation=SyncOperation.CONFLICT,
                    media_row_id=row_id,
                    review_required=True,
                    conflicts=["ASSOCIATION_PRECONDITION_FAILED"],
                    diagnostic_notes=[
                        f"Association precondition filename '{precond_filename}' does not match live row Filename '{live_filename}'"
                    ],
                ), request)

        # Helper to extract normalized comparison value
        def _get_row_field_value(row: Optional[Dict[str, Any]], field_name: str) -> Any:
            if not row:
                return None
            val = row.get(field_name)
            if val is None:
                for k, v in row.items():
                    if k.strip().lower() == field_name.strip().lower():
                        val = v
                        break
            if val is None:
                return None
            if isinstance(val, dict) and "value" in val:
                return str(val["value"]).strip()
            if isinstance(val, list):
                items = []
                for it in val:
                    if isinstance(it, dict) and "value" in it:
                        items.append(str(it["value"]).strip())
                    elif isinstance(it, str):
                        items.append(it.strip())
                    else:
                        items.append(str(it))
                return sorted(items)
            if isinstance(val, str):
                return val.strip()
            return val

        # 2. Check relevant preconditions on ALL fields Tool 4 intends to modify (R-002)
        if initial_live_row:
            planned_modified_fields = {
                diff.field_name for diff in plan.field_diffs
                if diff.action == FieldAction.SET and diff.field_name not in ("Last modified", "Last modified by")
            }
            for fld in planned_modified_fields:
                old_val = _get_row_field_value(initial_live_row, fld)
                new_val = _get_row_field_value(fresh_row, fld)
                if old_val != new_val:
                    return MediaDbSyncResult(
                        tracking_id=request.tracking_id,
                        status=SyncStatus.REVIEW_REQUIRED,
                        operation=SyncOperation.CONFLICT,
                        media_row_id=row_id,
                        review_required=True,
                        conflicts=[f"COLLABORATOR_CONFLICT: Field '{fld}' changed concurrently from '{old_val}' to '{new_val}'"],
                        diagnostic_notes=["Pre-update revalidation failed: collaborator changed planned-modified field"],
                    )

        # 3. Build minimal PATCH payload, verifying existence against fresh live schema (R-006, R-017)
        try:
            live_fields = self.write_adapter.fetch_table_fields()
        except BaserowUnavailableError as e:
            return MediaDbSyncResult(
                tracking_id=request.tracking_id,
                status=SyncStatus.DATABASE_UNAVAILABLE,
                operation=SyncOperation.BLOCKED,
                review_required=True,
                diagnostic_notes=[f"Failed to fetch live schema in commit_update: {e}"],
            )
        except Exception as e:
            return MediaDbSyncResult(
                tracking_id=request.tracking_id,
                status=SyncStatus.FAILED_RETRYABLE,
                operation=SyncOperation.BLOCKED,
                review_required=True,
                diagnostic_notes=[f"Failed to fetch live schema in commit_update: {e}"],
            )

        try:
            fields_by_name = index_fields_by_name(live_fields)
        except BaserowSchemaError as e:
            return MediaDbSyncResult(
                tracking_id=request.tracking_id,
                status=SyncStatus.FAILED_BLOCKED,
                operation=SyncOperation.BLOCKED,
                review_required=True,
                error_message=str(e),
                diagnostic_notes=[f"Deterministic schema error in commit_update: {e}"],
            )

        # Pre-validate EVERY planned SET field against the live schema snapshot BEFORE any option creation or DB write (R-017)
        payload: Dict[str, Any] = {}
        for diff in plan.field_diffs:
            if diff.action == FieldAction.SET:
                norm_name = diff.field_name.strip().lower()
                if norm_name not in fields_by_name:
                    return MediaDbSyncResult(
                        tracking_id=request.tracking_id,
                        status=SyncStatus.FAILED_BLOCKED,
                        operation=SyncOperation.BLOCKED,
                        review_required=True,
                        error_message=f"Intended field '{diff.field_name}' not found in live table schema",
                        diagnostic_notes=[f"Schema mismatch: field '{diff.field_name}' missing from live table"],
                    )
                f_def = fields_by_name[norm_name]
                if f_def.get("read_only"):
                    return MediaDbSyncResult(
                        tracking_id=request.tracking_id,
                        status=SyncStatus.FAILED_BLOCKED,
                        operation=SyncOperation.BLOCKED,
                        review_required=True,
                        error_message=f"Field '{diff.field_name}' is read-only",
                        diagnostic_notes=[f"Schema error: field '{diff.field_name}' is read-only"],
                    )
                try:
                    validate_field_schema(f_def["name"], diff.new_value, f_def)
                except BaserowSchemaError as e:
                    return MediaDbSyncResult(
                        tracking_id=request.tracking_id,
                        status=SyncStatus.FAILED_BLOCKED,
                        operation=SyncOperation.BLOCKED,
                        media_row_id=row_id,
                        review_required=True,
                        error_message=str(e),
                        diagnostic_notes=[f"Deterministic schema validation error on field '{diff.field_name}': {e}"],
                    )
                payload[f_def["name"]] = diff.new_value

        # 4. Ensure select options if country / location are being set
        for fld_name in ("Country", "Place, location"):
            val = payload.get(fld_name)
            if val and isinstance(val, str):
                try:
                    canonical_opt = self.write_adapter.ensure_select_option(fld_name, val)
                    payload[fld_name] = canonical_opt
                except AmbiguousOptionError as e:
                    return MediaDbSyncResult(
                        tracking_id=request.tracking_id,
                        status=SyncStatus.REVIEW_REQUIRED,
                        operation=SyncOperation.CONFLICT,
                        media_row_id=row_id,
                        review_required=True,
                        conflicts=[str(e)],
                        diagnostic_notes=[f"Ambiguous select option in {fld_name}: {e}"],
                    )
                except (BaserowSchemaError, TaxonomyForbiddenError) as e:
                    return MediaDbSyncResult(
                        tracking_id=request.tracking_id,
                        status=SyncStatus.FAILED_BLOCKED,
                        operation=SyncOperation.BLOCKED,
                        media_row_id=row_id,
                        review_required=True,
                        error_message=str(e),
                        diagnostic_notes=[f"Schema error ensuring select option for {fld_name}: {e}"],
                    )

        # 5. Send minimal PATCH
        try:
            self.write_adapter.patch_row(row_id, payload)
            plan.status = SyncStatus.SYNCED
            return plan
        except BaserowUnavailableError as e:
            return self.reconcile_uncertain_update(request, plan, payload, error_message=str(e))
        except Exception as e:
            plan.status = SyncStatus.FAILED_RETRYABLE
            plan.error_message = redact_secrets(str(e))
            return plan

    def reconcile_uncertain_create(
        self,
        request: MediaDbSyncRequest,
        plan: MediaDbSyncResult,
        error_message: str,
    ) -> MediaDbSyncResult:
        """Reconcile uncertain transport outcome for create without blind duplicate creation."""
        logger.info(f"Reconciling uncertain create for {request.tracking_id}...")
        try:
            if self.tool2_service is not None:
                rev = self.tool2_service.review_file(request.tracking_id, force_refresh=True)
                if rev and rev.selected_media_row_id:
                    plan.media_row_id = rev.selected_media_row_id
                    plan.status = SyncStatus.SYNCED
                    plan.diagnostic_notes.append("Reconciled uncertain create: row was confirmed created in live table")
                    return plan
        except Exception as e:
            logger.warning(f"Reconciliation query failed: {e}")

        plan.status = SyncStatus.FAILED_RETRYABLE
        plan.error_message = redact_secrets(f"Create timeout (transport uncertain): {error_message}")
        return plan

    def reconcile_uncertain_update(
        self,
        request: MediaDbSyncRequest,
        plan: MediaDbSyncResult,
        intended_payload: Dict[str, Any],
        error_message: str,
    ) -> MediaDbSyncResult:
        """Reconcile uncertain transport outcome for update by checking if minimal PATCH was applied."""
        row_id = plan.media_row_id
        if not row_id:
            plan.status = SyncStatus.FAILED_RETRYABLE
            plan.error_message = redact_secrets(error_message)
            return plan

        try:
            row = self.write_adapter.fetch_row_raw(row_id)
            if row:
                all_applied = True
                for k, v in intended_payload.items():
                    curr_val = row.get(k)
                    if isinstance(curr_val, dict) and "value" in curr_val:
                        curr_val = curr_val["value"]
                    if curr_val != v:
                        all_applied = False
                        break
                if all_applied:
                    plan.status = SyncStatus.SYNCED
                    plan.diagnostic_notes.append("Reconciled uncertain update: changes were verified applied in live row")
                    return plan
        except Exception as e:
            logger.warning(f"Reconciliation fetch failed: {e}")

        plan.status = SyncStatus.FAILED_RETRYABLE
        plan.error_message = redact_secrets(f"Update timeout (transport uncertain): {error_message}")
        return plan
