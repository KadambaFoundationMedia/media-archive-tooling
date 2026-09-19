"""Domain models and typed data contracts for the Main Tooling Script orchestrator."""
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class WorkflowType(str, Enum):
    ALL = "all"
    RENAMER = "renamer"
    PROCESSING = "processing"


class StageName(str, Enum):
    TOOL_1_INITIAL = "tool_1_initial"
    TOOL_2_REVIEW = "tool_2_media_review"
    TOOL_3_REVIEW = "tool_3_travel_review"
    TOOL_1_FINALIZE = "tool_1_finalize"
    TOOL_4_SYNC = "tool_4_media_sync"


class FileExecutionStatus(str, Enum):
    COMPLETED = "completed"
    DRY_RUN = "dry_run"
    UNCHANGED = "unchanged"
    REVIEW_REQUIRED = "review_required"
    PENDING_SYNC = "pending_sync"
    DATABASE_UNAVAILABLE = "database_unavailable"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_BLOCKED = "failed_blocked"
    FAILED = "failed"


class StageResult(BaseModel):
    stage_name: StageName
    success: bool = True
    summary: str = ""
    decision: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


class FileRunResult(BaseModel):
    target_path: str
    tracking_id: str
    original_filename: str
    final_filename: Optional[str] = None
    final_path: Optional[str] = None
    status: FileExecutionStatus
    review_reasons: List[str] = Field(default_factory=list)
    stage_results: List[StageResult] = Field(default_factory=list)
    tool4_row_id: Optional[int] = None
    tool4_operation: Optional[str] = None
    tool4_fields: Dict[str, Any] = Field(default_factory=dict)
    tool4_sync_status: Optional[str] = None
    tool4_live_row: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class RunSummary(BaseModel):
    run_id: str
    workflow: WorkflowType
    is_dry_run: bool
    total_discovered: int = 0
    completed: int = 0
    dry_run_previews: int = 0
    unchanged: int = 0
    review_required: int = 0
    pending_sync: int = 0
    database_unavailable: int = 0
    failed_retryable: int = 0
    failed_blocked: int = 0
    failed: int = 0
    skipped_unsupported: int = 0
    log_path: str = ""
    registry_path: str = ""
    file_results: List[FileRunResult] = Field(default_factory=list)
    skipped_files: List[str] = Field(default_factory=list)
    exit_code: int = 0
