"""Audio cutting, leading silence detection, format preservation, and verification for Tool 6."""
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AudioCutterError(Exception):
    """Raised when audio cutting or encoding fails."""
    pass


class AudioVerificationError(AudioCutterError):
    """Raised when an output audio file fails verification."""
    pass


class AudioCutSpec(BaseModel):
    """Specification for a two-part audio split."""
    source_path: Path
    cut_point_seconds: float
    class_start_seconds: Optional[float] = None
    source_duration_seconds: float
    singing_output_path: Path
    class_output_path: Path
    scratch_dir: Path
    trim_silence: bool = True


class AudioCutResult(BaseModel):
    """Result of staging and verifying both cut audio parts."""
    singing_staged_path: Path
    singing_duration: float
    singing_sha256: str
    singing_leading_silence: float
    class_staged_path: Path
    class_duration: float
    class_sha256: str
    class_leading_silence: float
    commands_executed: List[str] = Field(default_factory=list)
    codec_summary: str = ""


def compute_sha256(path: Path) -> str:
    """Deterministic file SHA-256 calculation."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class AudioCutter:
    """Executes FFmpeg cuts, leading silence detection, format-specific encoding, and validation."""

    def __init__(
        self,
        ffmpeg_bin: Optional[str] = None,
        ffprobe_bin: Optional[str] = None,
    ):
        self.ffmpeg_bin = ffmpeg_bin or shutil.which("ffmpeg") or "ffmpeg"
        self.ffprobe_bin = ffprobe_bin or shutil.which("ffprobe") or "ffprobe"

    def inspect_audio(self, path: Path) -> Dict[str, Any]:
        """Inspect audio file via ffprobe and return duration, size, format, and streams."""
        path = Path(path).resolve()
        if not path.is_file():
            raise AudioCutterError(f"Audio file does not exist: {path}")

        cmd = [
            self.ffprobe_bin,
            "-v", "error",
            "-show_entries", "format=duration,size,bit_rate:stream=codec_name,codec_type,sample_rate,channels",
            "-of", "json",
            str(path),
        ]
        try:
            res = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=True,
            )
            data = json.loads(res.stdout)
            fmt = data.get("format", {})
            duration = float(fmt.get("duration", 0.0))
            size = int(fmt.get("size", 0))
            streams = data.get("streams", [])
            audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
            return {
                "duration": duration,
                "size": size,
                "streams": streams,
                "audio_streams": audio_streams,
                "has_audio": len(audio_streams) > 0,
            }
        except Exception as e:
            raise AudioCutterError(f"Failed to inspect audio file {path}: {e}") from e

    def detect_leading_silence(
        self,
        path: Path,
        start_seconds: float = 0.0,
        end_seconds: Optional[float] = None,
        noise_threshold: str = "-50dB",
        min_silence_duration: float = 0.5,
    ) -> float:
        """Detect actual leading silence in seconds using FFmpeg silencedetect.

        Conservative rule: Only trims silence that occurs right at the start (<=0.05s).
        Preserves speech, prayers, soft vocals, mridanga/harmonium, and instrumental music.
        """
        path = Path(path).resolve()
        cmd = [
            self.ffmpeg_bin,
            "-nostdin",
            "-y",
        ]
        if start_seconds > 0.0:
            cmd.extend(["-ss", f"{start_seconds:.3f}"])
        if end_seconds is not None:
            cmd.extend(["-to", f"{end_seconds:.3f}"])

        cmd.extend([
            "-i", str(path),
            "-af", f"silencedetect=noise={noise_threshold}:d={min_silence_duration}",
            "-f", "null",
            "-",
        ])

        try:
            res = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )
            stderr = res.stderr or ""
            # Search for silence_start: 0 (or close to 0.0)
            start_match = re.search(r"silence_start:\s*([0-9\.]+)", stderr)
            if not start_match:
                return 0.0
            start_sec = float(start_match.group(1))
            if start_sec > 0.08:
                # Meaningful sound (music, speech, ambient prayer) precedes any silence
                return 0.0
            end_match = re.search(r"silence_end:\s*([0-9\.]+)", stderr)
            if not end_match:
                return 0.0
            silence_len = float(end_match.group(1))
            # Don't trim more than 60s or more than the full segment
            max_trim = (end_seconds - start_seconds) if end_seconds is not None else 60.0
            if silence_len >= max_trim - 1.0:
                logger.warning("Entire segment appears silent (%s s); retaining without trim", silence_len)
                return 0.0
            return round(silence_len, 3)
        except Exception as e:
            logger.warning("silencedetect failed for %s: %s; assuming 0 leading silence", path, e)
            return 0.0

    def _get_codec_args(self, ext: str) -> Tuple[List[str], str]:
        """Return format-appropriate high-quality FFmpeg audio codec arguments."""
        clean_ext = ext.lower().strip()
        if clean_ext == ".wma":
            return ["-c:a", "wmav2", "-b:a", "192k"], "wmav2 (192k)"
        elif clean_ext == ".mp3":
            return ["-c:a", "libmp3lame", "-q:a", "2"], "libmp3lame (V2 high quality)"
        elif clean_ext == ".wav":
            return ["-c:a", "pcm_s16le"], "pcm_s16le"
        elif clean_ext == ".flac":
            return ["-c:a", "flac"], "flac"
        elif clean_ext in (".m4a", ".aac"):
            return ["-c:a", "aac", "-b:a", "192k"], "aac (192k)"
        else:
            return ["-c:a", "libmp3lame", "-q:a", "2"], "libmp3lame fallback"

    def verify_audio_file(self, path: Path) -> Tuple[bool, float, str, Optional[str]]:
        """Verify decodability, duration, and compute SHA-256."""
        path = Path(path).resolve()
        if not path.is_file():
            return False, 0.0, "", f"File does not exist: {path}"
        if path.stat().st_size == 0:
            return False, 0.0, "", f"File is empty: {path}"

        try:
            info = self.inspect_audio(path)
            if not info.get("has_audio"):
                return False, 0.0, "", "No audio streams found"
            duration = info.get("duration", 0.0)
            if duration <= 0.1:
                return False, duration, "", f"Plausibility check failed: duration {duration}s too short"
            file_hash = compute_sha256(path)
            return True, duration, file_hash, None
        except Exception as e:
            return False, 0.0, "", str(e)

    def cut_audio(self, spec: AudioCutSpec) -> AudioCutResult:
        """Execute exact split into two outputs in bounded scratch directory."""
        source_path = spec.source_path.resolve()
        if not source_path.is_file():
            raise AudioCutterError(f"Source file does not exist: {source_path}")

        # Validate cut point strictly within source duration
        if spec.cut_point_seconds <= 1.0 or spec.cut_point_seconds >= spec.source_duration_seconds - 1.0:
            raise AudioCutterError(
                f"Cut point {spec.cut_point_seconds:.2f}s is not strictly inside source duration {spec.source_duration_seconds:.2f}s"
            )

        spec.scratch_dir.mkdir(parents=True, exist_ok=True)
        commands_run: List[str] = []

        # Determine output format matching target paths
        ext_singing = spec.singing_output_path.suffix.lower()
        ext_class = spec.class_output_path.suffix.lower()

        codec_args_singing, codec_summary_s = self._get_codec_args(ext_singing)
        codec_args_class, codec_summary_c = self._get_codec_args(ext_class)
        codec_summary = f"singing: {codec_summary_s}; class: {codec_summary_c}"

        # 1. Leading silence detection
        singing_trim = 0.0
        class_trim = 0.0
        actual_class_start = spec.cut_point_seconds
        if spec.trim_silence:
            singing_trim = self.detect_leading_silence(
                source_path,
                start_seconds=0.0,
                end_seconds=min(spec.cut_point_seconds, 60.0),
            )
            class_trim = self.detect_leading_silence(
                source_path,
                start_seconds=actual_class_start,
                end_seconds=min(spec.source_duration_seconds, actual_class_start + 60.0),
            )

        # 2. Stage singing output
        singing_staged = spec.scratch_dir / f"staged_singing_{os.getpid()}{ext_singing}"
        singing_start = singing_trim
        singing_end = spec.cut_point_seconds

        cmd_singing = [
            self.ffmpeg_bin,
            "-nostdin",
            "-y",
            "-ss", f"{singing_start:.3f}",
            "-to", f"{singing_end:.3f}",
            "-i", str(source_path),
        ] + codec_args_singing + [str(singing_staged)]

        commands_run.append(" ".join(cmd_singing))
        logger.info("Executing singing cut: %s", " ".join(cmd_singing))

        try:
            subprocess.run(
                cmd_singing,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            singing_staged.unlink(missing_ok=True)
            raise AudioCutterError(f"FFmpeg failed while cutting singing portion: {e.stderr}") from e

        # Verify singing staged output
        valid_s, dur_s, hash_s, err_s = self.verify_audio_file(singing_staged)
        if not valid_s:
            singing_staged.unlink(missing_ok=True)
            raise AudioVerificationError(f"Staged singing output failed verification: {err_s}")

        expected_dur_s = singing_end - singing_start
        if abs(dur_s - expected_dur_s) > 2.0:
            singing_staged.unlink(missing_ok=True)
            raise AudioVerificationError(
                f"Singing output duration {dur_s:.2f}s differs significantly from expected {expected_dur_s:.2f}s"
            )

        # 3. Stage class output
        class_staged = spec.scratch_dir / f"staged_class_{os.getpid()}{ext_class}"
        class_start = actual_class_start + class_trim
        class_end = spec.source_duration_seconds

        cmd_class = [
            self.ffmpeg_bin,
            "-nostdin",
            "-y",
            "-ss", f"{class_start:.3f}",
            "-to", f"{class_end:.3f}",
            "-i", str(source_path),
        ] + codec_args_class + [str(class_staged)]

        commands_run.append(" ".join(cmd_class))
        logger.info("Executing class cut: %s", " ".join(cmd_class))

        try:
            subprocess.run(
                cmd_class,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            singing_staged.unlink(missing_ok=True)
            class_staged.unlink(missing_ok=True)
            raise AudioCutterError(f"FFmpeg failed while cutting class portion: {e.stderr}") from e

        # Verify class staged output
        valid_c, dur_c, hash_c, err_c = self.verify_audio_file(class_staged)
        if not valid_c:
            singing_staged.unlink(missing_ok=True)
            class_staged.unlink(missing_ok=True)
            raise AudioVerificationError(f"Staged class output failed verification: {err_c}")

        expected_dur_c = class_end - class_start
        if abs(dur_c - expected_dur_c) > 2.0:
            singing_staged.unlink(missing_ok=True)
            class_staged.unlink(missing_ok=True)
            raise AudioVerificationError(
                f"Class output duration {dur_c:.2f}s differs significantly from expected {expected_dur_c:.2f}s"
            )

        return AudioCutResult(
            singing_staged_path=singing_staged,
            singing_duration=dur_s,
            singing_sha256=hash_s,
            singing_leading_silence=singing_trim,
            class_staged_path=class_staged,
            class_duration=dur_c,
            class_sha256=hash_c,
            class_leading_silence=class_trim,
            commands_executed=commands_run,
            codec_summary=codec_summary,
        )
