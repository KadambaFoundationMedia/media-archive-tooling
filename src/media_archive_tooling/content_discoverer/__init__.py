"""Tool 5 - Content Discoverer package."""
from .audio_extractor import AudioExtractionAdapter, FakeAudioExtractionAdapter, is_video_file
from .classifier import ContentClassifier
from .models import (
    ConfidenceLevel,
    ContentDiscoveryResult,
    ContentEvidence,
    ContentType,
    CutterBoundaryProposal,
    DerivedAudioDetails,
    MantraType,
    TranscriptArtifact,
    TranscriptSegment,
)
from .service import ContentDiscovererService
from .transcriber import (
    BaseTranscriptionAdapter,
    FakeTranscriptionAdapter,
    TranscriptionBlockedError,
    TranscriptionError,
    WhisperCppTranscriptionAdapter,
)

__all__ = [
    "AudioExtractionAdapter",
    "BaseTranscriptionAdapter",
    "ConfidenceLevel",
    "ContentClassifier",
    "ContentDiscoveryResult",
    "ContentDiscovererService",
    "ContentEvidence",
    "ContentType",
    "CutterBoundaryProposal",
    "DerivedAudioDetails",
    "FakeAudioExtractionAdapter",
    "FakeTranscriptionAdapter",
    "MantraType",
    "TranscriptArtifact",
    "TranscriptSegment",
    "TranscriptionBlockedError",
    "TranscriptionError",
    "WhisperCppTranscriptionAdapter",
    "is_video_file",
]
