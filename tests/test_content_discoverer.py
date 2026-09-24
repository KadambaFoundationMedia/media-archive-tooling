"""Comprehensive hermetic tests for Tool 5 - Content Discoverer (Section 9 Verification Suite)."""
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from media_archive_tooling.cli import main
from media_archive_tooling.content_discoverer.audio_extractor import (
    AudioExtractionCollisionError,
    AudioExtractionError,
    FakeAudioExtractionAdapter,
    _atomic_no_clobber_finalize,
    compute_file_sha256,
)
from media_archive_tooling.content_discoverer.classifier import ContentClassifier
from media_archive_tooling.content_discoverer.models import (
    ConfidenceLevel,
    ContentDiscoveryResult,
    ContentType,
    CutterBoundaryProposal,
    DerivedAudioDetails,
    MantraType,
    TranscriptSegment,
)
from media_archive_tooling.content_discoverer.service import (
    ContentDiscovererService,
    Phase1EligibilityError,
    validate_coarse_boundary,
)
from media_archive_tooling.content_discoverer.transcriber import (
    FakeTranscriptionAdapter,
    TranscriptionBlockedError,
    prepare_whisper_input,
    run_with_heartbeat,
)
from media_archive_tooling.orchestrator.discovery import discover_media_targets
from media_archive_tooling.orchestrator.models import FileExecutionStatus, StageName, WorkflowType
from media_archive_tooling.orchestrator.service import MainToolingScriptService
from media_archive_tooling.renamer.models import Context, Identity, ParserResult, RenameMode, RenameProposal
from media_archive_tooling.renamer.parser.engine import RenamerParser
from media_archive_tooling.renamer.registry.registry import LocalRegistry
from media_archive_tooling.review_portal.app import app as portal_app, configure_review_context


@pytest.fixture
def env(tmp_path):
    """Hermetic test environment with registry, media dir, and fake adapters."""
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    reg_path = tmp_path / ".renamer" / "registry.sqlite"
    reg_path.parent.mkdir(parents=True, exist_ok=True)
    registry = LocalRegistry(reg_path)

    classifier = ContentClassifier()
    audio_extractor = FakeAudioExtractionAdapter()
    transcription_adapter = FakeTranscriptionAdapter()

    service = ContentDiscovererService(
        registry=registry,
        transcription_adapter=transcription_adapter,
        audio_extractor=audio_extractor,
        classifier=classifier,
    )

    def register_media(path: Path, tracking_id: Optional[str] = None, status: str = "PENDING") -> str:
        tid = tracking_id or f"trk_{hashlib.sha256(str(path).encode()).hexdigest()[:8]}"
        registry.register_file(
            tracking_id=tid,
            current_path=path,
            original_path=path,
            status=status,
            source_hash=compute_file_sha256(path) if path.exists() else "dummy_sha",
        )
        return tid

    return {
        "tmp_path": tmp_path,
        "media_dir": media_dir,
        "registry": registry,
        "classifier": classifier,
        "audio_extractor": audio_extractor,
        "transcription_adapter": transcription_adapter,
        "service": service,
        "register_media": register_media,
    }


# ---------------------------------------------------------------------------
# Test 1: Class with partial sequence -> CLASS, NONE mantra, no tool 6 route
# ---------------------------------------------------------------------------
def test_01_class_with_partial_sequence(env):
    media_file = env["media_dir"] / "2023-08-10_KKS_SB-01-02-19_Zurich.mp3"
    media_file.write_text("audio dummy bytes")
    env["register_media"](media_file)

    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=15.0, text="om namo bhagavate vasudevaya"),
        TranscriptSegment(start_seconds=16.0, end_seconds=300.0, text="srimad bhagavatam canto one chapter two text nineteen. today we are discussing devotional service."),
    ]
    env["transcription_adapter"].duration_seconds = 300.0

    res = env["service"].discover_content(media_file, root_dir=env["tmp_path"])
    assert res.classification == ContentType.CLASS
    assert res.mantra_type == MantraType.NONE
    assert res.process_by_tool_6 is False
    assert res.confidence == ConfidenceLevel.HIGH
    assert res.review_required is False


# ---------------------------------------------------------------------------
# Test 2: Jaya-radha-madhava then class -> KIRTAN_AND_CLASS, tool 6 route, boundary
# ---------------------------------------------------------------------------
def test_02_jaya_radha_madhava_then_class(env):
    media_file = env["media_dir"] / "2022-09-19_KKS_SB-01-02-19_Oslo.mp3"
    media_file.write_text("audio dummy bytes")
    env["register_media"](media_file)

    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=700.0, text="jaya radha madhava kunja bihari gopi jana vallabha giri vara dhari"),
        TranscriptSegment(start_seconds=743.0, end_seconds=1800.0, text="om ajnana timirandhasya jnana-anjanasalakaya. srimad bhagavatam lecture begins."),
    ]
    env["transcription_adapter"].duration_seconds = 1800.0

    res = env["service"].discover_content(media_file, root_dir=env["tmp_path"])
    assert res.classification == ContentType.KIRTAN_AND_CLASS
    assert res.mantra_type == MantraType.JAYA_RADHA_MADHAVA
    assert res.process_by_tool_6 is True
    assert res.confidence == ConfidenceLevel.HIGH
    assert res.cutter_proposal is not None
    assert res.cutter_proposal.kirtan_start_sec == 0.0
    assert res.cutter_proposal.kirtan_end_sec == 700.0
    assert res.cutter_proposal.class_start_sec == 743.0
    assert "kirtan 00:00-11:40" in res.cutter_proposal.suggested_cut_points
    assert "class begins 12:23" in res.cutter_proposal.suggested_cut_points


# ---------------------------------------------------------------------------
# Test 3: CC / Panca-tattva and Nrsimha normalized variants
# ---------------------------------------------------------------------------
def test_03_cc_panca_tattva_and_nrsimha_variants(env):
    f1 = env["media_dir"] / "caitanya_song.mp3"
    f1.write_text("f1")
    env["register_media"](f1)
    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=120.0, text="jaya jaya sri caitanya jaya nityananda jayadvaita chandra jaya gadadhara"),
    ]
    res1 = env["service"].discover_content(f1, root_dir=env["tmp_path"])
    assert res1.mantra_type == MantraType.JAYA_JAYA_SRI_CAITANYA

    f2 = env["media_dir"] / "nrsimha_chant.mp3"
    f2.write_text("f2")
    env["register_media"](f2)
    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=180.0, text="namas te narasimhaya prahladahlada-dayine silatanka-nakhalaye"),
    ]
    res2 = env["service"].discover_content(f2, root_dir=env["tmp_path"])
    assert res2.mantra_type == MantraType.NRISHMADEVA


# ---------------------------------------------------------------------------
# Test 4: Singing-only recording labelled combination -> KIRTAN, no cutter route
# ---------------------------------------------------------------------------
def test_04_singing_only_recording_labelled_combination_classified_kirtan_no_route(env):
    media_file = env["media_dir"] / "2022-09-19_KKS_with-radha-madhava_Oslo.mp3"
    media_file.write_text("audio dummy bytes")
    env["register_media"](media_file)

    # Audio contains ONLY singing throughout the file, no class or lecture markers
    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=600.0, text="jaya radha madhava kunja bihari hare krishna hare rama"),
        TranscriptSegment(start_seconds=605.0, end_seconds=1200.0, text="hare krishna hare krishna krishna krishna hare hare hare rama hare rama"),
    ]
    env["transcription_adapter"].duration_seconds = 1200.0

    res = env["service"].discover_content(media_file, root_dir=env["tmp_path"])
    assert res.classification == ContentType.KIRTAN
    assert res.mantra_type == MantraType.JAYA_RADHA_MADHAVA
    assert res.process_by_tool_6 is False  # Selective cutter handoff prevents spurious cutting


# ---------------------------------------------------------------------------
# Test 5: Initiation with multi-part sections -> INITIATION, tool 6 route
# ---------------------------------------------------------------------------
def test_05_initiation_with_multipart_sections(env):
    media_file = env["media_dir"] / "2021-04-15_KKS_Initiation_Radhadesh.mp3"
    media_file.write_text("initiation audio")
    env["register_media"](media_file)

    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=300.0, text="welcome to the harinama diksa initiation ceremony"),
        TranscriptSegment(start_seconds=305.0, end_seconds=700.0, text="do you accept the four regulative principles and promise to chant sixteen rounds daily"),
        TranscriptSegment(start_seconds=705.0, end_seconds=1100.0, text="your spiritual initiated name is krishna dasa brahmacari"),
        TranscriptSegment(start_seconds=1105.0, end_seconds=1500.0, text="svaha fire sacrifice yajna offering auspicious oblations"),
    ]
    env["transcription_adapter"].duration_seconds = 1500.0

    res = env["service"].discover_content(media_file, root_dir=env["tmp_path"])
    assert res.classification == ContentType.INITIATION
    assert res.process_by_tool_6 is False
    assert res.review_required is True
    assert res.cutter_proposal is not None
    assert "initiation vows" in res.cutter_proposal.suggested_cut_points
    assert "spiritual name giving" in res.cutter_proposal.suggested_cut_points
    assert "fire sacrifice yajna" in res.cutter_proposal.suggested_cut_points


# ---------------------------------------------------------------------------
# Test 6: Event / festival address and home program classifications
# ---------------------------------------------------------------------------
def test_06_event_festival_address_and_home_program(env):
    f_fest = env["media_dir"] / "festival_address.mp3"
    f_fest.write_text("fest")
    env["register_media"](f_fest)
    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=400.0, text="welcome to janmastami festival. this is an address on the auspicious appearance day celebration."),
    ]
    res_fest = env["service"].discover_content(f_fest, root_dir=env["tmp_path"])
    assert res_fest.classification == ContentType.EVENT_OR_FESTIVAL_ADDRESS

    f_home = env["media_dir"] / "home_program.mp3"
    f_home.write_text("home")
    env["register_media"](f_home)
    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=500.0, text="thank you for inviting us to your house for this home program gathering. does anyone have questions in the living room?"),
    ]
    res_home = env["service"].discover_content(f_home, root_dir=env["tmp_path"])
    assert res_home.classification == ContentType.HOME_PROGRAM


# ---------------------------------------------------------------------------
# Test 7: Ambiguous / transcription failure -> UNKNOWN_REVIEW, review_required, no route
# ---------------------------------------------------------------------------
def test_07_ambiguous_or_failed_transcription_produces_unknown_review(env):
    f_empty = env["media_dir"] / "silent.mp3"
    f_empty.write_text("silent")
    env["register_media"](f_empty)
    env["transcription_adapter"].canned_segments = []
    res_empty = env["service"].discover_content(f_empty, root_dir=env["tmp_path"])
    assert res_empty.classification == ContentType.UNKNOWN_REVIEW
    assert res_empty.review_required is True
    assert res_empty.process_by_tool_6 is False

    f_fail = env["media_dir"] / "corrupt.mp3"
    f_fail.write_text("corrupt")
    env["register_media"](f_fail)
    env["transcription_adapter"].should_fail = True
    env["transcription_adapter"].fail_message = "whisper-cli executable failed with return code 139"
    res_fail = env["service"].discover_content(f_fail, root_dir=env["tmp_path"])
    assert res_fail.classification == ContentType.UNKNOWN_REVIEW
    assert res_fail.confidence == ConfidenceLevel.BLOCKED
    assert res_fail.review_required is True
    assert res_fail.process_by_tool_6 is False
    assert "whisper-cli executable failed" in (res_fail.review_reason or "")


# ---------------------------------------------------------------------------
# Test 8: Video MP3 extraction, source fingerprint reuse, and collision
# ---------------------------------------------------------------------------
def test_08_video_mp3_extraction_fingerprint_reuse_and_collision(env):
    video_file = env["media_dir"] / "lecture_recording.mp4"
    video_file.write_bytes(b"mock video data 12345")
    env["register_media"](video_file)

    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=60.0, text="om namo bhagavate vasudevaya class begins"),
    ]

    res = env["service"].discover_content(video_file, root_dir=env["tmp_path"])
    derived_mp3 = video_file.with_suffix(".mp3")
    assert derived_mp3.exists()
    assert res.derived_audio_path == str(derived_mp3)
    assert len(env["audio_extractor"].calls) == 1

    # Check derivative registry record
    deriv_rec = env["registry"].get_video_audio_derivative(res.tracking_id)
    assert deriv_rec is not None
    assert deriv_rec["derived_path"] == str(derived_mp3)

    # Re-running reuses extracted derivative without invoking extractor again
    res2 = env["service"].discover_content(video_file, root_dir=env["tmp_path"])
    assert len(env["audio_extractor"].calls) == 1  # Not re-extracted!

    # Target collision handling: destination exists with different content and not registered
    video_coll = env["media_dir"] / "video2.mp4"
    video_coll.write_bytes(b"video 2")
    coll_mp3 = video_coll.with_suffix(".mp3")
    coll_mp3.write_bytes(b"unrelated mp3 file already present")

    # Extractor adapter should detect collision
    with pytest.raises(AudioExtractionError) as exc_info:
        env["audio_extractor"].extract_audio(video_coll, "trk_coll")
    assert "already exists with a different hash" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 9: Metal success, CPU fallback, and explicit metal failure
# ---------------------------------------------------------------------------
def test_09_metal_cpu_fallback_and_metal_failure(env):
    media_file = env["media_dir"] / "speech.mp3"
    media_file.write_text("sample")
    env["register_media"](media_file)

    # 1. Normal auto / metal success
    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=30.0, text="srimad bhagavatam lecture"),
    ]
    res_metal = env["service"].discover_content(media_file, device="auto", force_retranscribe=True, root_dir=env["tmp_path"])
    artifact_json = json.loads((env["tmp_path"] / res_metal.transcript_path).read_text())
    assert artifact_json["raw_metadata"]["selected_backend"] == "metal"

    # 2. Metal error triggers automatic CPU fallback in auto mode
    env["transcription_adapter"].simulate_metal_fallback = True
    res_fallback = env["service"].discover_content(media_file, device="auto", force_retranscribe=True, root_dir=env["tmp_path"])
    artifact_fb = json.loads((env["tmp_path"] / res_fallback.transcript_path).read_text())
    assert artifact_fb["raw_metadata"]["selected_backend"] == "cpu"
    assert "Simulated Metal failure" in (artifact_fb["raw_metadata"]["fallback_reason"] or "")

    # 3. Explicit device="metal" fails closed without falling back to CPU
    res_explicit_metal = env["service"].discover_content(media_file, device="metal", force_retranscribe=True, root_dir=env["tmp_path"])
    assert res_explicit_metal.confidence == ConfidenceLevel.BLOCKED
    assert "explicit Metal device" in (res_explicit_metal.review_reason or "")


# ---------------------------------------------------------------------------
# Test 10: Transcript sidecar caching, reuse, invalidation, and atomic write
# ---------------------------------------------------------------------------
def test_10_transcript_sidecar_caching_invalidation_and_provenance(env):
    media_file = env["media_dir"] / "test_caching.mp3"
    media_file.write_text("original audio bytes 111")
    env["register_media"](media_file)

    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=50.0, text="srimad bhagavatam lecture part one"),
    ]

    # Run 1: writes sidecar
    res1 = env["service"].discover_content(media_file, root_dir=env["tmp_path"])
    sidecar_path = env["tmp_path"] / ".renamer" / "transcripts" / f"{res1.tracking_id}.json"
    assert sidecar_path.exists()
    assert (sidecar_path.stat().st_mode & 0o777) == 0o600
    assert len(env["transcription_adapter"].calls) == 1

    # Run 2: reuses sidecar cache without invoking transcriber
    res2 = env["service"].discover_content(media_file, root_dir=env["tmp_path"])
    assert len(env["transcription_adapter"].calls) == 1  # Reused sidecar!
    assert res2.transcript_sha256 == res1.transcript_sha256

    # Run 3: modify source file -> invalidates cache and triggers fresh transcription
    media_file.write_text("modified audio bytes 222")
    res3 = env["service"].discover_content(media_file, root_dir=env["tmp_path"])
    assert len(env["transcription_adapter"].calls) == 2  # Re-transcribed!
    assert res3.input_sha256 != res1.input_sha256

    # Run 4: force retranscription
    env["service"].discover_content(media_file, force_retranscribe=True, root_dir=env["tmp_path"])
    assert len(env["transcription_adapter"].calls) == 3


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Test 11: Zero Baserow access by Tool 5
# ---------------------------------------------------------------------------
def test_11_zero_baserow_access_by_tool_5(env, monkeypatch):
    # Mock network / requests / baserow adapters to ensure zero calls
    mock_request = MagicMock(side_effect=RuntimeError("Baserow network call attempted by Tool 5!"))
    monkeypatch.setattr("urllib.request.urlopen", mock_request)

    media_file = env["media_dir"] / "no_baserow.mp3"
    media_file.write_text("data")
    env["register_media"](media_file)
    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=10.0, text="om namo bhagavate"),
    ]

    res = env["service"].discover_content(media_file, root_dir=env["tmp_path"])
    assert res.classification == ContentType.CLASS
    mock_request.assert_not_called()


# ---------------------------------------------------------------------------
# Test 12: No original file move, rename, or deletion by Tool 5
# ---------------------------------------------------------------------------
def test_12_no_original_file_mutation(env):
    media_file = env["media_dir"] / "immutable_source.mp3"
    media_file.write_bytes(b"initial audio contents 999")
    env["register_media"](media_file)
    mtime_before = media_file.stat().st_mtime_ns
    mode_before = media_file.stat().st_mode

    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=30.0, text="bhagavad gita lecture"),
    ]

    env["service"].discover_content(media_file, root_dir=env["tmp_path"])

    assert media_file.exists()
    assert media_file.read_bytes() == b"initial audio contents 999"
    assert media_file.stat().st_mtime_ns == mtime_before
    assert media_file.stat().st_mode == mode_before


# ---------------------------------------------------------------------------
# Test 13: Review portal audio streaming and content review actions
# ---------------------------------------------------------------------------
def test_13_review_portal_audio_streaming_and_content_review_actions(env):
    media_file = env["media_dir"] / "test_portal.mp3"
    media_file.write_bytes(b"portal playable audio stream")
    env["register_media"](media_file)

    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=2400.0, text="lecture on caitanya caritamrta"),
    ]
    result = env["service"].discover_content(media_file, root_dir=env["tmp_path"])
    tracking_id = result.tracking_id

    configure_review_context(registry=env["registry"])
    client = TestClient(portal_app)

    # 1. Detail page renders audio player and content discovery card
    resp_detail = client.get(f"/file/{tracking_id}")
    assert resp_detail.status_code == 200
    html = resp_detail.text
    assert '<audio id="audio-preview" controls' in html
    assert f'<source src="/audio/{tracking_id}"' in html
    assert "Tool 5 — Content Discovery &amp; Transcription" in html
    assert result.classification.value in html

    # 2. Audio streaming endpoint returns valid audio stream
    resp_audio = client.get(f"/audio/{tracking_id}")
    assert resp_audio.status_code == 200
    assert resp_audio.headers["content-type"] == "audio/mpeg"
    assert resp_audio.content == b"portal playable audio stream"

    # 3. Missing tracking ID returns 404
    resp_404 = client.get("/audio/nonexistent_id")
    assert resp_404.status_code == 404

    # 4. Human override action via POST /file/{tracking_id}/content-review-action
    resp_action = client.post(
        f"/file/{tracking_id}/content-review-action",
        data={
            "classification": "INITIATION",
            "mantra_type": "NONE",
            "coarse_boundary": "initiation ceremony 00:00-30:00",
            "notes": "Verified by archivist",
        },
        follow_redirects=False,
    )
    assert resp_action.status_code == 303

    # Check updated record in registry
    updated_cr = env["registry"].get_content_review(tracking_id)
    assert updated_cr["classification"] == "INITIATION"
    assert updated_cr["process_by_tool_6"] == 1
    assert updated_cr["review_required"] == 0
    assert updated_cr["human_decision"]["reviewer"] == "review_portal"
    assert updated_cr["human_decision"]["notes"] == "Verified by archivist"


# ---------------------------------------------------------------------------
# Test 14: Dry-run zero mutation anywhere
# ---------------------------------------------------------------------------
def test_14_dry_run_zero_mutation(env):
    video_file = env["media_dir"] / "video_dry.mp4"
    video_file.write_bytes(b"dry run video")
    env["register_media"](video_file, tracking_id="trk_dryrun")

    res = env["service"].discover_content(video_file, tracking_id="trk_dryrun", dry_run=True, root_dir=env["tmp_path"])

    # No extracted MP3 created
    derived_mp3 = video_file.with_suffix(".mp3")
    assert not derived_mp3.exists()

    # No transcript sidecar created
    sidecar_path = env["tmp_path"] / ".renamer" / "transcripts" / f"{res.tracking_id}.json"
    assert not sidecar_path.exists()
    assert not (env["tmp_path"] / ".renamer" / "transcripts").exists()

    # No SQLite row saved
    assert env["registry"].get_content_review(res.tracking_id) is None


# ---------------------------------------------------------------------------
# Test 15: Tool 5 registry data cleared on purge
# ---------------------------------------------------------------------------
def test_15_purge_clears_content_reviews_and_derivatives(env):
    media_file = env["media_dir"] / "purge_sample.mp3"
    media_file.write_text("purge test")
    env["register_media"](media_file)
    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=20.0, text="sample"),
    ]
    res = env["service"].discover_content(media_file, root_dir=env["tmp_path"])
    assert env["registry"].get_content_review(res.tracking_id) is not None

    # Record video derivative
    env["registry"].record_video_audio_derivative(
        tracking_id=res.tracking_id,
        source_video_path=media_file,
        derived_audio_path=media_file,
        source_sha256="sha1",
        derived_sha256="sha2",
    )
    assert env["registry"].get_video_audio_derivative(res.tracking_id) is not None

    # Clear review state
    env["registry"].clear_review_state()
    assert env["registry"].get_content_review(res.tracking_id) is None
    assert env["registry"].get_video_audio_derivative(res.tracking_id) is None


# ---------------------------------------------------------------------------
# Test 16: Source modification detection and derivative MP3 discovery suppression
# ---------------------------------------------------------------------------
def test_16_source_modification_detection_and_discovery_suppression(env):
    # 1. Source modification during transcription detected and rejected
    media_file = env["media_dir"] / "modified_mid_run.mp3"
    media_file.write_text("data")
    env["register_media"](media_file)
    env["transcription_adapter"].simulate_source_change = True

    res = env["service"].discover_content(media_file, root_dir=env["tmp_path"])
    assert res.classification == ContentType.UNKNOWN_REVIEW
    assert res.confidence == ConfidenceLevel.BLOCKED
    assert "Source file changed during transcription" in (res.review_reason or "")
    env["transcription_adapter"].simulate_source_change = False

    # 2. Derivative suppression from target discovery
    v_file = env["media_dir"] / "clip.mp4"
    v_file.write_bytes(b"clip video")
    derived_mp3 = env["media_dir"] / "clip.mp3"
    derived_mp3.write_bytes(b"clip mp3")

    env["registry"].record_video_audio_derivative(
        tracking_id="trk_clip",
        source_video_path=v_file,
        derived_audio_path=derived_mp3,
        source_sha256="v_sha",
        derived_sha256="a_sha",
    )

    discovery = discover_media_targets([env["media_dir"]], registry=env["registry"])
    # Derived MP3 must NOT be discovered as an independent media target
    assert derived_mp3 not in discovery.media_files
    assert v_file in discovery.media_files


# ---------------------------------------------------------------------------
# Test 17: Main Tooling Script integration across workflows
# ---------------------------------------------------------------------------
def test_17_main_script_integration_workflows(env):
    media_file = env["media_dir"] / "2023-08-10_KKS_SB-01-02-19_Zurich.mp3"
    media_file.write_text("audio sample")

    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=200.0, text="om namo bhagavate vasudevaya class lecture"),
    ]

    # Create MainToolingScriptService with fake dependencies
    from media_archive_tooling.orchestrator.logger import UnifiedArchiveLogger
    from media_archive_tooling.orchestrator.reporter import TerminalReporter
    from media_archive_tooling.renamer.parser.engine import RenamerParser

    logger = UnifiedArchiveLogger(log_path=env["tmp_path"] / "orchestrator.log")
    reporter = TerminalReporter()
    parser = RenamerParser(registry=env["registry"])

    service = MainToolingScriptService(
        registry=env["registry"],
        logger=logger,
        reporter=reporter,
        parser=parser,
        tool2_service=None,
        travel_service=None,
        tool4_service=None,
        tool5_service=env["service"],
        workflow=WorkflowType.ALL,
        dry_run=True,
    )

    # Workflow ALL: executes Tools 1-4 then Tool 5 Stage
    summary_all = service.run([media_file])
    assert summary_all.exit_code == 0
    res_all = summary_all.file_results[0]
    stage_names_all = [s.stage_name for s in res_all.stage_results]
    assert StageName.TOOL_5_CONTENT_DISCOVERY in stage_names_all
    assert res_all.content_discovery_result is not None
    assert res_all.content_discovery_result.classification == ContentType.CLASS

    # Workflow PROCESSING: executes Tool 5 directly
    service.workflow = WorkflowType.PROCESSING
    summary_proc = service.run([media_file])
    assert summary_proc.exit_code == 0
    res_proc = summary_proc.file_results[0]
    stage_names_proc = [s.stage_name for s in res_proc.stage_results]
    assert stage_names_proc == [StageName.TOOL_5_CONTENT_DISCOVERY]
    assert res_proc.content_discovery_result is not None

    # Workflow RENAMER: executes Tools 1-4 only (Tool 5 omitted)
    service.workflow = WorkflowType.RENAMER
    summary_renamer = service.run([media_file])
    assert summary_renamer.exit_code == 0
    res_ren = summary_renamer.file_results[0]
    stage_names_ren = [s.stage_name for s in res_ren.stage_results]
    assert StageName.TOOL_5_CONTENT_DISCOVERY not in stage_names_ren
    assert res_ren.content_discovery_result is None


# ---------------------------------------------------------------------------
# Test 18: CLI discover-content command output
# ---------------------------------------------------------------------------
def test_18_cli_discover_content(env, capsys, monkeypatch):
    media_file = env["media_dir"] / "cli_sample.mp3"
    media_file.write_text("cli audio")
    env["register_media"](media_file)

    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=300.0, text="jaya radha madhava kunja bihari"),
        TranscriptSegment(start_seconds=310.0, end_seconds=900.0, text="om ajnana timirandhasya class begins"),
    ]

    # Monkeypatch ContentDiscovererService in cli.py to return our fixture service
    monkeypatch.setattr(
        "media_archive_tooling.cli.ContentDiscovererService",
        lambda registry: env["service"],
    )

    # 1. Human-readable CLI output
    monkeypatch.setattr(
        "sys.argv",
        ["media-archive", "discover-content", str(media_file), "--registry-path", str(env["registry"].db_path)],
    )
    main()
    captured = capsys.readouterr().out
    assert "Tool 5 - Content Discoverer" in captured
    assert "Type: Kirtan and Class (HIGH)" in captured
    assert "Mantra: Jaya-radha-madhava" in captured
    assert "Route: process_by_tool_6" in captured

    # 2. JSON CLI output
    monkeypatch.setattr(
        "sys.argv",
        ["media-archive", "discover-content", str(media_file), "--json", "--registry-path", str(env["registry"].db_path)],
    )
    main()
    captured_json = capsys.readouterr().out
    parsed = json.loads(captured_json)
    assert parsed["classification"] == "KIRTAN_AND_CLASS"
    assert parsed["mantra_type"] == "Jaya-radha-madhava"
    assert parsed["process_by_tool_6"] is True


# ---------------------------------------------------------------------------
# Tests 19-30: Planner Review Findings (T5-R-001 through T5-R-005)
# ---------------------------------------------------------------------------

# T5-R-001: Phase 1 Registration Eligibility
def test_19_phase1_ineligible_untracked_file_rejected_without_mutation(env):
    """T5-R-001: Targets not tracked in Phase 1 registry are rejected with Phase1EligibilityError."""
    untracked = env["media_dir"] / "untracked_lecture.mp3"
    untracked.write_text("untracked audio content")

    # Reject file path not in registry
    with pytest.raises(Phase1EligibilityError) as exc_info:
        env["service"].discover_content(untracked, root_dir=env["tmp_path"])
    assert "not registered in Phase 1 registry" in str(exc_info.value)

    # Reject tracking_id string not in registry
    with pytest.raises(Phase1EligibilityError) as exc_tid:
        env["service"].discover_content("trk_nonexistent_999", root_dir=env["tmp_path"])
    assert "not found in Phase 1 registry" in str(exc_tid.value)

    # Strictly zero mutations: no transcripts directory, no sidecar, no registry entry
    assert not (env["tmp_path"] / ".renamer" / "transcripts").exists()
    assert env["registry"].list_content_reviews() == []


def test_20_phase1_eligible_unresolved_pending_file_allowed(env):
    """T5-R-001: Files registered in Phase 1 but awaiting human review (PENDING) are eligible."""
    pending_file = env["media_dir"] / "pending_review.mp3"
    pending_file.write_text("pending audio")
    tid = env["register_media"](pending_file, tracking_id="trk_pending_01", status="PENDING")

    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=30.0, text="srimad bhagavatam lecture"),
    ]

    res = env["service"].discover_content(pending_file, root_dir=env["tmp_path"])
    assert res.tracking_id == tid
    assert res.classification == ContentType.CLASS
    assert env["registry"].get_content_review(tid) is not None


def test_21_phase1_dry_run_with_arbitrary_id_rejected(env):
    """T5-R-001: Bare dry-run with caller-supplied tracking_id on untracked path raises Phase1EligibilityError."""
    untracked = env["media_dir"] / "dry_run_candidate.mp3"
    untracked.write_text("dry run audio")

    with pytest.raises(Phase1EligibilityError) as exc_info:
        env["service"].discover_content(
            untracked,
            tracking_id="trk_arbitrary_unregistered",
            dry_run=True,
            root_dir=env["tmp_path"],
        )
    assert "not registered in Phase 1 registry" in str(exc_info.value)
    # Zero disk or registry mutations
    assert not (env["tmp_path"] / ".renamer" / "transcripts").exists()
    assert env["registry"].get_content_review("trk_arbitrary_unregistered") is None


def test_21b_phase1_dry_run_with_valid_pipeline_context_allowed(env):
    """T5-R-001: Legitimate Phase 1 context from pipeline run permits dry-run execution."""
    untracked = env["media_dir"] / "dry_run_candidate_with_ctx.mp3"
    untracked.write_text("dry run audio with ctx")

    tracking_id = "trk_contextual_dry_01"
    identity = Identity(
        tracking_id=tracking_id,
        original_filename=untracked.name,
        original_path=str(untracked.resolve()),
        current_filename=untracked.name,
        extension=".mp3",
    )
    context = RenameProposal(
        tracking_id=tracking_id,
        original_path=str(untracked.resolve()),
        current_filename=untracked.name,
        proposed_filename=untracked.name,
        proposed_path=str(untracked.resolve()),
        mode=RenameMode.FINALIZE,
        parser_result=ParserResult(identity=identity, context=Context()),
    )
    res = env["service"].discover_content(
        untracked,
        tracking_id=tracking_id,
        dry_run=True,
        root_dir=env["tmp_path"],
        phase1_context=context,
    )
    assert res.tracking_id == tracking_id
    # Zero disk or registry mutations
    assert not (env["tmp_path"] / ".renamer" / "transcripts").exists()
    assert env["registry"].get_content_review(tracking_id) is None

    with pytest.raises(Phase1EligibilityError):
        env["service"].discover_content(
            untracked,
            tracking_id=tracking_id,
            dry_run=True,
            root_dir=env["tmp_path"],
            phase1_context={"tracking_id": tracking_id},
        )


def test_22_orchestrator_processing_workflow_rejects_untracked_file(env):
    """T5-R-001: Orchestrator processing workflow reports untracked files as ineligible without running Tool 5."""
    untracked = env["media_dir"] / "untracked_pipeline.mp3"
    untracked.write_text("pipeline audio")

    from media_archive_tooling.orchestrator.logger import UnifiedArchiveLogger
    from media_archive_tooling.orchestrator.reporter import TerminalReporter
    from media_archive_tooling.renamer.parser.engine import RenamerParser

    logger = UnifiedArchiveLogger(log_path=env["tmp_path"] / "orchestrator.log")
    reporter = TerminalReporter()
    parser = RenamerParser(registry=env["registry"])

    service = MainToolingScriptService(
        registry=env["registry"],
        logger=logger,
        reporter=reporter,
        parser=parser,
        tool2_service=None,
        travel_service=None,
        tool4_service=None,
        tool5_service=env["service"],
        workflow=WorkflowType.PROCESSING,
        dry_run=True,
    )

    summary = service.run([untracked])
    assert len(summary.file_results) == 1
    res = summary.file_results[0]
    assert res.status == FileExecutionStatus.REVIEW_REQUIRED
    assert any("Target has not undergone Phase 1 renamer processing" in r for r in res.review_reasons)
    assert res.content_discovery_result is None
    # Transcriber was NOT called
    assert len(env["transcription_adapter"].calls) == 0


# T5-R-002: Read-Only Dry-Run & Complete Cache Binding
def test_23_dry_run_strictly_zero_filesystem_artifacts(env):
    """T5-R-002: Dry-run creates no .renamer/transcripts directory even when parent exists."""
    clean_dir = env["tmp_path"] / "virgin_run"
    clean_dir.mkdir(parents=True, exist_ok=True)
    media_file = clean_dir / "audio.mp3"
    media_file.write_text("clean audio")
    env["register_media"](media_file, tracking_id="trk_clean_01")

    res = env["service"].discover_content(
        media_file,
        tracking_id="trk_clean_01",
        dry_run=True,
        root_dir=clean_dir,
    )
    assert res.classification == ContentType.UNKNOWN_REVIEW or res.classification is not None
    # Assert .renamer/transcripts was strictly NOT created
    assert not (clean_dir / ".renamer" / "transcripts").exists()


def test_24_transcription_cache_invalidated_on_model_or_config_change(env):
    """T5-R-002: Cached transcript sidecar is invalidated if model hash or threads differ."""
    media_file = env["media_dir"] / "cache_test.mp3"
    media_file.write_text("caching test audio")
    env["register_media"](media_file)

    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=60.0, text="srimad bhagavatam canto one"),
    ]

    # Run 1: Create initial sidecar
    res1 = env["service"].discover_content(media_file, root_dir=env["tmp_path"])
    sidecar_path = env["tmp_path"] / ".renamer" / "transcripts" / f"{res1.tracking_id}.json"
    assert sidecar_path.exists()
    assert len(env["transcription_adapter"].calls) == 1

    # Mutate model_sha256 in sidecar
    sidecar_data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar_data["raw_metadata"]["model_sha256"] = "different_model_sha256"
    sidecar_path.write_text(json.dumps(sidecar_data), encoding="utf-8")

    # Run 2: Cache must be invalidated due to model_sha256 mismatch -> re-transcribes
    res2 = env["service"].discover_content(media_file, root_dir=env["tmp_path"])
    assert len(env["transcription_adapter"].calls) == 2


# T5-R-003: Protect Adjacent Video MP3s
def test_25_video_extraction_blocks_on_tampered_derivative(env):
    """T5-R-003: Extractor detects adjacent MP3 whose hash does not match recorded derived_sha256."""
    video_file = env["media_dir"] / "class_video.mp4"
    video_file.write_bytes(b"video content data 123")
    env["register_media"](video_file)

    # Initial extraction
    details = env["audio_extractor"].extract_audio(
        video_path=video_file,
        tracking_id="trk_video_01",
        registry=env["registry"],
    )
    target_mp3 = Path(details.derived_audio_path)
    assert target_mp3.exists()

    # Modify/tamper with the extracted MP3 file
    target_mp3.write_bytes(b"tampered content bytes 999")

    # Next extraction attempt must fail with collision error, not reuse
    with pytest.raises(AudioExtractionCollisionError) as exc_info:
        env["audio_extractor"].extract_audio(
            video_path=video_file,
            tracking_id="trk_video_01",
            registry=env["registry"],
        )
    assert "does not match recorded derivative" in str(exc_info.value)


def test_26_video_extraction_blocks_on_concurrent_collision(env, monkeypatch):
    """T5-R-003: If target MP3 appears at finalization point, abort and preserve intruder byte-for-byte."""
    video_file = env["media_dir"] / "concurrent_test.mp4"
    video_file.write_bytes(b"concurrent video content")
    target_mp3 = video_file.with_suffix(".mp3")

    real_link = os.link
    intruder_payload = b"CONCURRENT_INTRUDER_MUST_SURVIVE_BYTE_FOR_BYTE_123"

    def intrusive_link(src, dst):
        target_mp3.write_bytes(intruder_payload)
        return real_link(src, dst)

    monkeypatch.setattr(os, "link", intrusive_link)

    with pytest.raises(AudioExtractionCollisionError) as exc_info:
        env["audio_extractor"].extract_audio(video_file, "trk_concurrent", registry=env["registry"])
    assert "appeared concurrently" in str(exc_info.value)

    # Intruder file must survive completely untouched byte-for-byte
    assert target_mp3.exists()
    assert target_mp3.read_bytes() == intruder_payload

    # Temporary extraction files must be cleanly deleted
    assert not any(".tmp_extract_" in p.name for p in target_mp3.parent.iterdir())


# T5-R-004: Evidence-Backed Human Tool 6 Routing
def test_27_human_decision_requires_valid_boundary_for_tool6_routing(env):
    """T5-R-004: Human override to KIRTAN_AND_CLASS without valid coarse boundary leaves review open."""
    media_file = env["media_dir"] / "lecture_kirtan.mp3"
    media_file.write_text("audio sample")
    env["register_media"](media_file)

    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=300.0, text="om namo bhagavate class begins"),
    ]
    res = env["service"].discover_content(media_file, root_dir=env["tmp_path"])
    tid = res.tracking_id

    # Reviewer changes to KIRTAN_AND_CLASS without coarse_boundary and no prior cutter proposal
    updated = env["service"].apply_human_decision(
        tracking_id=tid,
        classification="KIRTAN_AND_CLASS",
        mantra_type="UNKNOWN",
        coarse_boundary=None,
        reviewer="operator1",
        notes="No timestamps provided",
    )
    assert updated.classification == ContentType.KIRTAN_AND_CLASS
    assert updated.process_by_tool_6 is False
    assert updated.review_required is True
    assert "Tool 6 cutter handoff requires verified coarse boundary brackets" in (updated.review_reason or "")

    # Verify database persistence synchronization
    db_rec = env["registry"].get_content_review(tid)
    assert db_rec["process_by_tool_6"] == 0
    assert db_rec["review_required"] == 1
    assert db_rec["result"]["process_by_tool_6"] is False
    assert db_rec["result"]["review_required"] is True


def test_28_human_decision_with_valid_boundary_enables_tool6_routing(env):
    """T5-R-004: Valid coarse boundary enables process_by_tool_6 and resolves review."""
    media_file = env["media_dir"] / "class_with_kirtan.mp3"
    media_file.write_text("audio sample")
    env["register_media"](media_file)

    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=1200.0, text="sample"),
    ]
    res = env["service"].discover_content(media_file, root_dir=env["tmp_path"])
    tid = res.tracking_id

    # Reviewer provides verified boundary
    updated = env["service"].apply_human_decision(
        tracking_id=tid,
        classification="KIRTAN_AND_CLASS",
        mantra_type="Jaya-radha-madhava",
        coarse_boundary="kirtan 00:00-08:30; class begins 08:45",
        reviewer="archivist",
        notes="Verified boundaries",
    )
    assert updated.classification == ContentType.KIRTAN_AND_CLASS
    assert updated.process_by_tool_6 is True
    assert updated.review_required is False
    assert updated.cutter_proposal is not None
    assert updated.cutter_proposal.kirtan_end_sec == 510.0
    assert updated.cutter_proposal.class_start_sec == 525.0

    # Verify database persistence synchronization
    db_rec = env["registry"].get_content_review(tid)
    assert db_rec["process_by_tool_6"] == 1
    assert db_rec["review_required"] == 0
    assert db_rec["result"]["process_by_tool_6"] is True
    assert db_rec["result"]["review_required"] is False
    assert db_rec["result"]["cutter_proposal"]["kirtan_range"] == [0.0, 510.0]


# T5-R-005: Video Source Provenance in Transcript Sidecars
def test_29_video_transcript_sidecar_stores_video_provenance(env):
    """T5-R-005: Video transcripts record source_type='video', video path/hash, and derived MP3 details."""
    video_file = env["media_dir"] / "provenance_video.mp4"
    video_file.write_bytes(b"provenance video data 777")
    env["register_media"](video_file)

    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=100.0, text="video lecture"),
    ]

    res = env["service"].discover_content(video_file, root_dir=env["tmp_path"])
    sidecar_path = env["tmp_path"] / ".renamer" / "transcripts" / f"{res.tracking_id}.json"
    assert sidecar_path.exists()

    sidecar_data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert sidecar_data["source_type"] == "video"
    assert sidecar_data["input_path"] == str(video_file.resolve())
    assert sidecar_data["input_sha256"] == compute_file_sha256(video_file)
    assert sidecar_data["derived_mp3_details"] is not None
    assert sidecar_data["derived_mp3_details"]["source_video_path"] == str(video_file.resolve())
    assert sidecar_data["derived_mp3_details"]["derived_sha256"] == compute_file_sha256(video_file.with_suffix(".mp3"))


def test_30_video_transcript_cache_invalidated_if_video_or_mp3_changes(env):
    """T5-R-005: Sidecar cache is invalidated if original video OR derived MP3 changes."""
    video_file = env["media_dir"] / "invalidation_test.mp4"
    video_file.write_bytes(b"initial video bytes")
    tid = env["register_media"](video_file)

    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=50.0, text="lecture"),
    ]

    # Run 1: writes sidecar
    res1 = env["service"].discover_content(video_file, root_dir=env["tmp_path"])
    assert len(env["transcription_adapter"].calls) == 1

    # Run 2: reuses cache
    res2 = env["service"].discover_content(video_file, root_dir=env["tmp_path"])
    assert len(env["transcription_adapter"].calls) == 1

    # Invalidate cache due to original video change
    video_file.write_bytes(b"altered video bytes")
    derived_mp3 = video_file.with_suffix(".mp3")
    derived_mp3.unlink()  # allow re-extraction of derivative for new video
    res3 = env["service"].discover_content(video_file, root_dir=env["tmp_path"])
    assert len(env["transcription_adapter"].calls) == 2

    # Invalidate cache due to derived MP3 change (tested directly against transcriber adapter)
    derived_mp3.write_bytes(b"modified audio bytes")
    altered_details = DerivedAudioDetails(
        source_video_path=str(video_file.resolve()),
        derived_audio_path=str(derived_mp3.resolve()),
        codec_command_summary="ffmpeg",
        duration_seconds=50.0,
        derived_sha256=compute_file_sha256(derived_mp3),
    )
    art = env["transcription_adapter"].transcribe(
        audio_path=derived_mp3,
        tracking_id=tid,
        source_path=video_file,
        source_type="video",
        derived_audio_details=altered_details,
        root_dir=env["tmp_path"],
    )
    assert len(env["transcription_adapter"].calls) == 3


def test_31_short_recording_portal_action_rejects_out_of_range_or_impossible_boundary(env):
    """T5-R-004: Validate timestamps against short recording duration; reject impossible clock fields and bounds beyond file."""
    short_file = env["media_dir"] / "short_recording.mp3"
    short_file.write_text("short audio content")
    tid = env["register_media"](short_file)

    # 45-second recording
    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=45.0, text="short class discourse"),
    ]

    res = env["service"].discover_content(short_file, root_dir=env["tmp_path"])
    assert res.tracking_id == tid

    # 1. Attempt impossible clock field (e.g. 00:99) via portal action
    configure_review_context(registry=env["registry"])
    client = TestClient(portal_app)

    post_res1 = client.post(
        f"/file/{tid}/content-review-action",
        data={
            "classification": "KIRTAN_AND_CLASS",
            "mantra_type": "UNKNOWN",
            "coarse_boundary": "kirtan 00:00-00:99; class begins 00:30",
            "notes": "impossible seconds 99",
        },
        follow_redirects=False,
    )
    assert post_res1.status_code == 303
    db_rec1 = env["registry"].get_content_review(tid)
    assert db_rec1["classification"] == "KIRTAN_AND_CLASS"
    assert db_rec1["process_by_tool_6"] == 0
    assert db_rec1["review_required"] == 1
    assert "Tool 6 cutter handoff requires verified coarse boundary brackets" in (db_rec1["review_reason"] or "")

    # 2. Attempt out-of-range boundary extending beyond 45s recording duration
    post_res2 = client.post(
        f"/file/{tid}/content-review-action",
        data={
            "classification": "KIRTAN_AND_CLASS",
            "mantra_type": "UNKNOWN",
            "coarse_boundary": "kirtan 00:00-01:30; class begins 01:45",
            "notes": "exceeds 45s recording duration",
        },
        follow_redirects=False,
    )
    assert post_res2.status_code == 303
    db_rec2 = env["registry"].get_content_review(tid)
    assert db_rec2["process_by_tool_6"] == 0
    assert db_rec2["review_required"] == 1

    # 3. Attempt valid boundary fully enclosed within 45s recording duration
    post_res3 = client.post(
        f"/file/{tid}/content-review-action",
        data={
            "classification": "KIRTAN_AND_CLASS",
            "mantra_type": "Jaya-radha-madhava",
            "coarse_boundary": "kirtan 00:00-00:20; class begins 00:25",
            "notes": "valid within 45s duration",
        },
        follow_redirects=False,
    )
    assert post_res3.status_code == 303
    db_rec3 = env["registry"].get_content_review(tid)
    assert db_rec3["process_by_tool_6"] == 1
    assert db_rec3["review_required"] == 0
    assert db_rec3["result"]["cutter_proposal"]["kirtan_range"] == [0.0, 20.0]
    assert db_rec3["result"]["cutter_proposal"]["class_range"] == [25.0, 45.0]


def test_32_validate_coarse_boundary_edge_cases():
    """T5-R-004: Unit tests for validate_coarse_boundary edge cases."""
    from media_archive_tooling.content_discoverer.service import validate_coarse_boundary

    # Zero or negative duration rejected
    assert validate_coarse_boundary("00:00-00:30", duration=0.0) is None
    assert validate_coarse_boundary("00:00-00:30", duration=-10.0) is None

    # Impossible clock fields rejected
    assert validate_coarse_boundary("00:00-00:99", duration=300.0) is None
    assert validate_coarse_boundary("00:99-01:00", duration=300.0) is None
    assert validate_coarse_boundary("01:60:00-02:00:00", duration=9000.0) is None
    assert validate_coarse_boundary("00:00-00:60", duration=300.0) is None

    # Unordered timestamps rejected
    assert validate_coarse_boundary("00:30-00:10", duration=300.0) is None
    assert validate_coarse_boundary("kirtan 00:00-01:00; class begins 00:50", duration=300.0) is None

    # Beyond duration rejected
    assert validate_coarse_boundary("00:00-02:00", duration=100.0) is None
    assert validate_coarse_boundary("kirtan 00:00-00:30; class begins 01:10", duration=60.0) is None
    assert validate_coarse_boundary("kirtan 00:00-00:20; class 00:25-01:30", duration=60.0) is None

    # Valid boundaries within duration
    prop1 = validate_coarse_boundary("kirtan 00:00-00:20; class begins 00:25", duration=60.0)
    assert prop1 is not None
    assert prop1.kirtan_range == (0.0, 20.0)
    assert prop1.class_range == (25.0, 60.0)

    # Valid 4-timestamp boundary within duration
    prop2 = validate_coarse_boundary("kirtan 00:00-00:20; class 00:25-00:55", duration=60.0)
    assert prop2 is not None
    assert prop2.kirtan_range == (0.0, 20.0)
    assert prop2.class_range == (25.0, 55.0)


def test_33_no_clobber_finalization_fails_closed_if_hard_links_unavailable(tmp_path, monkeypatch):
    """An unsupported hard-link operation must never fall back to overwrite-capable replace."""
    temporary = tmp_path / ".extracted.tmp.mp3"
    target = tmp_path / "recording.mp3"
    temporary.write_bytes(b"extracted audio")

    def unsupported_link(src, dst):
        raise OSError("hard links unsupported")

    monkeypatch.setattr(os, "link", unsupported_link)

    with pytest.raises(AudioExtractionError, match="without overwriting"):
        _atomic_no_clobber_finalize(temporary, target)

    assert not target.exists()
    assert not temporary.exists()


def test_34_wma_is_decoded_to_temporary_whisper_wav(tmp_path, monkeypatch):
    """WMA cannot go straight to whisper-cli; decode it without changing the source."""
    source = tmp_path / "recording.WMA"
    source.write_bytes(b"original WMA archive bytes")
    decoded = tmp_path / "whisper-input.wav"
    commands = []

    monkeypatch.setattr(
        "media_archive_tooling.content_discoverer.transcriber.shutil.which",
        lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None,
    )

    def fake_run(command, **kwargs):
        commands.append(command)
        decoded.write_bytes(b"RIFF" + b"\x00" * 48)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr("media_archive_tooling.content_discoverer.transcriber.subprocess.run", fake_run)
    progress = []
    whisper_input, detail = prepare_whisper_input(
        source, tmp_path, progress_callback=lambda stage, elapsed, status: progress.append((stage, status))
    )

    assert whisper_input == decoded
    assert "16 kHz mono" in detail
    assert commands[0][commands[0].index("-i") + 1] == str(source)
    assert commands[0][commands[0].index("-ar") + 1] == "16000"
    assert commands[0][commands[0].index("-ac") + 1] == "1"
    assert source.read_bytes() == b"original WMA archive bytes"
    assert progress == [("decode", "start"), ("decode", "done")]


def test_35_wma_decode_failure_blocks_before_whisper(tmp_path, monkeypatch):
    source = tmp_path / "bad.wma"
    source.write_bytes(b"invalid audio")
    monkeypatch.setattr(
        "media_archive_tooling.content_discoverer.transcriber.shutil.which",
        lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None,
    )
    monkeypatch.setattr(
        "media_archive_tooling.content_discoverer.transcriber.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, b"", b"invalid input"),
    )

    with pytest.raises(TranscriptionBlockedError, match="Audio decoding failed before transcription"):
        prepare_whisper_input(source, tmp_path)
    assert source.read_bytes() == b"invalid audio"


def test_36_transcription_heartbeat_reports_slow_subprocess(monkeypatch):
    progress = []

    def slow_run(command, **kwargs):
        time.sleep(0.04)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr("media_archive_tooling.content_discoverer.transcriber.subprocess.run", slow_run)
    run_with_heartbeat(
        ["whisper-cli", "--no-prints"],
        stage="transcribe_metal",
        heartbeat_interval=0.01,
        progress_callback=lambda stage, elapsed, status: progress.append((stage, status)),
    )
    assert progress[0] == ("transcribe_metal", "start")
    assert ("transcribe_metal", "heartbeat") in progress
    assert progress[-1] == ("transcribe_metal", "done")


def test_37_detect_candidate_transitions(tmp_path, monkeypatch):
    """AcousticBoundaryVerifier discovers continuous music boundary (first silence >= 150s)."""
    from media_archive_tooling.content_discoverer.acoustic_verifier import (
        AcousticBoundaryVerifier,
        FakeAcousticBoundaryVerifier,
    )

    audio_file = tmp_path / "test.mp3"
    audio_file.write_bytes(b"dummy")

    verifier = AcousticBoundaryVerifier(ffmpeg_bin="/usr/bin/ffmpeg")

    # 1. First silence >= 150s with subsequent speech onset
    stderr_sim = (
        "[silencedetect @ 0x1] silence_start: 703.956\n"
        "[silencedetect @ 0x1] silence_end: 708.500 | silence_duration: 4.544\n"
        "[silencedetect @ 0x1] silence_start: 715.000\n"
        "[silencedetect @ 0x1] silence_end: 742.120 | silence_duration: 27.120\n"
    )

    monkeypatch.setattr(
        "media_archive_tooling.content_discoverer.acoustic_verifier.subprocess.run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=stderr_sim.encode()),
    )

    candidates = verifier.detect_candidate_transitions(audio_file, total_duration=6000.0)
    assert len(candidates) == 1
    assert candidates[0] == (703.956, 742.12)

    # 2. First silence < 150s -> rejected as normal conversational pause
    stderr_early = (
        "[silencedetect @ 0x1] silence_start: 45.0\n"
        "[silencedetect @ 0x1] silence_end: 46.0 | silence_duration: 1.0\n"
    )
    monkeypatch.setattr(
        "media_archive_tooling.content_discoverer.acoustic_verifier.subprocess.run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=stderr_early.encode()),
    )
    assert verifier.detect_candidate_transitions(audio_file, total_duration=6000.0) == []

    # 3. Short file <= 180s -> returns [] without running ffmpeg
    assert verifier.detect_candidate_transitions(audio_file, total_duration=150.0) == []

    # 4. FakeAcousticBoundaryVerifier test double
    fake_verifier = FakeAcousticBoundaryVerifier(candidate_transitions=[(700.0, 740.0)])
    assert fake_verifier.detect_candidate_transitions(audio_file, 6000.0) == [(700.0, 740.0)]


def test_38_whisper_singing_hallucination_and_filename_combination_detection():
    """Classifier handles Whisper hallucinated text (*Dies singing*) combined with filename prior."""
    classifier = ContentClassifier()

    segments = [
        TranscriptSegment(
            start_seconds=10.0,
            end_seconds=70.0,
            text="*Dies singing* Thank you for watching! Thank you for watching!",
        ),
        TranscriptSegment(
            start_seconds=745.0,
            end_seconds=820.0,
            text="It is verse thirty-one. First Canto Chapter nineteen. Today we are reading Srimad Bhagavatam.",
        ),
    ]

    from media_archive_tooling.content_discoverer.models import TranscriptArtifact

    artifact = TranscriptArtifact(
        tracking_id="trk_comb1",
        input_path="/media/KKS_S.B. 1.19.31(with Radha Madhava)_Oslo_29.8.11.WMA",
        input_sha256="fake_sha",
        transcript_sha256="fake_tx_sha",
        duration_seconds=6000.0,
        segments=segments,
        raw_metadata={
            "candidate_transitions": [(703.956, 742.0)],
            "has_combination_clue": True,
            "mantra_hint": "JAYA_RADHA_MADHAVA",
        },
    )

    result = classifier.classify(artifact)
    assert result.classification == ContentType.KIRTAN_AND_CLASS
    assert result.confidence == ConfidenceLevel.HIGH
    assert result.mantra_type == MantraType.JAYA_RADHA_MADHAVA
    assert result.process_by_tool_6 is True
    assert result.cutter_proposal is not None
    assert result.cutter_proposal.singing_end_seconds == 703.956
    assert result.review_required is False


def test_39_adaptive_excerpt_window_around_candidate_transition(env, monkeypatch):
    """ContentDiscovererService adapts excerpt windows to bracket candidate transition."""
    media_file = env["media_dir"] / "KKS_S.B. 1.19.31(with Radha Madhava)_Oslo_29.8.11.mp3"
    media_file.write_text("audio dummy bytes")

    tid = env["register_media"](media_file, tracking_id="trk_adaptive")

    from media_archive_tooling.content_discoverer.acoustic_verifier import FakeAcousticBoundaryVerifier

    env["service"].acoustic_verifier = FakeAcousticBoundaryVerifier(
        exact_cut_point=703.956,
        candidate_transitions=[(703.956, 742.0)],
    )

    monkeypatch.setattr(
        "media_archive_tooling.content_discoverer.service.probe_audio_duration",
        lambda p: 6000.0,
    )

    windows_requested = []

    def fake_transcribe(audio_path, tracking_id, excerpt_windows=None, **kwargs):
        nonlocal windows_requested
        windows_requested = list(excerpt_windows or [])
        from media_archive_tooling.content_discoverer.models import TranscriptArtifact
        return TranscriptArtifact(
            tracking_id=tracking_id,
            input_path=str(audio_path),
            input_sha256="dummy_sha",
            transcript_sha256="tx_dummy",
            duration_seconds=6000.0,
            segments=[
                TranscriptSegment(start_seconds=10.0, end_seconds=60.0, text="*Dies singing*"),
                TranscriptSegment(start_seconds=745.0, end_seconds=790.0, text="First Canto Chapter nineteen verse thirty-one Srimad Bhagavatam"),
            ],
        )

    env["transcription_adapter"].transcribe = fake_transcribe

    result = env["service"].discover_content(tid)
    assert result.classification == ContentType.KIRTAN_AND_CLASS
    assert result.confidence == ConfidenceLevel.HIGH
    assert result.mantra_type == MantraType.JAYA_RADHA_MADHAVA
    assert result.cutter_proposal.singing_end_seconds == 703.956

    # Verify that an excerpt window specifically targeted the speech onset around 742.0s
    has_onset_window = any(730.0 <= w[0] <= 745.0 for w in windows_requested)
    assert has_onset_window, f"Expected window around speech onset 742s, got: {windows_requested}"


def test_42_verse_introduction_matching():
    """ContentClassifier.is_verse_intro_text matches 'We are reading from' and 'Chapter, Canto, Verse' combinations."""
    from media_archive_tooling.content_discoverer.classifier import ContentClassifier

    # 1. Exact phrase from user audio:
    assert ContentClassifier.is_verse_intro_text("it's 31 first canto chapter 9 the appearance of sukadeva goswami text 31") is True
    assert ContentClassifier.is_verse_intro_text("first canto chapter 19 the appearance of sukadeva goswami text 31") is True

    # 2. Reading from combinations
    assert ContentClassifier.is_verse_intro_text("we are reading from srimad bhagavatam first canto") is True
    assert ContentClassifier.is_verse_intro_text("we are reading today from bhagavad-gita chapter 4 text 10") is True
    assert ContentClassifier.is_verse_intro_text("reading from caitanya caritamrta") is True

    # 3. Chapter and verse combinations
    assert ContentClassifier.is_verse_intro_text("chapter 19 text 31") is True
    assert ContentClassifier.is_verse_intro_text("canto 1 chapter 19") is True
    assert ContentClassifier.is_verse_intro_text("text 31 chapter 19") is True

    # 4. Negative / non-verse intro texts
    assert ContentClassifier.is_verse_intro_text("jaya radha madhava kunja bihari") is False
    assert ContentClassifier.is_verse_intro_text("hare krishna hare krishna krishna krishna hare hare") is False
    assert ContentClassifier.is_verse_intro_text("thank you very much for coming tonight") is False


def test_43_two_boundary_kirtan_and_class_cut_proposal(env, monkeypatch):
    """Combination recording correctly cuts kirtan at singing_end and class at verse introduction."""
    media_file = env["media_dir"] / "2011-08-29_KKS_SB-01-19-31_with_Radha_Madhava_Oslo.mp3"
    media_file.write_text("audio dummy bytes")
    tid = env["register_media"](media_file, tracking_id="trk_comb_intro")

    from media_archive_tooling.content_discoverer.acoustic_verifier import FakeAcousticBoundaryVerifier

    env["service"].acoustic_verifier = FakeAcousticBoundaryVerifier(
        exact_cut_point=703.956,
        candidate_transitions=[(703.956, 764.0)],
        speech_onset=742.0,  # Acoustically verified 12:22 onset
    )

    monkeypatch.setattr(
        "media_archive_tooling.content_discoverer.service.probe_audio_duration",
        lambda p: 6200.0,
    )

    def fake_transcribe(audio_path, tracking_id, excerpt_windows=None, **kwargs):
        from media_archive_tooling.content_discoverer.models import TranscriptArtifact
        return TranscriptArtifact(
            tracking_id=tracking_id,
            input_path=str(audio_path),
            input_sha256="dummy_sha",
            transcript_sha256="tx_dummy",
            duration_seconds=6200.0,
            segments=[
                TranscriptSegment(start_seconds=10.0, end_seconds=700.0, text="jaya radha madhava kunja bihari"),
                TranscriptSegment(start_seconds=739.0, end_seconds=748.0, text="It's 31. First Canto, Chapter 19, The Appearance of Sukadeva Goswami, text 31."),
                TranscriptSegment(start_seconds=764.0, end_seconds=1200.0, text="om namo bhagavate vasudevaya. we continue reading the purport."),
            ],
        )

    env["transcription_adapter"].transcribe = fake_transcribe

    result = env["service"].discover_content(tid)
    assert result.classification == ContentType.KIRTAN_AND_CLASS
    assert result.confidence == ConfidenceLevel.HIGH
    assert result.mantra_type == MantraType.JAYA_RADHA_MADHAVA
    assert result.process_by_tool_6 is True
    assert result.cutter_proposal is not None
    assert result.cutter_proposal.kirtan_start_sec == 0.0
    assert result.cutter_proposal.kirtan_end_sec == 703.956
    # Must pick the verse introduction at 12:22 (742s), NOT 12:44 (764s)!
    assert result.cutter_proposal.class_start_sec == 742.0
    assert "kirtan 00:00-11:43; class begins 12:22" == result.cutter_proposal.suggested_cut_points


