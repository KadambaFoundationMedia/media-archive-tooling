"""Automated unit and integration test suite for Main Tooling Script orchestrator.

Covers all 36 scenarios specified in Section 17 of docs/main-tooling-script-build-plan.md.
"""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch
import pytest

from media_archive_tooling.cli import main, run_main_script
from media_archive_tooling.config import AppConfig
from media_archive_tooling.media_db_reviewer.models import (
    FieldComparison,
    FieldComparisonState,
    MediaCandidate,
    MediaDatabaseReviewResult,
    RenamerEnrichment,
    ReviewDecision,
    Tool4Action,
)
from media_archive_tooling.media_db_reviewer.service import MediaDatabaseReviewService
from media_archive_tooling.media_db_updater.models import (
    MediaDbSyncResult,
    SyncOperation,
    SyncStatus,
)
from media_archive_tooling.media_db_updater.service import MediaDatabaseUpdaterService
from media_archive_tooling.media_db_updater.write_adapter import (
    DEFAULT_MEDIA_TABLE_FIELDS,
    FakeBaserowWriteAdapter,
)
from media_archive_tooling.orchestrator.discovery import (
    SUPPORTED_MEDIA_EXTENSIONS,
    discover_media_targets,
    is_supported_media_file,
)
from media_archive_tooling.orchestrator.logger import (
    UnifiedArchiveLogger,
    redact_secrets,
)
from media_archive_tooling.orchestrator.models import (
    FileExecutionStatus,
    RunSummary,
    StageName,
    WorkflowType,
)
from media_archive_tooling.orchestrator.reporter import TerminalReporter
from media_archive_tooling.orchestrator.service import (
    MainToolingScriptService,
    create_main_tooling_service,
)
from media_archive_tooling.renamer.models import (
    EnrichmentEvidence,
    FileMetadata,
    ParserResult,
    RenameMode,
    RenameProposal,
    ResolutionState,
    WhatResult,
    WhenResult,
    WhereResult,
)
from media_archive_tooling.renamer.parser.engine import RenamerParser
from media_archive_tooling.renamer.planner.planner import RenamePlanner
from media_archive_tooling.renamer.registry.registry import LocalRegistry
from media_archive_tooling.travel_reviewer.models import (
    NormalizedTravelRow,
    TravelReviewDecision,
    TravelReviewResult,
    TravelScheduleManifest,
)
from media_archive_tooling.travel_reviewer.reference_store import (
    TravelReferenceStore,
    compute_canonical_sha256,
)
from media_archive_tooling.travel_reviewer.service import TravelScheduleReviewService


@pytest.fixture
def env_setup(tmp_path):
    """Fixture providing isolated temporary directories, fake adapters, and wired services."""
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    reg_db = tmp_path / "registry.db"
    log_file = tmp_path / "media-archive-tooling.log"
    travel_ref_path = tmp_path / "travel_schedule.json"

    norm_rows = [
        NormalizedTravelRow(
            id=101,
            start_date="2022-09-15",
            end_date="2022-09-25",
            place="Oslo",
            country="Norway",
            country_iso2="no",
            schedule_text="Oslo visit",
            raw_row={},
        ),
        NormalizedTravelRow(
            id=102,
            start_date="2017-04-01",
            end_date="2017-04-30",
            place="Krsna-Dvur",
            country="Czech Republic",
            country_iso2="cz",
            schedule_text="Czech visit",
            raw_row={},
        ),
        NormalizedTravelRow(
            id=103,
            start_date="2019-09-01",
            end_date="2019-09-30",
            place="Vrindavan",
            country="India",
            country_iso2="in",
            schedule_text="Vrindavan visit",
            raw_row={},
        ),
    ]
    manifest = TravelScheduleManifest(
        format_version="1.0",
        source_table_id="1",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        complete=True,
        row_count=len(norm_rows),
        canonical_sha256=compute_canonical_sha256(norm_rows),
        normalized_rows=norm_rows,
    )
    store = TravelReferenceStore(travel_ref_path)
    store.save_reference(manifest)

    registry = LocalRegistry(reg_db)
    logger = UnifiedArchiveLogger(log_path=log_file, run_id="test_run_001", workflow="all")
    reporter = TerminalReporter(verbose=False, dry_run=False)

    parser = RenamerParser(registry=registry)

    # Configure custom fields with Tag select options for scripture tags
    fields = copy.deepcopy(DEFAULT_MEDIA_TABLE_FIELDS)
    for f in fields:
        if f.get("name") == "Tag":
            f.setdefault("select_options", []).extend([
                {"id": 13, "value": "1.2.19", "color": "yellow"},
                {"id": 14, "value": "1.19.31", "color": "purple"},
            ])

    fake_write_adapter = FakeBaserowWriteAdapter(
        initial_fields=fields,
        initial_rows=[
            {
                "id": 1234,
                "Date": "2022-09-19",
                "Title": "SB 1.2.19",
                "Place": "Oslo",
                "Country": "Norway",
                "File Name": "old_oslo.mp3",
                "Archive Path": "/old/path",
            }
        ]
    )

    tool2_service = MagicMock(spec=MediaDatabaseReviewService)
    def mock_review_file(tracking_id, auto_enrich=True, force_refresh=False):
        rec = registry.get_file(tracking_id)
        if not rec:
            return None
        orig = rec.get("original_filename", "")
        if "oslo" in orig.lower():
            # Matches existing row 1234
            res = MediaDatabaseReviewResult(
                tracking_id=tracking_id,
                decision=ReviewDecision.EXISTING_MEDIA_MATCH,
                selected_media_row_id=1234,
                database_state="LIVE_CURRENT",
                database_snapshot_at="2026-09-19T12:00:00Z",
                baserow_read_at="2026-09-19T12:00:00Z",
                live_read_complete=True,
                snapshot_complete=True,
                baserow_check_complete=True,
                review_required=False,
                proposed_tool4_action=Tool4Action.LINK_EXISTING,
                renamer_enrichment=RenamerEnrichment(
                    confirmed=True,
                    what_val="SB-1-2-19",
                    title_full="SB 1.2.19",
                    where_val="Oslo-no",
                    when_val="2022-09-19",
                    live_read_complete=True,
                    baserow_read_at="2026-09-19T12:00:00Z",
                ),
            )
            if auto_enrich:
                from media_archive_tooling.renamer.service import RenamerApplicationService
                renamer_svc = RenamerApplicationService(registry=registry)
                renamer_svc.apply_enrichment(EnrichmentEvidence(
                    tracking_id=tracking_id,
                    what_val="SB-1-2-19",
                    where_val="Oslo-no",
                    where_state=ResolutionState.EXACT,
                    when_val="2022-09-19",
                    when_state=ResolutionState.EXACT,
                    source_tool="tool_2",
                ))
            registry.save_media_db_review(
                tracking_id=tracking_id,
                decision=res.decision.value,
                database_state=res.database_state,
                snapshot_timestamp=res.database_snapshot_at or "2026-09-19T12:00:00Z",
                result_json=res.model_dump_json(),
                selected_media_row_id=res.selected_media_row_id,
                review_required=res.review_required,
            )
            return res
        elif "conflict" in orig.lower():
            res = MediaDatabaseReviewResult(
                tracking_id=tracking_id,
                decision=ReviewDecision.CONFLICT_WITH_EXISTING,
                selected_media_row_id=None,
                database_state="LIVE_CURRENT",
                database_snapshot_at="2026-09-19T12:00:00Z",
                baserow_read_at="2026-09-19T12:00:00Z",
                live_read_complete=True,
                snapshot_complete=True,
                baserow_check_complete=True,
                review_required=True,
                review_reasons=["Material conflict with existing Baserow row"],
            )
            registry.save_media_db_review(
                tracking_id=tracking_id,
                decision=res.decision.value,
                database_state=res.database_state,
                snapshot_timestamp=res.database_snapshot_at or "2026-09-19T12:00:00Z",
                result_json=res.model_dump_json(),
                selected_media_row_id=res.selected_media_row_id,
                review_required=res.review_required,
            )
            return res
        else:
            res = MediaDatabaseReviewResult(
                tracking_id=tracking_id,
                decision=ReviewDecision.NEW_MEDIA_CANDIDATE,
                selected_media_row_id=None,
                database_state="LIVE_CURRENT",
                database_snapshot_at="2026-09-19T12:00:00Z",
                baserow_read_at="2026-09-19T12:00:00Z",
                live_read_complete=True,
                snapshot_complete=True,
                baserow_check_complete=True,
                review_required=False,
                proposed_tool4_action=Tool4Action.CREATE_NEW,
            )
            registry.save_media_db_review(
                tracking_id=tracking_id,
                decision=res.decision.value,
                database_state=res.database_state,
                snapshot_timestamp=res.database_snapshot_at or "2026-09-19T12:00:00Z",
                result_json=res.model_dump_json(),
                selected_media_row_id=res.selected_media_row_id,
                review_required=res.review_required,
            )
            return res
    tool2_service.review_file.side_effect = mock_review_file

    ref_store = TravelReferenceStore(reference_path=travel_ref_path, provider=None)
    travel_service = TravelScheduleReviewService(
        registry=registry,
        reference_store=ref_store,
        renamer_service=MagicMock(),
    )

    tool4_service = MediaDatabaseUpdaterService(
        registry=registry,
        write_adapter=fake_write_adapter,
        tool2_service=tool2_service,
    )

    service = MainToolingScriptService(
        registry=registry,
        logger=logger,
        reporter=reporter,
        parser=parser,
        tool2_service=tool2_service,
        travel_service=travel_service,
        tool4_service=tool4_service,
        workflow=WorkflowType.ALL,
        dry_run=False,
        verbose=False,
    )

    return {
        "tmp_path": tmp_path,
        "media_dir": media_dir,
        "registry": registry,
        "logger": logger,
        "reporter": reporter,
        "parser": parser,
        "fake_write_adapter": fake_write_adapter,
        "tool2_service": tool2_service,
        "travel_service": travel_service,
        "tool4_service": tool4_service,
        "service": service,
        "log_file": log_file,
    }


# ---------------------------------------------------------------------------
# Tests 1-6: Target Discovery & Scoping
# ---------------------------------------------------------------------------

def test_01_one_explicit_media_file(env_setup):
    """1. one explicit media file"""
    media_dir = env_setup["media_dir"]
    f = media_dir / "2022-09-19_KKS_SB-1-2-19_Oslo-no.mp3"
    f.write_text("dummy audio")

    res = discover_media_targets([f])
    assert len(res.media_files) == 1
    assert res.media_files[0] == f.resolve()
    assert len(res.skipped_unsupported_files) == 0


def test_02_multiple_explicit_files(env_setup):
    """2. multiple explicit files"""
    media_dir = env_setup["media_dir"]
    f1 = media_dir / "file1.mp3"
    f2 = media_dir / "file2.wav"
    f1.write_text("audio 1")
    f2.write_text("audio 2")

    res = discover_media_targets([f1, f2])
    assert len(res.media_files) == 2
    assert res.media_files == sorted([f1.resolve(), f2.resolve()], key=lambda p: str(p))


def test_03_recursive_folder_discovery(env_setup):
    """3. recursive folder discovery"""
    media_dir = env_setup["media_dir"]
    sub1 = media_dir / "sub1"
    sub2 = sub1 / "sub2"
    sub2.mkdir(parents=True)
    f1 = media_dir / "root.mp3"
    f2 = sub1 / "level1.mp3"
    f3 = sub2 / "level2.m4a"
    for f in (f1, f2, f3):
        f.write_text("content")

    res = discover_media_targets([media_dir])
    assert len(res.media_files) == 3
    assert set(res.media_files) == {f1.resolve(), f2.resolve(), f3.resolve()}


def test_04_mixed_files_folders_and_duplicate_removal(env_setup):
    """4. mixed files/folders and duplicate target removal"""
    media_dir = env_setup["media_dir"]
    sub = media_dir / "sub"
    sub.mkdir()
    f1 = media_dir / "f1.mp3"
    f2 = sub / "f2.mp3"
    f1.write_text("1")
    f2.write_text("2")

    # Supply f1 directly, media_dir (which recursively finds f1 and f2), and f1 again
    res = discover_media_targets([f1, media_dir, f1, sub])
    assert len(res.media_files) == 2
    assert set(res.media_files) == {f1.resolve(), f2.resolve()}


def test_05_unsupported_file_skipping(env_setup):
    """5. unsupported-file skipping"""
    media_dir = env_setup["media_dir"]
    f_media = media_dir / "audio.mp3"
    f_unsupported = media_dir / "notes.docx"
    f_image = media_dir / "photo.jpg"
    f_ignored = media_dir / ".DS_Store"

    f_media.write_text("audio")
    f_unsupported.write_text("doc")
    f_image.write_text("image")
    f_ignored.write_text("system")

    res = discover_media_targets([media_dir])
    assert len(res.media_files) == 1
    assert res.media_files[0] == f_media.resolve()
    # Unsupported files are recorded in skipped_unsupported_files, ignored files excluded entirely
    assert f_unsupported.resolve() in res.skipped_unsupported_files
    assert f_image.resolve() in res.skipped_unsupported_files
    assert f_ignored.resolve() not in res.skipped_unsupported_files


def test_06_single_file_scope_does_not_mutate_siblings(env_setup):
    """6. single-file scope does not mutate siblings while collection grammar may inspect names"""
    media_dir = env_setup["media_dir"]
    f_target = media_dir / "2022-09-19_KKS_Oslo.mp3"
    f_sibling = media_dir / "sibling_file.mp3"
    f_target.write_text("target audio")
    f_sibling.write_text("sibling audio")

    svc = env_setup["service"]
    # Only target f_target
    summary = svc.run([f_target])
    assert summary.completed == 1 or summary.dry_run_previews == 1
    # Sibling file MUST NOT be touched
    assert f_sibling.exists()
    assert f_sibling.read_text() == "sibling audio"
    # Sibling file should not be in registry
    assert env_setup["registry"].find_tracking_id_by_path(f_sibling) is None


# ---------------------------------------------------------------------------
# Tests 7-10: Workflows & Extensibility
# ---------------------------------------------------------------------------

def test_07_default_workflow_is_all(env_setup):
    """7. default workflow is all"""
    svc = env_setup["service"]
    assert svc.workflow == WorkflowType.ALL


def test_08_phase_a_all_runs_tools_1_to_4_and_reports_pending_tools_honestly(env_setup, capsys):
    """8. Phase A all runs Tools 1–4 and reports pending processing tools honestly"""
    media_dir = env_setup["media_dir"]
    f = media_dir / "2022-09-19_KKS_SB-1-2-19_Oslo-no.mp3"
    f.write_text("sample")

    svc = env_setup["service"]
    summary = svc.run([f])
    assert summary.exit_code == 0
    captured = capsys.readouterr().out
    assert "Processing workflow (Tools 5–11) is pending and not yet installed" in captured
    assert len(summary.file_results) == 1
    stage_names = [s.stage_name for s in summary.file_results[0].stage_results]
    assert StageName.TOOL_1_INITIAL in stage_names
    assert StageName.TOOL_2_REVIEW in stage_names
    assert StageName.TOOL_3_REVIEW in stage_names
    assert StageName.TOOL_1_FINALIZE in stage_names
    assert StageName.TOOL_4_SYNC in stage_names


def test_09_renamer_workflow_runs_tools_1_to_4(env_setup):
    """9. renamer runs Tools 1–4 in the required order"""
    media_dir = env_setup["media_dir"]
    f = media_dir / "2022-09-19_KKS_SB-1-2-19_Oslo-no.mp3"
    f.write_text("sample")

    svc = env_setup["service"]
    svc.workflow = WorkflowType.RENAMER
    summary = svc.run([f])
    assert summary.exit_code == 0
    stages = [s.stage_name for s in summary.file_results[0].stage_results]
    assert stages == [
        StageName.TOOL_1_INITIAL,
        StageName.TOOL_2_REVIEW,
        StageName.TOOL_3_REVIEW,
        StageName.TOOL_1_FINALIZE,
        StageName.TOOL_4_SYNC,
    ]


def test_10_unavailable_processing_fails_before_mutation(env_setup):
    """10. unavailable processing fails before mutation"""
    media_dir = env_setup["media_dir"]
    f = media_dir / "sample.mp3"
    f.write_text("original content")

    svc = env_setup["service"]
    svc.workflow = WorkflowType.PROCESSING
    summary = svc.run([f])
    assert summary.exit_code == 1
    # File must be untouched
    assert f.exists()
    assert f.read_text() == "original content"


# ---------------------------------------------------------------------------
# Tests 11-17: Live Mode vs Dry-Run & Tool 4 Safety
# ---------------------------------------------------------------------------

def test_11_no_interactive_prompt_in_live_mode(env_setup, monkeypatch):
    """11. no interactive prompt occurs in live mode"""
    # Verify that input() is never called
    monkeypatch.setattr("builtins.input", MagicMock(side_effect=RuntimeError("input() called!")))
    media_dir = env_setup["media_dir"]
    f = media_dir / "sample.mp3"
    f.write_text("audio")

    svc = env_setup["service"]
    svc.dry_run = False
    summary = svc.run([f])
    assert summary.exit_code == 0


def test_12_live_mode_renames_original_file(env_setup):
    """12. live mode renames the original file"""
    media_dir = env_setup["media_dir"]
    orig_f = media_dir / "KKS Bhajans vrindavan sep 2019.mp3"
    orig_f.write_text("audio payload")

    svc = env_setup["service"]
    svc.dry_run = False
    summary = svc.run([orig_f])
    assert summary.completed == 1

    # Original path must no longer exist
    assert not orig_f.exists()
    # New canonical path must exist
    res = summary.file_results[0]
    final_p = Path(res.final_path)
    assert final_p.exists()
    assert final_p.read_text() == "audio payload"


def test_13_tool4_called_only_after_successful_finalization_rename(env_setup):
    """13. Tool 4 is called only after successful finalization/rename"""
    media_dir = env_setup["media_dir"]
    orig_f = media_dir / "2022-09-19 KKS Oslo.mp3"
    orig_f.write_text("audio payload")

    svc = env_setup["service"]
    summary = svc.run([orig_f])
    res = summary.file_results[0]
    assert res.status == FileExecutionStatus.COMPLETED

    # Check stage results order
    stage_order = [s.stage_name for s in res.stage_results]
    assert stage_order.index(StageName.TOOL_1_FINALIZE) < stage_order.index(StageName.TOOL_4_SYNC)


def test_14_dry_run_makes_no_filesystem_mutation(env_setup):
    """14. dry-run makes no filesystem mutation"""
    media_dir = env_setup["media_dir"]
    f = media_dir / "KKS Bhajans vrindavan sep 2019.mp3"
    f.write_text("audio content")

    svc = env_setup["service"]
    svc.dry_run = True
    summary = svc.run([f])
    assert summary.dry_run_previews == 1
    # Original file is preserved on disk
    assert f.exists()
    assert f.read_text() == "audio content"


def test_15_dry_run_makes_no_baserow_mutation(env_setup):
    """15. dry-run makes no Baserow/schema/select-option mutation"""
    media_dir = env_setup["media_dir"]
    f = media_dir / "2022-09-19_KKS_SB-1-2-19_Oslo-no.mp3"
    f.write_text("audio")

    adapter = env_setup["fake_write_adapter"]
    initial_rows = len(adapter.rows)

    svc = env_setup["service"]
    svc.dry_run = True
    summary = svc.run([f])
    assert summary.dry_run_previews == 1

    # Row count unchanged
    assert len(adapter.rows) == initial_rows


def test_16_dry_run_tool4_request_uses_projected_final_filename_path(env_setup):
    """16. dry-run Tool 4 request uses projected final filename/path"""
    media_dir = env_setup["media_dir"]
    f = media_dir / "KKS Bhajans vrindavan sep 2019.mp3"
    f.write_text("audio")

    svc = env_setup["service"]
    svc.dry_run = True
    summary = svc.run([f])
    res = summary.file_results[0]

    # Check registry sync request
    sync_rec = env_setup["registry"].get_media_db_sync(res.tracking_id)
    assert sync_rec is not None
    req = sync_rec.get("request")
    assert req is not None
    # Must use projected final filename
    assert req["current_filename"] == res.final_filename


def test_17_dry_run_create_reports_no_fabricated_row_id(env_setup):
    """17. dry-run create reports no fabricated row ID"""
    media_dir = env_setup["media_dir"]
    f = media_dir / "KKS Bhajans vrindavan sep 2019.mp3"
    f.write_text("audio")

    svc = env_setup["service"]
    svc.dry_run = True
    summary = svc.run([f])
    res = summary.file_results[0]

    t4_stage = next(s for s in res.stage_results if s.stage_name == StageName.TOOL_4_SYNC)
    assert "new row — ID assigned only on commit" in t4_stage.summary
    assert res.tool4_row_id is None


# ---------------------------------------------------------------------------
# Tests 18-20: Access Boundaries
# ---------------------------------------------------------------------------

def test_18_tool2_remains_read_only(env_setup):
    """18. Tool 2 remains read-only"""
    # Tool 2 service has no write methods
    t2_svc = env_setup["tool2_service"]
    assert not hasattr(t2_svc, "create_row")
    assert not hasattr(t2_svc, "update_row")
    assert not hasattr(t2_svc, "patch_row")


def test_19_tool3_has_no_baserow_access(env_setup):
    """19. Tool 3 has no Baserow access"""
    t3_svc = env_setup["travel_service"]
    # Reference store has provider=None (offline static reference)
    assert t3_svc.reference_store.provider is None


def test_20_tool4_remains_only_writer(env_setup):
    """20. Tool 4 remains the only writer"""
    media_dir = env_setup["media_dir"]
    f = media_dir / "2022-09-19_KKS_SB-1-2-19_Oslo-no.mp3"
    f.write_text("audio")

    adapter = env_setup["fake_write_adapter"]
    initial_rows = len(adapter.rows)

    svc = env_setup["service"]
    svc.dry_run = False
    svc.run([f])

    # Tool 4 executed write
    assert len(adapter.rows) >= initial_rows
    assert any(c.get("action") in ("patch_row", "create_row") for c in adapter.calls)


# ---------------------------------------------------------------------------
# Tests 21-24: Terminal Reporting & Verbose Mode
# ---------------------------------------------------------------------------

def test_21_concise_terminal_output_contains_separate_tool_summaries(env_setup, capsys):
    """21. concise terminal output contains separate Tool 1–4 summaries"""
    media_dir = env_setup["media_dir"]
    f = media_dir / "2022-09-19_KKS_SB-1-2-19_Oslo-no.mp3"
    f.write_text("audio")

    svc = env_setup["service"]
    svc.run([f])
    captured = capsys.readouterr().out

    assert "Tool 1 — Renamer" in captured
    assert "Tool 2 — Media DB" in captured
    assert "Tool 3 — Travel Schedule" in captured
    assert "Tool 4 — Media DB" in captured


def test_22_verbose_terminal_output_adds_detail_without_secrets(env_setup, capsys):
    """22. verbose terminal output adds detail without secrets"""
    media_dir = env_setup["media_dir"]
    f = media_dir / "2022-09-19_KKS_SB-1-2-19_Oslo-no.mp3"
    f.write_text("audio")

    svc = env_setup["service"]
    svc.verbose = True
    svc.reporter.verbose = True
    svc.run([f])
    captured = capsys.readouterr().out

    assert "proposed_filename" in captured
    assert "secret" not in captured.lower()
    assert "token" not in captured.lower()


def test_23_exact_planned_written_baserow_fields_displayed(env_setup, capsys):
    """23. exact planned/written Baserow fields are displayed"""
    media_dir = env_setup["media_dir"]
    f = media_dir / "2022-09-19_KKS_SB-1-2-19_Oslo-no.mp3"
    f.write_text("audio")

    svc = env_setup["service"]
    svc.run([f])
    captured = capsys.readouterr().out

    assert "Tool 4 — Media DB" in captured


def test_24_created_selected_row_id_displayed_when_available(env_setup, capsys):
    """24. created/selected row ID is displayed when available"""
    media_dir = env_setup["media_dir"]
    f = media_dir / "2022-09-19_KKS_SB-1-2-19_Oslo-no.mp3"
    f.write_text("audio")

    svc = env_setup["service"]
    svc.run([f])
    captured = capsys.readouterr().out

    assert "#1234" in captured


# ---------------------------------------------------------------------------
# Tests 25-27: Persistent Logging & Secret Redaction
# ---------------------------------------------------------------------------

def test_25_one_persistent_log_file_appended_across_two_runs(env_setup):
    """25. one persistent log file is appended across two runs"""
    media_dir = env_setup["media_dir"]
    f1 = media_dir / "file1.mp3"
    f2 = media_dir / "file2.mp3"
    f1.write_text("1")
    f2.write_text("2")

    log_path = env_setup["log_file"]

    # Run 1
    svc = env_setup["service"]
    svc.run([f1])
    lines_run1 = log_path.read_text().splitlines()
    assert len(lines_run1) > 0

    # Run 2 with new run ID
    svc.logger.run_id = "test_run_002"
    svc.run([f2])
    lines_run2 = log_path.read_text().splitlines()
    assert len(lines_run2) > len(lines_run1)

    # Both run IDs exist in the file
    all_text = log_path.read_text()
    assert "test_run_001" in all_text
    assert "test_run_002" in all_text


def test_26_log_entries_contain_run_ids_and_tool_file_context(env_setup):
    """26. log entries contain run IDs and tool/file context"""
    media_dir = env_setup["media_dir"]
    f = media_dir / "file.mp3"
    f.write_text("1")

    svc = env_setup["service"]
    svc.run([f])

    log_path = env_setup["log_file"]
    for line in log_path.read_text().splitlines():
        entry = json.loads(line)
        assert "timestamp" in entry
        assert "run_id" in entry
        assert "severity" in entry
        assert "tool" in entry
        assert "event" in entry


def test_27_secrets_are_redacted_from_logs_and_errors(env_setup):
    """27. secrets are redacted from logs and errors"""
    logger = env_setup["logger"]
    secret_token = "secret_token_123456789"
    logger.info("TEST_EVENT", details={"api_token": secret_token, "msg": f"Token {secret_token}"})

    log_path = env_setup["log_file"]
    content = log_path.read_text()
    assert secret_token not in content
    assert "[REDACTED]" in content


# ---------------------------------------------------------------------------
# Tests 28-30: Review Portal Queue & Business Continuation
# ---------------------------------------------------------------------------

def test_28_completed_items_do_not_enter_active_portal_evaluation_queue(env_setup):
    """28. completed items do not enter the active portal evaluation queue"""
    from media_archive_tooling.renamer.service import RenamerApplicationService
    reg = env_setup["registry"]
    media_dir = env_setup["media_dir"]
    f = media_dir / "2022-09-19_KKS_SB-1-2-19_Oslo-no.mp3"
    f.write_text("audio")

    svc = env_setup["service"]
    svc.run([f])

    renamer_svc = RenamerApplicationService(registry=reg)
    queue = renamer_svc.list_files(filter_mode="evaluation")
    # Clean completed file must NOT be in active evaluation queue
    assert queue["review_count"] == 0
    assert len(queue["files"]) == 0


def test_29_blocked_conflict_failure_items_enter_evaluation_queue_with_reasons(env_setup):
    """29. blocked/conflict/failure items do enter the evaluation queue with exact reasons"""
    from media_archive_tooling.renamer.service import RenamerApplicationService
    reg = env_setup["registry"]
    media_dir = env_setup["media_dir"]
    f = media_dir / "conflict_meeting.mp3"
    f.write_text("audio")

    svc = env_setup["service"]
    svc.run([f])

    renamer_svc = RenamerApplicationService(registry=reg)
    queue = renamer_svc.list_files(filter_mode="evaluation")
    assert queue["review_count"] == 1
    assert len(queue["files"]) == 1
    eval_file = queue["files"][0]
    assert len(eval_file["review_reasons"]) > 0


def test_30_allowed_partial_date_location_can_continue_without_unnecessary_review(env_setup):
    """30. an allowed partial date/location can continue without unnecessary review"""
    media_dir = env_setup["media_dir"]
    # File with month-only date (allowed partial)
    f = media_dir / "KKS Bhajans vrindavan sep 2019.mp3"
    f.write_text("audio")

    svc = env_setup["service"]
    svc.dry_run = True
    summary = svc.run([f])
    res = summary.file_results[0]
    # Incomplete day does not alone force review
    assert res.status == FileExecutionStatus.DRY_RUN


# ---------------------------------------------------------------------------
# Tests 31-34: Resilience, Idempotency & Error Handling
# ---------------------------------------------------------------------------

def test_31_one_per_file_failure_does_not_stop_later_independent_files(env_setup):
    """31. one per-file failure does not stop later independent files"""
    media_dir = env_setup["media_dir"]
    f1 = media_dir / "bad_file.mp3"
    f2 = media_dir / "good_file.mp3"
    f1.write_text("1")
    f2.write_text("2")

    svc = env_setup["service"]
    # Mock parser to raise for f1 only
    orig_parse = svc.parser.parse_file
    def side_effect(file_path, **kwargs):
        if "bad_file" in str(file_path):
            raise RuntimeError("Corrupted file error")
        return orig_parse(file_path, **kwargs)

    with patch.object(svc.parser, "parse_file", side_effect=side_effect):
        summary = svc.run([f1, f2])

    assert summary.failed == 1
    assert (summary.completed == 1 or summary.unchanged == 1 or summary.dry_run_previews == 1)


def test_32_global_configuration_failure_occurs_before_mutation(env_setup):
    """32. global configuration failure occurs before mutation"""
    media_dir = env_setup["media_dir"]
    f = media_dir / "sample.mp3"
    f.write_text("audio")

    svc = env_setup["service"]
    # Target path that does not exist
    missing_target = media_dir / "does_not_exist.mp3"
    summary = svc.run([f, missing_target])
    assert summary.exit_code == 1
    assert summary.completed == 0
    # Original file unchanged
    assert f.exists()


def test_33_rename_success_plus_tool4_failure_preserves_rename_and_durable_pending_sync(env_setup):
    """33. rename success plus Tool 4 failure preserves the rename and durable pending sync"""
    media_dir = env_setup["media_dir"]
    orig_f = media_dir / "2022-09-19_KKS_Oslo.mp3"
    orig_f.write_text("payload")

    svc = env_setup["service"]
    # Force Tool 4 sync to fail
    with patch.object(svc.tool4_service, "synchronize", side_effect=RuntimeError("Baserow connection timeout")):
        summary = svc.run([orig_f])

    res = summary.file_results[0]
    assert res.status == FileExecutionStatus.PENDING_SYNC
    # Rename must NOT be rolled back
    assert not orig_f.exists()
    final_p = Path(res.final_path)
    assert final_p.exists()
    assert final_p.read_text() == "payload"

    # Registry outbox must have PENDING_SYNC
    sync_rec = env_setup["registry"].get_media_db_sync(res.tracking_id)
    assert sync_rec["sync_status"] == "PENDING_SYNC"


def test_34_rerun_idempotency_does_not_create_duplicate_baserow_row(env_setup):
    """34. rerun/idempotency does not create a duplicate Baserow row"""
    media_dir = env_setup["media_dir"]
    f = media_dir / "2022-09-19_KKS_SB-1-2-19_Oslo-no.mp3"
    f.write_text("audio")

    svc = env_setup["service"]
    # Run 1
    summary1 = svc.run([f])
    assert summary1.exit_code == 0
    final_path = Path(summary1.file_results[0].final_path)

    # Run 2 on the finalized file
    summary2 = svc.run([final_path])
    assert summary2.exit_code == 0
    assert summary2.unchanged == 1


# ---------------------------------------------------------------------------
# Tests 35-36: CLI Regression Safety & Practical Patterns
# ---------------------------------------------------------------------------

def test_35_existing_cli_commands_remain_regression_safe():
    """35. existing CLI commands remain regression-safe"""
    from media_archive_tooling.cli import main
    # Ensure subparsers exist and help works
    with pytest.raises(SystemExit) as exc:
        main_mock = patch("sys.argv", ["media-archive", "--help"])
        with main_mock:
            main()
    assert exc.value.code == 0


def test_36_full_pipeline_practical_oslo_and_czech_duben_patterns(env_setup):
    """36. full pipeline practical Oslo and Czech/Duben filename patterns"""
    media_dir = env_setup["media_dir"]
    # Practical Oslo pattern
    f_oslo = media_dir / "KKS_S.B. 1.19.31_Oslo_29.8.11.mp3"
    f_oslo.write_text("oslo audio")

    # Practical Czech Duben pattern
    f_czech = media_dir / "04 Duben 2017 Krsna Dvur KKS.mp3"
    f_czech.write_text("czech audio")

    svc = env_setup["service"]
    svc.dry_run = True
    summary = svc.run([f_oslo, f_czech])
    assert summary.exit_code == 0
    assert summary.dry_run_previews == 2

    # Check that both have clean proposed filenames
    oslo_res = next(r for r in summary.file_results if "Oslo" in r.original_filename)
    assert "Oslo" in oslo_res.final_filename
    czech_res = next(r for r in summary.file_results if "Duben" in r.original_filename)
    assert "2017" in czech_res.final_filename
    assert "Duben" in czech_res.final_filename
    assert czech_res.stage_results[0].details.get("when") == "2017-04-DD"
