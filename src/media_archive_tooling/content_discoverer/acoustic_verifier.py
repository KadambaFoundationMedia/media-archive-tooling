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
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class AcousticBoundaryVerifier:
    """Production acoustic boundary verifier using ffmpeg silencedetect and energy analysis."""

    def __init__(self, ffmpeg_bin: Optional[str] = None):
        self.ffmpeg_bin = ffmpeg_bin or shutil.which("ffmpeg")

    def detect_candidate_transitions(
        self,
        audio_path: Path,
        total_duration: float,
        max_search_sec: float = 2700.0,
        min_sustained_sound: float = 90.0,
    ) -> List[Tuple[float, float]]:
        """Detect candidate transition gaps (singing_end, speech_start) across early timeline.

        Continuous music (kirtan/bhajan) lacks speech pauses (>0.3s). When kirtan concludes,
        the music ceases, producing the first silence event. A transition pause follows before
        spoken discourse commences with typical conversational pause cadence.

        Returns a list of (candidate_singing_end, candidate_speech_start) tuples.
        """
        if not self.ffmpeg_bin or not audio_path.exists() or total_duration <= 0.0:
            return []

        search_dur = min(total_duration, max_search_sec)
        if search_dur <= 180.0:
            return []

        cmd = [
            self.ffmpeg_bin,
            "-nostdin",
            "-v", "info",
            "-to", f"{search_dur:.3f}",
            "-i", str(audio_path),
            "-af", "silencedetect=noise=-30dB:d=0.3",
            "-f", "null",
            "-",
        ]

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                timeout=60,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.warning("ffmpeg silencedetect failed during transition detection on %s: %s", audio_path, e)
            return []

        if res.returncode != 0:
            return []

        stderr_text = res.stderr.decode("utf-8", errors="replace")

        # Parse silence intervals: silence_start: X, silence_end: Y | silence_duration: Z
        events: List[Tuple[float, float, float]] = []
        current_start = None
        for line in stderr_text.splitlines():
            m_s = re.search(r"silence_start:\s*(\d+(?:\.\d+)?)", line)
            if m_s:
                current_start = float(m_s.group(1))
            m_e = re.search(r"silence_end:\s*(\d+(?:\.\d+)?)\s*\|\s*silence_duration:\s*(\d+(?:\.\d+)?)", line)
            if m_e and current_start is not None:
                end_val = float(m_e.group(1))
                dur_val = float(m_e.group(2))
                events.append((current_start, end_val, dur_val))
                current_start = None

        if not events:
            return []

        candidates = []
        for i, ev in enumerate(events):
            prev_end = 0.0 if i == 0 else events[i - 1][1]
            gap_before = ev[0] - prev_end
            if gap_before >= min_sustained_sound:
                singing_end = ev[0]
                cluster = [e for e in events if 0.0 <= e[0] - singing_end <= 60.0]
                speech_start = cluster[-1][1] if len(cluster) > 1 else ev[1]
                candidates.append((round(singing_end, 3), round(speech_start, 3)))

        return candidates

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

    def find_speech_onset(
        self,
        audio_path: Path,
        search_start: float,
        search_end: float,
        noise_threshold: str = "-30dB",
        target_time: Optional[float] = None,
    ) -> Optional[float]:
        """Find the exact speech onset (end of silence immediately preceding speech) in a search window."""
        if not self.ffmpeg_bin or not audio_path.exists() or search_end <= search_start:
            return None

        audio_path = audio_path.resolve()
        dur = search_end - search_start
        if dur <= 0.2:
            return None

        cmd = [
            self.ffmpeg_bin,
            "-nostdin",
            "-v", "info",
            "-ss", f"{search_start:.3f}",
            "-t", f"{dur:.3f}",
            "-i", str(audio_path),
            "-af", f"silencedetect=noise={noise_threshold}:d=0.3",
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
            logger.warning("ffmpeg silencedetect failed during speech onset detection on %s: %s", audio_path, e)
            return None

        if res.returncode != 0:
            return None

        stderr_text = res.stderr.decode("utf-8", errors="replace")
        silence_ends = [
            float(m.group(1))
            for m in re.finditer(r"silence_end:\s*(\d+(?:\.\d+)?)", stderr_text)
        ]
        if not silence_ends:
            return None

        abs_ends = [search_start + rel for rel in silence_ends]
        valid = [e for e in abs_ends if e < search_end - 0.2]
        if not valid:
            valid = abs_ends

        ref_time = target_time if target_time is not None else search_start
        best = min(valid, key=lambda e: abs(e - ref_time))
        return round(best, 3)


class FakeAcousticBoundaryVerifier:
    """Test double for acoustic boundary verification."""

    def __init__(
        self,
        exact_cut_point: Optional[float] = None,
        should_verify: bool = True,
        candidate_transitions: Optional[List[Tuple[float, float]]] = None,
        speech_onset: Optional[float] = None,
    ):
        self.exact_cut_point = exact_cut_point
        self.should_verify = should_verify
        self.candidate_transitions = candidate_transitions or []
        self.speech_onset = speech_onset
        self.calls = []

    def detect_candidate_transitions(
        self,
        audio_path: Path,
        total_duration: float,
        max_search_sec: float = 2700.0,
        min_sustained_sound: float = 90.0,
    ) -> List[Tuple[float, float]]:
        return self.candidate_transitions

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

    def find_speech_onset(
        self,
        audio_path: Path,
        search_start: float,
        search_end: float,
        noise_threshold: str = "-30dB",
        target_time: Optional[float] = None,
    ) -> Optional[float]:
        if not self.should_verify:
            return None
        return self.speech_onset
