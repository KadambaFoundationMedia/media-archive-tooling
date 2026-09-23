"""Service layer for Tool 5 - Content Discoverer."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, Dict, Optional, Union

from ..renamer.registry.registry import LocalRegistry
from .audio_extractor import (
    AudioExtractionAdapter,
    AudioExtractionError,
    compute_file_sha256,
    is_video_file,
)
from .classifier import ContentClassifier
from .models import (
    ConfidenceLevel,
    ContentDiscoveryResult,
    ContentType,
    MantraType,
    TranscriptArtifact,
)
from .transcriber import (
    BaseTranscriptionAdapter,
    TranscriptionBlockedError,
    WhisperCppTranscriptionAdapter,
)


class ContentDiscovererService:
    """Orchestrates video extraction, audio transcription, classification, and registry persistence."""

    def __init__(
        self,
        registry: LocalRegistry,
        transcription_adapter: Optional[BaseTranscriptionAdapter] = None,
        audio_extractor: Optional[AudioExtractionAdapter] = None,
        classifier: Optional[ContentClassifier] = None,
    ):
        self.registry = registry
        self.transcription_adapter = transcription_adapter or WhisperCppTranscriptionAdapter()
        self.audio_extractor = audio_extractor or AudioExtractionAdapter()
        self.classifier = classifier or ContentClassifier()

    def discover_content(
        self,
        target: Union[str, Path],
        tracking_id: Optional[str] = None,
        dry_run: bool = False,
        device: str = "auto",
        model_path: Optional[Path] = None,
        force_retranscribe: bool = False,
        root_dir: Optional[Path] = None,
    ) -> ContentDiscoveryResult:
        """Analyze a media file, transcribe audio, classify content, and record routing."""
        target_str = str(target)
        media_path: Optional[Path] = None
        resolved_tid = tracking_id

        # 1. Resolve target by tracking_id or by filesystem path
        if not resolved_tid:
            # Check if target is a known tracking_id in the registry
            file_rec = self.registry.get_file(target_str)
            if file_rec:
                resolved_tid = target_str
                media_path = Path(file_rec["current_path"])
            else:
                media_path = Path(target_str).resolve()
                if not media_path.exists():
                    raise FileNotFoundError(f"Target file not found: {target_str}")
                resolved_tid = self.registry.find_tracking_id_by_path(media_path)
                if not resolved_tid:
                    resolved_tid = f"trk_{hashlib.sha256(str(media_path).encode()).hexdigest()[:8]}"
        else:
            file_rec = self.registry.get_file(resolved_tid)
            if file_rec:
                media_path = Path(file_rec["current_path"])
            else:
                media_path = Path(target_str).resolve()

        if not media_path or not media_path.exists():
            raise FileNotFoundError(f"Media file not found for tracking ID {resolved_tid}: {media_path}")

        media_path = media_path.resolve()
        is_video = is_video_file(media_path)
        derived_mp3_path: Optional[Path] = None

        now_iso = datetime.now(timezone.utc).isoformat()

        # 2. Audio Extraction for Video
        audio_target = media_path
        if is_video:
            derived_mp3_path = media_path.with_suffix(".mp3")
            if dry_run:
                # Dry run: predict extraction path without performing disk mutation
                audio_target = derived_mp3_path
            else:
                try:
                    derived_details = self.audio_extractor.extract_audio(
                        video_path=media_path,
                        tracking_id=resolved_tid,
                        registry=self.registry,
                    )
                    audio_target = Path(derived_details.derived_audio_path)
                except Exception as e:
                    # Extraction failure -> fail closed with BLOCKED confidence
                    err_result = ContentDiscoveryResult(
                        tracking_id=resolved_tid,
                        classification=ContentType.UNKNOWN_REVIEW,
                        confidence=ConfidenceLevel.BLOCKED,
                        mantra_type=MantraType.NONE,
                        process_by_tool_6=False,
                        transcript_path=f".renamer/transcripts/{resolved_tid}.json",
                        transcript_sha256="",
                        input_sha256=compute_file_sha256(media_path),
                        source_path=str(media_path),
                        derived_audio_path=str(derived_mp3_path),
                        review_required=True,
                        review_reason=f"Video audio extraction failed: {e}",
                        created_at=now_iso,
                        updated_at=now_iso,
                    )
                    if not dry_run:
                        self.registry.save_content_review(err_result)
                    return err_result

        # 3. Transcription
        try:
            artifact = self.transcription_adapter.transcribe(
                audio_path=audio_target if audio_target.exists() else media_path,
                tracking_id=resolved_tid,
                requested_device=device,
                model_path=model_path,
                force=force_retranscribe,
                root_dir=root_dir,
                dry_run=dry_run,
            )
        except Exception as e:
            # Transcription failure -> fail closed with BLOCKED confidence
            err_result = ContentDiscoveryResult(
                tracking_id=resolved_tid,
                classification=ContentType.UNKNOWN_REVIEW,
                confidence=ConfidenceLevel.BLOCKED,
                mantra_type=MantraType.NONE,
                process_by_tool_6=False,
                transcript_path=f".renamer/transcripts/{resolved_tid}.json",
                transcript_sha256="",
                input_sha256=compute_file_sha256(media_path),
                source_path=str(media_path),
                derived_audio_path=str(derived_mp3_path) if derived_mp3_path else None,
                review_required=True,
                review_reason=f"Transcription failed: {e}",
                created_at=now_iso,
                updated_at=now_iso,
            )
            if not dry_run:
                self.registry.save_content_review(err_result)
            return err_result

        # 4. Classification
        result = self.classifier.classify(artifact)
        result.source_path = str(media_path)
        if derived_mp3_path:
            result.derived_audio_path = str(derived_mp3_path)
        result.created_at = now_iso
        result.updated_at = now_iso

        # 5. Persistence
        if not dry_run:
            self.registry.save_content_review(result)

        return result

    def apply_human_decision(
        self,
        tracking_id: str,
        classification: Optional[str] = None,
        mantra_type: Optional[str] = None,
        coarse_boundary: Optional[str] = None,
        reviewer: str = "portal",
        notes: str = "",
    ) -> ContentDiscoveryResult:
        """Record an audited human review decision in the registry without modifying transcripts."""
        existing = self.registry.get_content_review(tracking_id)
        if not existing:
            raise ValueError(f"Content review record not found for tracking ID: {tracking_id}")

        now_iso = datetime.now(timezone.utc).isoformat()
        human_decision = {
            "reviewer": reviewer,
            "notes": notes,
            "timestamp": now_iso,
            "override_classification": classification,
            "override_mantra": mantra_type,
            "override_boundary": coarse_boundary,
        }

        # Update classification if specified
        new_classification = ContentType(classification) if classification else ContentType(existing["classification"])
        new_mantra = MantraType(mantra_type) if mantra_type else MantraType(existing["mantra_type"])

        # Determine tool 6 routing based on updated decision
        process_by_tool6 = False
        if new_classification in (ContentType.KIRTAN_AND_CLASS, ContentType.INITIATION):
            process_by_tool6 = True

        self.registry.save_content_review_human_decision(
            tracking_id=tracking_id,
            classification=new_classification.value,
            mantra_type=new_mantra.value,
            process_by_tool_6=1 if process_by_tool6 else 0,
            review_required=0,  # Human review resolved it
            human_decision_json=human_decision,
            updated_at=now_iso,
        )

        updated_dict = self.registry.get_content_review(tracking_id)
        return ContentDiscoveryResult.model_validate(updated_dict)
