"""Comprehensive hermetic tests for Tool 5 - Content Discoverer (Section 9 Verification Suite)."""
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from media_archive_tooling.cli import main
from media_archive_tooling.content_discoverer.audio_extractor import (
    AudioExtractionError,
    FakeAudioExtractionAdapter,
    compute_file_sha256,
)
from media_archive_tooling.content_discoverer.classifier import ContentClassifier
from media_archive_tooling.content_discoverer.models import (
    ConfidenceLevel,
    ContentDiscoveryResult,
    ContentType,
    MantraType,
    TranscriptSegment,
)
from media_archive_tooling.content_discoverer.service import ContentDiscovererService
from media_archive_tooling.content_discoverer.transcriber import (
    FakeTranscriptionAdapter,
    TranscriptionBlockedError,
)
from media_archive_tooling.orchestrator.discovery import discover_media_targets
from media_archive_tooling.orchestrator.models import FileExecutionStatus, StageName, WorkflowType
from media_archive_tooling.orchestrator.service import MainToolingScriptService
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

    return {
        "tmp_path": tmp_path,
        "media_dir": media_dir,
        "registry": registry,
        "classifier": classifier,
        "audio_extractor": audio_extractor,
        "transcription_adapter": transcription_adapter,
        "service": service,
    }


# ---------------------------------------------------------------------------
# Test 1: Class with partial sequence -> CLASS, NONE mantra, no tool 6 route
# ---------------------------------------------------------------------------
def test_01_class_with_partial_sequence(env):
    media_file = env["media_dir"] / "2023-08-10_KKS_SB-01-02-19_Zurich.mp3"
    media_file.write_text("audio dummy bytes")

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
    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=120.0, text="jaya jaya sri caitanya jaya nityananda jayadvaita chandra jaya gadadhara"),
    ]
    res1 = env["service"].discover_content(f1, root_dir=env["tmp_path"])
    assert res1.mantra_type == MantraType.JAYA_JAYA_SRI_CAITANYA

    f2 = env["media_dir"] / "nrsimha_chant.mp3"
    f2.write_text("f2")
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

    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=300.0, text="welcome to the harinama diksa initiation ceremony"),
        TranscriptSegment(start_seconds=305.0, end_seconds=700.0, text="do you accept the four regulative principles and promise to chant sixteen rounds daily"),
        TranscriptSegment(start_seconds=705.0, end_seconds=1100.0, text="your spiritual initiated name is krishna dasa brahmacari"),
        TranscriptSegment(start_seconds=1105.0, end_seconds=1500.0, text="svaha fire sacrifice yajna offering auspicious oblations"),
    ]
    env["transcription_adapter"].duration_seconds = 1500.0

    res = env["service"].discover_content(media_file, root_dir=env["tmp_path"])
    assert res.classification == ContentType.INITIATION
    assert res.process_by_tool_6 is True
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
    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=400.0, text="welcome to janmastami festival. this is an address on the auspicious appearance day celebration."),
    ]
    res_fest = env["service"].discover_content(f_fest, root_dir=env["tmp_path"])
    assert res_fest.classification == ContentType.EVENT_OR_FESTIVAL_ADDRESS

    f_home = env["media_dir"] / "home_program.mp3"
    f_home.write_text("home")
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
    env["transcription_adapter"].canned_segments = []
    res_empty = env["service"].discover_content(f_empty, root_dir=env["tmp_path"])
    assert res_empty.classification == ContentType.UNKNOWN_REVIEW
    assert res_empty.review_required is True
    assert res_empty.process_by_tool_6 is False

    f_fail = env["media_dir"] / "corrupt.mp3"
    f_fail.write_text("corrupt")
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
# Test 11: Zero Baserow access by Tool 5
# ---------------------------------------------------------------------------
def test_11_zero_baserow_access_by_tool_5(env, monkeypatch):
    # Mock network / requests / baserow adapters to ensure zero calls
    mock_request = MagicMock(side_effect=RuntimeError("Baserow network call attempted by Tool 5!"))
    monkeypatch.setattr("urllib.request.urlopen", mock_request)

    media_file = env["media_dir"] / "no_baserow.mp3"
    media_file.write_text("data")
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

    env["transcription_adapter"].canned_segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=100.0, text="lecture on caitanya caritamrta"),
    ]
    result = env["service"].discover_content(media_file, root_dir=env["tmp_path"])
    tracking_id = result.tracking_id

    # Register file in registry for portal
    env["registry"].register_file(
        tracking_id=tracking_id,
        current_path=media_file,
        original_path=media_file,
        source_hash=compute_file_sha256(media_file),
    )

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

    res = env["service"].discover_content(video_file, dry_run=True, root_dir=env["tmp_path"])

    # No extracted MP3 created
    derived_mp3 = video_file.with_suffix(".mp3")
    assert not derived_mp3.exists()

    # No transcript sidecar created
    sidecar_path = env["tmp_path"] / ".renamer" / "transcripts" / f"{res.tracking_id}.json"
    assert not sidecar_path.exists()

    # No SQLite row saved
    assert env["registry"].get_content_review(res.tracking_id) is None


# ---------------------------------------------------------------------------
# Test 15: Tool 5 registry data cleared on purge
# ---------------------------------------------------------------------------
def test_15_purge_clears_content_reviews_and_derivatives(env):
    media_file = env["media_dir"] / "purge_sample.mp3"
    media_file.write_text("purge test")
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
