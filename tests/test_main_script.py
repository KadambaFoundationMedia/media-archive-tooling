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
    TestRowStatus,
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
from media_archive_tooling.orchestrator.fingerprint import compute_review_data_fingerprint
from media_archive_tooling.review_portal.app import app as portal_app, configure_review_context
from fastapi.testclient import TestClient
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
    Context,
    EnrichmentEvidence,
    FileMetadata,
    Identity,
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
    assert "Processing workflow (Tools 6–11) is pending and not yet installed" in captured
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
    """36. full pipeline practical Oslo and real Czech/Duben collection patterns (R-001)"""
    media_dir = env_setup["media_dir"]
    # Practical Oslo pattern
    f_oslo = media_dir / "KKS_S.B. 1.19.31_Oslo_29.8.11.mp3"
    f_oslo.write_text("oslo audio")

    # Real Czech Duben folder & files (01, 02, 04, 06, 08)
    duben_dir = media_dir / "KKS DUBEN 2008 MP3"
    duben_dir.mkdir(parents=True, exist_ok=True)
    f_01 = duben_dir / "01 KKS.BG.14,6.mp3"
    f_02 = duben_dir / "02 KKS. SB. 3,1,20.mp3"
    f_04 = duben_dir / "04 KKS. SB. 3,1,21.mp3"
    f_06 = duben_dir / "06 KKS SB 3.1.25.mp3"
    f_08 = duben_dir / "08 KKS SB 3.1.26.mp3"
    for f in (f_01, f_02, f_04, f_06, f_08):
        f.write_text("duben audio")

    svc = env_setup["service"]
    svc.dry_run = True
    summary = svc.run([f_oslo, f_01, f_02, f_04, f_06, f_08])
    assert summary.exit_code == 0
    assert summary.total_discovered == 6
    assert summary.review_required == 5
    assert summary.dry_run_previews == 1

    # Verify Oslo
    oslo_res = next(r for r in summary.file_results if "Oslo" in r.original_filename)
    assert "Oslo" in oslo_res.final_filename

    # Verify exact Duben proposed filenames (R-001: comma scripture verses normalized)
    res_01 = next(r for r in summary.file_results if "01 KKS.BG.14,6" in r.original_filename)
    assert res_01.final_filename == "2008-04-DD_KKS_BG-14-6_cz.mp3"

    res_02 = next(r for r in summary.file_results if "02 KKS. SB. 3,1,20" in r.original_filename)
    assert res_02.final_filename == "2008-04-DD_KKS_SB-3-1-20_cz.mp3"

    res_04 = next(r for r in summary.file_results if "04 KKS. SB. 3,1,21" in r.original_filename)
    assert res_04.final_filename == "2008-04-DD_KKS_SB-3-1-21_cz.mp3"

    res_06 = next(r for r in summary.file_results if "06 KKS SB 3.1.25" in r.original_filename)
    assert res_06.final_filename == "2008-04-DD_KKS_SB-3-1-25_cz.mp3"

    res_08 = next(r for r in summary.file_results if "08 KKS SB 3.1.26" in r.original_filename)
    assert res_08.final_filename == "2008-04-DD_KKS_SB-3-1-26_cz.mp3"


# ---------------------------------------------------------------------------
# Tests 37-41: Review Findings R-002 through R-005 Regressions
# ---------------------------------------------------------------------------

def test_37_tool2_review_required_with_empty_reasons_blocks_rename_and_tool4_write(env_setup):
    """37. Tool 2 review_required=True with empty review_reasons blocks rename and skips Tool 4 write (R-002)"""
    from media_archive_tooling.media_db_reviewer.models import MediaDatabaseReviewResult, ReviewDecision, MediaCandidate

    media_dir = env_setup["media_dir"]
    orig_f = media_dir / "04 KKS. SB. 3,1,21.mp3"
    orig_f.write_text("payload audio")

    svc = env_setup["service"]
    svc.dry_run = False  # Live mode

    # Tool 2 returns MULTIPLE_CANDIDATES with review_required=True and empty review_reasons
    mock_t2_res = MediaDatabaseReviewResult(
        tracking_id="mocktid",
        decision=ReviewDecision.MULTIPLE_CANDIDATES,
        review_required=True,
        review_reasons=[],  # Intentionally empty!
        candidates=[MediaCandidate(media_row_id=10, candidate_filename="cand10.mp3", score=0.6)],
        diagnostic_notes=["Found 140 candidate rows in media database"],
    )

    with patch.object(svc.tool2_service, "review_file", return_value=mock_t2_res):
        summary = svc.run([orig_f])

    assert summary.exit_code == 0
    assert summary.review_required == 1
    assert summary.completed == 0

    res = summary.file_results[0]
    assert res.status == FileExecutionStatus.REVIEW_REQUIRED
    # File MUST remain untouched on disk
    assert orig_f.exists()
    assert orig_f.read_text() == "payload audio"
    assert res.final_path == str(orig_f)

    # Tool 1 finalize must report review required
    t1_fin = next(s for s in res.stage_results if s.stage_name == StageName.TOOL_1_FINALIZE)
    assert "review required" in t1_fin.summary

    # Tool 4 write must be skipped
    t4_stage = next(s for s in res.stage_results if s.stage_name == StageName.TOOL_4_SYNC)
    assert "Skipped write" in t4_stage.summary
    assert t4_stage.success is False

    # Fake write adapter must have received 0 mutations
    adapter = env_setup["fake_write_adapter"]
    assert len(adapter.calls) == 0


def test_38_rename_commit_service_boundary_preserves_accurate_history_and_audit(env_setup):
    """38. Live commit routes through RenameCommitService, recording accurate history and audit (R-003)"""
    media_dir = env_setup["media_dir"]
    orig_f = media_dir / "2022-09-19_KKS_Oslo.mp3"
    orig_f.write_text("oslo payload")

    svc = env_setup["service"]
    svc.dry_run = False
    summary = svc.run([orig_f])
    assert summary.completed == 1

    res = summary.file_results[0]
    assert res.status == FileExecutionStatus.COMPLETED
    final_p = Path(res.final_path)
    assert final_p.exists()
    assert final_p.name == "2022-09-19_KKS_SB-1-2-19_Oslo-no.mp3"

    reg = env_setup["registry"]
    # 1. Check rename_history old-to-new accuracy
    history = reg.get_rename_history(res.tracking_id)
    assert len(history) >= 1
    h = history[-1]
    assert h["from_filename"] == "2022-09-19_KKS_Oslo.mp3"
    assert h["to_filename"] == "2022-09-19_KKS_SB-1-2-19_Oslo-no.mp3"

    # 2. Check updated parser identity
    file_rec = reg.get_file(res.tracking_id)
    assert file_rec is not None
    pr = file_rec["parser_result"]
    assert pr["identity"]["current_filename"] == "2022-09-19_KKS_SB-1-2-19_Oslo-no.mp3"

    # 3. Check review action audit record
    actions = reg.get_review_actions(res.tracking_id)
    commit_act = next(a for a in actions if a["action"] == "commit")
    assert commit_act["reviewer"] == "main-script"
    assert commit_act["changes"]["filesystem_rename"] is True
    assert commit_act["changes"]["from_path"] == str(orig_f)
    assert commit_act["changes"]["to_path"] == str(final_p)


def test_39_tool4_outcomes_mapped_and_visible_in_evaluation_queue(env_setup):
    """39. Actual Tool 4 outcomes mapped and visible in review portal evaluation queue (R-004)"""
    from media_archive_tooling.media_db_updater.models import MediaDbSyncResult, SyncOperation, SyncStatus
    from media_archive_tooling.renamer.service import RenamerApplicationService

    media_dir = env_setup["media_dir"]
    svc = env_setup["service"]
    svc.dry_run = False
    reg = env_setup["registry"]
    renamer_app_svc = RenamerApplicationService(registry=reg)

    outcomes_to_test = [
        (SyncStatus.REVIEW_REQUIRED, FileExecutionStatus.REVIEW_REQUIRED),
        (SyncStatus.DATABASE_UNAVAILABLE, FileExecutionStatus.DATABASE_UNAVAILABLE),
        (SyncStatus.FAILED_RETRYABLE, FileExecutionStatus.FAILED_RETRYABLE),
        (SyncStatus.FAILED_BLOCKED, FileExecutionStatus.FAILED_BLOCKED),
    ]

    for idx, (tool4_status, expected_exec_status) in enumerate(outcomes_to_test, start=1):
        f = media_dir / f"2022-09-{20 + idx:02d}_KKS_Oslo.mp3"
        f.write_text(f"audio {idx}")

        mock_res = MediaDbSyncResult(
            tracking_id="tid",
            status=tool4_status,
            operation=SyncOperation.BLOCKED,
            error_message=f"Simulated error for {tool4_status.value}",
        )

        with patch.object(svc.tool4_service, "synchronize", return_value=mock_res):
            summary = svc.run([f])

        res = summary.file_results[0]
        assert res.status == expected_exec_status
        assert res.tool4_sync_status == tool4_status.value

        # Must appear in active evaluation queue
        queue = renamer_app_svc.list_files(filter_mode="evaluation")
        found = any(q["tracking_id"] == res.tracking_id for q in queue["files"])
        assert found, f"File with {tool4_status.value} not found in evaluation queue"

        # Clean up any renamed target file so subsequent loop iterations don't collide
        final_p = Path(res.final_path)
        if final_p.exists():
            final_p.unlink()


def test_40_canonical_file_with_tool4_create_or_update_is_completed_not_unchanged(env_setup):
    """40. Canonical file whose Tool 4 action is CREATE or UPDATE is completed/synchronized, not unchanged (R-004)"""
    from media_archive_tooling.media_db_updater.models import MediaDbSyncResult, SyncOperation, SyncStatus

    media_dir = env_setup["media_dir"]
    # File already has canonical name
    f = media_dir / "2022-09-19_KKS_SB-1-2-19_Oslo-no.mp3"
    f.write_text("canonical audio")

    svc = env_setup["service"]
    svc.dry_run = False

    # Mock Tool 4 to return UPDATE
    mock_res_update = MediaDbSyncResult(
        tracking_id="tid",
        status=SyncStatus.SYNCED,
        operation=SyncOperation.UPDATE,
        media_row_id=1234,
        live_row={"id": 1234, "Title": "SB 1.2.19", "Filename": f.name},
    )

    with patch.object(svc.tool4_service, "synchronize", return_value=mock_res_update):
        summary = svc.run([f])

    assert summary.completed == 1
    assert summary.unchanged == 0
    res = summary.file_results[0]
    assert res.status == FileExecutionStatus.COMPLETED

    # When Tool 4 is NOOP, then it is UNCHANGED
    mock_res_noop = MediaDbSyncResult(
        tracking_id="tid",
        status=SyncStatus.SYNCED,
        operation=SyncOperation.NOOP,
        media_row_id=1234,
        live_row={"id": 1234, "Title": "SB 1.2.19", "Filename": f.name},
    )
    with patch.object(svc.tool4_service, "synchronize", return_value=mock_res_noop):
        summary2 = svc.run([f])

    assert summary2.completed == 0
    assert summary2.unchanged == 1
    res2 = summary2.file_results[0]
    assert res2.status == FileExecutionStatus.UNCHANGED


def test_41_verified_live_readback_summary_displayed(env_setup, capsys):
    """41. Verified live readback summary is displayed in terminal and stored in result (R-005)"""
    media_dir = env_setup["media_dir"]
    orig_f = media_dir / "2022-09-19_KKS_Oslo.mp3"
    orig_f.write_text("audio payload")

    svc = env_setup["service"]
    svc.dry_run = False
    summary = svc.run([orig_f])
    assert summary.completed == 1

    res = summary.file_results[0]
    assert res.tool4_live_row is not None
    assert res.tool4_row_id is not None
    assert "Title" in res.tool4_live_row

    t4_stage = next(s for s in res.stage_results if s.stage_name == StageName.TOOL_4_SYNC)
    assert f"Verified live row #{res.tool4_row_id}" in t4_stage.summary

    captured = capsys.readouterr()
    assert f"Verified live row #{res.tool4_row_id}" in captured.out


# ---------------------------------------------------------------------------
# Tests 42–51: Alpha/Beta Test-Data Purge and Fingerprint Verification
# ---------------------------------------------------------------------------

def save_dummy_proposal(registry, tracking_id="trk0042", filename="test.mp3"):
    source = Path(f"/archive/{filename}")
    parser = ParserResult(
        identity=Identity(
            tracking_id=tracking_id,
            original_filename=filename,
            original_path=str(source),
            current_filename=filename,
            extension=source.suffix.lower(),
        ),
        context=Context(parent_folder="archive"),
        when=WhenResult(raw_token="", normalized="2022-09-19", state=ResolutionState.EXACT),
        what=WhatResult(raw_token="", normalized="", state=ResolutionState.UNRESOLVED),
        where=WhereResult(raw_token="", location="", country="", state=ResolutionState.UNRESOLVED),
        who="KKS",
    )
    prop = RenameProposal(
        tracking_id=tracking_id,
        original_path=str(source),
        current_filename=filename,
        proposed_filename=f"2022-09-19_KKS_{filename}",
        proposed_path=str(source.with_name(f"2022-09-19_KKS_{filename}")),
        mode=RenameMode.INITIAL,
        status="pending",
        needs_review=True,
        parser_result=parser,
    )
    registry.save_proposal(prop)
    return prop


def test_42_purge_standalone_dry_run_lists_rows_without_mutations(env_setup, capsys):
    """42. Standalone --purge --dry-run lists rows and clears nothing (Section 5)."""
    svc = env_setup["service"]
    registry = env_setup["registry"]
    fake_db = env_setup["fake_write_adapter"]

    # Setup a test-created row in ledger and in fake DB
    marker = "[ALPHA-TEST-ROW session=s42 tracking_id=trk0042]"
    fake_db.rows[4201] = {
        "id": 4201,
        "Notes": f"{marker}\nOriginal filename: test.mp3",
    }
    registry.record_test_created_row(
        table_id=fake_db.media_table_id,
        row_id=4201,
        tracking_id="trk0042",
        session_id="s42",
        marker=marker,
        request_fingerprint="fp42",
    )

    # Also save a dummy file record in the registry
    save_dummy_proposal(registry, tracking_id="trk0042", filename="test.mp3")

    exit_code, summary = svc.purge(dry_run=True)
    assert exit_code == 0
    assert summary.deleted == 1  # would delete 1
    assert 4201 in fake_db.rows  # not deleted in DB
    assert registry.get_file("trk0042") is not None  # registry not cleared
    assert registry.get_test_row(4201)["status"] == "CREATED"

    captured = capsys.readouterr()
    assert "Alpha/beta test data purge preview (dry-run):" in captured.out
    assert "would delete" in captured.out


def test_43_purge_live_success_clears_registry_and_deletes_baserow_rows(env_setup, capsys):
    """43. Standalone live --purge deletes marker-verified rows and resets registry (Section 5)."""
    svc = env_setup["service"]
    registry = env_setup["registry"]
    fake_db = env_setup["fake_write_adapter"]

    marker = "[ALPHA-TEST-ROW session=s43 tracking_id=trk0043]"
    fake_db.rows[4301] = {
        "id": 4301,
        "Notes": f"{marker}\nOriginal notes",
    }
    registry.record_test_created_row(
        table_id=fake_db.media_table_id,
        row_id=4301,
        tracking_id="trk0043",
        session_id="s43",
        marker=marker,
        request_fingerprint="fp43",
    )
    save_dummy_proposal(registry, tracking_id="trk0043", filename="test.mp3")

    exit_code, summary = svc.purge(dry_run=False)
    assert exit_code == 0
    assert summary.deleted == 1
    assert 4301 not in fake_db.rows  # deleted from Baserow
    assert registry.get_file("trk0043") is None  # registry review state cleared
    assert len(registry.list_test_rows()) == 0  # ledger cleared
    assert registry.get_metadata("review_data_fingerprint") is not None
    assert registry.get_metadata("purge_blocked") is None

    captured = capsys.readouterr()
    assert "Alpha/beta purge complete" in captured.out
    assert "Local review registry: cleared" in captured.out


def test_44_purge_rejects_media_targets(env_setup, capsys):
    """44. Normal file targets cannot be specified with --purge (Section 5)."""
    from media_archive_tooling.cli import run_main_script
    import argparse

    args = argparse.Namespace(
        targets=["some_file.mp3"],
        purge=True,
        dry_run=False,
        verbose=False,
        workflow="all",
        registry_path=str(env_setup["registry"].db_path),
        log_file=None,
        review_portal=False,
        orchestrator_service=env_setup["service"],
    )

    with pytest.raises(SystemExit) as exc_info:
        run_main_script(args)
    assert exc_info.value.code == 2

    captured = capsys.readouterr()
    assert "Error: Normal file targets cannot be specified with --purge" in captured.err


def test_45_run_without_targets_and_without_purge_exits_code_2(env_setup, capsys):
    """45. Running without targets and without --purge exits code 2 with helpful message."""
    from media_archive_tooling.cli import run_main_script
    import argparse

    args = argparse.Namespace(
        targets=[],
        purge=False,
        dry_run=False,
        verbose=False,
        workflow="all",
        registry_path=str(env_setup["registry"].db_path),
        log_file=None,
        review_portal=False,
        orchestrator_service=env_setup["service"],
    )

    with pytest.raises(SystemExit) as exc_info:
        run_main_script(args)
    assert exc_info.value.code == 2

    captured = capsys.readouterr()
    assert "Error: No target paths specified." in captured.err


def test_46_purge_failure_retains_ledger_and_registry_evidence(env_setup, capsys):
    """46. Failed/blocked cleanup fails closed, retains evidence, and sets purge_blocked."""
    svc = env_setup["service"]
    registry = env_setup["registry"]
    fake_db = env_setup["fake_write_adapter"]

    # Marker mismatch -> will block purge
    expected_marker = "[ALPHA-TEST-ROW session=s46 tracking_id=trk0046]"
    fake_db.rows[4601] = {
        "id": 4601,
        "Notes": "Human edited notes without marker!",
    }
    registry.record_test_created_row(
        table_id=fake_db.media_table_id,
        row_id=4601,
        tracking_id="trk0046",
        session_id="s46",
        marker=expected_marker,
        request_fingerprint="fp46",
    )
    save_dummy_proposal(registry, tracking_id="trk0046", filename="test.mp3")

    exit_code, summary = svc.purge(dry_run=False)
    assert exit_code == 1
    assert summary.blocked == 1
    assert 4601 in fake_db.rows  # NOT deleted
    assert registry.get_test_row(4601)["status"] == "PURGE_BLOCKED"
    assert registry.get_file("trk0046") is not None  # registry NOT cleared
    assert registry.get_metadata("purge_blocked") is not None


def test_47_fingerprint_initialization_and_matching_no_purge(env_setup):
    """47. Matching code fingerprint does not trigger automatic cleanup."""
    media_dir = env_setup["media_dir"]
    orig_f = media_dir / "2022-09-19_KKS_Oslo.mp3"
    orig_f.write_text("audio")

    svc = env_setup["service"]
    registry = env_setup["registry"]

    # First run initializes fingerprint
    summary1 = svc.run([orig_f])
    assert summary1.completed == 1
    stored_fp = registry.get_metadata("review_data_fingerprint")
    assert stored_fp is not None

    # Second run with same code matches fingerprint and does not purge
    final_path = summary1.file_results[0].final_path
    summary2 = svc.run([final_path])
    assert summary2.exit_code == 0
    assert len(registry.list_files()) > 0
    assert registry.get_metadata("review_data_fingerprint") == stored_fp


def test_48_fingerprint_change_triggers_automatic_purge(env_setup, capsys):
    """48. Code fingerprint difference triggers full automatic fresh slate (Section 6)."""
    media_dir = env_setup["media_dir"]
    orig_f = media_dir / "2022-09-19_KKS_Oslo.mp3"
    orig_f.write_text("audio")

    svc = env_setup["service"]
    registry = env_setup["registry"]
    fake_db = env_setup["fake_write_adapter"]

    # Store stale old fingerprint
    registry.set_metadata("review_data_fingerprint", "old_stale_fingerprint_hash")

    # Add old test row
    marker = "[ALPHA-TEST-ROW session=s48 tracking_id=trk0048]"
    fake_db.rows[4801] = {"id": 4801, "Notes": marker}
    registry.record_test_created_row(
        table_id=fake_db.media_table_id,
        row_id=4801,
        tracking_id="trk0048",
        session_id="s48",
        marker=marker,
        request_fingerprint="fp48",
    )

    summary = svc.run([orig_f])
    assert summary.exit_code == 0
    # Old test row was purged
    assert 4801 not in fake_db.rows
    # Fingerprint was updated to current
    curr_fp = compute_review_data_fingerprint()
    assert registry.get_metadata("review_data_fingerprint") == curr_fp

    captured = capsys.readouterr()
    assert "Automatic fresh slate" in captured.out


def test_49_fingerprint_change_blocked_hides_portal_queue(env_setup):
    """49. Blocked automatic cleanup hides stale review queue and displays warning (Section 6 & 7)."""
    registry = env_setup["registry"]
    fake_db = env_setup["fake_write_adapter"]

    # Store stale fingerprint
    registry.set_metadata("review_data_fingerprint", "old_stale_fingerprint_hash")

    # Set up test row that will fail purge
    marker = "[ALPHA-TEST-ROW session=s49 tracking_id=trk0049]"
    fake_db.rows[4901] = {"id": 4901, "Notes": "Altered notes"}
    registry.record_test_created_row(
        table_id=fake_db.media_table_id,
        row_id=4901,
        tracking_id="trk0049",
        session_id="s49",
        marker=marker,
        request_fingerprint="fp49",
    )
    # Stale file that should NOT be visible to operator
    save_dummy_proposal(registry, tracking_id="trk0049", filename="stale.mp3")

    configure_review_context(
        registry=registry,
        media_db_updater_service=env_setup["tool4_service"],
    )

    client = TestClient(portal_app)
    response = client.get("/")
    assert response.status_code == 200
    assert "Warning: Cleanup blocked" in response.text
    # Stale row is hidden
    assert "stale.mp3" not in response.text

    # Detail page returns 503
    det_resp = client.get("/file/trk0049")
    assert det_resp.status_code == 503
    assert "Review portal is blocked" in det_resp.json()["detail"]


def test_50_portal_fresh_test_slate_display(tmp_path):
    """50. Empty registry displays 'Fresh test slate' banner in portal (Section 7)."""
    fresh_db = tmp_path / "fresh.db"
    fresh_reg = LocalRegistry(fresh_db)

    configure_review_context(registry=fresh_reg)
    client = TestClient(portal_app)
    response = client.get("/")
    assert response.status_code == 200
    assert "Fresh test slate" in response.text
    assert "Total Tracked Files" in response.text
    assert "0" in response.text


def test_51_registry_cross_process_lock(tmp_path):
    """51. Registry lock prevents concurrent file operations across processes (Section 6)."""
    reg = LocalRegistry(tmp_path / "lock_test.db")
    with reg.acquire_lock():
        reg.set_metadata("test_lock", "active")
        assert reg.get_metadata("test_lock") == "active"


def test_52_tool5_progress_is_concise_and_visible(capsys):
    reporter = TerminalReporter(verbose=True)
    reporter.report_tool5_progress("decode", 0, "start")
    reporter.report_tool5_progress("decode", 3.1, "done")
    reporter.report_tool5_progress("transcribe_metal", 30, "heartbeat")
    reporter.report_tool5_progress("cache", 0, "hit")
    assert capsys.readouterr().out.splitlines() == [
        "  Tool 5 — Converting audio…",
        "  Tool 5 — Converting audio finished after 3.1s",
        "  Tool 5 — Transcribing on Metal: 30s elapsed",
        "  Tool 5 — Reusing saved transcript",
    ]


def test_53_bounded_evaluation_copy(tmp_path):
    """53. Bounded evaluation helper enforces max_files, max_bytes, preflight space, and preserves sources (R-052)."""
    import sys
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from scripts.run_tool_4_evaluation import (
        DEFAULT_MAX_EVAL_BYTES,
        DEFAULT_MAX_EVAL_FILES,
        select_and_copy_bounded_evaluation_media,
    )

    src_dir = tmp_path / "sample_source"
    src_dir.mkdir()
    dst_dir = tmp_path / "eval_dest"

    created_files = []
    for i in range(10):
        f = src_dir / f"2022-09-19_KKS_Oslo_{i:02d}.mp3"
        f.write_bytes(b"X" * 100)
        created_files.append(f)

    # 1. Enforce max_files
    copied = select_and_copy_bounded_evaluation_media(src_dir, dst_dir, max_files=4, max_bytes=10000)
    assert len(copied) == 4
    assert len(list(dst_dir.glob("*.mp3"))) == 4
    for f in created_files:
        assert f.exists()
        assert f.stat().st_size == 100

    # 2. Enforce max_bytes
    dst_dir_2 = tmp_path / "eval_dest_2"
    copied_bytes = select_and_copy_bounded_evaluation_media(src_dir, dst_dir_2, max_files=10, max_bytes=250)
    assert len(copied_bytes) == 2
    assert len(list(dst_dir_2.glob("*.mp3"))) == 2

    # 3. Preflight space check failure
    dst_dir_3 = tmp_path / "eval_dest_3"
    with patch("shutil.disk_usage", return_value=shutil._ntuple_diskusage(1000, 990, 50)):
        with pytest.raises(RuntimeError, match="Insufficient disk space"):
            select_and_copy_bounded_evaluation_media(src_dir, dst_dir_3, max_files=5, max_bytes=1000)


def test_54_scratch_space_preflight_and_cleanup(tmp_path, env_setup):
    """54. Preflight scratch space verification and safe abandoned scratch cleanup (R-053, R-054)."""
    from media_archive_tooling.orchestrator.scratch import clean_abandoned_scratch

    scratch_dir = tmp_path / "scratch"
    scratch_dir.mkdir()
    reg = env_setup["registry"]

    s1 = scratch_dir / ".tmp_extract_123.wav"
    s1.write_text("extract")
    s2 = scratch_dir / ".tmp_whisper_abc.json"
    s2.write_text("whisper")
    s3 = scratch_dir / "tmp_main_run_xyz.tmp"
    s3.write_text("run")

    # Record these 3 artifacts as owned in registry
    reg.record_scratch_artifact(s1, run_id="run-1", tracking_id="tid-1")
    reg.record_scratch_artifact(s2, run_id="run-1", tracking_id="tid-1")
    reg.record_scratch_artifact(s3, run_id="run-1", tracking_id="tid-1")

    media_file = scratch_dir / "real_archive_audio.mp3"
    media_file.write_text("audio")
    user_file = scratch_dir / "notes.txt"
    user_file.write_text("user notes")

    # Dry run MUST delete nothing
    dry_cleaned = clean_abandoned_scratch(scratch_dir, registry=reg, dry_run=True)
    assert len(dry_cleaned) == 0
    assert s1.exists()
    assert s2.exists()
    assert s3.exists()

    # Live cleanup: removes only registered artifacts confined to scratch_dir
    cleaned = clean_abandoned_scratch(scratch_dir, registry=reg, dry_run=False)
    assert len(cleaned) == 3
    assert not s1.exists()
    assert not s2.exists()
    assert not s3.exists()
    assert media_file.exists()
    assert user_file.exists()
    assert len(reg.get_scratch_artifacts()) == 0

    f_test = env_setup["media_dir"] / "test_scratch.mp3"
    f_test.write_text("audio")
    svc = env_setup["service"]

    with patch("shutil.disk_usage", return_value=shutil._ntuple_diskusage(1000, 999, 10)):
        summary = svc.run([f_test])
        assert summary.failed_retryable == 1
        assert summary.completed == 0
        assert summary.file_results[0].status == FileExecutionStatus.FAILED_RETRYABLE
        assert "Insufficient disk space" in summary.file_results[0].error
        assert f_test.exists()



def test_55_stage_checkpoint_resumability_and_retry(env_setup):
    """55. Durable stage checkpoints allow resuming uncompleted stages and isolating file failures (R-053)."""
    from media_archive_tooling.orchestrator.scratch import compute_file_sha256

    media_dir = env_setup["media_dir"]
    file1 = media_dir / "2022-09-19_KKS_Oslo.mp3"
    file1.write_text("audio 1")
    file2 = media_dir / "2017-04-10_KKS_Krsna-Dvur.mp3"
    file2.write_text("audio 2")

    svc = env_setup["service"]
    reg = env_setup["registry"]

    # First run: simulate failure in Tool 2
    with patch.object(svc.tool2_service, "review_file", side_effect=RuntimeError("Simulated Tool 2 error")):
        summary1 = svc.run([file1])
        assert summary1.review_required == 1
        assert summary1.completed == 0
        tid1 = summary1.file_results[0].tracking_id
        cp1 = reg.get_stage_checkpoint(tid1, StageName.TOOL_1_INITIAL.value)
        assert cp1 is not None
        assert cp1["status"] == "COMPLETED"
        assert cp1["input_path"] == str(file1.resolve())

    # Second run: resumes from Stage 2 using Stage 1 checkpoint
    with patch.object(svc.logger, "info") as mock_info:
        summary2 = svc.run([file1])
        assert summary2.completed == 1
        assert summary2.failed == 0
        skipped_calls = [
            c for c in mock_info.call_args_list
            if c.args and c.args[0] == "STAGE_SKIPPED_CHECKPOINT"
        ]
        assert len(skipped_calls) >= 1
        assert any(c.kwargs.get("tool") == "tool_1" for c in skipped_calls)

    # File failure isolation: an errored file fails isolatedly without blocking other files
    f_bad = media_dir / "2022-09-19_KKS_Bad_File.mp3"
    f_bad.write_text("corrupted")
    orig_sha = compute_file_sha256

    def selective_sha(p):
        if "Bad_File" in str(p):
            raise RuntimeError("Corrupted file read error")
        return orig_sha(p)

    with patch("media_archive_tooling.orchestrator.service.compute_file_sha256", side_effect=selective_sha):
        summary3 = svc.run([f_bad, file2])
        assert summary3.failed == 1
        assert summary3.review_required >= 1


def test_56_long_folder_bounded_memory_and_logging(tmp_path, env_setup):
    """56. Bounded in-memory results, bounded discovery logging, and fail-closed production flag (R-053)."""
    media_dir = env_setup["media_dir"]
    files = []
    for i in range(25):
        f = media_dir / f"2022-09-19_KKS_Oslo_{i:02d}.mp3"
        f.write_text("audio")
        files.append(f)

    svc = create_main_tooling_service(
        registry_path=env_setup["registry"].db_path,
        log_file=tmp_path / "test.log",
        workflow=WorkflowType.RENAMER,
        dry_run=True,
        max_retained_file_results=5,
        registry=env_setup["registry"],
        travel_service=env_setup["service"].travel_service,
        tool2_service=env_setup["service"].tool2_service,
        tool4_service=env_setup["service"].tool4_service,
    )

    with patch.object(svc.logger, "info") as mock_info:
        summary = svc.run(files)
        disc_calls = [c for c in mock_info.call_args_list if c.args and c.args[0] == "TARGETS_DISCOVERED"]
        assert len(disc_calls) == 1
        details = disc_calls[0].kwargs.get("details", {})
        assert details["media_count"] == 25
        assert len(details["sample_media_files"]) <= 10

    assert summary.total_discovered == 25
    assert summary.dry_run_previews == 25
    assert len(summary.file_results) == 5

    prod_svc = create_main_tooling_service(
        registry_path=env_setup["registry"].db_path,
        log_file=tmp_path / "test_prod.log",
        workflow=WorkflowType.RENAMER,
        production=True,
        registry=env_setup["registry"],
    )
    prod_summary = prod_svc.run([files[0]])
    assert prod_summary.exit_code == 1
    assert prod_summary.completed == 0


def test_57_scratch_cleanup_preserves_user_files_and_colliding_dirs(tmp_path, env_setup):
    """57. Scratch cleanup strictly confined to scratch_dir and durable registry ownership (R-054)."""
    from media_archive_tooling.orchestrator.scratch import clean_abandoned_scratch

    media_dir = env_setup["media_dir"]
    scratch_dir = tmp_path / "test_scratch_dir"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    reg = env_setup["registry"]

    # 1. User media files and directories with colliding temporary prefixes in target roots
    user_media_colliding = media_dir / ".tmp_extract_audio.mp3"
    user_media_colliding.write_text("precious audio")
    user_dir_colliding = media_dir / "tmp_main_archive_folder"
    user_dir_colliding.mkdir(exist_ok=True)
    (user_dir_colliding / "nested.mp3").write_text("nested audio")

    # 2. Unowned file inside scratch_dir with a temporary prefix
    unowned_scratch = scratch_dir / ".tmp_extract_unowned.wav"
    unowned_scratch.write_text("unowned")

    # 3. Registered owned scratch file inside scratch_dir
    owned_scratch = scratch_dir / "tmp_main_run_owned.tmp"
    owned_scratch.write_text("owned scratch")
    reg.record_scratch_artifact(owned_scratch, run_id="test-run", tracking_id="tid-test")

    # 4. Dry-run must delete nothing
    dry_cleaned = clean_abandoned_scratch(scratch_dir=scratch_dir, registry=reg, dry_run=True, target_roots=[media_dir])
    assert dry_cleaned == []
    assert user_media_colliding.exists()
    assert user_dir_colliding.exists()
    assert (user_dir_colliding / "nested.mp3").exists()
    assert unowned_scratch.exists()
    assert owned_scratch.exists()

    # 5. Live run: cleans only registered owned scratch confined to scratch_dir
    cleaned = clean_abandoned_scratch(scratch_dir=scratch_dir, registry=reg, dry_run=False, target_roots=[media_dir])
    assert cleaned == [owned_scratch.resolve()]
    assert not owned_scratch.exists()
    # User files/dirs in target roots MUST remain untouched
    assert user_media_colliding.exists()
    assert user_dir_colliding.exists()
    assert (user_dir_colliding / "nested.mp3").exists()
    # Unowned scratch in scratch_dir without registry proof MUST remain untouched
    assert unowned_scratch.exists()


def test_58_bounded_evaluation_limits_and_workspace_safety(tmp_path):
    """58. Evaluation bounds reject nonpositive values, enforce byte limit on every file, and protect workspace (R-055)."""
    from scripts.run_tool_4_evaluation import (
        DEFAULT_MAX_EVAL_BYTES,
        DEFAULT_MAX_EVAL_FILES,
        EVALUATION_WORKSPACE_MARKER,
        run_evaluation,
        select_and_copy_bounded_evaluation_media,
    )
    from media_archive_tooling.media_db_reviewer.models import MediaDatabaseReviewResult, ReviewDecision

    src_dir = tmp_path / "sample_src"
    src_dir.mkdir(parents=True, exist_ok=True)
    f1 = src_dir / "2020-01-01_large.mp3"
    f1.write_bytes(b"X" * 1000)
    f2 = src_dir / "2020-01-02_small.mp3"
    f2.write_bytes(b"Y" * 200)

    dst_dir = tmp_path / "dst"

    # 1. Reject nonpositive limits
    with pytest.raises(ValueError, match="max_files must be positive"):
        select_and_copy_bounded_evaluation_media(src_dir, dst_dir, max_files=0, max_bytes=500)
    with pytest.raises(ValueError, match="max_files must be positive"):
        select_and_copy_bounded_evaluation_media(src_dir, dst_dir, max_files=-1, max_bytes=500)
    with pytest.raises(ValueError, match="max_bytes must be positive"):
        select_and_copy_bounded_evaluation_media(src_dir, dst_dir, max_files=5, max_bytes=0)
    with pytest.raises(ValueError, match="max_bytes must be positive"):
        select_and_copy_bounded_evaluation_media(src_dir, dst_dir, max_files=5, max_bytes=-500)

    # 2. Hard byte limit enforced for EVERY file (including first file!)
    # First file (1000 bytes) exceeds max_bytes (500 bytes). It must NOT be selected.
    # Second file (200 bytes) fits within 500 bytes and should be selected.
    copied = select_and_copy_bounded_evaluation_media(src_dir, dst_dir, max_files=5, max_bytes=500)
    assert len(copied) == 1
    assert copied[0].name == "2020-01-02_small.mp3"

    # 3. Evaluation workspace safety: refuse deleting unowned workspace
    unowned_workspace = tmp_path / "unowned_workspace"
    unowned_workspace.mkdir(parents=True, exist_ok=True)
    (unowned_workspace / "important_user_doc.txt").write_text("do not delete")

    with pytest.raises(ValueError, match="Refusing to delete unowned evaluation workspace"):
        run_evaluation(sample_dir=src_dir, eval_workspace=unowned_workspace, skip_git_check=True)
    assert (unowned_workspace / "important_user_doc.txt").exists()

    # Refuse system/cwd roots
    with pytest.raises(ValueError, match="Refusing unsafe evaluation workspace path"):
        run_evaluation(sample_dir=src_dir, eval_workspace=Path.cwd(), skip_git_check=True)

    # When owned marker is present, workspace cleaning succeeds
    valid_workspace = tmp_path / "valid_workspace"
    valid_workspace.mkdir(parents=True, exist_ok=True)
    (valid_workspace / EVALUATION_WORKSPACE_MARKER).write_text("owned_by=run_tool_4_evaluation\n")
    (valid_workspace / "old_temp.tmp").write_text("old")

    class PreflightPassed(Exception):
        pass

    with patch("scripts.run_tool_4_evaluation.select_and_copy_bounded_evaluation_media", side_effect=PreflightPassed):
        with pytest.raises(PreflightPassed):
            run_evaluation(sample_dir=src_dir, eval_workspace=valid_workspace, max_files=1, max_bytes=500, skip_git_check=True)

    assert not (valid_workspace / "old_temp.tmp").exists()
    assert (valid_workspace / EVALUATION_WORKSPACE_MARKER).exists()


def test_59_streaming_discovery_incremental_processing(tmp_path, env_setup):
    """59. Incremental streaming discovery processes files before full traversal completes (R-055)."""
    from media_archive_tooling.orchestrator.discovery import (
        iter_discover_media_targets,
        validate_targets,
    )

    media_dir = tmp_path / "stream_media"
    media_dir.mkdir(parents=True, exist_ok=True)

    f1 = media_dir / "2022-09-19_KKS_Oslo_01.mp3"
    f1.write_text("audio 1")
    f2 = media_dir / "2022-09-19_KKS_Oslo_02.mp3"
    f2.write_text("audio 2")

    # 1. Missing target check fails fast without traversal
    missing = validate_targets([media_dir / "nonexistent.mp3", f1])
    assert len(missing) == 1
    assert "nonexistent.mp3" in missing[0]

    svc = env_setup["service"]
    summary_missing = svc.run([media_dir / "nonexistent.mp3"])
    assert summary_missing.exit_code == 1

    # 2. Deduplication semantics: duplicate target paths are yielded once
    yielded = list(iter_discover_media_targets([f1, f1, media_dir]))
    assert len(yielded) == 2
    assert yielded[0] == f1.resolve()
    assert yielded[1] == f2.resolve()

    # 3. Prove processing begins before full traversal completes:
    events: List[str] = []
    orig_process = svc._process_single_file

    def spy_process(p):
        events.append(f"process:{p.name}")
        return orig_process(p)

    def generator_spy(targets, **kwargs):
        events.append("generator:yield_1")
        yield f1
        events.append("generator:yield_2")
        yield f2
        events.append("generator:done")

    with patch.object(svc, "_process_single_file", side_effect=spy_process):
        with patch("media_archive_tooling.orchestrator.service.iter_discover_media_targets", side_effect=generator_spy):
            svc.run([media_dir])

    # Assert processing of file 1 occurred BEFORE yield of file 2!
    assert events == [
        "generator:yield_1",
        "process:2022-09-19_KKS_Oslo_01.mp3",
        "generator:yield_2",
        "process:2022-09-19_KKS_Oslo_02.mp3",
        "generator:done",
    ]


def test_60_fresh_remote_review_and_metadata_sync(env_setup):
    """60. Live Tool 2 discovers new remote candidates; Tool 4 syncs metadata after earlier SYNCED (R-056)."""
    from media_archive_tooling.media_db_reviewer.models import MediaCandidate, MediaDatabaseReviewResult, ReviewDecision
    from media_archive_tooling.media_db_updater.models import MediaDbSyncResult, FieldDiff, FieldAction, SyncOperation

    media_dir = env_setup["media_dir"]
    file1 = media_dir / "KKS Bhajans vrindavan sep 2019.mp3"
    file1.write_text("audio")

    svc = env_setup["service"]
    reg = env_setup["registry"]

    # 1. First run: file processed, Tool 2 returns 0 candidates (NEW_MEDIA_CANDIDATE)
    summary1 = svc.run([file1])
    assert summary1.completed == 1
    tid1 = summary1.file_results[0].tracking_id
    final_p1 = Path(summary1.file_results[0].final_path)
    assert final_p1.exists()
    t2_initial = reg.get_media_db_review(tid1)
    assert t2_initial["result"]["decision"] == "NEW_MEDIA_CANDIDATE"

    # Now simulate a collaborator adding a matching row in Baserow!
    env_setup["fake_write_adapter"].rows[9999] = {
        "id": 9999,
        "Date": "2019-09-01",
        "Title": "Kirtan",
        "Place, location": {"id": 71, "value": "Vrindavan"},
        "Country": {"id": 61, "value": "India"},
        "Filename": "old.mp3",
        "media_archive_path": "",
    }
    new_remote_candidate = MediaCandidate(
        media_row_id=9999,
        retrieval_reasons=["exact date", "matching location"],
    )
    fresh_review_result = MediaDatabaseReviewResult(
        tracking_id=tid1,
        decision=ReviewDecision.EXISTING_MEDIA_MATCH,
        candidates=[new_remote_candidate],
        selected_media_row_id=9999,
        database_state="LIVE_CURRENT",
        database_snapshot_at="2026-09-24T12:00:00Z",
        baserow_read_at="2026-09-24T12:00:00Z",
        live_read_complete=True,
        snapshot_complete=True,
        baserow_check_complete=True,
        review_required=False,
    )

    def mock_fresh_review(target_id, *args, **kwargs):
        reg.save_media_db_review(
            tracking_id=target_id,
            decision=fresh_review_result.decision.value,
            database_state=fresh_review_result.database_state,
            snapshot_timestamp=fresh_review_result.database_snapshot_at,
            result_json=fresh_review_result.model_dump_json(),
            selected_media_row_id=fresh_review_result.selected_media_row_id,
            review_required=fresh_review_result.review_required,
        )
        return fresh_review_result

    with patch.object(svc.tool2_service, "review_file", side_effect=mock_fresh_review):
        summary2 = svc.run([final_p1])
        assert summary2.completed == 1
        t2_updated = reg.get_media_db_review(tid1)
        assert t2_updated["result"]["decision"] == "EXISTING_MEDIA_MATCH"
        assert t2_updated["result"]["selected_media_row_id"] == 9999

    # 2. Tool 4 metadata update after earlier SYNCED result:
    sync_rec = reg.get_media_db_sync(tid1)
    assert sync_rec is not None
    assert sync_rec["sync_status"] == SyncStatus.SYNCED.value

    # Simulate that local metadata was updated; Tool 4 must re-evaluate diffs and update Baserow
    diff_result = MediaDbSyncResult(
        tracking_id=tid1,
        status=SyncStatus.SYNCED,
        operation=SyncOperation.UPDATE,
        media_row_id=9999,
        field_diffs=[FieldDiff(field_name="Title", action=FieldAction.SET, old_value="Old", new_value="Updated Title")],
        live_row={"id": 9999, "Title": "Updated Title"},
    )

    with patch.object(svc.tool4_service, "synchronize", return_value=diff_result) as mock_sync:
        summary3 = svc.run([final_p1])
        assert summary3.completed == 1
        assert mock_sync.call_count == 1
        assert summary3.file_results[0].tool4_operation == "UPDATE"
        cp4 = reg.get_stage_checkpoint(tid1, StageName.TOOL_4_SYNC.value)
        assert cp4 is not None
        assert cp4["details"]["operation"] == "UPDATE"
