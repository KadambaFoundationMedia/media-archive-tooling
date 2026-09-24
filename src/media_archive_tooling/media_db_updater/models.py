"""Typed data models and contracts for Tool 4 — Media Database Updater."""
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field


class SyncStatus(str, Enum):
    """Lifecycle status of a local file's Media database synchronization."""
    PREVIEW = "PREVIEW"
    PENDING_SYNC = "PENDING_SYNC"
    SYNCING = "SYNCING"
    SYNCED = "SYNCED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_BLOCKED = "FAILED_BLOCKED"


class SyncOperation(str, Enum):
    """Action taken or proposed against the Baserow Media database."""
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    NOOP = "NOOP"
    CONFLICT = "CONFLICT"
    BLOCKED = "BLOCKED"


class TestRowStatus(str, Enum):
    """Lifecycle status of a test-created row in the alpha/beta test row ledger."""
    __test__ = False
    CREATED = "CREATED"
    PURGING = "PURGING"
    PURGED = "PURGED"
    PURGE_BLOCKED = "PURGE_BLOCKED"


class TestRowLedgerEntry(BaseModel):
    """Durable record of a Baserow row created during alpha/beta testing."""
    __test__ = False
    table_id: str
    row_id: int
    tracking_id: str
    run_id: Optional[str] = None
    created_at: str
    request_fingerprint: str
    session_id: str
    marker: str
    status: TestRowStatus = TestRowStatus.CREATED
    error_message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    updated_at: str


class PurgeItemResult(BaseModel):
    """Outcome of purging an individual test row."""
    row_id: int
    table_id: str
    status: TestRowStatus
    action: str  # "DELETED", "ALREADY_ABSENT", "BLOCKED", "WOULD_DELETE"
    reason: Optional[str] = None


class PurgeSummary(BaseModel):
    """Aggregate summary of a test row purge operation."""
    deleted_count: int = 0
    already_absent_count: int = 0
    blocked_count: int = 0
    total_processed: int = 0
    results: List[PurgeItemResult] = Field(default_factory=list)

    @property
    def deleted(self) -> int:
        return self.deleted_count

    @property
    def already_absent(self) -> int:
        return self.already_absent_count

    @property
    def blocked(self) -> int:
        return self.blocked_count

    @property
    def items(self) -> List[PurgeItemResult]:
        return self.results


class FieldAction(str, Enum):
    """Disposition of an individual field during synchronization."""
    SET = "SET"
    PRESERVED = "PRESERVED"
    CONFLICT = "CONFLICT"
    IGNORED = "IGNORED"
    SKIPPED = "SKIPPED"


class FieldApprovalAction(str, Enum):
    """Section 18 explicit field-level review actions."""
    KEEP_DATABASE = "keep_database"
    APPLY_CORRECTION = "apply_correction"
    CHOOSE_ASSOCIATION = "choose_association"
    DEFER = "defer"
    CONFIRM_NEW = "confirm_new"

    @classmethod
    def from_value(cls, val: Any) -> "FieldApprovalAction":
        """Parse string or enum into canonical FieldApprovalAction; raises ValueError on unknown/missing."""
        if isinstance(val, cls):
            return val
        if not val or not isinstance(val, str):
            raise ValueError(f"Invalid field approval action: {val!r}")
        clean = val.strip().lower()
        for member in cls:
            if member.value == clean or member.name.lower() == clean:
                return member
        raise ValueError(f"Invalid field approval action: {val}")


class FieldApproval(BaseModel):
    """Explicit human approval for a single field with reviewed precondition and action provenance."""
    field_name: str
    action: FieldApprovalAction
    approved_value: Optional[Any] = None
    # Must be explicitly true when the reviewer inspected the live database value
    has_reviewed_precondition: bool = False
    # Exact reviewed database value (can be None or "" for an explicitly reviewed blank precondition)
    reviewed_precondition_value: Optional[Any] = None
    reviewer: Optional[str] = "human_reviewer"
    reviewed_at: Optional[str] = None
    notes: Optional[str] = None


class FieldApprovalInput(BaseModel):
    """Strict validated input model for field approvals at the service boundary."""
    field_name: str
    action: FieldApprovalAction
    approved_value: Optional[Any] = None
    has_reviewed_precondition: bool = False
    reviewed_precondition_value: Optional[Any] = None
    reviewer: Optional[str] = "human_reviewer"
    reviewed_at: Optional[str] = None
    notes: Optional[str] = None


class AssociationApproval(BaseModel):
    """Explicit human approval to associate a file with an existing Baserow Media row."""
    selected_media_row_id: int
    action: FieldApprovalAction = FieldApprovalAction.CHOOSE_ASSOCIATION
    has_reviewed_precondition: bool = True
    reviewed_candidate_row_id: int
    reviewed_precondition_filename: Optional[str] = None
    reviewer: Optional[str] = "human_reviewer"
    reviewed_at: Optional[str] = None
    notes: Optional[str] = None


class FieldDiff(BaseModel):
    """Audit diff for a single field modified, preserved, or conflicting."""
    field_name: str
    old_value: Optional[Any] = None
    new_value: Optional[Any] = None
    action: FieldAction = FieldAction.SET
    details: Optional[str] = None


class MediaDbSyncRequest(BaseModel):
    """Input payload representing current committed archive-file state for sync."""
    tracking_id: str
    current_filename: str
    current_path: str
    original_filename: Optional[str] = None
    original_path: Optional[str] = None
    previous_filename: Optional[str] = None
    previous_path: Optional[str] = None
    audio_file_path: Optional[str] = None

    # Stable identity & table target
    request_id: Optional[str] = None
    request_fingerprint: Optional[str] = None
    table_id: Optional[str] = None

    # Alpha/Beta test tracking and cleanup provenance
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    test_marker: Optional[str] = None
    is_test_row: bool = True

    # Tool 1 metadata & resolution states
    when_val: Optional[str] = None
    when_state: Optional[str] = None
    when_provenance: Optional[List[Dict[str, Any]]] = None

    what_val: Optional[str] = None
    what_category: Optional[str] = None
    what_verse: Optional[str] = None
    what_state: Optional[str] = None
    what_provenance: Optional[List[Dict[str, Any]]] = None

    who_val: Optional[str] = None

    where_val: Optional[str] = None
    where_place: Optional[str] = None
    where_country: Optional[str] = None
    where_country_iso: Optional[str] = None
    where_state: Optional[str] = None
    where_provenance: Optional[List[Dict[str, Any]]] = None

    # Contextual fallbacks & tool evidence
    parent_folder_context: Optional[str] = None
    tool2_decision: Optional[str] = None
    tool2_timestamp: Optional[str] = None
    tool2_database_state: Optional[str] = None
    selected_media_row_id: Optional[int] = None
    association_approval: Optional[Union[AssociationApproval, Dict[str, Any]]] = None
    tool3_decision: Optional[str] = None
    tool3_evidence: Optional[Dict[str, Any]] = None
    live_query_timestamp: Optional[str] = None

    # Field-specific human review intent and preconditions
    # NOTE: is_human_approved is purely informational / legacy. It NEVER authorizes semantic field writes.
    is_human_approved: bool = False
    field_approvals: Dict[str, Union[FieldApproval, Dict[str, Any]]] = Field(default_factory=dict)
    reviewer_notes: Optional[str] = None

    def get_approval(self, field_name: str) -> Optional[FieldApproval]:
        """Extract and strictly validate field approval for a specific field name."""
        raw = self.field_approvals.get(field_name)
        if raw is None:
            # Check case-insensitively
            for k, v in self.field_approvals.items():
                if k.strip().lower() == field_name.strip().lower():
                    raw = v
                    break
        if raw is None:
            return None
        if isinstance(raw, FieldApproval):
            return raw
        if not isinstance(raw, dict):
            return None

        has_pre = raw.get("has_reviewed_precondition")
        if has_pre is None:
            has_pre = False
        else:
            has_pre = bool(has_pre)

        action_raw = raw.get("action")
        if not action_raw:
            return None
        try:
            action_val = FieldApprovalAction.from_value(action_raw)
        except ValueError:
            return None

        return FieldApproval(
            field_name=field_name,
            action=action_val,
            approved_value=raw.get("approved_value"),
            has_reviewed_precondition=has_pre,
            reviewed_precondition_value=raw.get("reviewed_precondition_value") if has_pre else None,
            reviewer=raw.get("reviewer", "human_reviewer"),
            reviewed_at=raw.get("reviewed_at"),
            notes=raw.get("notes"),
        )

    def get_association_approval(self) -> Optional[AssociationApproval]:
        """Extract and validate explicit association approval."""
        raw = self.association_approval
        if raw is None:
            raw_field = self.field_approvals.get("association")
            if isinstance(raw_field, dict):
                act_str = raw_field.get("action")
                if act_str and str(act_str).strip().lower() in ("choose_association", "choose_association"):
                    raw = raw_field
        if raw is None:
            return None
        if isinstance(raw, AssociationApproval):
            return raw
        if not isinstance(raw, dict):
            return None

        has_pre = raw.get("has_reviewed_precondition")
        if has_pre is not True:
            return None

        sel_id = raw.get("selected_media_row_id") or self.selected_media_row_id
        cand_id = raw.get("reviewed_candidate_row_id")
        if not sel_id or not cand_id or int(sel_id) != int(cand_id):
            return None

        action_raw = raw.get("action", FieldApprovalAction.CHOOSE_ASSOCIATION)
        try:
            act = FieldApprovalAction.from_value(action_raw)
        except ValueError:
            return None
        if act != FieldApprovalAction.CHOOSE_ASSOCIATION:
            return None

        return AssociationApproval(
            selected_media_row_id=int(sel_id),
            action=act,
            has_reviewed_precondition=True,
            reviewed_candidate_row_id=int(cand_id),
            reviewed_precondition_filename=raw.get("reviewed_precondition_filename"),
            reviewer=raw.get("reviewer", "human_reviewer"),
            reviewed_at=raw.get("reviewed_at"),
            notes=raw.get("notes"),
        )


class MediaDbSyncResult(BaseModel):
    """Comprehensive, serializable result and audit record of a sync operation."""
    tracking_id: str
    status: SyncStatus
    operation: SyncOperation
    media_row_id: Optional[int] = None

    # Request & table identity
    request_id: Optional[str] = None
    request_fingerprint: Optional[str] = None
    table_id: Optional[str] = None

    # Audit & diffs
    precondition_row_snapshot: Optional[Dict[str, Any]] = None
    field_diffs: List[FieldDiff] = Field(default_factory=list)
    fields_preserved: List[str] = Field(default_factory=list)
    fields_modified: List[str] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)
    diagnostic_notes: List[str] = Field(default_factory=list)
    audit_provenance: Dict[str, Any] = Field(default_factory=dict)

    # Operational lifecycle metadata
    attempt_count: int = 1
    last_attempt_at: Optional[str] = None
    error_message: Optional[str] = None
    review_required: bool = False
    live_row: Optional[Dict[str, Any]] = None
