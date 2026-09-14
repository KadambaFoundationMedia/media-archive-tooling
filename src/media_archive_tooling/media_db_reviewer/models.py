"""Data models and schemas for Tool 2 (Media Database Reviewer)."""
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class FieldComparisonState(str, Enum):
    """Result of comparing a single incoming field against a Baserow field."""
    AGREES = "AGREES"
    DATABASE_MISSING = "DATABASE_MISSING"
    LOCAL_MISSING = "LOCAL_MISSING"
    CONFLICT = "CONFLICT"
    NOT_COMPARABLE = "NOT_COMPARABLE"


class ReviewDecision(str, Enum):
    """Categorical decision produced by Tool 2 reconciliation."""
    EXISTING_MEDIA_MATCH = "EXISTING_MEDIA_MATCH"
    PROBABLE_EXISTING_MEDIA = "PROBABLE_EXISTING_MEDIA"
    NEW_MEDIA_CANDIDATE = "NEW_MEDIA_CANDIDATE"
    MULTIPLE_CANDIDATES = "MULTIPLE_CANDIDATES"
    CONFLICT_WITH_EXISTING = "CONFLICT_WITH_EXISTING"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"


class Tool4Action(str, Enum):
    """Proposed mutation action for downstream Tool 4 (Media Database Updater)."""
    LINK_EXISTING = "link_existing"
    ENRICH_EXISTING = "enrich_existing"
    CREATE_NEW = "create_new"
    NO_WRITE = "no_write"
    NEEDS_REVIEW = "needs_review"


class FieldComparison(BaseModel):
    """Comparison outcome for one specific field."""
    field_name: str
    state: FieldComparisonState
    local_value: Any = None
    database_value: Any = None
    details: Optional[str] = None


class MediaCandidate(BaseModel):
    """A plausible Baserow Media row evaluated as a match candidate."""
    media_row_id: int
    raw_row: Dict[str, Any] = Field(default_factory=dict)
    normalized_row: Dict[str, Any] = Field(default_factory=dict)
    retrieval_reasons: List[str] = Field(default_factory=list)
    identity_evidence: List[str] = Field(default_factory=list)
    field_comparisons: Dict[str, FieldComparison] = Field(default_factory=dict)
    conflicts: List[str] = Field(default_factory=list)
    possible_enrichments: Dict[str, Any] = Field(default_factory=dict)
    category_title_context: List[str] = Field(default_factory=list)
    travel_schedule_context: List[str] = Field(default_factory=list)
    score: float = 0.0


class RenamerEnrichment(BaseModel):
    """Confirmed enrichment data destined for Renamer Enrich pass.

    Candidate-only metadata must never set confirmed=True.
    """
    confirmed: bool = False
    media_row_id: Optional[int] = None
    when_val: Optional[str] = None
    what_val: Optional[str] = None
    title_full: Optional[str] = None
    where_val: Optional[str] = None
    category: Optional[str] = None
    source_identifiers: List[str] = Field(default_factory=list)
    evidence: List[str] = Field(default_factory=list)
    baserow_read_at: Optional[str] = None
    live_read_complete: bool = False


class MediaDatabaseReviewResult(BaseModel):
    """Comprehensive, typed, serializable result of Tool 2 review for one media item."""
    tracking_id: str
    database_state: str = "DATABASE_UNAVAILABLE"  # LIVE_CURRENT, LIVE_PARTIAL_OR_FAILED, DATABASE_UNAVAILABLE
    database_snapshot_at: str = ""
    baserow_read_at: str = ""
    snapshot_complete: bool = False
    live_read_complete: bool = False
    baserow_check_complete: bool = False

    decision: ReviewDecision = ReviewDecision.DATABASE_UNAVAILABLE
    decision_state: str = ""
    selected_media_row_id: Optional[int] = None

    candidates: List[MediaCandidate] = Field(default_factory=list)
    selected_field_evidence: Dict[str, Any] = Field(default_factory=dict)
    renamer_enrichment: RenamerEnrichment = Field(default_factory=RenamerEnrichment)
    proposed_tool4_action: Tool4Action = Tool4Action.NO_WRITE

    downstream_routing: List[str] = Field(default_factory=list)
    review_required: bool = False
    review_required_now: bool = False
    review_reasons: List[str] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)
    diagnostic_notes: List[str] = Field(default_factory=list)
    evidence: List[str] = Field(default_factory=list)


class BaserowSnapshot(BaseModel):
    """Serializable snapshot/response of relevant Baserow tables for audit or testing."""
    snapshot_at: str
    state: str  # LIVE_CURRENT, LIVE_PARTIAL_OR_FAILED, DATABASE_UNAVAILABLE
    complete: bool
    media_rows: List[Dict[str, Any]] = Field(default_factory=list)
    category_title_rows: List[Dict[str, Any]] = Field(default_factory=list)
    travel_schedule_rows: List[Dict[str, Any]] = Field(default_factory=list)
    schema_version: str = "1.0"
