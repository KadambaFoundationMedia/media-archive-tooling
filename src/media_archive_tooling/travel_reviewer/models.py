"""Data models and schemas for Tool 3 (Travel Schedule Reviewer)."""
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from ..renamer.models import ResolutionState


class TravelReviewDecision(str, Enum):
    """Categorical decision produced by Tool 3 travel schedule review."""
    CORROBORATED = "CORROBORATED"
    PROVISIONAL_ENRICHMENT = "PROVISIONAL_ENRICHMENT"
    MULTIPLE_SCHEDULE_CANDIDATES = "MULTIPLE_SCHEDULE_CANDIDATES"
    SCHEDULE_CONFLICT = "SCHEDULE_CONFLICT"
    NO_SCHEDULE_SUPPORT = "NO_SCHEDULE_SUPPORT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    REFERENCE_UNAVAILABLE = "REFERENCE_UNAVAILABLE"


class FieldComparisonState(str, Enum):
    """Comparison outcome for a single field."""
    AGREES = "AGREES"
    CONFLICT = "CONFLICT"
    SCHEDULE_MISSING = "SCHEDULE_MISSING"
    LOCAL_MISSING = "LOCAL_MISSING"
    NOT_COMPARABLE = "NOT_COMPARABLE"


class NormalizedTravelRow(BaseModel):
    """Normalized representation of a single travel_schedule Baserow row."""
    id: int
    start_date: str = ""  # ISO YYYY-MM-DD or partial
    end_date: str = ""    # ISO YYYY-MM-DD or partial
    place: str = ""
    country: str = ""
    country_iso2: Optional[str] = None
    schedule_text: str = ""
    raw_row: Dict[str, Any] = Field(default_factory=dict)


class TravelCandidate(BaseModel):
    """A plausible schedule period/location evaluated as candidate evidence."""
    schedule_row_ids: List[int] = Field(default_factory=list)
    start_date: str = ""
    end_date: str = ""
    place: str = ""
    country: str = ""
    country_iso2: Optional[str] = None
    schedule_text: str = ""
    match_reasons: List[str] = Field(default_factory=list)
    date_comparison: Optional[str] = None
    place_comparison: Optional[str] = None
    country_comparison: Optional[str] = None
    text_match_reason: Optional[str] = None
    possible_when: Optional[str] = None
    possible_where: Optional[str] = None


class TravelScheduleManifest(BaseModel):
    """Local immutable reference dataset manifest and payload."""
    format_version: str = "1.0"
    source_table_id: str
    retrieved_at: str
    complete: bool = True
    row_count: int
    canonical_sha256: str
    normalized_rows: List[NormalizedTravelRow] = Field(default_factory=list)


class TravelRenamerEnrichment(BaseModel):
    """Provisional schedule-derived enrichment destined for Tool 1 Renamer.

    Schedule evidence records planned travel and must NEVER set confirmed=True.
    Resolution states for schedule enrichment remain PROVISIONAL.
    """
    confirmed: bool = False
    source_tool: str = "tool_3_travel_schedule_review"
    when_val: Optional[str] = None
    when_state: ResolutionState = ResolutionState.PROVISIONAL
    where_val: Optional[str] = None
    where_state: ResolutionState = ResolutionState.PROVISIONAL
    schedule_row_ids: List[int] = Field(default_factory=list)
    reference_checksum: str = ""
    evidence: List[str] = Field(default_factory=list)


class TravelReviewResult(BaseModel):
    """Comprehensive, typed, serializable result of Tool 3 review for one media item."""
    tracking_id: str
    decision: TravelReviewDecision = TravelReviewDecision.REFERENCE_UNAVAILABLE
    reference_checksum: str = ""
    reference_row_count: int = 0

    input_when_val: Optional[str] = None
    input_when_state: Optional[str] = None
    input_where_val: Optional[str] = None
    input_where_state: Optional[str] = None

    tool2_decision: Optional[str] = None
    tool2_context_state: Optional[str] = None
    selected_media_row_id: Optional[int] = None

    candidates: List[TravelCandidate] = Field(default_factory=list)
    selected_schedule_row_ids: List[int] = Field(default_factory=list)
    provisional_enrichment: Optional[TravelRenamerEnrichment] = None

    conflicts: List[str] = Field(default_factory=list)
    diagnostic_notes: List[str] = Field(default_factory=list)
    downstream_routing: List[str] = Field(default_factory=list)
