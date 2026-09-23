"""Content and mantra classification engine for Tool 5 - Content Discoverer."""
import re
from typing import List, Optional, Tuple

from .models import (
    ConfidenceLevel,
    ContentDiscoveryResult,
    ContentEvidence,
    ContentType,
    CutterBoundaryProposal,
    MantraType,
    TranscriptArtifact,
    TranscriptSegment,
)


def normalize_text(text: str) -> str:
    """Normalize text for phonetic/alias matching across transliteration variations."""
    t = text.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# ---------------------------------------------------------------------------
# Mantra Matchers
# ---------------------------------------------------------------------------

JAYA_RADHA_MADHAVA_PATTERNS = [
    r"\bjaya\s+radha\s+madhava\b",
    r"\bradha\s+madhava\s+kunja\s+bihari\b",
    r"\bgopi\s+jana\s+vallabha\b",
    r"\bgiri\s+vara\s+dhari\b",
    r"\byasoda\s+nandana\b",
    r"\bvrajajana\s+ranjana\b",
    r"\byamuna\s+tira\s+vana\s+cari\b",
    r"\bkunja\s+bihari\b",
]

JAYA_SRI_CAITANYA_PATTERNS = [
    r"\bjaya\s+jaya\s+sri\s+caitanya\b",
    r"\bjaya\s+caitanya\b",
    r"\bjaya\s+nityananda\b",
    r"\bjayadvaita\s+candra\b",
    r"\bgadadhara\b",
    r"\bsrivasadi\s+gaura\s+bhakta\b",
    r"\bsri\s+krsna\s+caitanya\b",
    r"\bsri\s+krishna\s+chaitanya\b",
    r"\bpanca\s+tattva\b",
    r"\bpancatattva\b",
]

NRISIMHADEVA_PATTERNS = [
    r"\bnamas\s*te\s+na?r[si]?simha",
    r"\bnamaste\s+na?r[si]?simha",
    r"\bprahlad",
    r"\btavo?kara\s+kamala",
    r"\bnakham\s+adbhuta",
    r"\bdalita\s+hiranyakasipu",
    r"\bito\s+na?r[si]?simha",
    r"\bnarasimha",
    r"\bnrsimha",
    r"\bnrsimhadeva",
]

MAHA_MANTRA_PATTERNS = [
    r"\bhare\s+krishna\s+hare\s+krishna\b",
    r"\bhare\s+rama\s+hare\s+rama\b",
    r"\bkrishna\s+krishna\s+hare\s+hare\b",
    r"\brama\s+rama\s+hare\s+hare\b",
]

# ---------------------------------------------------------------------------
# Class Structure Matchers
# ---------------------------------------------------------------------------

OM_NAMO_PATTERNS = [
    r"\bom\s+namo\s+bhagavate\s+vasudevaya\b",
    r"\bom\s+namo\s+bhagavate\b",
    r"\bnamo\s+bhagavate\s+vasudevaya\b",
]

READING_INTRO_PATTERNS = [
    r"\bwe\s+are\s+reading\s+from\b",
    r"\breading\s+from\b",
    r"\bsrimad\s+bhagavatam\b",
    r"\bbhagavad\s+gita\b",
    r"\bcaitanya\s+caritamrta\b",
    r"\bcanto\s+\d+\b",
    r"\bchapter\s+\d+\b",
    r"\bverse\s+\d+\b",
    r"\btext\s+\d+\b",
]

GURU_PRANAMA_PATTERNS = [
    r"\bom\s+ajnana\s+timirandhasya\b",
    r"\bjnananjana\s+salakaya\b",
    r"\bcaksur\s+unmilitam\s+yena\b",
    r"\btasmai\s+sri\s+gurave\s+namah\b",
    r"\bsri\s+caitanya\s+mano\s+bhistam\b",
    r"\bvande\s+ham\s+sri\s+guroh\b",
]

PURPORT_SYNONYMS_PATTERNS = [
    r"\bword\s+for\s+word\b",
    r"\bsynonyms\b",
    r"\btranslation\b",
    r"\bpurport\b",
    r"\bprabhupada\s+writes\b",
    r"\bin\s+the\s+purport\b",
]

# ---------------------------------------------------------------------------
# Initiation Ceremony Matchers
# ---------------------------------------------------------------------------

INITIATION_VOWS_PATTERNS = [
    r"\bfour\s+regulative\s+principles\b",
    r"\bno\s+meat\b",
    r"\bno\s+gambling\b",
    r"\bno\s+intoxication\b",
    r"\bno\s+illicit\s+sex\b",
    r"\bsixteen\s+rounds\b",
    r"\bjapa\s+beads\b",
]

INITIATION_NAME_PATTERNS = [
    r"\bspiritual\s+name\b",
    r"\bspiritual\s+initiated\s+name\b",
    r"\binitiated\s+name\b",
    r"\byour\s+name\s+is\b",
    r"\byour\s+spiritual\s+name\b",
    r"\bdasa\b",
    r"\bdevi\s+dasi\b",
]

INITIATION_YAJNA_PATTERNS = [
    r"\bfire\s+sacrifice\b",
    r"\byajna\b",
    r"\bsvaha\b",
    r"\bgayatri\s+mantra\b",
    r"\bsecond\s+initiation\b",
    r"\bbrahmana\s+initiation\b",
]

INITIATION_GENERAL_PATTERNS = [
    r"\binitiation\s+ceremony\b",
    r"\bharinama\s+diksa\b",
    r"\bdiksa\b",
    r"\binitiation\b",
]

# ---------------------------------------------------------------------------
# Event / Festival Matchers
# ---------------------------------------------------------------------------

FESTIVAL_PATTERNS = [
    r"\bjanmastami\b",
    r"\bgaura\s+purnima\b",
    r"\brama\s+navami\b",
    r"\bradhastami\b",
    r"\bvyasa\s+puja\b",
    r"\bratha\s+yatra\b",
    r"\btemple\s+opening\b",
    r"\bappearance\s+day\b",
    r"\bdisappearance\s+day\b",
    r"\bwelcome\s+guests\b",
    r"\bwelcome\s+address\b",
    r"\binterfaith\b",
    r"\banniversary\s+celebration\b",
]

# ---------------------------------------------------------------------------
# Home Program Matchers
# ---------------------------------------------------------------------------

HOME_PROGRAM_PATTERNS = [
    r"\bhome\s+program\b",
    r"\bhouse\s+program\b",
    r"\bin\s+the\s+home\s+of\b",
    r"\bin\s+the\s+house\s+of\b",
    r"\bliving\s+room\b",
    r"\bgathered\s+here\s+at\s+the\s+home\b",
    r"\bthank\s+you\s+for\s+inviting\s+us\s+to\s+your\s+home\b",
]


class ContentClassifier:
    """Classifies audio transcripts and identifies mantra and cutter boundaries."""

    def classify(self, artifact: TranscriptArtifact) -> ContentDiscoveryResult:
        meta = dict(artifact.raw_metadata or {})
        meta["duration_seconds"] = artifact.duration_seconds

        segments = artifact.segments
        speech_segments = [s for s in segments if not s.is_silence and s.text.strip()]

        if not speech_segments:
            return ContentDiscoveryResult(
                tracking_id=artifact.tracking_id,
                classification=ContentType.UNKNOWN_REVIEW,
                confidence=ConfidenceLevel.LOW,
                mantra_type=MantraType.NONE,
                process_by_tool_6=False,
                transcript_path=f".renamer/transcripts/{artifact.tracking_id}.json",
                transcript_sha256=artifact.transcript_sha256,
                input_sha256=artifact.input_sha256,
                source_path=artifact.input_path,
                review_required=True,
                review_reason="No speech segments found in transcript",
                runtime_provenance=meta,
            )

        evidence: List[ContentEvidence] = []

        # 1. Detect Mantras
        detected_mantra = MantraType.NONE
        mantra_ranges: List[Tuple[float, float, str]] = []

        for seg in speech_segments:
            norm = normalize_text(seg.text)
            # Jaya Radha Madhava
            if any(re.search(pat, norm) for pat in JAYA_RADHA_MADHAVA_PATTERNS):
                detected_mantra = MantraType.JAYA_RADHA_MADHAVA
                mantra_ranges.append((seg.start_seconds, seg.end_seconds, "Jaya-radha-madhava"))
                evidence.append(
                    ContentEvidence(
                        kind="mantra",
                        start_seconds=seg.start_seconds,
                        end_seconds=seg.end_seconds,
                        raw_excerpt=seg.text,
                        normalized_text=norm,
                    )
                )
            # Jaya Jaya Sri Caitanya
            elif any(re.search(pat, norm) for pat in JAYA_SRI_CAITANYA_PATTERNS):
                if detected_mantra == MantraType.NONE or detected_mantra == MantraType.KIRTAN:
                    detected_mantra = MantraType.JAYA_JAYA_SRI_CAITANYA
                mantra_ranges.append((seg.start_seconds, seg.end_seconds, "Jaya-Jaya-Sri-Caitanya"))
                evidence.append(
                    ContentEvidence(
                        kind="mantra",
                        start_seconds=seg.start_seconds,
                        end_seconds=seg.end_seconds,
                        raw_excerpt=seg.text,
                        normalized_text=norm,
                    )
                )
            # Nrishmadeva
            elif any(re.search(pat, norm) for pat in NRISIMHADEVA_PATTERNS):
                if detected_mantra == MantraType.NONE or detected_mantra == MantraType.KIRTAN:
                    detected_mantra = MantraType.NRISHMADEVA
                mantra_ranges.append((seg.start_seconds, seg.end_seconds, "Nrishmadeva"))
                evidence.append(
                    ContentEvidence(
                        kind="mantra",
                        start_seconds=seg.start_seconds,
                        end_seconds=seg.end_seconds,
                        raw_excerpt=seg.text,
                        normalized_text=norm,
                    )
                )
            # Maha-mantra / general kirtan singing
            elif any(re.search(pat, norm) for pat in MAHA_MANTRA_PATTERNS):
                if detected_mantra == MantraType.NONE:
                    detected_mantra = MantraType.KIRTAN
                mantra_ranges.append((seg.start_seconds, seg.end_seconds, "Kirtan"))
                evidence.append(
                    ContentEvidence(
                        kind="mantra",
                        start_seconds=seg.start_seconds,
                        end_seconds=seg.end_seconds,
                        raw_excerpt=seg.text,
                        normalized_text=norm,
                    )
                )

        # 2. Detect Class Indicators
        class_ranges: List[Tuple[float, float, str]] = []
        for seg in speech_segments:
            norm = normalize_text(seg.text)
            if any(re.search(pat, norm) for pat in OM_NAMO_PATTERNS):
                class_ranges.append((seg.start_seconds, seg.end_seconds, "om_namo"))
                evidence.append(
                    ContentEvidence(
                        kind="om_namo",
                        start_seconds=seg.start_seconds,
                        end_seconds=seg.end_seconds,
                        raw_excerpt=seg.text,
                        normalized_text=norm,
                    )
                )
            if any(re.search(pat, norm) for pat in READING_INTRO_PATTERNS):
                class_ranges.append((seg.start_seconds, seg.end_seconds, "reading_intro"))
                evidence.append(
                    ContentEvidence(
                        kind="reading_intro",
                        start_seconds=seg.start_seconds,
                        end_seconds=seg.end_seconds,
                        raw_excerpt=seg.text,
                        normalized_text=norm,
                    )
                )
            if any(re.search(pat, norm) for pat in GURU_PRANAMA_PATTERNS):
                class_ranges.append((seg.start_seconds, seg.end_seconds, "guru_pranama"))
                evidence.append(
                    ContentEvidence(
                        kind="guru_pranama",
                        start_seconds=seg.start_seconds,
                        end_seconds=seg.end_seconds,
                        raw_excerpt=seg.text,
                        normalized_text=norm,
                    )
                )
            if any(re.search(pat, norm) for pat in PURPORT_SYNONYMS_PATTERNS):
                class_ranges.append((seg.start_seconds, seg.end_seconds, "purport_synonyms"))
                evidence.append(
                    ContentEvidence(
                        kind="purport_synonyms",
                        start_seconds=seg.start_seconds,
                        end_seconds=seg.end_seconds,
                        raw_excerpt=seg.text,
                        normalized_text=norm,
                    )
                )

        # 3. Detect Initiation Indicators
        initiation_ranges: List[Tuple[float, float, str]] = []
        initiation_stages: List[Tuple[str, float]] = []
        for seg in speech_segments:
            norm = normalize_text(seg.text)
            matched_stage = None
            if any(re.search(pat, norm) for pat in INITIATION_VOWS_PATTERNS):
                matched_stage = "initiation vows"
            elif any(re.search(pat, norm) for pat in INITIATION_NAME_PATTERNS):
                matched_stage = "spiritual name giving"
            elif any(re.search(pat, norm) for pat in INITIATION_YAJNA_PATTERNS):
                matched_stage = "fire sacrifice yajna"
            elif any(re.search(pat, norm) for pat in INITIATION_GENERAL_PATTERNS):
                matched_stage = "initiation ceremony"

            if matched_stage:
                initiation_ranges.append((seg.start_seconds, seg.end_seconds, matched_stage))
                initiation_stages.append((matched_stage, seg.start_seconds))
                evidence.append(
                    ContentEvidence(
                        kind=matched_stage,
                        start_seconds=seg.start_seconds,
                        end_seconds=seg.end_seconds,
                        raw_excerpt=seg.text,
                        normalized_text=norm,
                    )
                )

        # 4. Detect Festival Indicators
        festival_ranges: List[Tuple[float, float, str]] = []
        for seg in speech_segments:
            norm = normalize_text(seg.text)
            if any(re.search(pat, norm) for pat in FESTIVAL_PATTERNS):
                festival_ranges.append((seg.start_seconds, seg.end_seconds, "festival"))
                evidence.append(
                    ContentEvidence(
                        kind="festival",
                        start_seconds=seg.start_seconds,
                        end_seconds=seg.end_seconds,
                        raw_excerpt=seg.text,
                        normalized_text=norm,
                    )
                )

        # 5. Detect Home Program Indicators
        home_program_ranges: List[Tuple[float, float, str]] = []
        for seg in speech_segments:
            norm = normalize_text(seg.text)
            if any(re.search(pat, norm) for pat in HOME_PROGRAM_PATTERNS):
                home_program_ranges.append((seg.start_seconds, seg.end_seconds, "home_program"))
                evidence.append(
                    ContentEvidence(
                        kind="home_program",
                        start_seconds=seg.start_seconds,
                        end_seconds=seg.end_seconds,
                        raw_excerpt=seg.text,
                        normalized_text=norm,
                    )
                )

        # -------------------------------------------------------------------
        # Decision Synthesis
        # -------------------------------------------------------------------
        total_duration = artifact.duration_seconds
        has_class_evidence = len(class_ranges) >= 1
        has_mantra_evidence = len(mantra_ranges) >= 1
        has_initiation_evidence = len(initiation_ranges) >= 2  # multiple ceremony vows/names
        has_festival_evidence = len(festival_ranges) >= 1
        has_home_evidence = len(home_program_ranges) >= 1

        # Check for Initiation (Section 7.3)
        if has_initiation_evidence:
            first_vow = min(r[0] for r in initiation_ranges)
            last_vow = max(r[1] for r in initiation_ranges)
            cutter_prop = None
            process_tool6 = False
            # Check if multi-part (e.g. multiple ceremony stages, discourse before, or singing after)
            if len(initiation_ranges) >= 2 or total_duration > last_vow + 30.0 or first_vow > 60.0:
                process_tool6 = True
                split_point = first_vow if first_vow > 60.0 else last_vow
                # Build rich multi-stage description
                stage_desc_parts = []
                seen_stages = set()
                for stage, sec in initiation_stages:
                    if stage not in seen_stages:
                        seen_stages.add(stage)
                        stage_desc_parts.append(f"{stage} at {int(sec)//60:02d}:{int(sec)%60:02d}")
                stage_desc = "; ".join(stage_desc_parts) if stage_desc_parts else f"initiation vows at {int(first_vow)//60:02d}:{int(first_vow)%60:02d}"

                cutter_prop = CutterBoundaryProposal(
                    kirtan_range=(0.0, split_point),
                    class_range=(split_point, total_duration),
                    coarse_gap_bracket=(max(0.0, split_point - 10.0), split_point),
                    confidence="HIGH",
                    description=stage_desc,
                )
            return ContentDiscoveryResult(
                tracking_id=artifact.tracking_id,
                classification=ContentType.INITIATION,
                confidence=ConfidenceLevel.HIGH,
                mantra_type=detected_mantra,
                process_by_tool_6=process_tool6,
                cutter_proposal=cutter_prop,
                transcript_path=f".renamer/transcripts/{artifact.tracking_id}.json",
                transcript_sha256=artifact.transcript_sha256,
                input_sha256=artifact.input_sha256,
                source_path=artifact.input_path,
                evidence=evidence,
                review_required=False,
                runtime_provenance=meta,
            )

        # Check for Kirtan and Class combination (Section 7.3)
        if has_mantra_evidence and has_class_evidence:
            earliest_class = min(r[0] for r in class_ranges)
            latest_kirtan_before_class = max(
                (r[1] for r in mantra_ranges if r[1] <= earliest_class + 60.0),
                default=0.0,
            )

            # Requires distinct ordered time ranges: initial sustained kirtan + later class
            if latest_kirtan_before_class > 60.0 and earliest_class >= latest_kirtan_before_class - 10.0:
                coarse_gap = (latest_kirtan_before_class, earliest_class)
                cutter_prop = CutterBoundaryProposal(
                    kirtan_range=(0.0, latest_kirtan_before_class),
                    class_range=(earliest_class, total_duration),
                    coarse_gap_bracket=coarse_gap,
                    confidence="HIGH",
                )
                return ContentDiscoveryResult(
                    tracking_id=artifact.tracking_id,
                    classification=ContentType.KIRTAN_AND_CLASS,
                    confidence=ConfidenceLevel.HIGH,
                    mantra_type=detected_mantra,
                    process_by_tool_6=True,
                    cutter_proposal=cutter_prop,
                    transcript_path=f".renamer/transcripts/{artifact.tracking_id}.json",
                    transcript_sha256=artifact.transcript_sha256,
                    input_sha256=artifact.input_sha256,
                    source_path=artifact.input_path,
                    evidence=evidence,
                    review_required=False,
                    runtime_provenance=meta,
                )

        # Check for Pure KIRTAN (singing only, no lecture/class discourse)
        # Even if filename suggested a class or combination (Section 7.3)
        if has_mantra_evidence and not has_class_evidence and not has_festival_evidence:
            # Singing dominates recording
            return ContentDiscoveryResult(
                tracking_id=artifact.tracking_id,
                classification=ContentType.KIRTAN,
                confidence=ConfidenceLevel.HIGH,
                mantra_type=detected_mantra,
                process_by_tool_6=False,
                cutter_proposal=None,
                transcript_path=f".renamer/transcripts/{artifact.tracking_id}.json",
                transcript_sha256=artifact.transcript_sha256,
                input_sha256=artifact.input_sha256,
                source_path=artifact.input_path,
                evidence=evidence,
                review_required=False,
                runtime_provenance=meta,
            )

        # Check for Pure CLASS (e.g. partial or full class structure)
        if has_class_evidence:
            confidence = ConfidenceLevel.HIGH if len(class_ranges) >= 2 else ConfidenceLevel.MEDIUM
            return ContentDiscoveryResult(
                tracking_id=artifact.tracking_id,
                classification=ContentType.CLASS,
                confidence=confidence,
                mantra_type=detected_mantra,
                process_by_tool_6=False,
                cutter_proposal=None,
                transcript_path=f".renamer/transcripts/{artifact.tracking_id}.json",
                transcript_sha256=artifact.transcript_sha256,
                input_sha256=artifact.input_sha256,
                source_path=artifact.input_path,
                evidence=evidence,
                review_required=(confidence != ConfidenceLevel.HIGH),
                runtime_provenance=meta,
            )

        # Check for Festival / Event Address
        if has_festival_evidence:
            return ContentDiscoveryResult(
                tracking_id=artifact.tracking_id,
                classification=ContentType.EVENT_OR_FESTIVAL_ADDRESS,
                confidence=ConfidenceLevel.HIGH if len(festival_ranges) >= 2 else ConfidenceLevel.MEDIUM,
                mantra_type=detected_mantra,
                process_by_tool_6=False,
                cutter_proposal=None,
                transcript_path=f".renamer/transcripts/{artifact.tracking_id}.json",
                transcript_sha256=artifact.transcript_sha256,
                input_sha256=artifact.input_sha256,
                source_path=artifact.input_path,
                evidence=evidence,
                review_required=False,
                runtime_provenance=meta,
            )

        # Check for Home Program
        if has_home_evidence:
            return ContentDiscoveryResult(
                tracking_id=artifact.tracking_id,
                classification=ContentType.HOME_PROGRAM,
                confidence=ConfidenceLevel.HIGH,
                mantra_type=detected_mantra,
                process_by_tool_6=False,
                cutter_proposal=None,
                transcript_path=f".renamer/transcripts/{artifact.tracking_id}.json",
                transcript_sha256=artifact.transcript_sha256,
                input_sha256=artifact.input_sha256,
                source_path=artifact.input_path,
                evidence=evidence,
                review_required=False,
                runtime_provenance=meta,
            )

        # Ambiguous / Uncertain -> UNKNOWN_REVIEW
        return ContentDiscoveryResult(
            tracking_id=artifact.tracking_id,
            classification=ContentType.UNKNOWN_REVIEW,
            confidence=ConfidenceLevel.LOW,
            mantra_type=detected_mantra,
            process_by_tool_6=False,
            cutter_proposal=None,
            transcript_path=f".renamer/transcripts/{artifact.tracking_id}.json",
            transcript_sha256=artifact.transcript_sha256,
            input_sha256=artifact.input_sha256,
            source_path=artifact.input_path,
            evidence=evidence,
            review_required=True,
            review_reason="Ambiguous transcript evidence; unable to classify with high confidence",
            runtime_provenance=meta,
        )
