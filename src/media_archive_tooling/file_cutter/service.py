"""Application service layer for Tool 6 — File Cutter."""
from copy import deepcopy
import json
import logging
import os
from pathlib import Path
import re
import shutil
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

from .models import FileCutterResult, WaveformSummary, HumanCutDecision
from .audio_cutter import AudioCutter, AudioCutSpec, AudioCutResult, AudioCutterError, AudioVerificationError, compute_sha256
from .waveform import WaveformGenerator
from ..renamer.registry.registry import LocalRegistry
from ..renamer.models import ParserResult, RenameMode, ResolutionState, Identity
from ..renamer.planner.planner import RenamePlanner
from ..common.ascii_latin import to_ascii_latin, sanitize_filename_token

logger = logging.getLogger(__name__)


def _sanitize_what(raw: str) -> str:
    """Normalize WHAT token for filename."""
    token = sanitize_filename_token(to_ascii_latin(raw))
    return token.strip("-_")


def derive_split_whats(
    source_what: Optional[str],
    detected_mantra: Optional[str] = None,
) -> Tuple[str, str]:
    """Derive clean, distinct WHAT tokens for singing and class outputs.

    Returns (singing_what, class_what).
    """
    s_what = detected_mantra or "Jaya-radha-madhava"
    if s_what.lower() in ("none", "unknown"):
        s_what = "Kirtan"

    c_what = source_what or "Class"

    # Remove singing/mantra tokens from class WHAT if present
    mantra_patterns = [
        r"(?:^|[_\-])jaya[-_]?radha[-_]?madhava(?:[_\-]|$)",
        r"(?:^|[_\-])jaya[-_]?jaya[-_]?sri[-_]?caitanya(?:[_\-]|$)",
        r"(?:^|[_\-])nr[si]?simhadeva(?:[_\-]|$)",
        r"(?:^|[_\-])kirtan(?:[_\-]|$)",
    ]
    cleaned_c = c_what
    for pat in mantra_patterns:
        cleaned_c = re.sub(pat, "-", cleaned_c, flags=re.IGNORECASE)

    cleaned_c = re.sub(r"-+", "-", cleaned_c).strip("-_")
    if not cleaned_c:
        cleaned_c = "Class"

    return _sanitize_what(s_what), _sanitize_what(cleaned_c)


def _atomic_publish_file(staged_path: Path, target_path: Path) -> None:
    """Safely and atomically publish staged_path to target_path without clobbering."""
    staged_path = staged_path.resolve()
    target_path = target_path.resolve()

    if target_path.exists():
        staged_path.unlink(missing_ok=True)
        raise FileExistsError(f"Target file already exists: {target_path}")

    # Case-insensitive collision check in parent directory
    parent = target_path.parent
    target_lower = target_path.name.lower()
    for existing in parent.iterdir():
        if existing.is_file() and existing.name.lower() == target_lower and existing.name != target_path.name:
            staged_path.unlink(missing_ok=True)
            raise FileExistsError(f"Case-insensitive filename collision with existing file: {existing}")

    try:
        os.link(staged_path, target_path)
        staged_path.unlink(missing_ok=True)
    except OSError:
        # Cross-device link or unsupported link; fallback to atomic rename/copy
        try:
            shutil.move(str(staged_path), str(target_path))
        except Exception as e:
            staged_path.unlink(missing_ok=True)
            raise RuntimeError(f"Could not safely move file to {target_path}: {e}") from e


class FileCutterService:
    """Orchestrates audio cutting, Tool 1 canonical naming, verification, publication, and Tool 4 sync."""

    def __init__(
        self,
        registry: LocalRegistry,
        audio_cutter: Optional[AudioCutter] = None,
        waveform_generator: Optional[WaveformGenerator] = None,
        planner: Optional[RenamePlanner] = None,
        media_db_service: Optional[Any] = None,
    ):
        self.registry = registry
        self.audio_cutter = audio_cutter or AudioCutter()
        self.waveform_generator = waveform_generator or WaveformGenerator()
        self.planner = planner or RenamePlanner(mode=RenameMode.FINALIZE)
        self.media_db_service = media_db_service

    def get_waveform(
        self,
        tracking_id: str,
        num_samples: int = 500,
        root_dir: Optional[Path] = None,
    ) -> WaveformSummary:
        """Retrieve or generate waveform peak summary for portal visualization."""
        file_rec = self.registry.get_file(tracking_id)
        if not file_rec:
            raise ValueError(f"Tracking ID not found in registry: {tracking_id}")

        curr_path = Path(file_rec["current_path"])
        root = root_dir or Path.cwd()
        cache_dir = root / ".renamer" / "waveforms"

        # Check for video derivative
        audio_path = curr_path
        crev = self.registry.get_content_review(tracking_id)
        if crev and crev.get("derived_audio_path"):
            cand = Path(crev["derived_audio_path"])
            if cand.is_file():
                audio_path = cand

        source_sha256 = (
            file_rec.get("source_sha256")
            or file_rec.get("source_hash")
            or (crev.get("input_sha256") if crev else None)
            or (crev.get("source_sha256") if crev else None)
            or compute_sha256(audio_path)
        )
        human_dec = self.registry.get_human_cut_decision(tracking_id)
        cut_point = None
        if human_dec:
            cut_point = human_dec.get("cut_point_seconds")
        elif crev and crev.get("cutter_proposal"):
            cut_point = crev["cutter_proposal"].get("singing_end_seconds") or crev["cutter_proposal"].get("kirtan_range", [0, 0])[1]

        return self.waveform_generator.generate_waveform_summary(
            source_path=audio_path,
            tracking_id=tracking_id,
            source_sha256=source_sha256,
            cache_dir=cache_dir,
            num_samples=num_samples,
            cut_point_seconds=cut_point,
        )

    def get_audio_preview_path(
        self,
        tracking_id: str,
        root_dir: Optional[Path] = None,
    ) -> Path:
        """Resolve browser-playable audio path, transcoding WMA or unsupported formats to MP3 on demand."""
        file_rec = self.registry.get_file(tracking_id)
        if not file_rec:
            raise ValueError(f"Tracking ID not found: {tracking_id}")

        curr_path = Path(file_rec["current_path"])
        root = root_dir or Path.cwd()
        preview_cache_dir = root / ".renamer" / "preview_cache"

        audio_path = curr_path
        crev = self.registry.get_content_review(tracking_id)
        if crev and crev.get("derived_audio_path"):
            cand = Path(crev["derived_audio_path"])
            if cand.is_file():
                audio_path = cand

        return self.waveform_generator.transcode_preview_mp3(
            source_path=audio_path,
            cache_dir=preview_cache_dir,
            tracking_id=tracking_id,
        )

    def plan_output_filenames(
        self,
        tracking_id: str,
        detected_mantra: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Use Tool 1 RenamePlanner to produce distinct canonical names for singing and class parts.

        Returns (singing_filename, class_filename).
        """
        file_rec = self.registry.get_file(tracking_id)
        if not file_rec:
            raise ValueError(f"File not found in registry: {tracking_id}")

        parser_dict = file_rec.get("parser_result")
        if not parser_dict:
            raise ValueError(f"Missing parser_result for file: {tracking_id}")

        source_path = Path(file_rec["current_path"])
        ext = source_path.suffix.lower()

        # If source is video, audio outputs will be .mp3
        crev = self.registry.get_content_review(tracking_id)
        if crev and crev.get("derived_audio_path"):
            ext = ".mp3"

        source_what = file_rec.get("what_val")
        singing_what, class_what = derive_split_whats(source_what, detected_mantra)

        # Build singing ParserResult
        p_singing = ParserResult.model_validate(parser_dict)
        p_singing.what.selected_value = singing_what
        p_singing.what.state = ResolutionState.EXACT
        p_singing.identity.extension = ext
        p_singing.identity.tracking_id = "temp_singing"
        prop_singing = self.planner.plan_rename(p_singing)

        # Build class ParserResult
        p_class = ParserResult.model_validate(parser_dict)
        p_class.what.selected_value = class_what
        p_class.what.state = ResolutionState.EXACT
        p_class.identity.extension = ext
        prop_class = self.planner.plan_rename(p_class)

        fn_singing = prop_singing.proposed_filename
        fn_class = prop_class.proposed_filename

        # Remove temporary ID tags if planner added them
        fn_singing = re.sub(r"_ID-[0-9a-fA-F]{8}", "", fn_singing, flags=re.IGNORECASE)
        fn_class = re.sub(r"_ID-[0-9a-fA-F]{8}", "", fn_class, flags=re.IGNORECASE)

        return fn_singing, fn_class

    def cut_file(
        self,
        tracking_id_or_path: Union[str, Path],
        dry_run: bool = False,
        cut_point_override: Optional[float] = None,
        root_dir: Optional[Path] = None,
        reviewer: str = "human_reviewer",
        notes: Optional[str] = None,
    ) -> FileCutterResult:
        """Execute a verified two-part file cut or dry-run simulation."""
        root = root_dir or Path.cwd()

        # 1. Resolve file in registry
        file_rec = None
        if isinstance(tracking_id_or_path, str) and not Path(tracking_id_or_path).is_file():
            file_rec = self.registry.get_file(tracking_id_or_path)
        if not file_rec:
            resolved_p = str(Path(tracking_id_or_path).resolve())
            files = self.registry.list_files()
            for f in files:
                if str(Path(f["current_path"]).resolve()) == resolved_p or str(Path(f["original_path"]).resolve()) == resolved_p:
                    file_rec = f
                    break

        if not file_rec:
            return FileCutterResult(
                tracking_id=str(tracking_id_or_path),
                source_path=str(tracking_id_or_path),
                source_sha256="",
                source_duration_seconds=0.0,
                cut_point_seconds=0.0,
                success=False,
                review_required=True,
                review_reason=f"File not registered in local registry: {tracking_id_or_path}",
                error_message="Not found in registry",
            )

        tracking_id = file_rec["tracking_id"]
        source_path = Path(file_rec["current_path"]).resolve()

        if not source_path.is_file():
            return FileCutterResult(
                tracking_id=tracking_id,
                source_path=str(source_path),
                source_sha256=file_rec.get("source_sha256", ""),
                source_duration_seconds=0.0,
                cut_point_seconds=0.0,
                success=False,
                review_required=True,
                review_reason=f"Source file missing from disk: {source_path}",
                error_message="File missing on disk",
            )

        # Check if already split (Idempotency)
        existing_split = self.registry.get_file_split_by_source(tracking_id)
        if existing_split:
            logger.info("File %s already completed split (ID %s)", tracking_id, existing_split["id"])
            return FileCutterResult(
                tracking_id=tracking_id,
                source_path=existing_split["source_path"],
                source_sha256=existing_split["source_sha256"],
                source_duration_seconds=existing_split["source_duration_seconds"],
                cut_point_seconds=existing_split["cut_point_seconds"],
                singing_output_path=existing_split["singing_path"],
                singing_tracking_id=existing_split["singing_tracking_id"],
                singing_sha256=existing_split["singing_sha256"],
                singing_duration_seconds=existing_split["singing_duration_seconds"],
                singing_leading_silence_seconds=existing_split["singing_leading_silence_seconds"],
                singing_pending_tool_11_move=bool(existing_split["singing_pending_tool_11_move"]),
                class_output_path=existing_split["class_path"],
                class_tracking_id=existing_split["class_tracking_id"],
                class_sha256=existing_split["class_sha256"],
                class_duration_seconds=existing_split["class_duration_seconds"],
                class_leading_silence_seconds=existing_split["class_leading_silence_seconds"],
                class_pending_tool_11_move=bool(existing_split["class_pending_tool_11_move"]),
                success=True,
                tool_version=existing_split["tool_version"],
                details={"reused_existing_split": True},
            )

        # 2. Determine working audio file (Handling Video vs Audio) & fingerprint check
        current_sha256 = compute_sha256(source_path)
        crev = self.registry.get_content_review(tracking_id)
        expected_sha = (
            file_rec.get("source_sha256")
            or file_rec.get("source_hash")
            or (crev.get("input_sha256") if crev else None)
            or (crev.get("source_sha256") if crev else None)
            or ((crev.get("result") or {}).get("input_sha256") if crev else None)
            or ((crev.get("result") or {}).get("source_sha256") if crev else None)
        )
        if expected_sha and current_sha256 != expected_sha:
            return FileCutterResult(
                tracking_id=tracking_id,
                source_path=str(source_path),
                source_sha256=current_sha256,
                source_duration_seconds=0.0,
                cut_point_seconds=0.0,
                success=False,
                review_required=True,
                review_reason="Source file content changed since registration (hash mismatch)",
                error_message="Source fingerprint mismatch",
            )

        is_video = False
        working_audio_path = source_path
        if crev and crev.get("derived_audio_path"):
            cand = Path(crev["derived_audio_path"]).resolve()
            if cand.is_file():
                is_video = True
                working_audio_path = cand
                # Check derived audio registration
                deriv_rec = self.registry.get_video_audio_derivative(str(working_audio_path))
                if not deriv_rec:
                    logger.warning("Derived audio %s not tracked in video_audio_derivatives", working_audio_path)

        # Inspect working audio duration
        try:
            audio_info = self.audio_cutter.inspect_audio(working_audio_path)
            duration = audio_info["duration"]
        except Exception as e:
            return FileCutterResult(
                tracking_id=tracking_id,
                source_path=str(source_path),
                source_sha256=current_sha256,
                source_duration_seconds=0.0,
                cut_point_seconds=0.0,
                success=False,
                review_required=True,
                review_reason=f"Failed to inspect audio stream: {e}",
                error_message=str(e),
            )

        # 3. Content Gating & Cut Point Resolution
        if not crev:
            return FileCutterResult(
                tracking_id=tracking_id,
                source_path=str(source_path),
                source_sha256=current_sha256,
                source_duration_seconds=duration,
                cut_point_seconds=0.0,
                success=False,
                review_required=True,
                review_reason="Missing Tool 5 content discovery review; run Tool 5 first",
                error_message="No content review found",
            )

        classification = crev.get("classification")
        # Gating: Initiation and Vyasa-puja must NOT be auto-cut
        if classification in ("INITIATION", "VYASA_PUJA"):
            return FileCutterResult(
                tracking_id=tracking_id,
                source_path=str(source_path),
                source_sha256=current_sha256,
                source_duration_seconds=duration,
                cut_point_seconds=0.0,
                success=False,
                review_required=True,
                review_reason=f"{classification} recordings require multi-part specification and review; not auto-cut by Tool 6",
            )

        # Check human cut decision
        human_dec = self.registry.get_human_cut_decision(tracking_id)
        if cut_point_override is not None:
            # Audit and persist human cut decision
            self.registry.save_human_cut_decision(
                tracking_id=tracking_id,
                source_sha256=current_sha256,
                cut_point_seconds=cut_point_override,
                reviewer=reviewer,
                notes=notes,
            )
            human_dec = {"cut_point_seconds": cut_point_override, "source_sha256": current_sha256}

        cut_point: Optional[float] = None
        if human_dec and human_dec.get("source_sha256") == current_sha256:
            cut_point = float(human_dec["cut_point_seconds"])
        elif classification == "KIRTAN_AND_CLASS" and crev.get("confidence") == "HIGH":
            prop = crev.get("cutter_proposal")
            if prop:
                cut_point = prop.get("singing_end_seconds") or prop.get("kirtan_range", [0, 0])[1]
        else:
            return FileCutterResult(
                tracking_id=tracking_id,
                source_path=str(source_path),
                source_sha256=current_sha256,
                source_duration_seconds=duration,
                cut_point_seconds=0.0,
                success=False,
                review_required=True,
                review_reason=f"Classification '{classification}' (confidence '{crev.get('confidence')}') is not eligible for auto-cut; manual review required",
            )

        if cut_point is None or cut_point <= 1.0 or cut_point >= duration - 1.0:
            return FileCutterResult(
                tracking_id=tracking_id,
                source_path=str(source_path),
                source_sha256=current_sha256,
                source_duration_seconds=duration,
                cut_point_seconds=cut_point or 0.0,
                success=False,
                review_required=True,
                review_reason=f"Cut point {cut_point}s is invalid for duration {duration:.2f}s",
            )

        # 4. Plan canonical filenames using Tool 1
        mantra_str = crev.get("mantra_type")
        fn_singing, fn_class = self.plan_output_filenames(tracking_id, mantra_str)

        # Destination paths stay beside working audio source (pending Tool 11)
        singing_dest = working_audio_path.parent / fn_singing
        class_dest = working_audio_path.parent / fn_class

        if singing_dest == class_dest:
            return FileCutterResult(
                tracking_id=tracking_id,
                source_path=str(source_path),
                source_sha256=current_sha256,
                source_duration_seconds=duration,
                cut_point_seconds=cut_point,
                success=False,
                review_required=True,
                review_reason="Tool 1 produced identical filenames for singing and class portions; review required",
            )

        # Collision preflight: check that neither target path already exists
        if singing_dest.exists():
            return FileCutterResult(
                tracking_id=tracking_id,
                source_path=str(source_path),
                source_sha256=current_sha256,
                source_duration_seconds=duration,
                cut_point_seconds=cut_point,
                singing_output_path=str(singing_dest),
                class_output_path=str(class_dest),
                success=False,
                review_required=True,
                review_reason=f"Target singing output path already exists: {singing_dest}",
            )
        if class_dest.exists():
            return FileCutterResult(
                tracking_id=tracking_id,
                source_path=str(source_path),
                source_sha256=current_sha256,
                source_duration_seconds=duration,
                cut_point_seconds=cut_point,
                singing_output_path=str(singing_dest),
                class_output_path=str(class_dest),
                success=False,
                review_required=True,
                review_reason=f"Target class output path already exists: {class_dest}",
            )

        # 5. Disk Space Preflight
        free_bytes = shutil.disk_usage(working_audio_path.parent).free
        source_size = working_audio_path.stat().st_size
        required_space = (source_size * 2) + (10 * 1024 * 1024)  # 2x source + 10MB budget
        if free_bytes < required_space:
            return FileCutterResult(
                tracking_id=tracking_id,
                source_path=str(source_path),
                source_sha256=current_sha256,
                source_duration_seconds=duration,
                cut_point_seconds=cut_point,
                success=False,
                review_required=True,
                review_reason=f"Insufficient disk space: available {free_bytes // 1024 // 1024}MB, required {required_space // 1024 // 1024}MB",
            )

        # 6. Dry Run Execution
        if dry_run:
            singing_trim = self.audio_cutter.detect_leading_silence(
                working_audio_path,
                start_seconds=0.0,
                end_seconds=min(cut_point, 60.0),
            )
            class_trim = self.audio_cutter.detect_leading_silence(
                working_audio_path,
                start_seconds=cut_point,
                end_seconds=min(duration, cut_point + 60.0),
            )
            return FileCutterResult(
                tracking_id=tracking_id,
                source_path=str(source_path),
                source_sha256=current_sha256,
                source_duration_seconds=duration,
                cut_point_seconds=cut_point,
                singing_output_path=str(singing_dest),
                singing_tracking_id=f"dry_sing_{tracking_id}",
                singing_sha256="dry_run_hash_singing",
                singing_duration_seconds=round(cut_point - singing_trim, 3),
                singing_leading_silence_seconds=singing_trim,
                singing_pending_tool_11_move=True,
                class_output_path=str(class_dest),
                class_tracking_id=tracking_id,
                class_sha256="dry_run_hash_class",
                class_duration_seconds=round((duration - cut_point) - class_trim, 3),
                class_leading_silence_seconds=class_trim,
                class_pending_tool_11_move=True,
                success=True,
                dry_run=True,
                details={
                    "is_video": is_video,
                    "working_audio_path": str(working_audio_path),
                    "projected_audio_file_path": str(class_dest) if is_video else None,
                },
            )

        # 7. Live Cutting into Bounded Scratch Directory
        run_uuid = uuid.uuid4().hex[:8]
        scratch_dir = root / ".renamer" / "scratch" / f"tool6_{tracking_id}_{run_uuid}"
        scratch_dir.mkdir(parents=True, exist_ok=True)
        self.registry.record_scratch_artifact(scratch_dir, run_id=run_uuid, tracking_id=tracking_id)

        spec = AudioCutSpec(
            source_path=working_audio_path,
            cut_point_seconds=cut_point,
            source_duration_seconds=duration,
            singing_output_path=singing_dest,
            class_output_path=class_dest,
            scratch_dir=scratch_dir,
            trim_silence=True,
        )

        try:
            cut_res = self.audio_cutter.cut_audio(spec)
        except Exception as e:
            shutil.rmtree(scratch_dir, ignore_errors=True)
            self.registry.remove_scratch_artifact(scratch_dir)
            return FileCutterResult(
                tracking_id=tracking_id,
                source_path=str(source_path),
                source_sha256=current_sha256,
                source_duration_seconds=duration,
                cut_point_seconds=cut_point,
                success=False,
                review_required=True,
                review_reason=f"Audio cut failed: {e}",
                error_message=str(e),
            )

        # 8. Safe Atomic Publication & Rollback
        try:
            _atomic_publish_file(cut_res.singing_staged_path, singing_dest)
        except Exception as e:
            shutil.rmtree(scratch_dir, ignore_errors=True)
            self.registry.remove_scratch_artifact(scratch_dir)
            return FileCutterResult(
                tracking_id=tracking_id,
                source_path=str(source_path),
                source_sha256=current_sha256,
                source_duration_seconds=duration,
                cut_point_seconds=cut_point,
                success=False,
                review_required=True,
                review_reason=f"Failed to publish singing output: {e}",
                error_message=str(e),
            )

        try:
            _atomic_publish_file(cut_res.class_staged_path, class_dest)
        except Exception as e:
            # Rollback singing output
            singing_dest.unlink(missing_ok=True)
            shutil.rmtree(scratch_dir, ignore_errors=True)
            self.registry.remove_scratch_artifact(scratch_dir)
            return FileCutterResult(
                tracking_id=tracking_id,
                source_path=str(source_path),
                source_sha256=current_sha256,
                source_duration_seconds=duration,
                cut_point_seconds=cut_point,
                success=False,
                review_required=True,
                review_reason=f"Failed to publish class output; rolled back singing output to retain input: {e}",
                error_message=str(e),
            )

        # Final verification of published files on disk
        valid_s, dur_s, hash_s, err_s = self.audio_cutter.verify_audio_file(singing_dest)
        valid_c, dur_c, hash_c, err_c = self.audio_cutter.verify_audio_file(class_dest)

        if not valid_s or not valid_c:
            singing_dest.unlink(missing_ok=True)
            class_dest.unlink(missing_ok=True)
            shutil.rmtree(scratch_dir, ignore_errors=True)
            self.registry.remove_scratch_artifact(scratch_dir)
            return FileCutterResult(
                tracking_id=tracking_id,
                source_path=str(source_path),
                source_sha256=current_sha256,
                source_duration_seconds=duration,
                cut_point_seconds=cut_point,
                success=False,
                review_required=True,
                review_reason=f"Post-publication verification failed: s={err_s}, c={err_c}; rolled back outputs to retain input",
                error_message=f"Post-pub fail: {err_s or err_c}",
            )

        # 9. Clean up Input Audio (Only after verified publication)
        if is_video:
            # Video source: keep original video! Delete ONLY the owned extracted MP3
            try:
                working_audio_path.unlink(missing_ok=True)
                logger.info("Removed owned video-extracted MP3 after verified split: %s", working_audio_path)
            except Exception as e:
                logger.warning("Could not remove owned extracted MP3 %s: %s", working_audio_path, e)
        else:
            # Audio source: remove original working audio file
            try:
                working_audio_path.unlink(missing_ok=True)
                logger.info("Removed working audio input after verified split: %s", working_audio_path)
            except Exception as e:
                logger.warning("Could not remove working audio %s: %s", working_audio_path, e)

        # Clean scratch directory
        shutil.rmtree(scratch_dir, ignore_errors=True)
        self.registry.remove_scratch_artifact(scratch_dir)

        # 10. Update Local Registry Lineage
        singing_tracking_id = uuid.uuid4().hex[:8]

        # Register singing file in registry
        self.registry.register_file(
            tracking_id=singing_tracking_id,
            current_path=singing_dest,
            original_path=singing_dest,
            original_filename=singing_dest.name,
            current_filename=singing_dest.name,
            proposed_filename=singing_dest.name,
            what_val=derive_split_whats(file_rec.get("what_val"), mantra_str)[0],
            status="committed",
            source_hash=hash_s,
        )

        # Update class successor in registry
        self.registry.update_file_status(
            tracking_id=tracking_id,
            status="committed",
            proposed_filename=class_dest.name,
            current_path=str(class_dest),
        )

        # Record file split in registry
        split_record_data = {
            "source_tracking_id": tracking_id,
            "source_path": str(source_path),
            "source_sha256": current_sha256,
            "source_duration_seconds": duration,
            "cut_point_seconds": cut_point,
            "singing_tracking_id": singing_tracking_id,
            "singing_path": str(singing_dest),
            "singing_sha256": hash_s,
            "singing_duration_seconds": dur_s,
            "singing_leading_silence_seconds": cut_res.singing_leading_silence,
            "singing_pending_tool_11_move": True,
            "class_tracking_id": tracking_id,
            "class_path": str(class_dest),
            "class_sha256": hash_c,
            "class_duration_seconds": dur_c,
            "class_leading_silence_seconds": cut_res.class_leading_silence,
            "class_pending_tool_11_move": True,
            "tool_version": "1.0.0",
            "evidence_ids": [crev.get("transcript_path", "")] if crev else [],
            "details": {
                "is_video": is_video,
                "commands": cut_res.commands_executed,
                "codec": cut_res.codec_summary,
            },
        }
        self.registry.record_file_split(split_record_data)

        # Save checkpoint
        self.registry.save_stage_checkpoint(
            tracking_id=tracking_id,
            stage_name="tool_6_file_cutter",
            input_path=str(source_path),
            input_sha256=current_sha256,
            status="COMPLETED",
            summary=f"Split into singing ({fn_singing}) and class ({fn_class}) at {cut_point:.2f}s",
            details=split_record_data,
        )

        # 11. Tool 4 Baserow Synchronization (Optional / Non-destructive)
        sync_res_class = None
        sync_res_singing = None

        if self.media_db_service is not None:
            try:
                from ..media_db_updater.models import MediaDbSyncRequest

                # Sync Class successor
                # For video: keep video filename and media_archive_path; write class MP3 to audio_file_path!
                clean_class_what = derive_split_whats(file_rec.get("what_val"), mantra_str)[1]
                req_class = MediaDbSyncRequest(
                    tracking_id=tracking_id,
                    current_filename=source_path.name if is_video else class_dest.name,
                    current_path=str(source_path) if is_video else str(class_dest),
                    audio_file_path=str(class_dest) if is_video else None,
                    what_val=clean_class_what,
                )

                if hasattr(self.media_db_service, "synchronize"):
                    res_c = self.media_db_service.synchronize(tracking_id, commit=not dry_run, request=req_class)
                elif hasattr(self.media_db_service, "sync_file"):
                    res_c = self.media_db_service.sync_file(req_class)
                else:
                    res_c = None
                if res_c:
                    sync_res_class = res_c.model_dump() if hasattr(res_c, "model_dump") else dict(res_c)

                # Sync Singing child
                clean_singing_what = derive_split_whats(file_rec.get("what_val"), mantra_str)[0]
                req_singing = MediaDbSyncRequest(
                    tracking_id=singing_tracking_id,
                    current_filename=singing_dest.name,
                    current_path=str(singing_dest),
                    what_val=clean_singing_what,
                    what_category="Kirtan",
                )
                if hasattr(self.media_db_service, "synchronize"):
                    res_s = self.media_db_service.synchronize(singing_tracking_id, commit=not dry_run, request=req_singing)
                elif hasattr(self.media_db_service, "sync_file"):
                    res_s = self.media_db_service.sync_file(req_singing)
                else:
                    res_s = None
                if res_s:
                    sync_res_singing = res_s.model_dump() if hasattr(res_s, "model_dump") else dict(res_s)
            except Exception as e:
                logger.warning("Tool 4 synchronization failed for split %s: %s; local split preserved", tracking_id, e)

        return FileCutterResult(
            tracking_id=tracking_id,
            source_path=str(source_path),
            source_sha256=current_sha256,
            source_duration_seconds=duration,
            cut_point_seconds=cut_point,
            singing_output_path=str(singing_dest),
            singing_tracking_id=singing_tracking_id,
            singing_sha256=hash_s,
            singing_duration_seconds=dur_s,
            singing_leading_silence_seconds=cut_res.singing_leading_silence,
            singing_pending_tool_11_move=True,
            class_output_path=str(class_dest),
            class_tracking_id=tracking_id,
            class_sha256=hash_c,
            class_duration_seconds=dur_c,
            class_leading_silence_seconds=cut_res.class_leading_silence,
            class_pending_tool_11_move=True,
            success=True,
            tool_version="1.0.0",
            media_db_sync_class=sync_res_class,
            media_db_sync_singing=sync_res_singing,
            details={
                "is_video": is_video,
                "codec": cut_res.codec_summary,
            },
        )
