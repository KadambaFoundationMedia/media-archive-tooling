"""Audio extraction from video files for Tool 5 content discovery."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Optional, Set
import uuid

from .models import DerivedAudioDetails


SUPPORTED_VIDEO_EXTENSIONS: Set[str] = {
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".m4v",
    ".wmv",
    ".webm",
}


class AudioExtractionError(Exception):
    """Raised when video-to-audio extraction fails."""
    pass


class AudioExtractionCollisionError(AudioExtractionError):
    """Raised when an adjacent MP3 exists that does not match the source video."""
    pass


def is_video_file(path: Path) -> bool:
    """Check whether a media path is a video container requiring audio extraction."""
    return path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS


def compute_file_sha256(path: Path) -> str:
    """Calculate deterministic SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_no_clobber_finalize(tmp_path: Path, target_path: Path) -> None:
    """Atomically finalize tmp_path to target_path without clobbering an existing target.

    On collision, cleans up only tmp_path, leaving target_path completely untouched.
    """
    try:
        os.link(tmp_path, target_path)
        tmp_path.unlink(missing_ok=True)
    except FileExistsError:
        tmp_path.unlink(missing_ok=True)
        raise AudioExtractionCollisionError(
            f"Adjacent audio file appeared concurrently during extraction: {target_path}"
        )
    except OSError as e:
        if target_path.exists():
            tmp_path.unlink(missing_ok=True)
            raise AudioExtractionCollisionError(
                f"Adjacent audio file appeared concurrently during extraction: {target_path}"
            )
        try:
            os.replace(tmp_path, target_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise


class AudioExtractionAdapter:
    """Adapter for extracting high-quality MP3 audio from video files using ffmpeg."""

    def __init__(self, ffmpeg_executable: Optional[str] = None):
        self.ffmpeg_executable = ffmpeg_executable or shutil.which("ffmpeg")

    def extract_audio(
        self,
        video_path: Path,
        tracking_id: str,
        registry: Optional[Any] = None,
        duration_seconds: float = 0.0,
    ) -> DerivedAudioDetails:
        """Extract an adjacent MP3 from a video file with collision protection and registry tracking."""
        video_path = video_path.resolve()
        if not video_path.is_file():
            raise AudioExtractionError(f"Video source file does not exist: {video_path}")

        video_sha256 = compute_file_sha256(video_path)
        target_mp3 = video_path.with_suffix(".mp3")

        # Collision protection
        if target_mp3.exists():
            existing_record = None
            if registry is not None and hasattr(registry, "get_video_audio_derivative"):
                existing_record = registry.get_video_audio_derivative(str(target_mp3))

            if existing_record is not None:
                source_path_match = existing_record.get("source_video_path") == str(video_path)
                source_sha_match = existing_record.get("source_video_sha256") == video_sha256
                current_derived_sha = compute_file_sha256(target_mp3)
                derived_sha_match = existing_record.get("derived_sha256") == current_derived_sha

                if source_path_match and source_sha_match and derived_sha_match:
                    # Proven to be generated from the exact same video file and untouched: reuse!
                    return DerivedAudioDetails(
                        source_video_path=str(video_path),
                        derived_audio_path=str(target_mp3),
                        codec_command_summary="ffmpeg libmp3lame -q:a 0 (reused)",
                        duration_seconds=duration_seconds,
                        derived_sha256=current_derived_sha,
                    )
                else:
                    raise AudioExtractionCollisionError(
                        f"Adjacent audio file already exists with a different hash and does not match recorded derivative: {target_mp3}"
                    )
            else:
                raise AudioExtractionCollisionError(
                    f"Adjacent audio file already exists with a different hash and is not recorded as a derivative of {video_path}: {target_mp3}"
                )

        if not self.ffmpeg_executable:
            raise AudioExtractionError("ffmpeg executable not found in PATH or configured location")

        tmp_target = target_mp3.parent / f".tmp_extract_{tracking_id}_{os.getpid()}_{uuid.uuid4().hex[:8]}.mp3"

        cmd = [
            str(self.ffmpeg_executable),
            "-nostdin",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "0",
            str(tmp_target),
        ]

        try:
            completed = subprocess.run(
                cmd,
                check=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=600,
            )
            if completed.returncode != 0:
                err = completed.stderr.decode("utf-8", errors="replace").strip()
                tmp_target.unlink(missing_ok=True)
                raise AudioExtractionError(f"ffmpeg extraction failed (exit {completed.returncode}): {err}")
        except subprocess.TimeoutExpired:
            tmp_target.unlink(missing_ok=True)
            raise AudioExtractionError("ffmpeg extraction timed out after 600s")
        except Exception as e:
            tmp_target.unlink(missing_ok=True)
            raise AudioExtractionError(f"ffmpeg execution error: {e}")

        _atomic_no_clobber_finalize(tmp_target, target_mp3)
        derived_sha256 = compute_file_sha256(target_mp3)

        if registry is not None and hasattr(registry, "record_video_audio_derivative"):
            registry.record_video_audio_derivative(
                derived_path=str(target_mp3),
                source_video_path=str(video_path),
                source_video_tracking_id=tracking_id,
                source_video_sha256=video_sha256,
                derived_sha256=derived_sha256,
            )

        return DerivedAudioDetails(
            source_video_path=str(video_path),
            derived_audio_path=str(target_mp3),
            codec_command_summary="ffmpeg -vn -codec:a libmp3lame -q:a 0",
            duration_seconds=duration_seconds,
            derived_sha256=derived_sha256,
        )


class FakeAudioExtractionAdapter(AudioExtractionAdapter):
    """Test double for video audio extraction that does not invoke external ffmpeg."""

    def __init__(self, should_fail: bool = False, fail_message: str = "Simulated extraction failure"):
        super().__init__(ffmpeg_executable="fake-ffmpeg")
        self.should_fail = should_fail
        self.fail_message = fail_message
        self.extracted_calls = []
        self.calls = self.extracted_calls

    def extract_audio(
        self,
        video_path: Path,
        tracking_id: str,
        registry: Optional[Any] = None,
        duration_seconds: float = 0.0,
    ) -> DerivedAudioDetails:
        if self.should_fail:
            raise AudioExtractionError(self.fail_message)

        video_path = video_path.resolve()
        video_sha256 = compute_file_sha256(video_path)
        target_mp3 = video_path.with_suffix(".mp3")

        if target_mp3.exists():
            existing_record = None
            if registry is not None and hasattr(registry, "get_video_audio_derivative"):
                existing_record = registry.get_video_audio_derivative(str(target_mp3))

            if existing_record is not None:
                source_path_match = existing_record.get("source_video_path") == str(video_path)
                source_sha_match = existing_record.get("source_video_sha256") == video_sha256
                current_derived_sha = compute_file_sha256(target_mp3)
                derived_sha_match = existing_record.get("derived_sha256") == current_derived_sha

                if source_path_match and source_sha_match and derived_sha_match:
                    return DerivedAudioDetails(
                        source_video_path=str(video_path),
                        derived_audio_path=str(target_mp3),
                        codec_command_summary="fake-ffmpeg libmp3lame (reused)",
                        duration_seconds=duration_seconds,
                        derived_sha256=current_derived_sha,
                    )
                else:
                    raise AudioExtractionCollisionError(
                        f"Adjacent audio file already exists with a different hash and does not match recorded derivative: {target_mp3}"
                    )
            else:
                raise AudioExtractionCollisionError(
                    f"Adjacent audio file already exists with a different hash and is not recorded as a derivative of {video_path}: {target_mp3}"
                )

        tmp_target = target_mp3.parent / f".tmp_extract_{tracking_id}_{os.getpid()}_{uuid.uuid4().hex[:8]}.mp3"
        tmp_target.write_bytes(b"FAKE_EXTRACTED_AUDIO_DATA_" + tracking_id.encode())

        _atomic_no_clobber_finalize(tmp_target, target_mp3)
        self.extracted_calls.append((video_path, tracking_id))
        derived_sha256 = compute_file_sha256(target_mp3)

        if registry is not None and hasattr(registry, "record_video_audio_derivative"):
            registry.record_video_audio_derivative(
                derived_path=str(target_mp3),
                source_video_path=str(video_path),
                source_video_tracking_id=tracking_id,
                source_video_sha256=video_sha256,
                derived_sha256=derived_sha256,
            )

        return DerivedAudioDetails(
            source_video_path=str(video_path),
            derived_audio_path=str(target_mp3),
            codec_command_summary="fake-ffmpeg libmp3lame -q:a 0",
            duration_seconds=duration_seconds,
            derived_sha256=derived_sha256,
        )
