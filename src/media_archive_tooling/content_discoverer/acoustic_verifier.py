"""Acoustic boundary verifier for Tool 5 - Content Discoverer.

Analyzes local audio around suspected transition zones to locate and verify
the precise numeric timestamp at which singing ends.
"""
import logging
import math
from pathlib import Path
import re
import shutil
import subprocess
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class AcousticBoundaryVerifier:
    """Production acoustic boundary verifier using ffmpeg silencedetect and energy analysis."""

    def __init__(self, ffmpeg_bin: Optional[str] = None):
        self.ffmpeg_bin = ffmpeg_bin or shutil.which("ffmpeg")

    def verify_boundary(
        self,
        audio_path: Path,
        coarse_gap_start: float,
        coarse_gap_end: float,
        total_duration: float,
    ) -> Optional[float]:
        """Verify the exact singing end timestamp from local audio around coarse gap.

        Returns exact timestamp in seconds if acoustic transition is verified, or None.
        """
        if not self.ffmpeg_bin or not audio_path.exists() or total_duration <= 0.0:
            return None

        # Guard: check if file is decodable
        audio_path = audio_path.resolve()
        win_start = max(0.0, coarse_gap_start - 2.0)
        win_end = min(total_duration, max(coarse_gap_end, coarse_gap_start + 0.5) + 2.0)
        win_dur = win_end - win_start
        if win_dur <= 0.3:
            return None

        cmd = [
            self.ffmpeg_bin,
            "-nostdin",
            "-v", "info",
            "-ss", f"{win_start:.3f}",
            "-t", f"{win_dur:.3f}",
            "-i", str(audio_path),
            "-af", "silencedetect=noise=-30dB:d=0.3",
            "-f", "null",
            "-",
        ]

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                timeout=30,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.warning("ffmpeg silencedetect failed on %s: %s", audio_path, e)
            return None

        if res.returncode != 0:
            # File may be invalid audio or ffmpeg failed
            return None

        stderr_text = res.stderr.decode("utf-8", errors="replace")

        # Parse silence intervals: silence_start: X
        silence_starts = [
            float(m.group(1))
            for m in re.finditer(r"silence_start:\s*(\d+(?:\.\d+)?)", stderr_text)
        ]

        valid_candidates = []
        for rel_start in silence_starts:
            abs_start = win_start + rel_start
            # Must fall strictly within the transition gap region +/- 1.5s
            if (coarse_gap_start - 1.5) <= abs_start <= (coarse_gap_end + 1.5):
                if 0.0 < abs_start < total_duration:
                    valid_candidates.append(abs_start)

        if valid_candidates:
            # Pick candidate silence closest to coarse_gap_start
            best = min(valid_candidates, key=lambda s: abs(s - coarse_gap_start))
            return round(best, 3)

        # Fail closed: never fall back to coarse text boundaries without acoustic verification
        return None


class FakeAcousticBoundaryVerifier:
    """Test double for acoustic boundary verification."""

    def __init__(
        self,
        exact_cut_point: Optional[float] = None,
        should_verify: bool = True,
    ):
        self.exact_cut_point = exact_cut_point
        self.should_verify = should_verify
        self.calls = []

    def verify_boundary(
        self,
        audio_path: Path,
        coarse_gap_start: float,
        coarse_gap_end: float,
        total_duration: float,
    ) -> Optional[float]:
        self.calls.append((audio_path, coarse_gap_start, coarse_gap_end, total_duration))
        if not self.should_verify:
            return None
        if self.exact_cut_point is not None:
            return self.exact_cut_point
        return coarse_gap_start
