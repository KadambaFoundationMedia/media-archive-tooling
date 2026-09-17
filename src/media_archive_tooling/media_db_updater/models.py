"""Typed data models and contracts for Tool 4 — Media Database Updater."""
from enum import Enum
from typing import Any, Dict, List, Optional
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


class FieldAction(str, Enum):
    """Disposition of an individual field during synchronization."""
    SET = "SET"
    PRESERVED = "PRESERVED"
    CONFLICT = "CONFLICT"
    IGNORED = "IGNORED"
    SKIPPED = "SKIPPED"


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

    # Stable identity & table target
    request_id: Optional[str] = None
    request_fingerprint: Optional[str] = None
    table_id: Optional[str] = None

    # Tool 1 metadata & resolution states
    when_val: Optional[str] = None
    when_state: Optional[str] = None
    when_provenance: Optional[Dict[str, Any]] = None

    what_val: Optional[str] = None
    what_category: Optional[str] = None
    what_verse: Optional[str] = None
    what_state: Optional[str] = None
    what_provenance: Optional[Dict[str, Any]] = None

    who_val: Optional[str] = None

    where_val: Optional[str] = None
    where_place: Optional[str] = None
    where_country: Optional[str] = None
    where_country_iso: Optional[str] = None
    where_state: Optional[str] = None
    where_provenance: Optional[Dict[str, Any]] = None

    # Contextual fallbacks & tool evidence
    parent_folder_context: Optional[str] = None
    tool2_decision: Optional[str] = None
    tool2_timestamp: Optional[str] = None
    selected_media_row_id: Optional[int] = None
    tool3_decision: Optional[str] = None
    tool3_evidence: Optional[Dict[str, Any]] = None
    live_query_timestamp: Optional[str] = None

    # Field-specific human review intent and preconditions
    is_human_approved: bool = False
    field_approvals: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    reviewer_notes: Optional[str] = None


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
