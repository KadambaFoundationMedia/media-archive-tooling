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
    _safe_rollback_output,
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

        if derived_audio_path:
            deriv_p = Path(derived_audio_path).resolve()
            deriv_sha = compute_sha256(deriv_p) if deriv_p.is_file() else "mock_deriv_sha"
            registry.record_video_audio_derivative(
                derived_path=deriv_p,
                source_video_path=path.resolve(),
                source_video_tracking_id=tracking_id,
                source_video_sha256=sha,
                derived_sha256=deriv_sha,
            )

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
        review_root=env["tmp_path"],
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


# ===========================================================================
# 11. Regression Tests: T6-R-001 through T6-R-005
# ===========================================================================
def test_r001_tool5_excerpt_only_and_missing_cut_evidence_blocks(env):
    """T6-R-001: Tool 5 runs bounded excerpt windows, text endpoints alone yield MEDIUM confidence without cut point, and missing cut point blocks Tool 6."""
    from media_archive_tooling.content_discoverer.service import ContentDiscovererService
    from media_archive_tooling.content_discoverer.transcriber import FakeTranscriptionAdapter
    from media_archive_tooling.content_discoverer.models import TranscriptSegment
    from media_archive_tooling.content_discoverer.acoustic_verifier import FakeAcousticBoundaryVerifier

    # 1. Verify ContentDiscovererService uses excerpt_windows on transcription adapter
    media_file = make_audio_file(env["media_dir"] / "r001_test.mp3", duration=300.0)
    tid = env["register_test_file"](media_file, tracking_id="trk_r001", source_duration_seconds=300.0)

    fake_adapter = FakeTranscriptionAdapter(
        canned_segments=[
            TranscriptSegment(start_seconds=0.0, end_seconds=120.0, text="jaya radha madhava kunja bihari gopi jana vallabha", language="en", avg_logprob=-0.2),
            TranscriptSegment(start_seconds=125.0, end_seconds=280.0, text="om ajnana timirandhasya jnana-anjanasalakaya srimad bhagavatam lecture begins", language="en", avg_logprob=-0.2),
        ],
        duration_seconds=300.0,
    )
    disc_svc = ContentDiscovererService(
        registry=env["registry"],
        transcription_adapter=fake_adapter,
        acoustic_verifier=FakeAcousticBoundaryVerifier(should_verify=False),  # Acoustic verifier cannot confirm boundary
    )
    res_disc = disc_svc.discover_content(tid, root_dir=env["tmp_path"])

    # Verify adapter was called with excerpt_windows and full_file_transcriptions_count is 0
    assert len(fake_adapter.calls) == 1
    assert fake_adapter.last_excerpt_windows is not None
    assert len(fake_adapter.last_excerpt_windows) > 0
    assert fake_adapter.full_file_transcriptions_count == 0

    # 2. Classifier without acoustic verification produces MEDIUM confidence and no cut point
    assert res_disc.classification == "KIRTAN_AND_CLASS"
    assert res_disc.confidence == "MEDIUM"
    assert res_disc.process_by_tool_6 is False
    assert res_disc.review_required is True
    if res_disc.cutter_proposal:
        assert res_disc.cutter_proposal.singing_end_seconds is None

    # 3. Tool 6 must block auto-cut when exact singing_end_seconds is missing or confidence is not HIGH
    res_cut = env["cutter_service"].cut_file(tid, root_dir=env["tmp_path"])
    assert res_cut.success is False
    assert res_cut.review_required is True
    assert "not eligible for auto-cut" in (res_cut.review_reason or "") or "Exact singing_end_seconds is missing" in (res_cut.review_reason or "")
    assert media_file.exists()


def test_r002_unowned_adjacent_mp3_survives_untouched(env):
    """T6-R-002: Unowned adjacent MP3 survives untouched; video container integrity preserved."""
    # 1. Video file with unindexed adjacent MP3
    video_file = env["media_dir"] / "r002_lecture.mp4"
    video_file.write_bytes(b"VIDEO_HEADER_AND_STREAM_DATA")
    unindexed_mp3 = make_audio_file(env["media_dir"] / "r002_lecture.mp3", duration=6.0)
    unindexed_bytes = unindexed_mp3.read_bytes()
    unindexed_sha = compute_sha256(unindexed_mp3)

    tid = env["register_test_file"](
        video_file,
        tracking_id="trk_r002",
        singing_end_seconds=3.0,
        derived_audio_path=unindexed_mp3,
    )
    # Tamper registry: delete the derivative registration to simulate unindexed/unowned MP3
    with env["registry"]._get_conn() as conn:
        conn.execute("DELETE FROM video_audio_derivatives WHERE source_video_tracking_id = ?", (tid,))
        conn.commit()

    # Tool 6 must fail closed and refuse to touch or cut
    res = env["cutter_service"].cut_file(tid, root_dir=env["tmp_path"])
    assert res.success is False
    assert res.review_required is True
    assert "not tracked in video_audio_derivatives" in (res.review_reason or "")

    # Both files survive completely untouched!
    assert video_file.exists()
    assert unindexed_mp3.exists()
    assert unindexed_mp3.read_bytes() == unindexed_bytes
    assert compute_sha256(unindexed_mp3) == unindexed_sha

    # 2. Properly registered video derivative: verify video path preserved in current_path and audio_file_path synced
    env["registry"].record_video_audio_derivative(
        derived_path=unindexed_mp3,
        source_video_path=video_file,
        source_video_tracking_id=tid,
        source_video_sha256=compute_sha256(video_file),
        derived_sha256=unindexed_sha,
    )
    res_ok = env["cutter_service"].cut_file(tid, root_dir=env["tmp_path"])
    assert res_ok.success is True
    # Video file remains untouched
    assert video_file.exists()
    # Video registry current_path is still the video file, NOT overwritten by mp3!
    updated_file = env["registry"].get_file(tid)
    assert Path(updated_file["current_path"]).resolve() == video_file.resolve()
    assert updated_file["current_filename"] == video_file.name


def test_r003_dry_run_immutability_and_non_combination_rejection(env):
    """T6-R-003: Dry run never writes human decisions or output files; non-combination cannot be forced with cut point."""
    src = make_audio_file(env["media_dir"] / "r003_audio.mp3", duration=6.0)
    src_bytes = src.read_bytes()
    tid = env["register_test_file"](src, tracking_id="trk_r003", singing_end_seconds=2.5)

    # 1. Dry run with cut point override does NOT record human decision or create outputs
    res_dry = env["cutter_service"].cut_file(
        tid,
        dry_run=True,
        cut_point_override=2.8,
        root_dir=env["tmp_path"],
        reviewer="dry_auditor",
    )
    assert res_dry.success is True
    assert res_dry.dry_run is True
    assert env["registry"].get_human_cut_decision(tid) is None
    assert not Path(res_dry.singing_output_path).exists()
    assert not Path(res_dry.class_output_path).exists()
    assert src.read_bytes() == src_bytes

    # 2. Non-combination file (CLASS) with cut point override is strictly rejected
    class_src = make_audio_file(env["media_dir"] / "r003_pure_class.mp3", duration=6.0)
    tid_class = env["register_test_file"](
        class_src,
        tracking_id="trk_r003_cls",
        classification="CLASS",
        confidence="HIGH",
    )
    res_rejected = env["cutter_service"].cut_file(
        tid_class,
        cut_point_override=2.5,
        root_dir=env["tmp_path"],
    )
    assert res_rejected.success is False
    assert res_rejected.review_required is True
    assert "not KIRTAN_AND_CLASS" in (res_rejected.review_reason or "")
    assert class_src.exists()

    # 3. Invalid cut point overrides: <= 1.0 or >= duration - 1.0
    res_invalid1 = env["cutter_service"].cut_file(tid, cut_point_override=0.5, root_dir=env["tmp_path"])
    assert res_invalid1.success is False
    assert res_invalid1.review_required is True
    assert "invalid for duration" in (res_invalid1.review_reason or "")

    res_invalid2 = env["cutter_service"].cut_file(tid, cut_point_override=5.5, root_dir=env["tmp_path"])
    assert res_invalid2.success is False
    assert res_invalid2.review_required is True
    assert "invalid for duration" in (res_invalid2.review_reason or "")


def test_r004_exclusive_publication_and_lineage_before_source_deletion(env, tmp_path):
    """T6-R-004: Atomic exclusive publication, safe rollback, and lineage persisted before source deletion."""
    # 1. _atomic_publish_file fails if target already exists (exclusive no-clobber)
    staged = tmp_path / "staged.mp3"
    staged.write_text("STAGED")
    target = tmp_path / "target.mp3"
    target.write_text("TARGET_EXISTING")

    with pytest.raises(FileExistsError):
        _atomic_publish_file(staged, target)
    assert target.read_text() == "TARGET_EXISTING"

    # 2. _safe_rollback_output unlinks ONLY if hash matches expected
    own_file = tmp_path / "own_file.mp3"
    own_file.write_text("OWN_FILE_DATA")
    own_hash = compute_sha256(own_file)
    other_file = tmp_path / "other_file.mp3"
    other_file.write_text("OTHER_FILE_DATA")

    # Mismatched hash does NOT unlink
    _safe_rollback_output(other_file, own_hash)
    assert other_file.exists()

    # Matching hash unlinks
    _safe_rollback_output(own_file, own_hash)
    assert not own_file.exists()

    # 3. Lineage persisted before source deletion:
    src = make_audio_file(env["media_dir"] / "r004_split.mp3", duration=6.0)
    tid = env["register_test_file"](src, tracking_id="trk_r004", singing_end_seconds=2.5)

    res = env["cutter_service"].cut_file(tid, root_dir=env["tmp_path"])
    assert res.success is True

    # Lineage is registered in SQLite
    split_rec = env["registry"].get_file_split_by_source(tid)
    assert split_rec is not None
    assert split_rec["cut_point_seconds"] == 2.5
    assert split_rec["source_tracking_id"] == tid
    assert split_rec["class_tracking_id"] == tid
    assert split_rec["singing_tracking_id"] == res.singing_tracking_id


def test_r005_unknown_song_title_and_tool4_outbox_pending_sync(env):
    """T6-R-005: Unknown song falls back to Kirtan (not Jaya-radha-madhava); Tool 4 failure records durable outbox."""
    # 1. derive_split_whats fallback logic
    s_what, c_what = derive_split_whats("Lecture_SB-01-02-19", detected_mantra=None)
    assert s_what == "Kirtan"
    assert "Jaya-radha-madhava" not in s_what
    assert "Jaya-Radha-Madhava" not in s_what
    assert c_what == "Lecture-SB-01-02-19"

    s_what2, c_what2 = derive_split_whats("Jaya-Radha-Madhava_SB-01-02-19", detected_mantra="Unknown")
    assert s_what2 == "Kirtan"

    s_what3, c_what3 = derive_split_whats("BG-01-01", detected_mantra="Nama-om-visnu-padaya")
    assert s_what3 == "Nama-om-visnu-padaya"

    # 2. Tool 4 failure / absence records durable outbox PENDING_SYNC in media_db_syncs
    src = make_audio_file(env["media_dir"] / "r005_sync.mp3", duration=6.0)
    tid = env["register_test_file"](src, tracking_id="trk_r005", singing_end_seconds=3.0)

    # Set media_db_service to failing mock
    mock_db = MagicMock()
    mock_db.synchronize.side_effect = ConnectionError("Baserow unreachable")
    env["cutter_service"].media_db_service = mock_db

    res = env["cutter_service"].cut_file(tid, root_dir=env["tmp_path"])
    assert res.success is True
    assert Path(res.singing_output_path).is_file()
    assert Path(res.class_output_path).is_file()

    # Registry has 2 PENDING_SYNC outbox entries
    with env["registry"]._get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT tracking_id, sync_status FROM media_db_syncs WHERE sync_status = 'PENDING_SYNC'")
        pending = {row[0]: row[1] for row in cursor.fetchall()}

    assert tid in pending
    assert res.singing_tracking_id in pending
    assert pending[tid] == "PENDING_SYNC"
    assert pending[res.singing_tracking_id] == "PENDING_SYNC"


def test_r006_whisper_excerpt_mode_never_sends_full_recording_to_whisper(env, monkeypatch):
    """T6-R-006: Excerpt mode must only slice and send targeted windows to Whisper, never the full recording."""
    from media_archive_tooling.content_discoverer.transcriber import WhisperCppTranscriptionAdapter

    audio_file = make_audio_file(env["media_dir"] / "r006_long_audio.mp3", duration=300.0)
    fake_model = env["tmp_path"] / "fake_model.bin"
    fake_model.write_bytes(b"MODEL_BYTES")

    adapter = WhisperCppTranscriptionAdapter(
        whisper_executable=Path("/bin/echo"),
        default_model_path=fake_model,
    )

    whisper_cli_invocations = []

    def mock_run_whisper_cli(wav_input, tmp_stem, resolved_model, device_mode, progress_callback=None):
        whisper_cli_invocations.append({
            "wav_input": Path(wav_input),
            "size": wav_input.stat().st_size if Path(wav_input).exists() else 0,
            "tmp_stem": tmp_stem,
        })
        json_path = Path(f"{tmp_stem}.json")
        json_path.write_text(json.dumps({
            "result": {"language": "en"},
            "transcription": [
                {"timestamps": {"from": "00:00:01.000", "to": "00:00:04.000"}, "text": "excerpt speech"},
            ]
        }), encoding="utf-8")
        return True, "cpu", None

    monkeypatch.setattr(adapter, "_run_whisper_cli", mock_run_whisper_cli)

    # Call with excerpt windows: [0, 10] and [200, 210]
    artifact = adapter.transcribe(
        audio_path=audio_file,
        tracking_id="trk_r006",
        root_dir=env["tmp_path"],
        excerpt_windows=[(0.0, 10.0), (200.0, 210.0)],
        model_path=fake_model,
    )

    # Whisper CLI must be invoked exactly twice (once per excerpt slice), NEVER on the full audio file!
    assert len(whisper_cli_invocations) == 2
    for inv in whisper_cli_invocations:
        wav_name = inv["wav_input"].name
        assert "excerpt_" in wav_name
        assert wav_name != audio_file.name

    # Check that speech segments are properly offset by window starts (0.0 + 1.0 = 1.0, 200.0 + 1.0 = 201.0)
    speech_segs = [s for s in artifact.segments if not s.is_silence]
    assert len(speech_segs) == 2
    assert speech_segs[0].start_seconds == 1.0
    assert speech_segs[0].end_seconds == 4.0
    assert speech_segs[1].start_seconds == 201.0
    assert speech_segs[1].end_seconds == 204.0
    assert artifact.raw_metadata.get("is_full_file") is False


def test_r007_acoustic_verifier_rejects_unrelated_silence_and_fails_closed(env, monkeypatch):
    """T6-R-007: Acoustic verifier strictly narrows window around coarse gap, rejects unrelated silence, and fails closed without fallback."""
    from media_archive_tooling.content_discoverer.acoustic_verifier import AcousticBoundaryVerifier

    audio_file = make_audio_file(env["media_dir"] / "r007_audio.mp3", duration=120.0)
    verifier = AcousticBoundaryVerifier()

    captured_cmds = []

    # Case 1: Unrelated silence earlier in singing portion
    def fake_subprocess_run_outside(cmd, **kwargs):
        captured_cmds.append(cmd)
        # Silence at relative 0.2s: win_start + 0.2 = 28.0 + 0.2 = 28.2 (coarse_gap_start - 1.5 = 28.5, so 28.2 is outside)
        stderr_sim = "[silencedetect @ 0x123] silence_start: 0.2\n[silencedetect @ 0x123] silence_end: 0.8 | silence_duration: 0.6\n"
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=stderr_sim.encode())

    monkeypatch.setattr("media_archive_tooling.content_discoverer.acoustic_verifier.subprocess.run", fake_subprocess_run_outside)

    res_outside = verifier.verify_boundary(
        audio_path=audio_file,
        coarse_gap_start=30.0,
        coarse_gap_end=34.0,
        total_duration=120.0,
    )
    # Must reject silence outside transition zone and return None
    assert res_outside is None

    # Verify command analyzed strictly bounded window: win_start = 30.0 - 2.0 = 28.0, NOT 30 - 30 = 0.0!
    cmd = captured_cmds[-1]
    ss_idx = cmd.index("-ss")
    assert float(cmd[ss_idx + 1]) == 28.0

    # Case 2: Clean transition silence within coarse gap [30.0, 34.0] (e.g. at abs 31.0s -> rel 3.0s)
    def fake_subprocess_run_inside(cmd, **kwargs):
        stderr_sim = "[silencedetect @ 0x123] silence_start: 3.0\n[silencedetect @ 0x123] silence_end: 5.0 | silence_duration: 2.0\n"
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=stderr_sim.encode())

    monkeypatch.setattr("media_archive_tooling.content_discoverer.acoustic_verifier.subprocess.run", fake_subprocess_run_inside)

    res_inside = verifier.verify_boundary(
        audio_path=audio_file,
        coarse_gap_start=30.0,
        coarse_gap_end=34.0,
        total_duration=120.0,
    )
    assert res_inside == 31.0  # 28.0 + 3.0 = 31.0s

    # Case 3: No silence detected at all, gap <= 2.0s: must FAIL CLOSED and return None (NO fallback to coarse_gap_start!)
    def fake_subprocess_run_nosilence(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"no silence detected\n")

    monkeypatch.setattr("media_archive_tooling.content_discoverer.acoustic_verifier.subprocess.run", fake_subprocess_run_nosilence)

    res_nosilence = verifier.verify_boundary(
        audio_path=audio_file,
        coarse_gap_start=30.0,
        coarse_gap_end=31.0,  # Narrow gap (<= 2s)
        total_duration=120.0,
    )
    assert res_nosilence is None  # Never fall back to 30.0!


def test_r008_tool6_tool4_sync_requests_and_retry_metadata_preservation(env):
    """T6-R-008: Tool 6 populates valid Tool 2 decisions for Tool 4, and retry preserves all split-specific metadata."""
    from media_archive_tooling.media_db_updater.service import MediaDatabaseUpdaterService
    from media_archive_tooling.media_db_updater.models import SyncOperation

    src = make_audio_file(env["media_dir"] / "2008-04-13_KKS_JRM_Lecture_Oslo.mp3", duration=6.0)
    tid = env["register_test_file"](src, tracking_id="trk_r008", singing_end_seconds=2.5)

    # Record Tool 2 review for parent file matching Baserow row 42
    env["registry"].save_media_db_review(
        tracking_id=tid,
        decision="EXISTING_MEDIA_MATCH",
        database_state="MATCHED",
        selected_media_row_id=42,
        snapshot_timestamp="2026-09-24T12:00:00Z",
        result_json=json.dumps({"decision": "EXISTING_MEDIA_MATCH"}),
    )

    # 1. Execute cut with failing Tool 4 service (to test durable pending outbox)
    updater_service = MediaDatabaseUpdaterService(
        registry=env["registry"],
        write_adapter=MagicMock(),
        tool2_service=None,
    )
    mock_db = MagicMock()
    mock_db.synchronize.side_effect = ConnectionError("Baserow temporarily offline")
    env["cutter_service"].media_db_service = mock_db

    res = env["cutter_service"].cut_file(tid, root_dir=env["tmp_path"])
    assert res.success is True

    # 2. Check requests passed to Tool 4 in mock_db.synchronize calls
    sync_calls = mock_db.synchronize.call_args_list
    assert len(sync_calls) == 2
    # Call 1: Class successor
    req_class = sync_calls[0].kwargs.get("request") or sync_calls[0][1].get("request")
    assert req_class.tool2_decision == "EXISTING_MEDIA_MATCH"
    assert req_class.selected_media_row_id == 42

    # Call 2: Singing child
    req_singing = sync_calls[1].kwargs.get("request") or sync_calls[1][1].get("request")
    assert req_singing.tool2_decision == "NEW_MEDIA_CANDIDATE"
    assert req_singing.what_category == "Kirtan"
    assert req_singing.tracking_id == res.singing_tracking_id

    # 3. Check SQLite media_db_reviews has entry for singing child
    singing_t2 = env["registry"].get_media_db_review(res.singing_tracking_id)
    assert singing_t2 is not None
    assert singing_t2["decision"] == "NEW_MEDIA_CANDIDATE"

    # 4. Now test MediaDbUpdaterService.build_sync_request() across refresh:
    # Both pending syncs should be rebuilt without losing split metadata:
    # - singing keeps what_category="Kirtan", tool2_decision="NEW_MEDIA_CANDIDATE"
    # - class keeps tool2_decision="EXISTING_MEDIA_MATCH", selected_media_row_id=42
    req_singing_rebuilt = updater_service.build_sync_request(res.singing_tracking_id, force_refresh=True)
    assert req_singing_rebuilt is not None
    assert req_singing_rebuilt.what_category == "Kirtan"
    assert req_singing_rebuilt.tool2_decision == "NEW_MEDIA_CANDIDATE"

    req_class_rebuilt = updater_service.build_sync_request(tid, force_refresh=True)
    assert req_class_rebuilt is not None
    assert req_class_rebuilt.tool2_decision == "EXISTING_MEDIA_MATCH"
    assert req_class_rebuilt.selected_media_row_id == 42

    # Verify that plan_and_revalidate does NOT reject with "Unrecognized or unassociated Tool 2 decision"
    from media_archive_tooling.media_db_updater.write_adapter import FakeBaserowWriteAdapter
    live_fields = FakeBaserowWriteAdapter().fields
    # Singing plan -> CREATE (not blocked by missing decision)
    singing_plan = updater_service.engine.plan_and_revalidate(req_singing_rebuilt, live_fields)
    assert singing_plan.operation == SyncOperation.CREATE
    assert "Unrecognized or unassociated Tool 2 decision" not in str(singing_plan.diagnostic_notes)

    # Class plan -> UPDATE (not blocked by missing decision)
    live_row = {"id": 42, "Filename": req_class_rebuilt.current_filename, "Title": "Old Title"}
    class_plan = updater_service.engine.plan_and_revalidate(req_class_rebuilt, live_fields, live_row=live_row)
    assert class_plan.operation == SyncOperation.UPDATE
    assert "Unrecognized or unassociated Tool 2 decision" not in str(class_plan.diagnostic_notes)


def test_r009_split_kirtan_row_preserves_confirmed_media_metadata_and_field_diffs(env):
    """T6-R-009: Split kirtan row preserves confirmed media metadata (Category, Date, Place, Title)

    Verifies that Tool 6 passes confirmed metadata and eligibility states so Tool 4's
    plan_and_revalidate produces a CREATE with authoritative Title, Kirtan Category, Date,
    and Location (not falling back to filename or excluding fields), that the class row
    retains its class WHAT and provenance, and that the retry path preserves all fields.
    """
    from media_archive_tooling.media_db_updater.service import MediaDatabaseUpdaterService
    from media_archive_tooling.media_db_updater.models import SyncOperation, FieldAction
    from media_archive_tooling.media_db_updater.write_adapter import FakeBaserowWriteAdapter

    # 1. Setup source recording with confirmed date, scripture reference, and location
    src = make_audio_file(env["media_dir"] / "2008-04-13_KKS_Jaya-Radha-Madhava_SB-01-02-19_Oslo.mp3", duration=6.0)
    tid = env["register_test_file"](
        src,
        tracking_id="trk_r009",
        what_val="Jaya-Radha-Madhava_SB-01-02-19",
        when_val="2008-04-13",
        where_val="Oslo",
        singing_end_seconds=2.5,
        mantra_type="Jaya-Radha-Madhava",
    )

    # Record Tool 2 review for parent file matching Baserow row 42
    env["registry"].save_media_db_review(
        tracking_id=tid,
        decision="EXISTING_MEDIA_MATCH",
        database_state="MATCHED",
        selected_media_row_id=42,
        snapshot_timestamp="2026-09-24T12:00:00Z",
        result_json=json.dumps({"decision": "EXISTING_MEDIA_MATCH"}),
    )

    fake_adapter = FakeBaserowWriteAdapter(
        initial_rows=[{
            "id": 42,
            "Filename": src.name,
            "Title": "SB 1.2.19",
            "Category": "Srimad Bhagavatam",
            "Date": "2008-04-13",
            "Place, location": "Oslo",
        }]
    )
    updater_service = MediaDatabaseUpdaterService(
        registry=env["registry"],
        write_adapter=fake_adapter,
        tool2_service=None,
    )
    env["cutter_service"].media_db_service = updater_service

    # 2. Execute cut
    res = env["cutter_service"].cut_file(tid, root_dir=env["tmp_path"])
    assert res.success is True
    singing_tid = res.singing_tracking_id

    # 3. Verify SQLite files table contains confirmed metadata for both successors
    singing_file = env["registry"].get_file(singing_tid)
    assert singing_file is not None
    assert singing_file["what_val"] == "Jaya-Radha-Madhava"
    assert singing_file["when_val"] == "2008-04-13"
    assert singing_file["where_val"] == "Oslo"
    assert singing_file["parser_result"]["what"]["category"] == "Kirtan"
    assert singing_file["parser_result"]["what"]["state"] == "exact"
    assert singing_file["parser_result"]["when"]["state"] == "exact"
    assert singing_file["parser_result"]["where"]["state"] == "exact"

    class_file = env["registry"].get_file(tid)
    assert class_file is not None
    assert class_file["what_val"] == "SB-01-02-19"

    # 4. Check initial sync requests built during cut
    live_fields = fake_adapter.fields

    # Singing child request check
    req_singing = updater_service.build_sync_request(singing_tid, force_refresh=False)
    assert req_singing is not None
    assert req_singing.what_val == "Jaya-Radha-Madhava"
    assert req_singing.what_category == "Kirtan"
    assert req_singing.what_state == "exact"
    assert req_singing.when_val == "2008-04-13"
    assert req_singing.when_state == "exact"
    assert req_singing.where_place == "Oslo"
    assert req_singing.where_state == "exact"
    assert req_singing.who_val == "KKS"
    assert req_singing.tool2_decision == "NEW_MEDIA_CANDIDATE"

    # Direct call to Tool 4 engine plan_and_revalidate for singing child
    singing_plan = updater_service.engine.plan_and_revalidate(req_singing, live_fields)
    assert singing_plan.operation == SyncOperation.CREATE
    diffs_by_name = {d.field_name: d for d in singing_plan.field_diffs}

    # Verify actual field diffs: must NOT fall back to filename or omit Category/Date/Place
    assert "Title" in diffs_by_name
    assert diffs_by_name["Title"].action == FieldAction.SET
    assert diffs_by_name["Title"].new_value == "Jaya-Radha-Madhava"
    assert diffs_by_name["Title"].new_value != req_singing.current_filename

    assert "Category" in diffs_by_name
    assert diffs_by_name["Category"].action == FieldAction.SET
    assert diffs_by_name["Category"].new_value == "Kirtan"

    assert "Date" in diffs_by_name
    assert diffs_by_name["Date"].action == FieldAction.SET
    assert diffs_by_name["Date"].new_value == "2008-04-13"

    assert "Place, location" in diffs_by_name
    assert diffs_by_name["Place, location"].action == FieldAction.SET
    assert diffs_by_name["Place, location"].new_value == "Oslo"

    # 5. Class successor request check
    req_class = updater_service.build_sync_request(tid, force_refresh=False)
    assert req_class is not None
    assert req_class.what_val == "SB-01-02-19"
    assert req_class.what_category == "Srimad Bhagavatam"
    assert req_class.what_state == "exact"
    assert req_class.tool2_decision == "EXISTING_MEDIA_MATCH"
    assert req_class.selected_media_row_id == 42

    # Class plan revalidation against existing row
    live_row = fake_adapter.rows[42]
    class_plan = updater_service.engine.plan_and_revalidate(req_class, live_fields, live_row=live_row)
    assert class_plan.operation in (SyncOperation.UPDATE, SyncOperation.NOOP)
    class_diffs_by_name = {d.field_name: d for d in class_plan.field_diffs}
    assert class_diffs_by_name["Category"].action == FieldAction.PRESERVED
    assert class_diffs_by_name["Date"].action == FieldAction.PRESERVED
    assert class_diffs_by_name["Title"].action == FieldAction.PRESERVED

    # 6. Verify retry path: force_refresh=True reconstructs full requests with all metadata
    req_singing_retry = updater_service.build_sync_request(singing_tid, force_refresh=True)
    assert req_singing_retry is not None
    assert req_singing_retry.what_val == "Jaya-Radha-Madhava"
    assert req_singing_retry.what_category == "Kirtan"
    assert req_singing_retry.what_state == "exact"
    assert req_singing_retry.when_val == "2008-04-13"
    assert req_singing_retry.when_state == "exact"
    assert req_singing_retry.where_place == "Oslo"
    assert req_singing_retry.where_state == "exact"

    retry_singing_plan = updater_service.engine.plan_and_revalidate(req_singing_retry, live_fields)
    retry_diffs = {d.field_name: d for d in retry_singing_plan.field_diffs}
    assert retry_diffs["Category"].new_value == "Kirtan"
    assert retry_diffs["Date"].new_value == "2008-04-13"
    assert retry_diffs["Title"].new_value == "Jaya-Radha-Madhava"
    assert retry_diffs["Place, location"].new_value == "Oslo"

    req_class_retry = updater_service.build_sync_request(tid, force_refresh=True)
    assert req_class_retry is not None
    assert req_class_retry.what_val == "SB-01-02-19"
    assert req_class_retry.what_category == "Srimad Bhagavatam"
    assert req_class_retry.selected_media_row_id == 42


def test_split_does_not_promote_unconfirmed_date_or_location(env):
    """A date-shaped value or named place is not proof of an exact Tool 1 resolution."""
    from media_archive_tooling.media_db_updater.service import MediaDatabaseUpdaterService
    from media_archive_tooling.media_db_updater.write_adapter import FakeBaserowWriteAdapter

    src = make_audio_file(env["media_dir"] / "unconfirmed.mp3", duration=6.0)
    tid = env["register_test_file"](src, tracking_id="trk_unconfirmed", singing_end_seconds=2.5)
    parser = env["registry"].get_file(tid)["parser_result"]
    parser["when"]["state"] = "unresolved"
    parser["where"]["state"] = "provisional"
    env["registry"].update_file_status(tid, parser_result_json=json.dumps(parser))

    result = env["cutter_service"].cut_file(tid, root_dir=env["tmp_path"])
    assert result.success is True

    updater = MediaDatabaseUpdaterService(
        registry=env["registry"], write_adapter=FakeBaserowWriteAdapter(), tool2_service=None
    )
    request = updater.build_sync_request(result.singing_tracking_id, force_refresh=True)
    assert request.when_state == "unresolved"
    assert request.where_state == "provisional"
    plan = updater.engine.plan_and_revalidate(request, updater.write_adapter.fields)
    assert "Date" not in {diff.field_name for diff in plan.field_diffs if diff.action == FieldAction.SET}
    assert "Place, location" not in {diff.field_name for diff in plan.field_diffs if diff.action == FieldAction.SET}


def test_in_place_split_destination_collision_resolution(env):
    """When source file already occupies the planned canonical class destination filename,
    Tool 6 must stage cleanly, replace the class file in-place, and produce the singing file."""
    src = make_audio_file(
        env["media_dir"] / "2008-04-13_KKS_SB-01-02-19_Oslo.mp3",
        duration=6.0,
    )
    tid = env["register_test_file"](
        src,
        tracking_id="trk_inplace",
        what_val="SB-01-02-19",
        mantra_type="Jaya-Radha-Madhava",
        singing_end_seconds=2.5,
        source_duration_seconds=6.0,
    )

    fn_singing, fn_class = env["cutter_service"].plan_output_filenames(tid, "Jaya-Radha-Madhava")
    assert fn_class == "2008-04-13_KKS_SB-01-02-19_Oslo.mp3"
    assert (env["media_dir"] / fn_class).resolve() == src.resolve()

    # Dry run must succeed without collision failure
    dry_res = env["cutter_service"].cut_file(tid, dry_run=True, root_dir=env["tmp_path"])
    assert dry_res.success is True
    assert dry_res.review_required is False
    assert dry_res.class_output_path == str(src)

    # Live cut must succeed without collision failure
    res = env["cutter_service"].cut_file(tid, dry_run=False, root_dir=env["tmp_path"])
    assert res.success is True
    assert res.review_required is False
    assert Path(res.singing_output_path).is_file()
    assert Path(res.class_output_path).is_file()
    assert Path(res.class_output_path).resolve() == src.resolve()

    info_c = env["audio_cutter"].inspect_audio(Path(res.class_output_path))
    assert 3.0 <= info_c["duration"] <= 4.0

    info_s = env["audio_cutter"].inspect_audio(Path(res.singing_output_path))
    assert 2.0 <= info_s["duration"] <= 3.0


def test_in_place_split_rollback_restores_original_source(env):
    """If publication fails during in-place cut, rollback must restore the original source file."""
    src = make_audio_file(
        env["media_dir"] / "2008-04-13_KKS_SB-01-02-19_Oslo.mp3",
        duration=6.0,
    )
    orig_bytes = src.read_bytes()
    tid = env["register_test_file"](
        src,
        tracking_id="trk_inplace_rb",
        what_val="SB-01-02-19",
        mantra_type="Jaya-Radha-Madhava",
        singing_end_seconds=2.5,
        source_duration_seconds=6.0,
    )

    call_count = 0
    original_publish = _atomic_publish_file

    def mock_publish(staged, target):
        nonlocal call_count
        call_count += 1
        if call_count == 2:  # Fail on class publication
            raise RuntimeError("Simulated class publish failure during in-place split")
        original_publish(staged, target)

    with patch("media_archive_tooling.file_cutter.service._atomic_publish_file", side_effect=mock_publish):
        res = env["cutter_service"].cut_file(tid, dry_run=False, root_dir=env["tmp_path"])

    assert res.success is False
    assert res.review_required is True
    # Working input retained and restored!
    assert src.is_file()
    assert src.read_bytes() == orig_bytes
    # Singing output was rolled back
    singing_dest = env["media_dir"] / "2008-04-13_KKS_Jaya-Radha-Madhava_Oslo.mp3"
    assert not singing_dest.exists()


def test_two_boundary_audio_cut_omits_transition_gap(env):
    """AudioCutSpec with distinct cut_point_seconds (kirtan end) and class_start_seconds cuts cleanly and omits dead air gap."""
    # 10s source: singing 0-3s, silence gap 3-5s, class 5-10s
    src = make_audio_file(env["media_dir"] / "two_boundary_source.mp3", duration=10.0)
    tid = env["register_test_file"](
        src,
        tracking_id="trk_twobound",
        what_val="SB-01-19-31",
        mantra_type="Jaya-Radha-Madhava",
        singing_end_seconds=3.0,
        source_duration_seconds=10.0,
    )
    # Manually update cutter proposal to have class_start_seconds=5.0
    crev = env["registry"].get_content_review(tid)
    prop = crev["cutter_proposal"]
    prop["class_start_seconds"] = 5.0
    prop["class_range"] = [5.0, 10.0]
    with env["registry"]._get_conn() as conn:
        conn.execute("UPDATE content_reviews SET cutter_proposal_json = ? WHERE tracking_id = ?", (json.dumps(prop), tid))
        conn.commit()

    res = env["cutter_service"].cut_file(tid, dry_run=False, root_dir=env["tmp_path"])
    assert res.success is True

    # Inspect outputs:
    # Singing part was cut from 0 to 3s (duration ~3s)
    info_s = env["audio_cutter"].inspect_audio(Path(res.singing_output_path))
    assert 2.5 <= info_s["duration"] <= 3.5

    # Class part was cut from 5s to 10s (duration ~5s), NOT from 3s to 10s (which would be 7s)!
    info_c = env["audio_cutter"].inspect_audio(Path(res.class_output_path))
    assert 4.5 <= info_c["duration"] <= 5.5



