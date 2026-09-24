"""Data models and type definitions for Tool 5 - Content Discoverer."""
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field


class ContentType(str, Enum):
    """Broad content category of the recording determined from actual audio."""
    CLASS = "CLASS"
    KIRTAN_AND_CLASS = "KIRTAN_AND_CLASS"
    KIRTAN = "KIRTAN"
    INITIATION = "INITIATION"
    EVENT_OR_FESTIVAL_ADDRESS = "EVENT_OR_FESTIVAL_ADDRESS"
    HOME_PROGRAM = "HOME_PROGRAM"
    VYASA_PUJA = "VYASA_PUJA"
    UNKNOWN_REVIEW = "UNKNOWN_REVIEW"


class MantraType(str, Enum):
    """Leading/opening mantra or chanting detected in the recording."""
    JAYA_RADHA_MADHAVA = "Jaya-radha-madhava"
    JAYA_JAYA_SRI_CAITANYA = "Jaya-Jaya-Sri-Caitanya"
    NRISHMADEVA = "Nrishmadeva"
    KIRTAN = "Kirtan"
    UNKNOWN = "UNKNOWN"
    NONE = "NONE"


class ConfidenceLevel(str, Enum):
    """Confidence classification for content discovery."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    BLOCKED = "BLOCKED"


class TranscriptSegment(BaseModel):
    """Normalized timestamped segment with continuous timeline coverage."""
    start_seconds: float
    end_seconds: float
    text: str
    is_silence: bool = False
    avg_logprob: Optional[float] = None
    no_speech_prob: Optional[float] = None


class DerivedAudioDetails(BaseModel):
    """Metadata recorded when extracting local MP3 audio from a video input."""
    source_video_path: str
    derived_audio_path: str
    codec_command_summary: str
    duration_seconds: float
    derived_sha256: str


class TranscriptArtifact(BaseModel):
    """Durable JSON artifact stored in .renamer/transcripts/<tracking-id>.json."""
    contract_version: str = "1.0"
    tracking_id: str
    input_path: str
    input_sha256: str
    source_type: str = "audio"  # "audio" or "video"
    derived_mp3_details: Optional[DerivedAudioDetails] = None
    duration_seconds: float
    detected_language: str = "en"
    language_confidence: Optional[float] = None
    segments: List[TranscriptSegment] = Field(default_factory=list)
    raw_metadata: Dict[str, Any] = Field(default_factory=dict)
    transcript_sha256: str = ""
    created_at: str = ""
    classification_version: str = "1.0"


class ContentEvidence(BaseModel):
    """Timed excerpt supporting a classification or mantra finding."""
    kind: str  # "mantra", "scripture_citation", "purport", "discussion", "initiation_vows", etc.
    start_seconds: float
    end_seconds: float
    raw_excerpt: str
    normalized_text: str
    confidence: float = 1.0


class CutterBoundaryProposal(BaseModel):
    """Exact cut point and boundary proposal between distinct recording sections for Tool 6 handoff."""
    kirtan_range: Tuple[float, float] = (0.0, 0.0)
    class_range: Tuple[float, float] = (0.0, 0.0)
    coarse_gap_bracket: Tuple[float, float] = (0.0, 0.0)
    singing_end_seconds: Optional[float] = None
    class_start_seconds: Optional[float] = None
    source_duration_seconds: Optional[float] = None
    source_sha256: Optional[str] = None
    method: Optional[str] = None
    confidence: str = "HIGH"
    description: Optional[str] = None

    @property
    def exact_cut_seconds(self) -> float:
        if self.singing_end_seconds is not None:
            return self.singing_end_seconds
        return self.kirtan_range[1]

    @property
    def suggested_cut_points(self) -> str:
        if self.description:
            return self.description
        k_start_min, k_start_sec = divmod(int(self.kirtan_range[0]), 60)
        k_end_val = self.singing_end_seconds if self.singing_end_seconds is not None else self.kirtan_range[1]
        k_end_min, k_end_sec = divmod(int(k_end_val), 60)
        c_start_val = self.class_start_seconds if self.class_start_seconds is not None else self.class_range[0]
        c_start_min, c_start_sec = divmod(int(c_start_val), 60)
        return f"kirtan {k_start_min:02d}:{k_start_sec:02d}-{k_end_min:02d}:{k_end_sec:02d}; class begins {c_start_min:02d}:{c_start_sec:02d}"

    @property
    def kirtan_start_sec(self) -> float:
        return self.kirtan_range[0]

    @property
    def kirtan_end_sec(self) -> float:
        if self.singing_end_seconds is not None:
            return self.singing_end_seconds
        return self.kirtan_range[1]

    @property
    def class_start_sec(self) -> float:
        if self.class_start_seconds is not None:
            return self.class_start_seconds
        return self.class_range[0]


class ContentDiscoveryResult(BaseModel):
    """Durable classification, evidence, and routing outcome from Tool 5."""
    tracking_id: str
    classification: ContentType
    confidence: ConfidenceLevel
    mantra_type: MantraType
    process_by_tool_6: bool = False
    cutter_proposal: Optional[CutterBoundaryProposal] = None
    transcript_path: str = ""
    transcript_sha256: str = ""
    input_sha256: str = ""
    source_path: str
    derived_audio_path: Optional[str] = None
    evidence: List[ContentEvidence] = Field(default_factory=list)
    review_required: bool = False
    review_reason: Optional[str] = None
    human_decision: Optional[Dict[str, Any]] = None
    runtime_provenance: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
