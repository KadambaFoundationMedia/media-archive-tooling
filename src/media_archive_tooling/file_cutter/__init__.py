"""Tool 6 — File Cutter package."""
from .models import (
    FileCutterResult,
    CutPointProposal,
    WaveformSummary,
    HumanCutDecision,
)
from .audio_cutter import (
    AudioCutter,
    AudioCutSpec,
    AudioCutResult,
    AudioCutterError,
    AudioVerificationError,
    compute_sha256,
)
from .waveform import WaveformGenerator
from .service import FileCutterService, derive_split_whats

__all__ = [
    "FileCutterResult",
    "CutPointProposal",
    "WaveformSummary",
    "HumanCutDecision",
    "AudioCutter",
    "AudioCutSpec",
    "AudioCutResult",
    "AudioCutterError",
    "AudioVerificationError",
    "compute_sha256",
    "WaveformGenerator",
    "FileCutterService",
    "derive_split_whats",
]
