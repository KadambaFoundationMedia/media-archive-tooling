"""Service layer for Tool 5 - Content Discoverer."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Callable, Dict, Optional, Union

from ..renamer.registry.registry import LocalRegistry
from ..renamer.models import RenameProposal
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
    CutterBoundaryProposal,
    DerivedAudioDetails,
    MantraType,
    TranscriptArtifact,
)
from .acoustic_verifier import AcousticBoundaryVerifier, FakeAcousticBoundaryVerifier
from .transcriber import (
    BaseTranscriptionAdapter,
    FakeTranscriptionAdapter,
    TranscriptionBlockedError,
    WhisperCppTranscriptionAdapter,
    probe_audio_duration,
)


class Phase1EligibilityError(ValueError):
    """Raised when a target has not undergone Phase 1 renamer processing."""
    pass


def _matches_phase1_dry_run_context(
    context: Any, media_path: Path, tracking_id: str, dry_run: bool
) -> bool:
    """Accept only the matching typed proposal produced by the current Phase 1 pass."""
    if not dry_run or not isinstance(context, RenameProposal):
        return False
    if context.tracking_id != tracking_id:
        return False
    identity = context.parser_result.identity
    if identity.tracking_id != tracking_id:
        return False
    original_path = Path(context.original_path).resolve()
    if Path(identity.original_path).resolve() != original_path:
        return False
    return media_path in (original_path, Path(context.proposed_path).resolve())


def validate_coarse_boundary(boundary_str: str, duration: float = 0.0) -> Optional[CutterBoundaryProposal]:
    """Parse and validate coarse boundary string against physical recording duration.

    Rejects impossible clock fields (e.g. seconds >= 60, minutes >= 60 when hours present),
    unordered timestamps, or brackets extending beyond the actual media duration.
    """
    if not boundary_str or not boundary_str.strip() or duration <= 0.0:
        return None

    clean = boundary_str.strip()
    time_pattern = r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2}(?:\.\d+)?)"

    def parse_time_group(m: re.Match) -> Optional[float]:
        hrs_str = m.group(1)
        mins_str = m.group(2)
        secs_str = m.group(3)

        secs = float(secs_str)
        if secs < 0.0 or secs >= 60.0:
            return None

        mins = int(mins_str)
        if hrs_str is not None:
            hrs = int(hrs_str)
            if hrs < 0 or mins < 0 or mins >= 60:
                return None
            return hrs * 3600.0 + mins * 60.0 + secs
        else:
            if mins < 0:
                return None
            return mins * 60.0 + secs

    matches = list(re.finditer(time_pattern, clean))
    if len(matches) < 2:
        return None

    parsed_times: List[float] = []
    for m in matches:
        t = parse_time_group(m)
        if t is None:
            return None
        parsed_times.append(t)

    t0 = parsed_times[0]
    t1 = parsed_times[1]

    # Must be strictly ordered and within duration
    if t0 < 0.0 or t1 <= t0 or t1 > duration:
        return None

    if len(parsed_times) >= 3:
        t2 = parsed_times[2]
        if t2 < t1 or t2 >= duration:
            return None
        class_start = t2
    else:
        class_start = t1

    if len(parsed_times) >= 4:
        t3 = parsed_times[3]
        if t3 <= class_start or t3 > duration:
            return None
        class_end = t3
    else:
        class_end = duration

    gap_start = t1
    gap_end = max(t1, class_start)
    if gap_start == gap_end:
        gap_start = max(0.0, t1 - 10.0)
        gap_end = min(duration, t1 + 10.0)

    return CutterBoundaryProposal(
        kirtan_range=(t0, t1),
        class_range=(class_start, class_end),
        coarse_gap_bracket=(gap_start, gap_end),
        singing_end_seconds=t1,
        source_duration_seconds=duration,
        method="manual_boundary_validation",
        confidence="HIGH",
        description=clean,
    )


class ContentDiscovererService:
    """Orchestrates video extraction, audio transcription, classification, and registry persistence."""

    def __init__(
        self,
        registry: LocalRegistry,
        transcription_adapter: Optional[BaseTranscriptionAdapter] = None,
        audio_extractor: Optional[AudioExtractionAdapter] = None,
        classifier: Optional[ContentClassifier] = None,
        acoustic_verifier: Optional[Any] = None,
    ):
        self.registry = registry
        self.transcription_adapter = transcription_adapter or WhisperCppTranscriptionAdapter()
        self.audio_extractor = audio_extractor or AudioExtractionAdapter()
        self.classifier = classifier or ContentClassifier()
        if acoustic_verifier is not None:
            self.acoustic_verifier = acoustic_verifier
        elif isinstance(self.transcription_adapter, FakeTranscriptionAdapter):
            simulated = getattr(self.transcription_adapter, "simulated_acoustic_boundary", None)
            verify_flag = getattr(self.transcription_adapter, "verify_acoustic_boundary", True)
            self.acoustic_verifier = FakeAcousticBoundaryVerifier(
                exact_cut_point=simulated,
                should_verify=verify_flag,
            )
        else:
            self.acoustic_verifier = AcousticBoundaryVerifier()

    def discover_content(
        self,
        target: Union[str, Path],
        tracking_id: Optional[str] = None,
        dry_run: bool = False,
        device: str = "auto",
        model_path: Optional[Path] = None,
        force_retranscribe: bool = False,
        root_dir: Optional[Path] = None,
        phase1_context: Optional[Any] = None,
        progress_callback: Optional[Callable[[str, float, str], None]] = None,
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
                target_path = Path(target_str)
                if not target_path.exists():
                    if target_str.startswith("trk_"):
                        raise Phase1EligibilityError(f"Tracking ID '{target_str}' not found in Phase 1 registry.")
                    raise FileNotFoundError(f"Target file not found: {target_str}")
                media_path = target_path.resolve()
                resolved_tid = self.registry.find_tracking_id_by_path(media_path)
                if not resolved_tid:
                    ctx_tid = getattr(phase1_context, "tracking_id", None)
                    if not ctx_tid or not _matches_phase1_dry_run_context(
                        phase1_context, media_path, ctx_tid, dry_run
                    ):
                        raise Phase1EligibilityError(
                            f"Target '{target_str}' is not registered in Phase 1 registry; files must be processed by Phase 1 before Content Discovery."
                        )
                    resolved_tid = ctx_tid
        else:
            file_rec = self.registry.get_file(resolved_tid)
            if file_rec:
                media_path = Path(file_rec["current_path"])
            else:
                target_path = Path(target_str)
                if not target_path.exists():
                    raise FileNotFoundError(f"Media file not found for tracking ID {resolved_tid}: {target_str}")
                media_path = target_path.resolve()
                tid_by_path = self.registry.find_tracking_id_by_path(media_path)
                if tid_by_path:
                    resolved_tid = tid_by_path
                else:
                    if not _matches_phase1_dry_run_context(
                        phase1_context, media_path, resolved_tid, dry_run
                    ):
                        raise Phase1EligibilityError(
                            f"Target '{target_str}' with tracking ID '{resolved_tid}' is not registered in Phase 1 registry."
                        )

        if not media_path or not media_path.exists():
            raise FileNotFoundError(f"Media file not found for tracking ID {resolved_tid}: {media_path}")

        media_path = media_path.resolve()
        is_video = is_video_file(media_path)
        derived_mp3_path: Optional[Path] = None
        derived_details: Optional[DerivedAudioDetails] = None

        now_iso = datetime.now(timezone.utc).isoformat()

        # 2. Audio Extraction for Video
        audio_target = media_path
        if is_video:
            derived_mp3_path = media_path.with_suffix(".mp3")
            if dry_run:
                # Dry run: predict extraction path without performing disk mutation
                audio_target = derived_mp3_path
                derived_details = DerivedAudioDetails(
                    source_video_path=str(media_path),
                    derived_audio_path=str(derived_mp3_path),
                    codec_command_summary="ffmpeg libmp3lame -q:a 0 (dry-run)",
                    duration_seconds=0.0,
                    derived_sha256="dry_run_derived_sha256",
                )
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

        # 3. Bounded Timeline Excerpt Transcription
        actual_audio = audio_target if audio_target.exists() else media_path
        duration_sec = 0.0
        try:
            duration_sec = probe_audio_duration(actual_audio)
        except Exception:
            duration_sec = 0.0
        if duration_sec <= 0.0 and derived_details:
            duration_sec = derived_details.duration_seconds
        if duration_sec <= 0.0:
            duration_sec = 600.0

        # Extract metadata hints for combination and mantra clues
        has_combination_clue = False
        mantra_hint = None
        category_hint = None
        orig_fn = ""

        if resolved_tid:
            file_rec = self.registry.get_file(resolved_tid)
            if file_rec:
                orig_fn = file_rec.get("original_filename") or ""
                pjson = file_rec.get("parser_result_json")
                if pjson:
                    try:
                        pdata = json.loads(pjson)
                        fmeta = pdata.get("file_metadata") or {}
                        if fmeta.get("possible_combination"):
                            has_combination_clue = True
                        wdata = pdata.get("what") or {}
                        category_hint = wdata.get("category")
                        unclass = pdata.get("unclassified_text") or []
                        unclass_str = " ".join(unclass).lower()
                        if "radha" in unclass_str or "madhava" in unclass_str:
                            mantra_hint = MantraType.JAYA_RADHA_MADHAVA.value
                        elif "kirtan" in unclass_str or "bhajan" in unclass_str:
                            mantra_hint = MantraType.KIRTAN.value
                        elif "caitanya" in unclass_str:
                            mantra_hint = MantraType.JAYA_JAYA_SRI_CAITANYA.value
                        elif "nrsimha" in unclass_str or "narasimha" in unclass_str:
                            mantra_hint = MantraType.NRISHMADEVA.value
                    except Exception:
                        pass

        if phase1_context:
            pres = getattr(phase1_context, "parser_result", None)
            if pres:
                fmeta = getattr(pres, "file_metadata", None)
                if fmeta and getattr(fmeta, "possible_combination", False):
                    has_combination_clue = True

        fn_target = (orig_fn or media_path.name).lower()
        if any(term in fn_target for term in ["with radha madhava", "radha madhava", "radhamadhava", "radha-madhava"]):
            has_combination_clue = True
            mantra_hint = MantraType.JAYA_RADHA_MADHAVA.value
        elif any(term in fn_target for term in ["with kirtan", "+ kirtan", "& kirtan", "and kirtan"]):
            has_combination_clue = True
            if not mantra_hint:
                mantra_hint = MantraType.KIRTAN.value
        elif re.search(r"\b(with|and|&|\+|plus|followed\s+by)\b", fn_target):
            has_combination_clue = True

        # Detect candidate acoustic transitions (continuous music ending in silence)
        candidate_transitions: List[Tuple[float, float]] = []
        if duration_sec > 180.0 and self.acoustic_verifier and hasattr(self.acoustic_verifier, "detect_candidate_transitions"):
            try:
                candidate_transitions = self.acoustic_verifier.detect_candidate_transitions(
                    actual_audio,
                    total_duration=duration_sec,
                    max_search_sec=min(duration_sec, 2400.0),
                )
            except Exception:
                candidate_transitions = []

        excerpt_windows: List[Tuple[float, float]] = []
        if duration_sec <= 120.0:
            excerpt_windows.append((0.0, duration_sec))
        else:
            excerpt_windows.append((0.0, min(120.0, duration_sec)))

            if candidate_transitions:
                # Add targeted excerpt around speech onset
                for _, speech_start in candidate_transitions[:2]:
                    t_start = max(120.0, speech_start - 10.0)
                    t_end = min(duration_sec, t_start + 60.0)
                    if t_start < duration_sec and not any(abs(w[0] - t_start) < 20.0 for w in excerpt_windows):
                        excerpt_windows.append((t_start, t_end))

            mid_s = max(120.0, duration_sec * 0.4)
            mid_e = min(duration_sec, mid_s + 60.0)
            if mid_s < duration_sec and not any(abs(w[0] - mid_s) < 30.0 for w in excerpt_windows):
                excerpt_windows.append((mid_s, mid_e))
            if duration_sec > 600.0:
                end_s = max(mid_e, duration_sec - 120.0)
                if end_s < duration_sec and not any(abs(w[0] - end_s) < 30.0 for w in excerpt_windows):
                    excerpt_windows.append((end_s, duration_sec))

        excerpt_windows.sort(key=lambda w: w[0])

        try:
            artifact = self.transcription_adapter.transcribe(
                audio_path=actual_audio,
                tracking_id=resolved_tid,
                source_path=media_path if is_video else None,
                source_type="video" if is_video else "audio",
                derived_audio_details=derived_details,
                requested_device=device,
                model_path=model_path,
                force=force_retranscribe,
                root_dir=root_dir,
                dry_run=dry_run,
                progress_callback=progress_callback,
                excerpt_windows=excerpt_windows,
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

        # Populate context in artifact metadata for classification
        if artifact.raw_metadata is None:
            artifact.raw_metadata = {}
        artifact.raw_metadata["candidate_transitions"] = candidate_transitions
        artifact.raw_metadata["has_combination_clue"] = has_combination_clue
        artifact.raw_metadata["mantra_hint"] = mantra_hint
        artifact.raw_metadata["category_hint"] = category_hint

        # 4. Classification from Excerpts
        result = self.classifier.classify(artifact)
        result.source_path = str(media_path)
        if derived_mp3_path:
            result.derived_audio_path = str(derived_mp3_path)
        result.created_at = now_iso
        result.updated_at = now_iso

        # 4b. Local Acoustic Boundary Verification for Suspected KIRTAN_AND_CLASS Combination
        if result.classification == ContentType.KIRTAN_AND_CLASS and result.cutter_proposal:
            coarse_s = result.cutter_proposal.kirtan_range[1]
            coarse_e = result.cutter_proposal.class_range[0]
            dur = result.cutter_proposal.source_duration_seconds or duration_sec

            if result.cutter_proposal.singing_end_seconds is not None and result.cutter_proposal.confidence == "HIGH":
                # Already verified acoustically via transition detection
                result.process_by_tool_6 = True
                result.confidence = ConfidenceLevel.HIGH
                result.review_required = False
                result.review_reason = None
            else:
                exact_cut = None
                if self.acoustic_verifier:
                    exact_cut = self.acoustic_verifier.verify_boundary(
                        actual_audio,
                        coarse_s,
                        coarse_e,
                        dur,
                    )

                if exact_cut is not None and exact_cut > 0:
                    result.cutter_proposal.singing_end_seconds = exact_cut
                    result.cutter_proposal.confidence = "HIGH"
                    result.cutter_proposal.method = "acoustic_local_boundary_verified"
                    result.process_by_tool_6 = True
                    result.confidence = ConfidenceLevel.HIGH
                    result.review_required = False
                    result.review_reason = None
                else:
                    result.cutter_proposal.singing_end_seconds = None
                    result.cutter_proposal.confidence = "LOW"
                    result.cutter_proposal.method = "acoustic_verification_failed"
                    result.process_by_tool_6 = False
                    result.confidence = ConfidenceLevel.MEDIUM
                    result.review_required = True
                    result.review_reason = "Exact singing end boundary could not be verified acoustically from local audio; manual review required"

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

        # Resolve recording duration from multiple sources
        media_duration = 0.0
        res_data = existing.get("result", {})
        if isinstance(res_data, dict):
            if "runtime_provenance" in res_data and isinstance(res_data["runtime_provenance"], dict):
                media_duration = float(res_data["runtime_provenance"].get("duration_seconds", 0.0))
            if media_duration <= 0.0 and "cutter_proposal" in res_data and res_data["cutter_proposal"]:
                c_range = res_data["cutter_proposal"].get("class_range", [0, 0])
                if len(c_range) >= 2 and float(c_range[1]) > 0:
                    media_duration = float(c_range[1])

        if media_duration <= 0.0 and existing.get("transcript_path"):
            t_path = Path(existing["transcript_path"])
            if not t_path.is_absolute():
                t_path = Path.cwd() / t_path
            if t_path.exists():
                try:
                    t_json = json.loads(t_path.read_text(encoding="utf-8"))
                    media_duration = float(t_json.get("duration_seconds", 0.0))
                except Exception:
                    pass

        if media_duration <= 0.0:
            media_cand = existing.get("derived_audio_path") or existing.get("source_path")
            if media_cand:
                m_path = Path(media_cand)
                if m_path.exists():
                    try:
                        media_duration = probe_audio_duration(m_path)
                    except Exception:
                        pass

        # Determine tool 6 routing based on updated decision and boundary evidence
        process_by_tool6 = False
        review_required = 0
        review_reason: Optional[str] = None
        cutter_proposal_dict: Optional[Dict[str, Any]] = None

        if new_classification in (ContentType.KIRTAN_AND_CLASS, ContentType.INITIATION):
            validated_proposal: Optional[CutterBoundaryProposal] = None
            if coarse_boundary:
                validated_proposal = validate_coarse_boundary(coarse_boundary, duration=media_duration)

            if not validated_proposal and existing.get("cutter_proposal"):
                try:
                    cand = CutterBoundaryProposal.model_validate(existing["cutter_proposal"])
                    if media_duration > 0 and cand.class_range[1] <= media_duration:
                        validated_proposal = cand
                except Exception:
                    validated_proposal = None

            if validated_proposal:
                process_by_tool6 = True
                review_required = 0
                cutter_proposal_dict = validated_proposal.model_dump()
            else:
                process_by_tool6 = False
                review_required = 1
                review_reason = "Tool 6 cutter handoff requires verified coarse boundary brackets"
        else:
            process_by_tool6 = False
            review_required = 0

        self.registry.save_content_review_human_decision(
            tracking_id=tracking_id,
            classification=new_classification.value,
            mantra_type=new_mantra.value,
            process_by_tool_6=1 if process_by_tool6 else 0,
            review_required=review_required,
            human_decision_json=human_decision,
            updated_at=now_iso,
            cutter_proposal_json=json.dumps(cutter_proposal_dict) if cutter_proposal_dict else None,
            review_reason=review_reason,
        )

        updated_dict = self.registry.get_content_review(tracking_id)
        return ContentDiscoveryResult.model_validate(updated_dict)
