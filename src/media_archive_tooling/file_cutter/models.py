"""Data models and type definitions for Tool 6 — File Cutter."""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class CutPointProposal(BaseModel):
    """Proposal for the exact end-of-singing cut point."""
    singing_end_seconds: float
    source_duration_seconds: float
    source_sha256: str
    confidence: str = "HIGH"
    method: str = "exact_timestamp"
    description: Optional[str] = None


class WaveformSummary(BaseModel):
    """Normalized waveform peak summary for browser visualization without loading raw audio."""
    tracking_id: str
    source_sha256: str
    duration_seconds: float
    sample_count: int
    peaks: List[float] = Field(default_factory=list)
    cut_point_seconds: Optional[float] = None


class HumanCutDecision(BaseModel):
    """Audited human-adjusted or approved cut point bound to source SHA-256."""
    tracking_id: str
    source_sha256: str
    cut_point_seconds: float
    reviewer: str = "human_reviewer"
    notes: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""


class FileCutterResult(BaseModel):
    """Outcome and audit record of a Tool 6 split operation."""
    tracking_id: str
    source_path: str
    source_sha256: str
    source_duration_seconds: float
    cut_point_seconds: float

    # Singing output
    singing_output_path: Optional[str] = None
    singing_tracking_id: Optional[str] = None
    singing_sha256: Optional[str] = None
    singing_duration_seconds: Optional[float] = None
    singing_leading_silence_seconds: float = 0.0
    singing_pending_tool_11_move: bool = True

    # Class output
    class_output_path: Optional[str] = None
    class_tracking_id: Optional[str] = None
    class_sha256: Optional[str] = None
    class_duration_seconds: Optional[float] = None
    class_leading_silence_seconds: float = 0.0
    class_pending_tool_11_move: bool = True

    # Lifecycle & status
    success: bool = False
    review_required: bool = False
    review_reason: Optional[str] = None
    error_message: Optional[str] = None
    dry_run: bool = False
    tool_version: str = "1.0.0"

    # Baserow sync results if synchronized
    media_db_sync_class: Optional[Dict[str, Any]] = None
    media_db_sync_singing: Optional[Dict[str, Any]] = None

    # Diagnostic & provenance details
    details: Dict[str, Any] = Field(default_factory=dict)
