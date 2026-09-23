"""Local transcription runtime for Tool 5 using whisper.cpp."""
from abc import ABC, abstractmethod
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .audio_extractor import compute_file_sha256
from .models import DerivedAudioDetails, TranscriptArtifact, TranscriptSegment


MAX_TRANSCRIPT_BYTES = 100 * 1024 * 1024  # 100 MB max allowed JSON payload


class TranscriptionError(Exception):
    """Base exception for transcription errors."""
    pass


class TranscriptionBlockedError(TranscriptionError):
    """Raised when transcription cannot proceed or model/hardware fails."""
    pass


def parse_timestamp_seconds(value: Any) -> Optional[float]:
    """Parse various timestamp representations (float, string 'HH:MM:SS.mmm') into seconds."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        sec = float(value)
        return sec if math.isfinite(sec) and sec >= 0 else None

    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    parts = text.split(":")
    try:
        if len(parts) == 1:
            sec = float(parts[0])
        elif len(parts) == 2:
            sec = float(parts[0]) * 60.0 + float(parts[1])
        elif len(parts) == 3:
            sec = float(parts[0]) * 3600.0 + float(parts[1]) * 60.0 + float(parts[2])
        else:
            return None
    except ValueError:
        return None
    return sec if math.isfinite(sec) and sec >= 0 else None


def probe_audio_duration(audio_path: Path, ffprobe_bin: Optional[str] = None) -> float:
    """Probe audio duration in seconds using ffprobe."""
    ffprobe_bin = ffprobe_bin or shutil.which("ffprobe")
    if not ffprobe_bin:
        return 0.0
    try:
        res = subprocess.run(
            [
                ffprobe_bin,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            capture_output=True,
            timeout=15,
            check=False,
            stdin=subprocess.DEVNULL,
        )
        if res.returncode == 0:
            val = float(res.stdout.decode().strip())
            return val if math.isfinite(val) and val >= 0 else 0.0
    except Exception:
        pass
    return 0.0


class BaseTranscriptionAdapter(ABC):
    """Abstract interface for transcribing audio files into normalized transcripts."""

    @abstractmethod
    def probe_availability(self) -> Dict[str, Any]:
        """Check availability of transcription binary and model."""
        pass

    @abstractmethod
    def transcribe(
        self,
        audio_path: Path,
        tracking_id: str,
        source_path: Optional[Path] = None,
        source_type: str = "audio",
        derived_audio_details: Optional[DerivedAudioDetails] = None,
        requested_device: str = "auto",
        model_path: Optional[Path] = None,
        force: bool = False,
        root_dir: Optional[Path] = None,
        dry_run: bool = False,
    ) -> TranscriptArtifact:
        """Transcribe audio into a durable normalized TranscriptArtifact."""
        pass


class WhisperCppTranscriptionAdapter(BaseTranscriptionAdapter):
    """Production transcription adapter wrapping Homebrew whisper-cli from whisper.cpp."""

    def __init__(
        self,
        whisper_executable: Optional[Path] = None,
        default_model_path: Optional[Path] = None,
        threads: int = 6,
    ):
        self.whisper_executable = whisper_executable or Path(shutil.which("whisper-cli") or "/opt/homebrew/bin/whisper-cli")
        self.default_model_path = default_model_path or (Path.home() / ".cache" / "whisper.cpp" / "ggml-large-v3-turbo.bin")
        self.threads = threads

    def probe_availability(self) -> Dict[str, Any]:
        exec_ok = self.whisper_executable.exists() and os.access(self.whisper_executable, os.X_OK)
        model_ok = self.default_model_path.exists() and self.default_model_path.is_file()
        return {
            "backend": "whisper.cpp",
            "executable_path": str(self.whisper_executable),
            "executable_available": exec_ok,
            "default_model_path": str(self.default_model_path),
            "model_available": model_ok,
            "threads": self.threads,
        }

    def _resolve_model(self, model_path: Optional[Path]) -> Path:
        resolved = model_path or self.default_model_path
        if not resolved.exists() or not resolved.is_file():
            raise TranscriptionBlockedError(f"Whisper model not found at: {resolved}")
        return resolved

    def _whisper_environment(self) -> Dict[str, str]:
        return {
            "HOME": os.environ.get("HOME", ""),
            "LC_ALL": "C",
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"),
        }

    def _get_whisper_version(self) -> str:
        try:
            res = subprocess.run(
                [str(self.whisper_executable), "--version"],
                capture_output=True,
                timeout=10,
                check=False,
                stdin=subprocess.DEVNULL,
                env=self._whisper_environment(),
            )
            out = (res.stdout + res.stderr).decode(errors="replace").strip()
            return out.splitlines()[0] if out else "whisper.cpp"
        except Exception:
            return "whisper.cpp"

    def transcribe(
        self,
        audio_path: Path,
        tracking_id: str,
        source_path: Optional[Path] = None,
        source_type: str = "audio",
        derived_audio_details: Optional[DerivedAudioDetails] = None,
        requested_device: str = "auto",
        model_path: Optional[Path] = None,
        force: bool = False,
        root_dir: Optional[Path] = None,
        dry_run: bool = False,
    ) -> TranscriptArtifact:
        audio_path = audio_path.resolve()
        if not audio_path.is_file():
            raise TranscriptionBlockedError(f"Audio file does not exist: {audio_path}")

        resolved_model = self._resolve_model(model_path)
        model_sha256 = compute_file_sha256(resolved_model)

        orig_source_path = source_path.resolve() if source_path else audio_path
        input_sha256 = compute_file_sha256(orig_source_path)
        audio_sha256 = compute_file_sha256(audio_path)
        duration_seconds = probe_audio_duration(audio_path)

        # Artifact directory
        base_dir = root_dir or Path.cwd()
        transcripts_dir = base_dir / ".renamer" / "transcripts"
        artifact_path = transcripts_dir / f"{tracking_id}.json"

        # Check existing sidecar cache
        if artifact_path.exists() and not force:
            try:
                cached_data = json.loads(artifact_path.read_text(encoding="utf-8"))
                raw_meta = cached_data.get("raw_metadata", {})

                input_match = cached_data.get("input_sha256") == input_sha256
                model_path_match = raw_meta.get("model_path") == str(resolved_model)
                model_hash_match = raw_meta.get("model_sha256") == model_sha256
                threads_match = raw_meta.get("threads") == self.threads
                contract_match = cached_data.get("contract_version") == "1.0"
                classification_match = cached_data.get("classification_version") == "1.0"
                source_type_match = cached_data.get("source_type", "audio") == source_type

                derived_match = True
                if source_type == "video":
                    cached_derived = cached_data.get("derived_mp3_details")
                    if not cached_derived:
                        derived_match = False
                    else:
                        if cached_derived.get("derived_sha256") != audio_sha256:
                            derived_match = False

                if (
                    input_match
                    and model_path_match
                    and model_hash_match
                    and threads_match
                    and contract_match
                    and classification_match
                    and source_type_match
                    and derived_match
                ):
                    return TranscriptArtifact.model_validate(cached_data)
            except Exception:
                pass  # Corrupted or outdated cache, regenerate

        if not self.whisper_executable.exists() or not os.access(self.whisper_executable, os.X_OK):
            raise TranscriptionBlockedError(f"whisper-cli executable not available at: {self.whisper_executable}")

        device_mode = requested_device.casefold()
        if device_mode not in {"auto", "metal", "cpu"}:
            raise TranscriptionBlockedError(f"Invalid requested device: {requested_device}")

        selected_backend = "metal" if device_mode in {"auto", "metal"} else "cpu"
        fallback_reason: Optional[str] = None

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_stem = Path(tmp_dir) / "output"
            expected_json = Path(f"{tmp_stem}.json")

            start_t = time.time()
            success = False

            # Attempt 1: Metal if auto or metal
            if selected_backend == "metal":
                cmd = [
                    str(self.whisper_executable),
                    "--model", str(resolved_model),
                    "--file", str(audio_path),
                    "--language", "auto",
                    "--output-json",
                    "--output-file", str(tmp_stem),
                    "--no-prints",
                    "--threads", str(self.threads),
                    "--temperature", "0",
                    "--max-context", "0",
                    "--flash-attn",
                ]
                try:
                    res = subprocess.run(
                        cmd,
                        capture_output=True,
                        timeout=1800,
                        check=False,
                        stdin=subprocess.DEVNULL,
                        env=self._whisper_environment(),
                    )
                    if res.returncode == 0 and expected_json.exists():
                        success = True
                    else:
                        err = res.stderr.decode(errors="replace").strip()
                        if device_mode == "metal":
                            raise TranscriptionBlockedError(f"Transcription failed on explicit Metal device: {err}")
                        fallback_reason = f"Metal failure (exit {res.returncode}): {err[:200]}"
                except Exception as e:
                    if device_mode == "metal":
                        raise TranscriptionBlockedError(f"Transcription error on explicit Metal device: {e}")
                    fallback_reason = f"Metal exception: {str(e)[:200]}"

            # Attempt 2: CPU fallback if auto and Metal failed, or if cpu was requested
            if not success and (device_mode == "cpu" or fallback_reason is not None):
                selected_backend = "cpu"
                cmd = [
                    str(self.whisper_executable),
                    "--model", str(resolved_model),
                    "--file", str(audio_path),
                    "--language", "auto",
                    "--output-json",
                    "--output-file", str(tmp_stem),
                    "--no-prints",
                    "--threads", str(self.threads),
                    "--temperature", "0",
                    "--max-context", "0",
                    "--flash-attn",
                    "--no-gpu",
                ]
                try:
                    res = subprocess.run(
                        cmd,
                        capture_output=True,
                        timeout=1800,
                        check=False,
                        stdin=subprocess.DEVNULL,
                        env=self._whisper_environment(),
                    )
                    if res.returncode == 0 and expected_json.exists():
                        success = True
                    else:
                        err = res.stderr.decode(errors="replace").strip()
                        raise TranscriptionBlockedError(f"Transcription failed on CPU: {err}")
                except Exception as e:
                    if isinstance(e, TranscriptionBlockedError):
                        raise
                    raise TranscriptionBlockedError(f"Transcription CPU execution error: {e}")

            if not success or not expected_json.exists():
                raise TranscriptionBlockedError("Whisper did not produce expected JSON output")

            raw_bytes = expected_json.read_bytes()
            if len(raw_bytes) > MAX_TRANSCRIPT_BYTES:
                raise TranscriptionBlockedError("Whisper JSON output exceeded maximum allowed size")

            try:
                raw_payload = json.loads(raw_bytes.decode("utf-8"))
            except Exception as exc:
                raise TranscriptionBlockedError("Malformed Whisper JSON output") from exc

            # Post-transcription check: ensure files were not altered during run
            post_sha256 = compute_file_sha256(audio_path)
            if post_sha256 != audio_sha256:
                raise TranscriptionBlockedError("Source file changed during transcription")
            if source_path and source_path.exists():
                post_src_sha = compute_file_sha256(source_path)
                if post_src_sha != input_sha256:
                    raise TranscriptionBlockedError("Source video file changed during transcription")

            elapsed = time.time() - start_t
            rtf = (elapsed / duration_seconds) if duration_seconds > 0 else None

            # Parse and normalize segments
            detected_lang = "en"
            if isinstance(raw_payload.get("result"), dict):
                detected_lang = raw_payload["result"].get("language", "en")

            raw_transcription = raw_payload.get("transcription") or []
            speech_segments: List[TranscriptSegment] = []

            for item in raw_transcription:
                if not isinstance(item, dict):
                    continue
                start_sec = None
                end_sec = None
                ts = item.get("timestamps")
                if isinstance(ts, dict):
                    start_sec = parse_timestamp_seconds(ts.get("from"))
                    end_sec = parse_timestamp_seconds(ts.get("to"))
                if start_sec is None or end_sec is None:
                    offsets = item.get("offsets")
                    if isinstance(offsets, dict):
                        f_ms = parse_timestamp_seconds(offsets.get("from"))
                        t_ms = parse_timestamp_seconds(offsets.get("to"))
                        if f_ms is not None and t_ms is not None:
                            start_sec = f_ms / 1000.0
                            end_sec = t_ms / 1000.0
                if start_sec is not None and end_sec is not None and end_sec >= start_sec:
                    text_str = str(item.get("text", "")).strip()
                    speech_segments.append(
                        TranscriptSegment(
                            start_seconds=start_sec,
                            end_seconds=end_sec,
                            text=text_str,
                            is_silence=False,
                            avg_logprob=item.get("avg_logprob"),
                            no_speech_prob=item.get("no_speech_prob"),
                        )
                    )

            speech_segments.sort(key=lambda s: s.start_seconds)

            # Continuous timeline coverage 00:00 to duration_seconds with explicit silence gaps
            effective_duration = max(
                duration_seconds,
                speech_segments[-1].end_seconds if speech_segments else 0.0,
            )
            normalized_segments: List[TranscriptSegment] = []
            curr_cursor = 0.0

            for seg in speech_segments:
                if seg.start_seconds > curr_cursor + 0.5:
                    normalized_segments.append(
                        TranscriptSegment(
                            start_seconds=curr_cursor,
                            end_seconds=seg.start_seconds,
                            text="",
                            is_silence=True,
                        )
                    )
                normalized_segments.append(seg)
                curr_cursor = max(curr_cursor, seg.end_seconds)

            if curr_cursor < effective_duration - 0.5:
                normalized_segments.append(
                    TranscriptSegment(
                        start_seconds=curr_cursor,
                        end_seconds=effective_duration,
                        text="",
                        is_silence=True,
                    )
                )

            model_sha256 = compute_file_sha256(resolved_model)
            now_iso = datetime.now(timezone.utc).isoformat()

            artifact = TranscriptArtifact(
                contract_version="1.0",
                tracking_id=tracking_id,
                input_path=str(orig_source_path),
                input_sha256=input_sha256,
                source_type=source_type,
                derived_mp3_details=derived_audio_details,
                duration_seconds=effective_duration,
                detected_language=detected_lang,
                segments=normalized_segments,
                raw_metadata={
                    "whisper_version": self._get_whisper_version(),
                    "model_name": resolved_model.name,
                    "model_path": str(resolved_model),
                    "model_sha256": model_sha256,
                    "selected_backend": selected_backend,
                    "requested_device": requested_device,
                    "threads": self.threads,
                    "fallback_reason": fallback_reason,
                    "elapsed_seconds": elapsed,
                    "rtf": rtf,
                },
                created_at=now_iso,
                classification_version="1.0",
            )

            # Calculate deterministic transcript SHA-256
            serialized = json.dumps(artifact.model_dump(), sort_keys=True).encode("utf-8")
            artifact.transcript_sha256 = hashlib.sha256(serialized).hexdigest()

            if not dry_run:
                transcripts_dir.mkdir(parents=True, exist_ok=True)
                # Atomic write with 0o600 permissions
                tmp_art = artifact_path.parent / f".{artifact_path.name}.{os.getpid()}.tmp"
                descriptor = os.open(tmp_art, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as h:
                    h.write(json.dumps(artifact.model_dump(), indent=2))
                    h.flush()
                    os.fsync(h.fileno())
                os.replace(tmp_art, artifact_path)

            return artifact


class FakeTranscriptionAdapter(BaseTranscriptionAdapter):
    """Test double for transcription supporting pre-configured segments, simulated fallbacks, and errors."""

    def __init__(
        self,
        canned_segments: Optional[List[TranscriptSegment]] = None,
        detected_language: str = "en",
        duration_seconds: Optional[float] = None,
        should_fail: bool = False,
        fail_message: str = "Simulated transcription failure",
        simulate_metal_fallback: bool = False,
        simulate_source_change: bool = False,
    ):
        self.canned_segments = canned_segments or []
        self.detected_language = detected_language
        self.duration_seconds = duration_seconds
        self.should_fail = should_fail
        self.fail_message = fail_message
        self.simulate_metal_fallback = simulate_metal_fallback
        self.simulate_source_change = simulate_source_change
        self.calls = []

    def probe_availability(self) -> Dict[str, Any]:
        return {
            "backend": "fake-whisper",
            "executable_available": True,
            "model_available": True,
            "threads": 4,
        }

    def transcribe(
        self,
        audio_path: Path,
        tracking_id: str,
        source_path: Optional[Path] = None,
        source_type: str = "audio",
        derived_audio_details: Optional[DerivedAudioDetails] = None,
        requested_device: str = "auto",
        model_path: Optional[Path] = None,
        force: bool = False,
        root_dir: Optional[Path] = None,
        dry_run: bool = False,
    ) -> TranscriptArtifact:
        audio_path = audio_path.resolve()

        if self.should_fail:
            raise TranscriptionBlockedError(self.fail_message)

        if self.simulate_source_change:
            raise TranscriptionBlockedError("Source file changed during transcription")

        orig_source_path = source_path.resolve() if source_path else audio_path
        input_sha256 = compute_file_sha256(orig_source_path) if orig_source_path.exists() else "fake_sha256"
        audio_sha256 = compute_file_sha256(audio_path) if audio_path.exists() else "fake_audio_sha256"

        device_mode = requested_device.casefold()
        if device_mode == "metal" and self.simulate_metal_fallback:
            raise TranscriptionBlockedError("Transcription failed on explicit Metal device: simulated metal error")

        selected_backend = "metal"
        fallback_reason = None
        if self.simulate_metal_fallback:
            selected_backend = "cpu"
            fallback_reason = "Simulated Metal failure: Metal shader init error"
        elif device_mode == "cpu":
            selected_backend = "cpu"

        # Continuous coverage with silence
        segments = list(self.canned_segments)
        if self.duration_seconds is not None:
            effective_duration = self.duration_seconds
        elif segments:
            effective_duration = segments[-1].end_seconds
        else:
            effective_duration = 600.0
        normalized: List[TranscriptSegment] = []
        cursor = 0.0
        for seg in segments:
            if seg.start_seconds > cursor + 0.5:
                normalized.append(TranscriptSegment(start_seconds=cursor, end_seconds=seg.start_seconds, text="", is_silence=True))
            normalized.append(seg)
            cursor = max(cursor, seg.end_seconds)
        if cursor < effective_duration - 0.5:
            normalized.append(TranscriptSegment(start_seconds=cursor, end_seconds=effective_duration, text="", is_silence=True))

        base_dir = root_dir or Path.cwd()
        transcripts_dir = base_dir / ".renamer" / "transcripts"
        artifact_path = transcripts_dir / f"{tracking_id}.json"

        # Check cached sidecar
        if artifact_path.exists() and not force:
            try:
                cached_data = json.loads(artifact_path.read_text(encoding="utf-8"))
                raw_meta = cached_data.get("raw_metadata", {})
                input_match = cached_data.get("input_sha256") == input_sha256
                model_hash_match = raw_meta.get("model_sha256") == "fake_model_sha256"
                threads_match = raw_meta.get("threads") == 4
                contract_match = cached_data.get("contract_version") == "1.0"
                classification_match = cached_data.get("classification_version") == "1.0"
                source_type_match = cached_data.get("source_type", "audio") == source_type

                derived_match = True
                if source_type == "video":
                    cached_derived = cached_data.get("derived_mp3_details")
                    if not cached_derived:
                        derived_match = False
                    else:
                        if cached_derived.get("derived_sha256") != audio_sha256:
                            derived_match = False

                if (
                    input_match
                    and model_hash_match
                    and threads_match
                    and contract_match
                    and classification_match
                    and source_type_match
                    and derived_match
                ):
                    return TranscriptArtifact.model_validate(cached_data)
            except Exception:
                pass

        self.calls.append((audio_path, tracking_id, requested_device))

        now_iso = datetime.now(timezone.utc).isoformat()
        artifact = TranscriptArtifact(
            contract_version="1.0",
            tracking_id=tracking_id,
            input_path=str(orig_source_path),
            input_sha256=input_sha256,
            source_type=source_type,
            derived_mp3_details=derived_audio_details,
            duration_seconds=effective_duration,
            detected_language=self.detected_language,
            segments=normalized,
            raw_metadata={
                "whisper_version": "whisper.cpp-fake-1.0",
                "model_name": "fake-model.bin",
                "model_path": str(model_path or "/fake/model.bin"),
                "model_sha256": "fake_model_sha256",
                "selected_backend": selected_backend,
                "requested_device": requested_device,
                "threads": 4,
                "fallback_reason": fallback_reason,
            },
            created_at=now_iso,
            classification_version="1.0",
        )
        serialized = json.dumps(artifact.model_dump(), sort_keys=True).encode("utf-8")
        artifact.transcript_sha256 = hashlib.sha256(serialized).hexdigest()

        if not dry_run:
            transcripts_dir.mkdir(parents=True, exist_ok=True)
            # Atomic write with 0o600 permissions
            tmp_art = artifact_path.parent / f".{artifact_path.name}.{os.getpid()}.tmp"
            descriptor = os.open(tmp_art, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as h:
                h.write(json.dumps(artifact.model_dump(), indent=2))
            os.replace(tmp_art, artifact_path)

        return artifact
