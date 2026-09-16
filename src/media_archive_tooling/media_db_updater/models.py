"""Typed data models and contracts for Tool 4 — Media Database Updater."""
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class SyncStatus(str, Enum):
    """Lifecycle status of a local file's Media database synchronization."""
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

    # Tool 1 metadata & resolution states
    when_val: Optional[str] = None
    when_state: Optional[str] = None
    what_val: Optional[str] = None
    what_category: Optional[str] = None
    what_verse: Optional[str] = None
    who_val: Optional[str] = None
    where_val: Optional[str] = None
    where_place: Optional[str] = None
    where_country: Optional[str] = None
    where_country_iso: Optional[str] = None
    where_state: Optional[str] = None

    # Contextual fallbacks & tool evidence
    parent_folder_context: Optional[str] = None
    tool2_decision: Optional[str] = None
    selected_media_row_id: Optional[int] = None
    tool3_decision: Optional[str] = None
    tool3_evidence: Optional[Dict[str, Any]] = None

    # Human review intent
    is_human_approved: bool = False
    reviewer_notes: Optional[str] = None


class MediaDbSyncResult(BaseModel):
    """Comprehensive, serializable result and audit record of a sync operation."""
    tracking_id: str
    status: SyncStatus
    operation: SyncOperation
    media_row_id: Optional[int] = None

    # Audit & diffs
    precondition_row_snapshot: Optional[Dict[str, Any]] = None
    field_diffs: List[FieldDiff] = Field(default_factory=list)
    fields_preserved: List[str] = Field(default_factory=list)
    fields_modified: List[str] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)
    diagnostic_notes: List[str] = Field(default_factory=list)

    # Operational lifecycle metadata
    attempt_count: int = 1
    last_attempt_at: Optional[str] = None
    error_message: Optional[str] = None
    review_required: bool = False
