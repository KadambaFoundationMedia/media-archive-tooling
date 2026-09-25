"""Application service layer for Tool 6 — File Cutter."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
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
from ..renamer.parser.what import SB_REGEX, BG_REGEX, CC_REGEX
from ..media_db_updater.country_mapper import get_country_name_for_iso, is_valid_country_display_name
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
    if detected_mantra and detected_mantra.strip().lower() not in ("none", "unknown"):
        s_what = detected_mantra.strip()
    else:
        # Default to generic Kirtan rather than inventing a specific song title
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


def _atomic_publish_file(staged_path: Path, target_path: Path, overwrite: bool = False) -> None:
    """Safely, exclusively, and atomically publish staged_path to target_path."""
    staged_path = staged_path.resolve()
    target_path = target_path.resolve()

    if target_path.exists():
        if overwrite:
            os.replace(staged_path, target_path)
            return
        staged_path.unlink(missing_ok=True)
        raise FileExistsError(f"Target file already exists: {target_path}")

    # Case-insensitive collision check in parent directory
    parent = target_path.parent
    target_lower = target_path.name.lower()
    for existing in parent.iterdir():
        if existing.is_file() and existing.name.lower() == target_lower and existing.name != target_path.name:
            staged_path.unlink(missing_ok=True)
            raise FileExistsError(f"Case-insensitive filename collision with existing file: {existing}")

    # Step 1: Attempt atomic link on same filesystem
    try:
        os.link(staged_path, target_path)
        staged_path.unlink(missing_ok=True)
        return
    except FileExistsError:
        staged_path.unlink(missing_ok=True)
        raise FileExistsError(f"Target file already exists: {target_path}")
    except OSError:
        pass

    # Step 2: Exclusive creation via O_CREAT | O_EXCL to prevent concurrent clobbering
    try:
        fd = os.open(target_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        staged_path.unlink(missing_ok=True)
        raise FileExistsError(f"Target file already exists: {target_path}")
    except Exception as e:
        staged_path.unlink(missing_ok=True)
        raise RuntimeError(f"Could not open exclusive publication target {target_path}: {e}") from e

    try:
        with os.fdopen(fd, "wb") as dst:
            with staged_path.open("rb") as src:
                shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())
        staged_path.unlink(missing_ok=True)
    except Exception as e:
        target_path.unlink(missing_ok=True)
        staged_path.unlink(missing_ok=True)
        raise RuntimeError(f"Could not safely copy file to {target_path}: {e}") from e


def _safe_rollback_output(output_path: Path, expected_hash: str) -> None:
    """Unlink an output file during rollback ONLY if its physical hash matches this attempt."""
    if not output_path.exists():
        return
    try:
        curr_hash = compute_sha256(output_path)
        if curr_hash == expected_hash:
            output_path.unlink(missing_ok=True)
            logger.info("Rolled back owned output file: %s", output_path)
        else:
            logger.warning(
                "Rollback skipped for %s: current hash %s does not match expected %s",
                output_path,
                curr_hash,
                expected_hash,
            )
    except Exception as e:
        logger.warning("Error during ownership-checked rollback of %s: %s", output_path, e)


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
        p_singing.file_metadata.edited = False
        p_singing.file_metadata.possible_combination = False
        prop_singing = self.planner.plan_rename(p_singing)

        # Build class ParserResult
        p_class = ParserResult.model_validate(parser_dict)
        p_class.what.selected_value = class_what
        p_class.what.state = ResolutionState.EXACT
        p_class.identity.extension = ext
        p_class.file_metadata.edited = False
        p_class.file_metadata.possible_combination = False
        prop_class = self.planner.plan_rename(p_class)

        # Distinct proposed filenames through Tool 1 RenamePlanner boundary
        fn_singing = prop_singing.proposed_filename
        fn_class = prop_class.proposed_filename
        if fn_singing == fn_class:
            stem = Path(fn_class).stem
            ext = Path(fn_class).suffix
            fn_class = f"{stem}-02{ext}"

        return fn_singing, fn_class

    def cut_file(
        self,
        tracking_id_or_path: Union[str, Path],
        dry_run: bool = False,
        cut_point_override: Optional[float] = None,
        force: bool = False,
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

        # Check if already split (Idempotency)
        existing_split = self.registry.get_file_split_by_source(tracking_id)
        if existing_split and not force:
            # T6-R-010: Validate recorded split before idempotent reuse
            singing_tid = existing_split.get("singing_tracking_id")
            class_tid = existing_split.get("class_tracking_id")

            # Check Tool 11 current locations from registry files table, or fallback to stored paths
            singing_rec = self.registry.get_file(singing_tid) if singing_tid else None
            if singing_rec and singing_rec.get("current_path") and Path(singing_rec["current_path"]).is_file():
                singing_p = Path(singing_rec["current_path"]).resolve()
            elif existing_split.get("singing_path") and Path(existing_split["singing_path"]).is_file():
                singing_p = Path(existing_split["singing_path"]).resolve()
            else:
                singing_p = None

            class_rec = self.registry.get_file(class_tid) if class_tid else None
            if class_rec and class_rec.get("current_path") and Path(class_rec["current_path"]).is_file():
                class_p = Path(class_rec["current_path"]).resolve()
            elif existing_split.get("class_path") and Path(existing_split["class_path"]).is_file():
                class_p = Path(existing_split["class_path"]).resolve()
            else:
                class_p = None

            outputs_exist = (singing_p is not None and class_p is not None)
            hashes_match = False
            if outputs_exist:
                hash_s = compute_sha256(singing_p)
                hash_c = compute_sha256(class_p)
                hashes_match = (
                    hash_s == existing_split.get("singing_sha256")
                    and hash_c == existing_split.get("class_sha256")
                )

            # Check if source input was restored on disk (for non-video audio where source is removed upon cut)
            split_details = {}
            if existing_split.get("split_details_json"):
                try:
                    split_details = json.loads(existing_split["split_details_json"])
                except Exception:
                    pass
            is_video_split = split_details.get("is_video", False) or source_path.suffix.lower() in {
                ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"
            }
            orig_path_str = file_rec.get("original_path") or existing_split.get("source_path")
            source_p_exists = source_path.is_file() and (
                str(source_path) == str(Path(orig_path_str).resolve())
                or source_path.name == Path(orig_path_str).name
            )
            source_restored = source_p_exists and not is_video_split

            if not outputs_exist or not hashes_match or source_restored:
                reasons = []
                if not outputs_exist:
                    missing = []
                    if singing_p is None:
                        missing.append(f"singing ({existing_split.get('singing_path')})")
                    if class_p is None:
                        missing.append(f"class ({existing_split.get('class_path')})")
                    reasons.append(f"output files missing on disk: {', '.join(missing)}")
                elif not hashes_match:
                    reasons.append("output physical hashes do not match recorded split hashes")
                if source_restored:
                    reasons.append(f"original source audio file is present on disk despite recorded completed split ({source_path})")

                review_msg = (
                    f"Prior split recorded in registry (ID {existing_split.get('id')}) cannot be verified: "
                    f"{'; '.join(reasons)}. Recovery or manual review required; use --force to re-cut."
                )
                logger.warning(review_msg)
                return FileCutterResult(
                    tracking_id=tracking_id,
                    source_path=str(source_path),
                    source_sha256=file_rec.get("source_sha256", "") or (compute_sha256(source_path) if source_path.is_file() else ""),
                    source_duration_seconds=float(existing_split.get("source_duration_seconds") or 0.0),
                    cut_point_seconds=float(existing_split.get("cut_point_seconds") or 0.0),
                    singing_output_path=str(singing_p) if singing_p else existing_split.get("singing_path"),
                    singing_tracking_id=singing_tid,
                    class_output_path=str(class_p) if class_p else existing_split.get("class_path"),
                    class_tracking_id=class_tid,
                    success=False,
                    review_required=True,
                    review_reason=review_msg,
                    error_message="Recorded split lineage unverified",
                    dry_run=dry_run,
                    details={
                        "reused_existing_split": False,
                        "lineage_unverified": True,
                        "missing_outputs": not outputs_exist,
                        "source_restored": source_restored,
                    },
                )

            logger.info("File %s already completed split (ID %s) and outputs verified on disk", tracking_id, existing_split["id"])
            return FileCutterResult(
                tracking_id=tracking_id,
                source_path=existing_split["source_path"],
                source_sha256=existing_split["source_sha256"],
                source_duration_seconds=existing_split["source_duration_seconds"],
                cut_point_seconds=existing_split["cut_point_seconds"],
                singing_output_path=str(singing_p),
                singing_tracking_id=existing_split["singing_tracking_id"],
                singing_sha256=existing_split["singing_sha256"],
                singing_duration_seconds=existing_split["singing_duration_seconds"],
                singing_leading_silence_seconds=existing_split["singing_leading_silence_seconds"],
                singing_pending_tool_11_move=bool(existing_split["singing_pending_tool_11_move"]),
                class_output_path=str(class_p),
                class_tracking_id=existing_split["class_tracking_id"],
                class_sha256=existing_split["class_sha256"],
                class_duration_seconds=existing_split["class_duration_seconds"],
                class_leading_silence_seconds=existing_split["class_leading_silence_seconds"],
                class_pending_tool_11_move=bool(existing_split["class_pending_tool_11_move"]),
                success=True,
                dry_run=dry_run,
                tool_version=existing_split["tool_version"],
                details={"reused_existing_split": True},
            )

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
                # Check derived audio registration and verified ownership
                deriv_rec = self.registry.get_video_audio_derivative(str(working_audio_path))
                if not deriv_rec:
                    return FileCutterResult(
                        tracking_id=tracking_id,
                        source_path=str(source_path),
                        source_sha256=current_sha256,
                        source_duration_seconds=0.0,
                        cut_point_seconds=0.0,
                        success=False,
                        review_required=True,
                        review_reason=f"Derived audio {working_audio_path} is not tracked in video_audio_derivatives; cannot safely cut or delete",
                        error_message="Derived audio not registered",
                    )
                if deriv_rec.get("source_video_tracking_id") != tracking_id:
                    return FileCutterResult(
                        tracking_id=tracking_id,
                        source_path=str(source_path),
                        source_sha256=current_sha256,
                        source_duration_seconds=0.0,
                        cut_point_seconds=0.0,
                        success=False,
                        review_required=True,
                        review_reason=f"Derived audio {working_audio_path} owner mismatch: expected {tracking_id}, found {deriv_rec.get('source_video_tracking_id')}",
                        error_message="Derived audio owner mismatch",
                    )
                if deriv_rec.get("source_video_sha256") != current_sha256:
                    return FileCutterResult(
                        tracking_id=tracking_id,
                        source_path=str(source_path),
                        source_sha256=current_sha256,
                        source_duration_seconds=0.0,
                        cut_point_seconds=0.0,
                        success=False,
                        review_required=True,
                        review_reason=f"Derived audio source hash mismatch: expected {current_sha256}, found {deriv_rec.get('source_video_sha256')}",
                        error_message="Derived audio source hash mismatch",
                    )
                derived_sha = compute_sha256(working_audio_path)
                if deriv_rec.get("derived_sha256") and derived_sha != deriv_rec.get("derived_sha256"):
                    return FileCutterResult(
                        tracking_id=tracking_id,
                        source_path=str(source_path),
                        source_sha256=current_sha256,
                        source_duration_seconds=0.0,
                        cut_point_seconds=0.0,
                        success=False,
                        review_required=True,
                        review_reason=f"Derived audio physical hash mismatch: expected {deriv_rec.get('derived_sha256')}, found {derived_sha}",
                        error_message="Derived audio physical hash mismatch",
                    )

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

        # Handle cut point override
        human_dec = self.registry.get_human_cut_decision(tracking_id)
        if cut_point_override is not None:
            # Validate cut point override value
            if cut_point_override <= 1.0 or cut_point_override >= duration - 1.0 or not math.isfinite(cut_point_override):
                return FileCutterResult(
                    tracking_id=tracking_id,
                    source_path=str(source_path),
                    source_sha256=current_sha256,
                    source_duration_seconds=duration,
                    cut_point_seconds=cut_point_override,
                    success=False,
                    review_required=True,
                    review_reason=f"Cut point override {cut_point_override}s is invalid for duration {duration:.2f}s",
                )

            # A cut point alone must NOT approve a non-combination file for two-part cut
            if classification != "KIRTAN_AND_CLASS":
                return FileCutterResult(
                    tracking_id=tracking_id,
                    source_path=str(source_path),
                    source_sha256=current_sha256,
                    source_duration_seconds=duration,
                    cut_point_seconds=cut_point_override,
                    success=False,
                    review_required=True,
                    review_reason=f"File classification '{classification}' is not KIRTAN_AND_CLASS; a cut point alone cannot approve non-combination recording for two-part cut",
                )

            # Multi-part ceremonies (Vyasa-puja, Initiation) must never be automatically two-part cut
            source_p = Path(source_path)
            source_fn = source_p.name.lower()
            source_parent = source_p.parent.name.lower()
            is_ceremony_source = any(p in source_fn or p in source_parent for p in ["vyasa-puja", "vyasa puja", "vyasapuja", "vyasa_puja", "initiation", "diksa", "diksha"])
            if classification in ("VYASA_PUJA", "INITIATION") or is_ceremony_source:
                return FileCutterResult(
                    tracking_id=tracking_id,
                    source_path=str(source_path),
                    source_sha256=current_sha256,
                    source_duration_seconds=duration,
                    cut_point_seconds=0.0,
                    success=False,
                    review_required=True,
                    review_reason=f"Multi-part ceremony ({classification or 'ceremony'}) requires multi-part specification and review; not eligible for automatic two-part cut",
                )

            # Save human decision only when NOT dry_run!
            if not dry_run:
                self.registry.save_human_cut_decision(
                    tracking_id=tracking_id,
                    source_sha256=current_sha256,
                    cut_point_seconds=cut_point_override,
                    reviewer=reviewer,
                    notes=notes,
                )
            human_dec = {"cut_point_seconds": cut_point_override, "source_sha256": current_sha256}

        # Multi-part ceremonies (Vyasa-puja, Initiation) must never be automatically two-part cut
        source_p = Path(source_path)
        source_fn = source_p.name.lower()
        source_parent = source_p.parent.name.lower()
        is_ceremony_source = any(p in source_fn or p in source_parent for p in ["vyasa-puja", "vyasa puja", "vyasapuja", "vyasa_puja", "initiation", "diksa", "diksha"])
        if classification in ("VYASA_PUJA", "INITIATION") or is_ceremony_source:
            return FileCutterResult(
                tracking_id=tracking_id,
                source_path=str(source_path),
                source_sha256=current_sha256,
                source_duration_seconds=duration,
                cut_point_seconds=0.0,
                success=False,
                review_required=True,
                review_reason=f"Multi-part ceremony ({classification or 'ceremony'}) requires multi-part specification and review; not eligible for automatic two-part cut",
            )

        cut_point: Optional[float] = None
        if human_dec and human_dec.get("source_sha256") == current_sha256:
            # Validate content type for stored human cut decision as well
            if classification != "KIRTAN_AND_CLASS":
                return FileCutterResult(
                    tracking_id=tracking_id,
                    source_path=str(source_path),
                    source_sha256=current_sha256,
                    source_duration_seconds=duration,
                    cut_point_seconds=float(human_dec["cut_point_seconds"]),
                    success=False,
                    review_required=True,
                    review_reason=f"File classification '{classification}' is not KIRTAN_AND_CLASS; a cut point alone cannot approve non-combination recording for two-part cut",
                )
            cut_point = float(human_dec["cut_point_seconds"])
        elif classification == "KIRTAN_AND_CLASS" and crev.get("confidence") == "HIGH":
            prop = crev.get("cutter_proposal")
            if prop and prop.get("confidence") == "HIGH" and prop.get("singing_end_seconds") is not None:
                cut_point = float(prop["singing_end_seconds"])
            else:
                return FileCutterResult(
                    tracking_id=tracking_id,
                    source_path=str(source_path),
                    source_sha256=current_sha256,
                    source_duration_seconds=duration,
                    cut_point_seconds=0.0,
                    success=False,
                    review_required=True,
                    review_reason="Exact singing_end_seconds is missing or confidence is not HIGH; manual review required",
                )
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

        # Detect if one of the targets is the source/working audio file being split in-place
        source_resolved = {source_path.resolve(), working_audio_path.resolve()}
        in_place_target: Optional[Path] = None
        if singing_dest.resolve() in source_resolved:
            in_place_target = singing_dest
        elif class_dest.resolve() in source_resolved:
            in_place_target = class_dest

        # Collision preflight: check that neither target path already exists,
        # unless it is the source/working audio file being split in-place or force=True.
        if not force and singing_dest.exists() and singing_dest.resolve() not in source_resolved:
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
        if not force and class_dest.exists() and class_dest.resolve() not in source_resolved:
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
            first_retained_class = round(cut_point + class_trim, 3)
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
                    "first_retained_class_audio_seconds": first_retained_class,
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
            class_start_seconds=cut_point,
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
        in_place_backup_path: Optional[Path] = None
        if in_place_target is not None and in_place_target.exists():
            in_place_backup_path = scratch_dir / f"backup_{in_place_target.name}"
            try:
                shutil.move(in_place_target, in_place_backup_path)
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
                    review_reason=f"Failed to stage in-place source backup: {e}",
                    error_message=str(e),
                )

        try:
            if force:
                _atomic_publish_file(cut_res.singing_staged_path, singing_dest, overwrite=True)
            else:
                _atomic_publish_file(cut_res.singing_staged_path, singing_dest)
        except Exception as e:
            if in_place_backup_path and in_place_backup_path.exists():
                try:
                    shutil.move(in_place_backup_path, in_place_target)
                except Exception:
                    pass
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
            if force:
                _atomic_publish_file(cut_res.class_staged_path, class_dest, overwrite=True)
            else:
                _atomic_publish_file(cut_res.class_staged_path, class_dest)
        except Exception as e:
            # Ownership-checked rollback of singing output
            _safe_rollback_output(singing_dest, cut_res.singing_sha256)
            if in_place_backup_path and in_place_backup_path.exists():
                try:
                    shutil.move(in_place_backup_path, in_place_target)
                except Exception:
                    pass
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
            _safe_rollback_output(singing_dest, hash_s or cut_res.singing_sha256)
            _safe_rollback_output(class_dest, hash_c or cut_res.class_sha256)
            if in_place_backup_path and in_place_backup_path.exists():
                try:
                    shutil.move(in_place_backup_path, in_place_target)
                except Exception:
                    pass
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

        # 9. Update Local Registry Lineage & Checkpoints FIRST (Durable before deletion)
        singing_tracking_id = uuid.uuid4().hex[:8]
        clean_singing_what, clean_class_what = derive_split_whats(file_rec.get("what_val"), mantra_str)

        # Extract confirmed Tool 1 metadata from parent file record
        parser_dict = file_rec.get("parser_result") or {}
        when_data = parser_dict.get("when") or {}
        what_data = parser_dict.get("what") or {}
        where_data = parser_dict.get("where") or {}
        context_data = parser_dict.get("context") or {}

        when_val = when_data.get("selected_value") or file_rec.get("when_val")
        raw_when_state = when_data.get("state")
        when_prov = when_data.get("evidence") or when_data.get("provenance")

        where_val = file_rec.get("where_val")
        where_place = where_data.get("place_location")
        raw_country = where_data.get("country")
        raw_iso = where_data.get("country_iso2") or where_val
        country_name = None
        if raw_country:
            clean_c = str(raw_country).strip()
            if len(clean_c) == 2:
                country_name = get_country_name_for_iso(clean_c)
            elif is_valid_country_display_name(clean_c) or len(clean_c) > 2:
                country_name = clean_c
        elif raw_iso:
            clean_iso = str(raw_iso).strip()
            country_name = get_country_name_for_iso(clean_iso)

        raw_where_state = where_data.get("state")
        where_prov = where_data.get("evidence") or where_data.get("provenance")

        who_val = parser_dict.get("who") or file_rec.get("who_val") or "KKS"
        parent_ctx = context_data.get("parent_folder") or Path(file_rec["current_path"]).parent.name

        def _clean_st(st: Any) -> Optional[str]:
            if st is None:
                return None
            val = st.value if hasattr(st, "value") else str(st)
            clean = val.strip().lower()
            return clean if clean and clean != "none" else None

        clean_when_state = _clean_st(raw_when_state)

        clean_where_state = _clean_st(raw_where_state)

        # Resolve category for class successor
        class_cat = what_data.get("category")
        if not class_cat and clean_class_what:
            if SB_REGEX.search(clean_class_what):
                class_cat = "Srimad Bhagavatam"
            elif BG_REGEX.search(clean_class_what):
                class_cat = "Bhagavad-gita"
            elif CC_REGEX.search(clean_class_what):
                class_cat = "Caitanya caritamrta"

        raw_what_state = what_data.get("state")
        clean_class_what_state = _clean_st(raw_what_state)

        class_what_prov = list(what_data.get("evidence") or what_data.get("provenance") or [])
        if crev:
            class_what_prov.append({
                "source": "tool_5_content_discoverer",
                "classification": crev.get("classification"),
                "confidence": crev.get("confidence"),
            })

        singing_what_prov = list(what_data.get("evidence") or what_data.get("provenance") or [])
        if crev:
            singing_what_prov.append({
                "source": "tool_5_content_discoverer",
                "classification": crev.get("classification"),
                "mantra_type": mantra_str,
                "confidence": crev.get("confidence"),
            })

        # Construct Tool 1 ParserResult for singing child with full confirmed metadata
        p_singing = ParserResult.model_validate(parser_dict)
        p_singing.identity.tracking_id = singing_tracking_id
        p_singing.identity.current_filename = singing_dest.name
        p_singing.identity.original_filename = singing_dest.name
        p_singing.identity.original_path = str(singing_dest.resolve())
        p_singing.identity.extension = singing_dest.suffix.lower()
        p_singing.what.selected_value = clean_singing_what
        p_singing.what.category = "Kirtan"
        p_singing.what.state = ResolutionState.EXACT
        p_singing.file_metadata.edited = False
        p_singing.file_metadata.possible_combination = False

        # Construct Tool 1 ParserResult for class successor with full confirmed metadata
        p_class = ParserResult.model_validate(parser_dict)
        p_class.identity.current_filename = source_path.name if is_video else class_dest.name
        p_class.identity.extension = source_path.suffix.lower() if is_video else class_dest.suffix.lower()
        p_class.what.selected_value = clean_class_what
        if class_cat:
            p_class.what.category = class_cat
        p_class.file_metadata.edited = False
        p_class.file_metadata.possible_combination = False

        # Register singing file in registry with confirmed metadata
        self.registry.register_file(
            tracking_id=singing_tracking_id,
            current_path=singing_dest,
            original_path=singing_dest,
            original_filename=singing_dest.name,
            current_filename=singing_dest.name,
            proposed_filename=singing_dest.name,
            when_val=when_val,
            who_val=who_val,
            what_val=clean_singing_what,
            where_val=where_val,
            status="committed",
            source_hash=hash_s,
            parser_result_json=p_singing.model_dump_json(),
        )

        # Update class successor in registry
        # For video: retain the original video as current_path; class audio tracked separately!
        if is_video:
            self.registry.update_file_status(
                tracking_id=tracking_id,
                status="committed",
                proposed_filename=source_path.name,
                current_path=str(source_path),
                what_val=clean_class_what,
                parser_result_json=p_class.model_dump_json(),
            )
        else:
            self.registry.update_file_status(
                tracking_id=tracking_id,
                status="committed",
                proposed_filename=class_dest.name,
                current_path=str(class_dest),
                what_val=clean_class_what,
                parser_result_json=p_class.model_dump_json(),
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

        # 10. Clean up Input Audio (Only AFTER durable lineage persistence)
        if in_place_target is None:
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

        # 11. Tool 4 Baserow Synchronization (Preserves local split on failure)
        sync_res_class = None
        sync_res_singing = None

        from ..media_db_updater.models import MediaDbSyncRequest

        # Resolve Tool 2 decision for class successor: check SQLite media_db_reviews or prior sync
        class_t2_rec = self.registry.get_media_db_review(tracking_id)
        class_t2_decision = "EXISTING_MEDIA_MATCH"
        class_selected_row_id = None
        if class_t2_rec:
            class_t2_decision = class_t2_rec.get("decision") or "EXISTING_MEDIA_MATCH"
            class_selected_row_id = class_t2_rec.get("selected_media_row_id")
        else:
            prior_s = self.registry.get_media_db_sync(tracking_id)
            if prior_s:
                class_selected_row_id = prior_s.get("media_row_id")
                if not class_selected_row_id and prior_s.get("request"):
                    class_selected_row_id = prior_s["request"].get("selected_media_row_id")
                    if prior_s["request"].get("tool2_decision"):
                        class_t2_decision = prior_s["request"].get("tool2_decision")

        table_id = ""
        if self.media_db_service and hasattr(self.media_db_service, "write_adapter"):
            table_id = str(getattr(self.media_db_service.write_adapter, "media_table_id", "") or "")

        fp_str_c = f"{tracking_id}|{source_path if is_video else class_dest}|{source_path.name if is_video else class_dest.name}|{file_rec.get('original_path')}|{file_rec.get('original_filename')}|||{class_selected_row_id or ''}|{when_val}|{clean_class_what}|{country_name}|{where_place}"
        req_fp_c = hashlib.sha256(fp_str_c.encode("utf-8")).hexdigest()
        req_id_c = f"req_{tracking_id}_{int(datetime.now(timezone.utc).timestamp())}"

        req_class = MediaDbSyncRequest(
            tracking_id=tracking_id,
            current_filename=source_path.name if is_video else class_dest.name,
            current_path=str(source_path) if is_video else str(class_dest),
            original_filename=file_rec.get("original_filename"),
            original_path=file_rec.get("original_path"),
            audio_file_path=str(class_dest) if is_video else None,
            request_id=req_id_c,
            request_fingerprint=req_fp_c,
            table_id=table_id,
            when_val=when_val,
            when_state=clean_when_state,
            when_provenance=when_prov,
            what_val=clean_class_what,
            what_category=class_cat,
            what_verse=what_data.get("verse"),
            what_state=clean_class_what_state,
            what_provenance=class_what_prov,
            who_val=who_val,
            where_val=where_val,
            where_place=where_place,
            where_country=country_name,
            where_country_iso=where_data.get("country_iso2"),
            where_state=clean_where_state,
            where_provenance=where_prov,
            parent_folder_context=parent_ctx,
            tool2_decision=class_t2_decision,
            selected_media_row_id=class_selected_row_id,
        )

        fp_str_s = f"{singing_tracking_id}|{singing_dest}|{singing_dest.name}||||||{when_val}|{clean_singing_what}|{country_name}|{where_place}"
        req_fp_s = hashlib.sha256(fp_str_s.encode("utf-8")).hexdigest()
        req_id_s = f"req_{singing_tracking_id}_{int(datetime.now(timezone.utc).timestamp())}"

        req_singing = MediaDbSyncRequest(
            tracking_id=singing_tracking_id,
            current_filename=singing_dest.name,
            current_path=str(singing_dest),
            original_filename=singing_dest.name,
            original_path=str(singing_dest),
            request_id=req_id_s,
            request_fingerprint=req_fp_s,
            table_id=table_id,
            when_val=when_val,
            when_state=clean_when_state,
            when_provenance=when_prov,
            what_val=clean_singing_what,
            what_category="Kirtan",
            what_state="exact",
            what_provenance=singing_what_prov,
            who_val=who_val,
            where_val=where_val,
            where_place=where_place,
            where_country=country_name,
            where_country_iso=where_data.get("country_iso2"),
            where_state=clean_where_state,
            where_provenance=where_prov,
            parent_folder_context=parent_ctx,
            tool2_decision="NEW_MEDIA_CANDIDATE",
        )

        # Record Tool 2 review for singing child in local registry
        self.registry.save_media_db_review(
            tracking_id=singing_tracking_id,
            decision="NEW_MEDIA_CANDIDATE",
            database_state="CLEAN",
            selected_media_row_id=None,
            snapshot_timestamp=datetime.now(timezone.utc).isoformat(),
            result_json=json.dumps({
                "decision": "NEW_MEDIA_CANDIDATE",
                "proposed_tool4_action": "CREATE",
                "reason": "Tool 6 split singing portion",
            }),
        )

        if self.media_db_service is not None:
            # Sync Class successor
            try:
                res_c = None
                if hasattr(self.media_db_service, "synchronize"):
                    res_c = self.media_db_service.synchronize(tracking_id, commit=not dry_run, request=req_class)
                elif hasattr(self.media_db_service, "sync_file"):
                    res_c = self.media_db_service.sync_file(req_class)
                if res_c:
                    sync_res_class = res_c.model_dump() if hasattr(res_c, "model_dump") else dict(res_c)
            except Exception as e:
                logger.warning("Tool 4 synchronization failed for class %s: %s; recording pending sync", tracking_id, e)
                self.registry.save_media_db_sync(
                    tracking_id=tracking_id,
                    sync_status="PENDING_SYNC",
                    operation_type="UPDATE",
                    error_message=str(e),
                    request_json=json.dumps(req_class.model_dump()),
                )

            # Sync Singing child
            try:
                res_s = None
                if hasattr(self.media_db_service, "synchronize"):
                    res_s = self.media_db_service.synchronize(singing_tracking_id, commit=not dry_run, request=req_singing)
                elif hasattr(self.media_db_service, "sync_file"):
                    res_s = self.media_db_service.sync_file(req_singing)
                if res_s:
                    sync_res_singing = res_s.model_dump() if hasattr(res_s, "model_dump") else dict(res_s)
            except Exception as e:
                logger.warning("Tool 4 synchronization failed for singing %s: %s; recording pending sync", singing_tracking_id, e)
                self.registry.save_media_db_sync(
                    tracking_id=singing_tracking_id,
                    sync_status="PENDING_SYNC",
                    operation_type="CREATE",
                    error_message=str(e),
                    request_json=json.dumps(req_singing.model_dump()),
                )
        else:
            # Media DB service not configured; record pending sync outbox records
            self.registry.save_media_db_sync(
                tracking_id=tracking_id,
                sync_status="PENDING_SYNC",
                operation_type="UPDATE",
                request_json=json.dumps(req_class.model_dump()),
            )
            self.registry.save_media_db_sync(
                tracking_id=singing_tracking_id,
                sync_status="PENDING_SYNC",
                operation_type="CREATE",
                request_json=json.dumps(req_singing.model_dump()),
            )

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
