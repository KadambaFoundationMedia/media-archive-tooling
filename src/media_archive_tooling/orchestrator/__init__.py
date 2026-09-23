"""Orchestrator package for the Main Tooling Script."""
from .discovery import (
    DiscoveryResult,
    SUPPORTED_AUDIO_EXTENSIONS,
    SUPPORTED_VIDEO_EXTENSIONS,
    SUPPORTED_COMPANION_EXTENSIONS,
    SUPPORTED_MEDIA_EXTENSIONS,
    discover_media_targets,
    is_supported_media_file,
)
from .logger import UnifiedArchiveLogger
from .models import (
    FileExecutionStatus,
    FileRunResult,
    RunSummary,
    StageName,
    StageResult,
    WorkflowType,
)
from .reporter import TerminalReporter
from .scratch import (
    ScratchTracker,
    check_available_scratch_space,
    clean_abandoned_scratch,
    compute_file_sha256,
    ensure_sufficient_scratch_space,
)
from .service import MainToolingScriptService, create_main_tooling_service

__all__ = [
    "DiscoveryResult",
    "FileExecutionStatus",
    "FileRunResult",
    "MainToolingScriptService",
    "create_main_tooling_service",
    "RunSummary",
    "SUPPORTED_AUDIO_EXTENSIONS",
    "SUPPORTED_VIDEO_EXTENSIONS",
    "SUPPORTED_COMPANION_EXTENSIONS",
    "SUPPORTED_MEDIA_EXTENSIONS",
    "StageName",
    "StageResult",
    "TerminalReporter",
    "UnifiedArchiveLogger",
    "WorkflowType",
    "ScratchTracker",
    "check_available_scratch_space",
    "clean_abandoned_scratch",
    "compute_file_sha256",
    "ensure_sufficient_scratch_space",
    "discover_media_targets",
    "is_supported_media_file",
]
