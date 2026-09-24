"""Waveform peak generation and preview transcoding for Tool 6 review portal."""
import array
import json
import logging
from pathlib import Path
import shutil
import subprocess
from typing import Optional
from .models import WaveformSummary
from .audio_cutter import compute_sha256

logger = logging.getLogger(__name__)


class WaveformGenerator:
    """Generates bounded waveform peak summaries and browser-playable audio previews."""

    def __init__(
        self,
        ffmpeg_bin: Optional[str] = None,
        ffprobe_bin: Optional[str] = None,
    ):
        self.ffmpeg_bin = ffmpeg_bin or shutil.which("ffmpeg") or "ffmpeg"
        self.ffprobe_bin = ffprobe_bin or shutil.which("ffprobe") or "ffprobe"

    def generate_waveform_summary(
        self,
        source_path: Path,
        tracking_id: str,
        source_sha256: str,
        cache_dir: Optional[Path] = None,
        num_samples: int = 500,
        cut_point_seconds: Optional[float] = None,
    ) -> WaveformSummary:
        """Generate normalized float peaks [0.0, 1.0] across the audio duration.

        Cached in cache_dir/<tracking_id>.json bound to source_sha256.
        """
        source_path = Path(source_path).resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"Source audio file not found: {source_path}")

        # Check cache
        if cache_dir is not None:
            cache_file = cache_dir / f"{tracking_id}.json"
            if cache_file.is_file():
                try:
                    data = json.loads(cache_file.read_text())
                    if data.get("source_sha256") == source_sha256:
                        summary = WaveformSummary.model_validate(data)
                        if cut_point_seconds is not None:
                            summary.cut_point_seconds = cut_point_seconds
                        return summary
                except Exception as e:
                    logger.warning("Corrupt waveform cache for %s: %s; regenerating", tracking_id, e)

        # Inspect duration via ffprobe
        cmd_probe = [
            self.ffprobe_bin,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(source_path),
        ]
        probe_res = subprocess.run(
            cmd_probe,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=True,
        )
        duration = float(json.loads(probe_res.stdout)["format"]["duration"])

        # Decode downsampled mono 16-bit PCM at 4000 Hz
        cmd_ffmpeg = [
            self.ffmpeg_bin,
            "-nostdin",
            "-y",
            "-i", str(source_path),
            "-ac", "1",
            "-ar", "4000",
            "-f", "s16le",
            "-",
        ]
        res = subprocess.run(
            cmd_ffmpeg,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
        )

        arr = array.array("h")
        arr.frombytes(res.stdout)

        if not arr:
            peaks = [0.0] * num_samples
        else:
            bin_size = len(arr) / float(num_samples)
            raw_peaks = []
            for i in range(num_samples):
                start_idx = int(i * bin_size)
                end_idx = int((i + 1) * bin_size)
                chunk = arr[start_idx:end_idx]
                p = max(abs(s) for s in chunk) if chunk else 0
                raw_peaks.append(float(p))

            max_p = max(raw_peaks) if raw_peaks and max(raw_peaks) > 0 else 32768.0
            peaks = [round(p / max_p, 3) for p in raw_peaks]

        summary = WaveformSummary(
            tracking_id=tracking_id,
            source_sha256=source_sha256,
            duration_seconds=round(duration, 3),
            sample_count=len(peaks),
            peaks=peaks,
            cut_point_seconds=cut_point_seconds,
        )

        # Cache summary
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = cache_dir / f"{tracking_id}.json"
            tmp_cache = cache_dir / f"{tracking_id}.json.tmp"
            tmp_cache.write_text(summary.model_dump_json(indent=2))
            tmp_cache.replace(cache_file)

        return summary

    def transcode_preview_mp3(
        self,
        source_path: Path,
        cache_dir: Path,
        tracking_id: str,
    ) -> Path:
        """Transcode unsupported formats (such as WMA) to a playable MP3 preview for browser playback.

        If already an MP3, returns the source path directly.
        """
        source_path = Path(source_path).resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"Source file not found: {source_path}")

        ext = source_path.suffix.lower()
        if ext == ".mp3":
            return source_path

        cache_dir.mkdir(parents=True, exist_ok=True)
        preview_file = cache_dir / f"{tracking_id}.mp3"

        if preview_file.is_file() and preview_file.stat().st_size > 0:
            return preview_file

        tmp_preview = cache_dir / f"{tracking_id}.mp3.tmp"
        cmd = [
            self.ffmpeg_bin,
            "-nostdin",
            "-y",
            "-i", str(source_path),
            "-c:a", "libmp3lame",
            "-b:a", "128k",
            "-f", "mp3",
            str(tmp_preview),
        ]
        try:
            res = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=True,
            )
            tmp_preview.replace(preview_file)
            return preview_file
        except subprocess.CalledProcessError as e:
            tmp_preview.unlink(missing_ok=True)
            err_msg = e.stderr.decode("utf-8", errors="replace") if isinstance(e.stderr, bytes) else str(e.stderr)
            raise RuntimeError(f"Failed to transcode preview MP3 for {source_path}: exit {e.returncode}, stderr: {err_msg}") from e
        except Exception as e:
            tmp_preview.unlink(missing_ok=True)
            raise RuntimeError(f"Failed to transcode preview MP3 for {source_path}: {e}") from e
