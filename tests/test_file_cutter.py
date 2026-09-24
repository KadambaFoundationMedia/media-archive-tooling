"""Hermetic test suite for Tool 6 — File Cutter (Section 8 Verification Suite)."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from media_archive_tooling.cli import main
from media_archive_tooling.file_cutter.audio_cutter import (
    AudioCutResult,
    AudioCutSpec,
    AudioCutter,
    AudioCutterError,
    AudioVerificationError,
    compute_sha256,
)
from media_archive_tooling.file_cutter.models import (
    CutPointProposal,
    FileCutterResult,
    HumanCutDecision,
    WaveformSummary,
)
from media_archive_tooling.file_cutter.service import (
    FileCutterService,
    _atomic_publish_file,
    derive_split_whats,
)
from media_archive_tooling.file_cutter.waveform import WaveformGenerator
from media_archive_tooling.media_db_updater.models import (
    FieldAction,
    FieldDiff,
    MediaDbSyncRequest,
    MediaDbSyncResult,
    SyncOperation,
    SyncStatus,
)
from media_archive_tooling.orchestrator.models import (
    FileExecutionStatus,
    StageName,
    WorkflowType,
)
from media_archive_tooling.orchestrator.service import (
    MainToolingScriptService,
    create_main_tooling_service,
)
from media_archive_tooling.renamer.models import (
    Context,
    Identity,
    ParserResult,
    RenameMode,
    RenameProposal,
    ResolutionState,
    WhatResult,
    WhenResult,
    WhereResult,
)
from media_archive_tooling.renamer.registry.registry import LocalRegistry
from media_archive_tooling.review_portal.app import app as portal_app, configure_review_context

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg binary is required for Tool 6 file cutter tests",
)


def make_audio_file(
    path: Path,
    duration: float = 6.0,
    ext: str = ".mp3",
    lead_silence: float = 0.0,
) -> Path:
    """Generate a clean synthetic audio file using FFmpeg lavfi."""
    path = path.with_suffix(ext)
    path.parent.mkdir(parents=True, exist_ok=True)

    tone_dur = max(0.5, duration - lead_silence)
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency=1000:duration={tone_dur:.2f}",
    ]

    if lead_silence > 0.05:
        delay_ms = int(lead_silence * 1000)
        cmd.extend(["-af", f"adelay={delay_ms}|{delay_ms}"])

    if ext.lower() == ".wma":
        cmd.extend(["-c:a", "wmav2", "-b:a", "128k"])
    else:
        cmd.extend(["-c:a", "libmp3lame", "-q:a", "2"])

    cmd.append(str(path))
    subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, check=True)
    return path


@pytest.fixture
def env(tmp_path):
    """Hermetic test environment with registry, media dir, and real FFmpeg cutter."""
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    reg_path = tmp_path / ".renamer" / "registry.sqlite"
    reg_path.parent.mkdir(parents=True, exist_ok=True)
    registry = LocalRegistry(reg_path)

    audio_cutter = AudioCutter()
    waveform_generator = WaveformGenerator()
    cutter_service = FileCutterService(
        registry=registry,
        audio_cutter=audio_cutter,
        waveform_generator=waveform_generator,
    )

    def register_test_file(
        path: Path,
        tracking_id: str = "trk00001",
        what_val: str = "Jaya-Radha-Madhava_SB-01-02-19",
        when_val: str = "2008-04-13",
        where_val: str = "Oslo",
        classification: str = "KIRTAN_AND_CLASS",
        confidence: str = "HIGH",
        singing_end_seconds: float = 3.0,
        source_duration_seconds: float = 6.0,
        mantra_type: str = "Jaya-radha-madhava",
        derived_audio_path: Optional[Path] = None,
    ) -> str:
        sha = compute_sha256(path)
        parser_res = ParserResult(
            identity=Identity(
                tracking_id=tracking_id,
                original_filename=path.name,
                original_path=str(path),
                current_filename=path.name,
                extension=path.suffix,
            ),
            when=WhenResult(selected_value=when_val, state=ResolutionState.EXACT),
            what=WhatResult(selected_value=what_val, state=ResolutionState.EXACT),
            where=WhereResult(place_location=where_val, state=ResolutionState.EXACT),
            who="KKS",
            context=Context(parent_folder=path.parent.name),
        )
        registry.register_file(
            tracking_id=tracking_id,
            current_path=path,
            original_path=path,
            original_filename=path.name,
            current_filename=path.name,
            proposed_filename=path.name,
            when_val=when_val,
            what_val=what_val,
            where_val=where_val,
            status="RESOLVED",
            source_hash=sha,
            parser_result_json=parser_res.model_dump_json(),
        )

        crev_data = {
            "tracking_id": tracking_id,
            "source_path": str(path),
            "source_sha256": sha,
            "input_sha256": sha,
            "transcript_sha256": "mock_transcript_sha",
            "classification": classification,
            "confidence": confidence,
            "mantra_type": mantra_type,
            "cutter_proposal": {
                "singing_end_seconds": singing_end_seconds,
                "source_duration_seconds": source_duration_seconds,
                "source_sha256": sha,
                "confidence": confidence,
                "method": "exact_timestamp",
                "kirtan_range": [0.0, singing_end_seconds],
                "class_range": [singing_end_seconds, source_duration_seconds],
                "coarse_gap_bracket": [max(0.0, singing_end_seconds - 0.5), singing_end_seconds + 0.5],
                "suggested_cut_points": [singing_end_seconds],
            },
            "derived_audio_path": str(derived_audio_path) if derived_audio_path else None,
            "transcript_path": str(tmp_path / ".renamer" / "transcripts" / f"{tracking_id}.json"),
            "process_by_tool_6": classification == "KIRTAN_AND_CLASS" and confidence == "HIGH",
            "review_required": classification != "KIRTAN_AND_CLASS" or confidence != "HIGH",
        }
        registry.save_content_review(crev_data)
        return tracking_id

    return {
        "tmp_path": tmp_path,
        "media_dir": media_dir,
        "registry": registry,
        "audio_cutter": audio_cutter,
        "waveform_generator": waveform_generator,
        "cutter_service": cutter_service,
        "register_test_file": register_test_file,
    }


# ===========================================================================
# 1. Exact numeric cut point handoff & confidence gating
# ===========================================================================
def test_01_tool5_handoff_and_exact_cut_boundary(env):
    """Tool 5 handoff: exact numeric cut point auto-cuts; low confidence or missing proposal blocks."""
    media_file = make_audio_file(env["media_dir"] / "2008-04-13_KKS_JRM_SB_Oslo.mp3", duration=6.0)

    # 1. High confidence with exact cut point -> auto-cut succeeds
    tid1 = env["register_test_file"](media_file, tracking_id="trk_ex01", singing_end_seconds=2.5)
    res = env["cutter_service"].cut_file(tid1, root_dir=env["tmp_path"])
    assert res.success is True
    assert res.cut_point_seconds == 2.5
    assert res.singing_output_path is not None
    assert res.class_output_path is not None
    assert Path(res.singing_output_path).is_file()
    assert Path(res.class_output_path).is_file()

    # 2. Low confidence -> blocks auto-cut and requires review
    media_file2 = make_audio_file(env["media_dir"] / "2008-04-13_KKS_Ambiguous_Oslo.mp3", duration=6.0)
    tid2 = env["register_test_file"](media_file2, tracking_id="trk_ex02", confidence="LOW")
    res2 = env["cutter_service"].cut_file(tid2, root_dir=env["tmp_path"])
    assert res2.success is False
    assert res2.review_required is True
    assert "not eligible for auto-cut" in (res2.review_reason or "")

    # 3. Invalid cut point (too close to start or end)
    media_file3 = make_audio_file(env["media_dir"] / "2008-04-13_KKS_Edge_Oslo.mp3", duration=6.0)
    tid3 = env["register_test_file"](media_file3, tracking_id="trk_ex03", singing_end_seconds=0.5)
    res3 = env["cutter_service"].cut_file(tid3, root_dir=env["tmp_path"])
    assert res3.success is False
    assert res3.review_required is True
    assert "invalid for duration" in (res3.review_reason or "")


# ===========================================================================
# 2. Container format preservation & video-derived audio lifecycle
# ===========================================================================
def test_02_container_format_preservation_and_video_lifecycle(env):
    """Container format preservation (.wma -> .wma, .mp3 -> .mp3); video untouched and derived MP3 deleted."""
    # 1. WMA input preserves WMA container and wmav2 codec
    wma_source = make_audio_file(env["media_dir"] / "2008-04-13_KKS_JRM_Lecture_Oslo.wma", duration=6.0, ext=".wma")
    tid_wma = env["register_test_file"](wma_source, tracking_id="trk_wma1", singing_end_seconds=3.0)

    res_wma = env["cutter_service"].cut_file(tid_wma, root_dir=env["tmp_path"])
    assert res_wma.success is True
    assert res_wma.singing_output_path.endswith(".wma")
    assert res_wma.class_output_path.endswith(".wma")

    # Verify codec via ffprobe
    probe_s = env["audio_cutter"].inspect_audio(Path(res_wma.singing_output_path))
    assert probe_s["audio_streams"][0]["codec_name"] == "wmav2"
    probe_c = env["audio_cutter"].inspect_audio(Path(res_wma.class_output_path))
    assert probe_c["audio_streams"][0]["codec_name"] == "wmav2"

    # Original WMA was removed after verified publication
    assert not wma_source.exists()

    # 2. Video source (.mp4) with extracted MP3
    video_source = env["media_dir"] / "2008-04-13_KKS_JRM_Video_Oslo.mp4"
    video_source.write_bytes(b"FAKE_ORIGINAL_VIDEO_BYTES_NEVER_MUTATE")

    extracted_mp3 = make_audio_file(env["media_dir"] / "2008-04-13_KKS_JRM_Video_Oslo.mp3", duration=6.0, ext=".mp3")
    tid_vid = env["register_test_file"](
        video_source,
        tracking_id="trk_vid1",
        singing_end_seconds=2.5,
        derived_audio_path=extracted_mp3,
    )
    env["registry"].record_video_audio_derivative(
        tracking_id=tid_vid,
        source_video_path=video_source,
        derived_audio_path=extracted_mp3,
        source_sha256=compute_sha256(video_source),
        derived_sha256=compute_sha256(extracted_mp3),
    )

    res_vid = env["cutter_service"].cut_file(tid_vid, root_dir=env["tmp_path"])
    assert res_vid.success is True
    assert res_vid.singing_output_path.endswith(".mp3")
    assert res_vid.class_output_path.endswith(".mp3")

    # CRITICAL: Video source remains INTACT!
    assert video_source.exists()
    assert video_source.read_bytes() == b"FAKE_ORIGINAL_VIDEO_BYTES_NEVER_MUTATE"

    # Owned extracted MP3 was cleaned up/deleted
    assert not extracted_mp3.exists()


# ===========================================================================
# 3. Distinct Tool 1 names, identities, and lineage recording
# ===========================================================================
def test_03_distinct_tool1_names_identities_and_lineage(env):
    """Singing and class receive distinct canonical Tool 1 names; lineage recorded in registry."""
    src = make_audio_file(env["media_dir"] / "2008-04-13_KKS_Jaya-Radha-Madhava_SB-01-02-19_Oslo.mp3", duration=6.0)
    tid = env["register_test_file"](
        src,
        tracking_id="trk_lin1",
        what_val="Jaya-Radha-Madhava_SB-01-02-19",
        singing_end_seconds=2.5,
    )

    res = env["cutter_service"].cut_file(tid, root_dir=env["tmp_path"])
    assert res.success is True

    singing_p = Path(res.singing_output_path)
    class_p = Path(res.class_output_path)

    # Distinct filenames: Singing WHAT = Jaya-radha-madhava; Class WHAT = SB-01-02-19 (mantra stripped)
    assert "Jaya-radha-madhava" in singing_p.name or "Jaya-Radha-Madhava" in singing_p.name
    assert "SB-01-02-19" in class_p.name
    assert "Jaya-radha-madhava" not in class_p.name and "Jaya-Radha-Madhava" not in class_p.name

    # Identity lineage
    assert res.class_tracking_id == tid  # Class inherits tracking ID
    assert res.singing_tracking_id != tid  # Singing gets new tracking ID
    assert len(res.singing_tracking_id) == 8
    assert res.singing_pending_tool_11_move is True
    assert res.class_pending_tool_11_move is True

    # Database registry record
    split_rec = env["registry"].get_file_split_by_source(tid)
    assert split_rec is not None
    assert split_rec["singing_tracking_id"] == res.singing_tracking_id
    assert split_rec["class_tracking_id"] == tid
    assert split_rec["cut_point_seconds"] == 2.5
    assert split_rec["singing_path"] == str(singing_p)
    assert split_rec["class_path"] == str(class_p)

    child_rec = env["registry"].get_file_split_by_child(res.singing_tracking_id)
    assert child_rec is not None
    assert child_rec["source_tracking_id"] == tid


# ===========================================================================
# 4. Conservative leading silence trimming
# ===========================================================================
def test_04_conservative_leading_silence_trimming(env):
    """Trims actual leading silence; preserves music/prayers when immediate; records trim."""
    # 1. File with 1.0s silence at start
    delayed_file = make_audio_file(env["media_dir"] / "lead_silence.mp3", duration=7.0, lead_silence=1.0)
    tid1 = env["register_test_file"](delayed_file, tracking_id="trk_sil1", singing_end_seconds=3.5, source_duration_seconds=7.0)

    res1 = env["cutter_service"].cut_file(tid1, root_dir=env["tmp_path"])
    assert res1.success is True
    # Singing portion has detected and trimmed ~1.0s leading silence
    assert res1.singing_leading_silence_seconds >= 0.8
    assert res1.singing_duration_seconds < 3.0  # (3.5 - ~1.0) ~ 2.5s

    # 2. File with immediate sound at start (0s silence)
    clean_file = make_audio_file(env["media_dir"] / "no_silence.mp3", duration=6.0, lead_silence=0.0)
    tid2 = env["register_test_file"](
        clean_file,
        tracking_id="trk_sil2",
        when_val="2008-04-14",
        what_val="Jaya-Radha-Madhava_BG-02-13",
        singing_end_seconds=3.0,
        source_duration_seconds=6.0,
    )

    res2 = env["cutter_service"].cut_file(tid2, root_dir=env["tmp_path"])
    assert res2.success is True
    assert res2.singing_leading_silence_seconds == 0.0
    assert abs(res2.singing_duration_seconds - 3.0) < 0.3


# ===========================================================================
# 5. Exact cut, validation, source-change check, collision, and rollback
# ===========================================================================
def test_05_safety_validation_source_change_collision_and_rollback(env, monkeypatch):
    """Safety guarantees: source alteration aborts, collisions refuse, failure rolls back."""
    # 1. Source fingerprint change between registration and cut -> rejected
    tampered_file = make_audio_file(env["media_dir"] / "tampered.mp3", duration=6.0)
    tid_tamp = env["register_test_file"](tampered_file, tracking_id="trk_tamp", singing_end_seconds=3.0)
    # Modify source on disk
    tampered_file.write_bytes(tampered_file.read_bytes() + b"TAMPERED")

    res_tamp = env["cutter_service"].cut_file(tid_tamp, root_dir=env["tmp_path"])
    assert res_tamp.success is False
    assert res_tamp.review_required is True
    assert "Source file content changed" in (res_tamp.review_reason or "")

    # 2. Collision refusal: projected class target already exists
    coll_file = make_audio_file(env["media_dir"] / "coll_src.mp3", duration=6.0)
    tid_coll = env["register_test_file"](coll_file, tracking_id="trk_coll", singing_end_seconds=3.0)
    _, fn_class = env["cutter_service"].plan_output_filenames(tid_coll)
    existing_dest = env["media_dir"] / fn_class
    existing_dest.write_text("ALREADY_EXISTS")

    res_coll = env["cutter_service"].cut_file(tid_coll, root_dir=env["tmp_path"])
    assert res_coll.success is False
    assert res_coll.review_required is True
    assert "Target class output path already exists" in (res_coll.review_reason or "")
    existing_dest.unlink()

    # 3. Rollback on second output failure: singing file removed, original source retained
    rollback_file = make_audio_file(env["media_dir"] / "rollback_test.mp3", duration=6.0)
    orig_bytes = rollback_file.read_bytes()
    tid_rb = env["register_test_file"](rollback_file, tracking_id="trk_rb1", singing_end_seconds=3.0)

    # Monkeypatch cut_audio or atomic publish to simulate failure on class publish
    original_publish = _atomic_publish_file
    call_count = 0

    def mock_publish(staged, target):
        nonlocal call_count
        call_count += 1
        if call_count == 2:  # Fail on class publication
            raise RuntimeError("Simulated class publication disk failure")
        original_publish(staged, target)

    with patch("media_archive_tooling.file_cutter.service._atomic_publish_file", side_effect=mock_publish):
        res_rb = env["cutter_service"].cut_file(tid_rb, root_dir=env["tmp_path"])

    assert res_rb.success is False
    assert res_rb.review_required is True
    assert "rolled back singing output" in (res_rb.review_reason or "")
    # Working input retained!
    assert rollback_file.exists()
    assert rollback_file.read_bytes() == orig_bytes

    # 4. Idempotency: re-running cut on already-split file returns existing split
    ok_file = make_audio_file(env["media_dir"] / "idempotent.mp3", duration=6.0)
    tid_ok = env["register_test_file"](ok_file, tracking_id="trk_idemp", singing_end_seconds=2.5)
    first_res = env["cutter_service"].cut_file(tid_ok, root_dir=env["tmp_path"])
    assert first_res.success is True

    second_res = env["cutter_service"].cut_file(tid_ok, root_dir=env["tmp_path"])
    assert second_res.success is True
    assert second_res.details.get("reused_existing_split") is True
    assert second_res.singing_output_path == first_res.singing_output_path
    assert second_res.class_output_path == first_res.class_output_path


# ===========================================================================
# 6. Dry-run mode: zero filesystem, registry, or Baserow mutation
# ===========================================================================
def test_06_dry_run_immutability(env):
    """Dry-run simulates cut points, filenames, and durations with zero mutations."""
    src = make_audio_file(env["media_dir"] / "dryrun_source.mp3", duration=6.0)
    src_bytes = src.read_bytes()
    tid = env["register_test_file"](src, tracking_id="trk_dry1", singing_end_seconds=3.0)

    res = env["cutter_service"].cut_file(tid, dry_run=True, root_dir=env["tmp_path"])
    assert res.success is True
    assert res.dry_run is True
    assert res.cut_point_seconds == 3.0
    assert res.singing_output_path is not None
    assert res.class_output_path is not None

    # Source file completely intact
    assert src.exists()
    assert src.read_bytes() == src_bytes

    # No output files created on disk
    assert not Path(res.singing_output_path).exists()
    assert not Path(res.class_output_path).exists()

    # No file_splits recorded in registry
    assert env["registry"].get_file_split_by_source(tid) is None

    # No scratch artifacts retained
    scratch_dir = env["tmp_path"] / ".renamer" / "scratch"
    if scratch_dir.exists():
        assert list(scratch_dir.iterdir()) == []


# ===========================================================================
# 7. Tool 4 synchronization and video audio_file_path column
# ===========================================================================
def test_07_tool4_synchronization_and_video_audio_file_path(env):
    """Tool 4 sync: class row updated, singing row separate; video writes audio_file_path."""
    mock_media_db = MagicMock()
    mock_media_db.synchronize.return_value = MediaDbSyncResult(
        tracking_id="mock_tid",
        status=SyncStatus.SYNCED,
        operation=SyncOperation.UPDATE,
        media_row_id=1234,
    )

    env["cutter_service"].media_db_service = mock_media_db

    # 1. Video source sync writes class MP3 path to audio_file_path
    video_src = env["media_dir"] / "video_sync.mp4"
    video_src.write_bytes(b"VIDEO_CONTENT")
    extracted_mp3 = make_audio_file(env["media_dir"] / "video_sync.mp3", duration=6.0)

    tid_vid = env["register_test_file"](
        video_src,
        tracking_id="trk_vsync",
        singing_end_seconds=3.0,
        derived_audio_path=extracted_mp3,
    )
    res = env["cutter_service"].cut_file(tid_vid, root_dir=env["tmp_path"])
    assert res.success is True

    # Check mock calls to Tool 4 synchronize
    calls = mock_media_db.synchronize.call_args_list
    assert len(calls) == 2

    # Class call: inherits tid_vid, filename is original video filename, audio_file_path is class MP3 path
    req_class = calls[0].kwargs.get("request") or calls[0][1].get("request")
    assert req_class.tracking_id == tid_vid
    assert req_class.current_filename == video_src.name
    assert req_class.audio_file_path == res.class_output_path

    # Singing call: new tracking ID, Kirtan category
    req_singing = calls[1].kwargs.get("request") or calls[1][1].get("request")
    assert req_singing.tracking_id == res.singing_tracking_id
    assert req_singing.what_category == "Kirtan"
    assert req_singing.audio_file_path is None

    # 2. Tool 4 synchronization failure does NOT reverse local split
    mock_failing_db = MagicMock()
    mock_failing_db.synchronize.side_effect = RuntimeError("Baserow connection timeout")
    env["cutter_service"].media_db_service = mock_failing_db

    audio_src = make_audio_file(env["media_dir"] / "audio_sync_fail.mp3", duration=6.0)
    tid_fail = env["register_test_file"](
        audio_src,
        tracking_id="trk_s_fail",
        when_val="2008-04-15",
        singing_end_seconds=2.5,
    )

    res_fail = env["cutter_service"].cut_file(tid_fail, root_dir=env["tmp_path"])
    # Local split MUST remain successful!
    assert res_fail.success is True
    assert Path(res_fail.singing_output_path).is_file()
    assert Path(res_fail.class_output_path).is_file()


# ===========================================================================
# 8. Review portal waveform, playback transcoding, and cut execution endpoint
# ===========================================================================
def test_08_portal_waveform_transcoding_and_cut_api(env):
    """Review portal: waveform peaks, WMA preview transcoding, and cut endpoint."""
    wma_file = make_audio_file(env["media_dir"] / "portal_sample.wma", duration=6.0, ext=".wma")
    tid = env["register_test_file"](wma_file, tracking_id="trk_port1", singing_end_seconds=3.0)

    # 1. Waveform summary generation (500 samples normalized)
    wf = env["cutter_service"].get_waveform(tid, root_dir=env["tmp_path"])
    assert isinstance(wf, WaveformSummary)
    assert len(wf.peaks) == 500
    assert all(0.0 <= p <= 1.0 for p in wf.peaks)
    assert wf.duration_seconds > 0.0

    # 2. WMA preview transcoding for web browser playback
    preview_mp3 = env["cutter_service"].get_audio_preview_path(tid, root_dir=env["tmp_path"])
    assert preview_mp3.is_file()
    assert preview_mp3.suffix.lower() == ".mp3"

    # Second call returns cached preview directly
    preview_mp3_cached = env["cutter_service"].get_audio_preview_path(tid, root_dir=env["tmp_path"])
    assert preview_mp3 == preview_mp3_cached

    # 3. Portal API endpoints via TestClient
    configure_review_context(
        registry=env["registry"],
        file_cutter_service=env["cutter_service"],
    )
    client = TestClient(portal_app)

    # GET /api/file/{tracking_id}/waveform
    resp_wf = client.get(f"/api/file/{tid}/waveform")
    assert resp_wf.status_code == 200
    data_wf = resp_wf.json()
    assert data_wf["sample_count"] == 500
    assert len(data_wf["peaks"]) == 500

    # GET /audio/{tracking_id}
    resp_audio = client.get(f"/audio/{tid}")
    assert resp_audio.status_code in (200, 206)
    assert resp_audio.headers.get("content-type") == "audio/mpeg"

    # POST /api/file/{tracking_id}/cut (Execute cut from portal with custom cut point)
    resp_cut = client.post(
        f"/api/file/{tid}/cut",
        data={
            "cut_point": "2.8",
            "reviewer": "portal_auditor",
            "notes": "Adjusted cut point",
        },
    )
    assert resp_cut.status_code == 200
    cut_data = resp_cut.json()
    assert cut_data["success"] is True
    assert cut_data["cut_point_seconds"] == 2.8

    # Human decision was recorded and audited in registry
    dec = env["registry"].get_human_cut_decision(tid)
    assert dec is not None
    assert dec["cut_point_seconds"] == 2.8
    assert dec["reviewer"] == "portal_auditor"


# ===========================================================================
# 9. Gating (Initiation & Vyasa-puja) and Orchestrator integration
# ===========================================================================
def test_09_gating_initiation_vyasapuja_and_main_script_integration(env):
    """Initiation and Vyasa-puja are blocked from auto-cut; Main script workflow integration."""
    # 1. Initiation recording blocked
    init_file = make_audio_file(env["media_dir"] / "initiation.mp3", duration=6.0)
    tid_init = env["register_test_file"](init_file, tracking_id="trk_init", classification="INITIATION")
    res_init = env["cutter_service"].cut_file(tid_init, root_dir=env["tmp_path"])
    assert res_init.success is False
    assert res_init.review_required is True
    assert "require multi-part specification" in (res_init.review_reason or "")
    assert init_file.exists()  # Source remains intact

    # 2. Vyasa-puja recording blocked
    vp_file = make_audio_file(env["media_dir"] / "vyasapuja.mp3", duration=6.0)
    tid_vp = env["register_test_file"](vp_file, tracking_id="trk_vp", classification="VYASA_PUJA")
    res_vp = env["cutter_service"].cut_file(tid_vp, root_dir=env["tmp_path"])
    assert res_vp.success is False
    assert res_vp.review_required is True
    assert "require multi-part specification" in (res_vp.review_reason or "")
    assert vp_file.exists()

    # 3. Main Tooling Script Service integration
    from media_archive_tooling.orchestrator.logger import UnifiedArchiveLogger
    from media_archive_tooling.orchestrator.reporter import TerminalReporter
    from media_archive_tooling.renamer.parser.engine import RenamerParser

    orch_file = make_audio_file(env["media_dir"] / "2008-04-13_KKS_JRM_Lecture_Oslo.mp3", duration=6.0)
    tid_orch = env["register_test_file"](orch_file, tracking_id="trk_orch", singing_end_seconds=2.5)

    logger = UnifiedArchiveLogger(log_path=env["tmp_path"] / "orchestrator.log")
    reporter = TerminalReporter()
    parser = RenamerParser(registry=env["registry"])

    # Workflow ALL includes Tool 6
    service_all = MainToolingScriptService(
        registry=env["registry"],
        logger=logger,
        reporter=reporter,
        parser=parser,
        tool2_service=None,
        travel_service=None,
        tool4_service=None,
        tool5_service=None,
        tool6_service=env["cutter_service"],
        workflow=WorkflowType.ALL,
        dry_run=True,
    )
    summary_all = service_all.run([orch_file])
    assert summary_all.exit_code == 0
    file_res = summary_all.file_results[0]
    stage_names = [s.stage_name for s in file_res.stage_results]
    assert StageName.TOOL_6_FILE_CUTTER in stage_names
    assert file_res.file_cutter_result is not None
    assert file_res.file_cutter_result.cut_point_seconds == 2.5

    # Workflow RENAMER omits Tool 6
    service_renamer = MainToolingScriptService(
        registry=env["registry"],
        logger=logger,
        reporter=reporter,
        parser=parser,
        tool2_service=None,
        travel_service=None,
        tool4_service=None,
        tool5_service=None,
        tool6_service=env["cutter_service"],
        workflow=WorkflowType.RENAMER,
        dry_run=True,
    )
    summary_renamer = service_renamer.run([orch_file])
    assert summary_renamer.exit_code == 0
    file_res_ren = summary_renamer.file_results[0]
    stage_names_ren = [s.stage_name for s in file_res_ren.stage_results]
    assert StageName.TOOL_6_FILE_CUTTER not in stage_names_ren


# ===========================================================================
# 10. CLI Cut Command Integration
# ===========================================================================
def test_10_cli_cut_command(env, capsys, monkeypatch):
    """Test media-archive cut CLI command with --dry-run and --json."""
    src = make_audio_file(env["media_dir"] / "cli_cut_file.mp3", duration=6.0)
    tid = env["register_test_file"](src, tracking_id="trk_cli1", singing_end_seconds=3.0)

    # 1. CLI cut --dry-run
    monkeypatch.setattr(
        "sys.argv",
        ["media-archive", "cut", tid, "--dry-run", "--registry-path", str(env["registry"].db_path)],
    )
    main()
    out = capsys.readouterr().out
    assert "Tool 6" in out and "File Cutter" in out
    assert "DRY-RUN" in out
    assert "3.00s" in out

    # 2. CLI cut --json
    monkeypatch.setattr(
        "sys.argv",
        ["media-archive", "cut", tid, "--dry-run", "--json", "--registry-path", str(env["registry"].db_path)],
    )
    main()
    out_json = capsys.readouterr().out
    parsed = json.loads(out_json)
    assert parsed["success"] is True
    assert parsed["dry_run"] is True
    assert parsed["cut_point_seconds"] == 3.0
