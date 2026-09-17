"""Comprehensive unit and integration tests for Tool 4 (Media Database Updater).

Covers all 48 required test cases from Section 22 of docs/tool-4-media-database-updater-build-plan.md,
plus CLI and review portal integration tests.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from media_archive_tooling.media_db_reviewer.models import MediaDatabaseReviewResult, ReviewDecision
from media_archive_tooling.media_db_updater.models import (
    FieldAction,
    FieldDiff,
    MediaDbSyncRequest,
    MediaDbSyncResult,
    SyncOperation,
    SyncStatus,
)
from media_archive_tooling.media_db_updater.write_adapter import (
    AmbiguousOptionError,
    BaserowSchemaError,
    BaserowUnavailableError,
    FakeBaserowWriteAdapter,
    TaxonomyForbiddenError,
    redact_secrets,
    validate_field_schema,
)
from media_archive_tooling.media_db_updater.country_mapper import are_countries_equivalent
from media_archive_tooling.media_db_updater.engine import (
    MediaDatabaseUpdateEngine,
    merge_notes,
    _resolve_title,
    _is_complete_date,
    _extract_scripture_verse,
)
from media_archive_tooling.media_db_updater.service import MediaDatabaseUpdaterService
from media_archive_tooling.renamer.commit_service import RenameCommitService
from media_archive_tooling.renamer.models import (
    Context,
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
from media_archive_tooling.renamer.registry.registry import LocalRegistry
from media_archive_tooling.review_portal.app import app, configure_review_context


def make_mock_tool2(
    decision: str = "NEW_MEDIA_CANDIDATE",
    row_id: Optional[int] = None,
    live_read_complete: bool = True,
    snapshot_complete: bool = True,
    baserow_check_complete: bool = True,
    database_state: str = "LIVE_CURRENT",
    tracking_id: Optional[str] = None,
):
    mock_t2 = MagicMock()

    def _review_file(tid: str, force_refresh: bool = False):
        dec_enum = None
        for d in ReviewDecision:
            if d.value == decision:
                dec_enum = d
                break
        if dec_enum is None:
            dec_enum = ReviewDecision.DATABASE_UNAVAILABLE
        return MediaDatabaseReviewResult(
            tracking_id=tracking_id or tid,
            decision=dec_enum,
            selected_media_row_id=row_id,
            live_read_complete=live_read_complete,
            snapshot_complete=snapshot_complete,
            baserow_check_complete=baserow_check_complete,
            database_state=database_state,
            baserow_read_at="2026-09-17T12:00:00Z",
            database_snapshot_at="2026-09-17T12:00:00Z",
        )

    mock_t2.review_file.side_effect = _review_file
    return mock_t2


def make_parser_result(
    tracking_id: str = "trk0001",
    orig_filename: str = "2014-08-04_KKS_BG-01-18_Leipzig-de.mp3",
    date_val: Optional[str] = "2014-08-04",
    when_state: str = "exact",
    what_val: Optional[str] = "BG-01-18",
    what_category: Optional[str] = "Bhagavad-gita",
    what_verse: Optional[str] = "1.18",
    place: Optional[str] = "Leipzig",
    country: Optional[str] = "Germany",
    country_iso: Optional[str] = "de",
    parent_folder: Optional[str] = None,
) -> ParserResult:
    return ParserResult(
        identity=Identity(
            tracking_id=tracking_id,
            original_filename=orig_filename,
            original_path=f"/archive/{orig_filename}",
            current_filename=orig_filename,
            extension=Path(orig_filename).suffix,
        ),
        context=Context(parent_folder=parent_folder or ""),
        when=WhenResult(
            selected_value=date_val or "YYYY-MM-DD",
            precision="exact" if date_val else "none",
            state=ResolutionState.EXACT if when_state == "exact" else ResolutionState.PROVISIONAL,
        ),
        who="KKS",
        what=WhatResult(
            selected_value=what_val,
            category=what_category,
            state=ResolutionState.EXACT if what_val else ResolutionState.UNRESOLVED,
        ),
        where=WhereResult(
            place_location=place,
            country_iso2=country_iso,
            state=ResolutionState.EXACT if place else ResolutionState.UNRESOLVED,
        ),
        file_metadata=FileMetadata(),
        review_reasons=[],
    )


def save_test_file(
    registry: LocalRegistry,
    tracking_id: str = "trk0001",
    filename: str = "2014-08-04_KKS_BG-01-18_Leipzig-de.mp3",
    original_filename: Optional[str] = None,
    original_path: Optional[str] = None,
    current_path: Optional[str] = None,
    date_val: Optional[str] = "2014-08-04",
    when_state: str = "exact",
    what_val: Optional[str] = "BG-01-18",
    what_category: Optional[str] = "Bhagavad-gita",
    what_verse: Optional[str] = "1.18",
    place: Optional[str] = "Leipzig",
    country: Optional[str] = "Germany",
    country_iso: Optional[str] = "de",
    parent_folder: Optional[str] = None,
    status: str = "committed",
    needs_review: bool = False,
) -> RenameProposal:
    orig_fn = original_filename or filename
    orig_p = original_path or f"/archive/{orig_fn}"
    curr_p = current_path or f"/archive/{filename}"
    pr = make_parser_result(
        tracking_id=tracking_id,
        orig_filename=orig_fn,
        date_val=date_val,
        when_state=when_state,
        what_val=what_val,
        what_category=what_category,
        what_verse=what_verse,
        place=place,
        country=country,
        country_iso=country_iso,
        parent_folder=parent_folder,
    )
    pr.identity.original_path = orig_p
    pr.identity.current_filename = filename
    prop = RenameProposal(
        tracking_id=tracking_id,
        original_path=orig_p,
        current_filename=filename,
        proposed_filename=filename,
        proposed_path=curr_p,
        mode=RenameMode.INITIAL,
        status=status,
        needs_review=needs_review,
        parser_result=pr,
    )
    registry.save_proposal(prop)
    with registry._get_conn() as conn:
        conn.cursor().execute(
            "UPDATE files SET current_path = ?, current_filename = ? WHERE tracking_id = ?",
            (curr_p, filename, tracking_id)
        )
    return prop


# ---------------------------------------------------------------------------
# Test 1: Confirmed existing match updates only safe relevant fields
# ---------------------------------------------------------------------------
def test_01_confirmed_existing_match_updates_only_safe_relevant_fields(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(
        registry,
        tracking_id="trk0001",
        filename="new_name.mp3",
        original_filename="2014-08-04_KKS_BG-01-18_Leipzig-de.mp3",
    )

    fake_db = FakeBaserowWriteAdapter(initial_rows=[{
        "id": 100,
        "Title": "Old Title",
        "Date": "2014-08-04",
        "Filename": "old_name.mp3",
        "media_archive_path": "/archive/2014-08-04_KKS_BG-01-18_Leipzig-de.mp3",
        "Youtube": "https://youtu.be/keep_me",
        "Audio link": "https://audio.com/keep_me",
    }])
    service = MediaDatabaseUpdaterService(registry, fake_db)

    req = service.build_sync_request("trk0001")
    req.tool2_decision = "EXISTING_MEDIA_MATCH"
    req.selected_media_row_id = 100

    result = service.synchronize("trk0001", commit=True, request=req)
    assert result.status == SyncStatus.SYNCED
    assert result.operation == SyncOperation.UPDATE

    updated_row = fake_db.rows[100]
    # Relevant archive fields updated
    assert updated_row["Filename"] == "new_name.mp3"
    assert updated_row["media_archive_path"] == "/archive/new_name.mp3"
    # Online fields preserved
    assert updated_row["Youtube"] == "https://youtu.be/keep_me"
    assert updated_row["Audio link"] == "https://audio.com/keep_me"


# ---------------------------------------------------------------------------
# Test 2: Existing online links/transcript/audio fields survive archive synchronization unchanged
# ---------------------------------------------------------------------------
def test_02_existing_online_links_transcript_audio_survive_unchanged(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0002")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[{
        "id": 101,
        "Filename": "file.mp3",
        "Youtube": "https://youtube.com/watch?v=123",
        "Youtube descr": "Full description",
        "Audio link": "https://audiolink.org/audio.mp3",
        "Thumb image": "thumb.jpg",
        "Transcriber": "John Doe",
        "Status Transcript": "Completed",
        "Status Media": "Published",
    }])
    service = MediaDatabaseUpdaterService(registry, fake_db)
    req = service.build_sync_request("trk0002")
    req.tool2_decision = "EXISTING_MEDIA_MATCH"
    req.selected_media_row_id = 101

    res = service.synchronize("trk0002", commit=True, request=req)
    assert res.status == SyncStatus.SYNCED

    row = fake_db.rows[101]
    assert row["Youtube"] == "https://youtube.com/watch?v=123"
    assert row["Youtube descr"] == "Full description"
    assert row["Audio link"] == "https://audiolink.org/audio.mp3"
    assert row["Thumb image"] == "thumb.jpg"
    assert row["Transcriber"] == "John Doe"
    assert row["Status Transcript"] == "Completed"
    assert row["Status Media"] == "Published"


# ---------------------------------------------------------------------------
# Test 3: Blank trusted semantic field is enriched
# ---------------------------------------------------------------------------
def test_03_blank_trusted_semantic_field_is_enriched(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(
        registry,
        tracking_id="trk0003",
        filename="file.mp3",
        original_filename="file.mp3",
        date_val="2014-08-04",
        what_val="Sunday Feast Lecture",
    )
    fake_db = FakeBaserowWriteAdapter(initial_rows=[{
        "id": 102,
        "Date": None,
        "Title": None,
        "Filename": "file.mp3",
    }])
    service = MediaDatabaseUpdaterService(registry, fake_db)
    req = service.build_sync_request("trk0003")
    req.tool2_decision = "EXISTING_MEDIA_MATCH"
    req.selected_media_row_id = 102

    res = service.synchronize("trk0003", commit=True, request=req)
    assert res.status == SyncStatus.SYNCED
    assert "Date" in res.fields_modified
    assert "Title" in res.fields_modified
    assert fake_db.rows[102]["Date"] == "2014-08-04"


# ---------------------------------------------------------------------------
# Test 4: Equivalent semantic field is a no-op
# ---------------------------------------------------------------------------
def test_04_equivalent_semantic_field_is_a_noop(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(
        registry,
        tracking_id="trk0004",
        filename="test.mp3",
        original_filename="test.mp3",
        date_val="2014-08-04",
        what_val=None,
        what_category=None,
        what_verse=None,
        place=None,
        country=None,
        country_iso=None,
    )
    fake_db = FakeBaserowWriteAdapter(initial_rows=[{
        "id": 103,
        "Date": "2014-08-04",
        "Title": "test",
        "Filename": "test.mp3",
        "media_archive_path": "/archive/test.mp3",
        "Notes": "Added from archive",
    }])
    service = MediaDatabaseUpdaterService(registry, fake_db)
    req = service.build_sync_request("trk0004")
    req.tool2_decision = "EXISTING_MEDIA_MATCH"
    req.selected_media_row_id = 103

    res = service.synchronize("trk0004", commit=True, request=req)
    assert res.status == SyncStatus.SYNCED
    assert res.operation == SyncOperation.NOOP
    assert len(fake_db.calls) == 2  # fetch_fields, fetch_row_raw, 0 patches!
    assert not any(c["action"] == "patch_row" for c in fake_db.calls)


# ---------------------------------------------------------------------------
# Test 5: Conflicting populated semantic field is preserved and routed to review
# ---------------------------------------------------------------------------
def test_05_conflicting_populated_semantic_field_is_preserved_and_routed_to_review(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0005", date_val="2014-08-04")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[{
        "id": 104,
        "Date": "2015-09-09",
        "Filename": "test.mp3",
    }])
    service = MediaDatabaseUpdaterService(registry, fake_db)
    req = service.build_sync_request("trk0005")
    req.tool2_decision = "EXISTING_MEDIA_MATCH"
    req.selected_media_row_id = 104
    req.is_human_approved = False

    res = service.synchronize("trk0005", commit=True, request=req)
    assert res.status == SyncStatus.REVIEW_REQUIRED
    assert res.review_required is True
    assert any("Date conflict" in c for c in res.conflicts)
    assert fake_db.rows[104]["Date"] == "2015-09-09"  # Preserved!


# ---------------------------------------------------------------------------
# Test 6: Explicit human overwrite is live-revalidated before write
# ---------------------------------------------------------------------------
def test_06_explicit_human_overwrite_is_live_revalidated_before_write(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0006", date_val="2014-08-04")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[{
        "id": 105,
        "Date": "2015-09-09",
        "Filename": "test.mp3",
    }])
    service = MediaDatabaseUpdaterService(registry, fake_db)
    req = service.build_sync_request("trk0006")
    req.tool2_decision = "EXISTING_MEDIA_MATCH"
    req.selected_media_row_id = 105

    # Bare is_human_approved=True must NOT authorize overwrite of conflicting Date (R-016)
    req.is_human_approved = True
    res_bare = service.synchronize("trk0006", commit=True, request=req)
    assert res_bare.status == SyncStatus.REVIEW_REQUIRED
    assert fake_db.rows[105]["Date"] == "2015-09-09"

    # Field-specific approval with matching precondition authorizes write
    from media_archive_tooling.media_db_updater.models import FieldApproval, FieldApprovalAction
    req.field_approvals = {
        "Date": FieldApproval(
            field_name="Date",
            action=FieldApprovalAction.APPLY_CORRECTION,
            approved_value="2014-08-04",
            has_reviewed_precondition=True,
            reviewed_precondition_value="2015-09-09",
            reviewer="reviewer1",
        )
    }

    res = service.synchronize("trk0006", commit=True, request=req)
    assert res.status == SyncStatus.SYNCED
    assert fake_db.rows[105]["Date"] == "2014-08-04"


# ---------------------------------------------------------------------------
# Test 7: Collaborator relevant-field change between review and write blocks stale write
# ---------------------------------------------------------------------------
def test_07_collaborator_relevant_field_change_blocks_stale_write(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0007", date_val="2014-08-04")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[{
        "id": 106,
        "Title": "Initial Title",
        "Date": "2014-08-04",
        "Filename": "old.mp3",
    }])
    service = MediaDatabaseUpdaterService(registry, fake_db)
    req = service.build_sync_request("trk0007")
    req.tool2_decision = "EXISTING_MEDIA_MATCH"
    req.selected_media_row_id = 106
    req.field_approvals = {
        "Title": {
            "action": "apply_correction",
            "has_reviewed_precondition": True,
            "approved_value": "New Approved Title",
            "reviewed_precondition_value": "Initial Title",
        }
    }

    # Plan based on snapshot where Title is "Initial Title"
    plan = service.preview("trk0007", request=req)
    assert plan.status in (SyncStatus.SYNCING, SyncStatus.SYNCED)

    # Collaborator changes Title concurrently in live database!
    fake_db.rows[106]["Title"] = "Collaborator Edited Title"

    # Pre-update commit detects relevant field change and blocks
    commit_res = service.engine._commit_update(req, plan, initial_live_row=plan.precondition_row_snapshot)
    assert commit_res.status == SyncStatus.REVIEW_REQUIRED
    assert commit_res.operation == SyncOperation.CONFLICT
    assert any("COLLABORATOR_CONFLICT" in c for c in commit_res.conflicts)
    assert fake_db.rows[106]["Title"] == "Collaborator Edited Title"


# ---------------------------------------------------------------------------
# Test 8: Collaborator unrelated-field change is preserved by minimal PATCH
# ---------------------------------------------------------------------------
def test_08_collaborator_unrelated_field_change_preserved_by_minimal_patch(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0008", filename="renamed.mp3", original_filename="old.mp3")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[{
        "id": 107,
        "Filename": "old.mp3",
        "Youtube": "https://youtube.com/v1",
    }])
    service = MediaDatabaseUpdaterService(registry, fake_db)
    req = service.build_sync_request("trk0008")
    req.tool2_decision = "EXISTING_MEDIA_MATCH"
    req.selected_media_row_id = 107

    # Collaborator updates Youtube concurrently
    fake_db.rows[107]["Youtube"] = "https://youtube.com/collaborator_new"

    res = service.synchronize("trk0008", commit=True, request=req)
    assert res.status == SyncStatus.SYNCED
    # Youtube change survived!
    assert fake_db.rows[107]["Youtube"] == "https://youtube.com/collaborator_new"
    assert fake_db.rows[107]["Filename"] == "renamed.mp3"


# ---------------------------------------------------------------------------
# Test 9: Current NEW_MEDIA_CANDIDATE creates one row with required defaults
# ---------------------------------------------------------------------------
def test_09_current_new_media_candidate_creates_one_row_with_required_defaults(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0009", filename="2014-08-04_KKS_BG-01-18_Leipzig-de.mp3")
    fake_db = FakeBaserowWriteAdapter()
    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=make_mock_tool2())
    req = service.build_sync_request("trk0009")
    req.tool2_decision = "NEW_MEDIA_CANDIDATE"

    res = service.synchronize("trk0009", commit=True, request=req)
    assert res.status == SyncStatus.SYNCED
    assert res.operation == SyncOperation.CREATE
    assert res.media_row_id is not None

    created = fake_db.rows[res.media_row_id]
    assert created["Status Media"] == "Not-started"
    assert created["Status thumb"] == "Not-started"
    assert created["Status Transcript"] == "Not-started"
    assert created["Language"] == "English"
    assert created["Filename"] == "2014-08-04_KKS_BG-01-18_Leipzig-de.mp3"
    assert created["Notes"].startswith("Added from archive")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert created["Created_on"] == today
    assert created["imported_on"] == today
    assert created["Last modified"] == today


# ---------------------------------------------------------------------------
# Test 10: Collaborator-created matching row before create prevents duplicate creation
# ---------------------------------------------------------------------------
def test_10_collaborator_created_matching_row_before_create_prevents_duplicate(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0010")
    fake_db = FakeBaserowWriteAdapter()

    # Tool 2 mock that detects new row on pre-create revalidation
    mock_t2 = MagicMock()
    mock_res = MediaDatabaseReviewResult(
        tracking_id="trk0010",
        decision=ReviewDecision.EXISTING_MEDIA_MATCH,
        selected_media_row_id=999,
        live_read_complete=True,
        snapshot_complete=True,
        baserow_check_complete=True,
        database_state="LIVE_CURRENT",
        baserow_read_at="2026-09-17T12:00:00Z",
        database_snapshot_at="2026-09-17T12:00:00Z",
    )
    mock_t2.review_file.return_value = mock_res

    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=mock_t2)
    req = service.build_sync_request("trk0010")
    req.tool2_decision = "NEW_MEDIA_CANDIDATE"

    res = service.synchronize("trk0010", commit=True, request=req)
    assert res.status == SyncStatus.REVIEW_REQUIRED
    assert res.operation == SyncOperation.CONFLICT
    assert any("COLLABORATOR_NEW_ROW_CREATED" in c for c in res.conflicts)
    assert len(fake_db.rows) == 0  # No row was created!


# ---------------------------------------------------------------------------
# Test 11: Database failure never becomes a no-match/create decision
# ---------------------------------------------------------------------------
def test_11_database_failure_never_becomes_no_match_or_create(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0011")
    fake_db = FakeBaserowWriteAdapter()
    service = MediaDatabaseUpdaterService(registry, fake_db)
    req = service.build_sync_request("trk0011")
    req.tool2_decision = "DATABASE_UNAVAILABLE"

    res = service.synchronize("trk0011", commit=True, request=req)
    assert res.status == SyncStatus.DATABASE_UNAVAILABLE
    assert res.operation == SyncOperation.BLOCKED
    assert len(fake_db.rows) == 0


# ---------------------------------------------------------------------------
# Test 12: Multiple candidates never create first-match-wins or new duplicate row
# ---------------------------------------------------------------------------
def test_12_multiple_candidates_never_create_first_match_or_duplicate(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0012")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[
        {"id": 1, "Title": "Cand 1"},
        {"id": 2, "Title": "Cand 2"},
    ])
    service = MediaDatabaseUpdaterService(registry, fake_db)
    req = service.build_sync_request("trk0012")
    req.tool2_decision = "MULTIPLE_CANDIDATES"

    res = service.synchronize("trk0012", commit=True, request=req)
    assert res.status == SyncStatus.REVIEW_REQUIRED
    assert res.operation == SyncOperation.BLOCKED
    assert len(fake_db.rows) == 2  # No mutation!


# ---------------------------------------------------------------------------
# Test 13: Repeated create/update synchronization is idempotent
# ---------------------------------------------------------------------------
def test_13_repeated_create_update_is_idempotent(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0013", filename="test.mp3", date_val="2014-08-04")
    fake_db = FakeBaserowWriteAdapter()
    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=make_mock_tool2())

    # 1. First sync creates row
    req = service.build_sync_request("trk0013")
    req.tool2_decision = "NEW_MEDIA_CANDIDATE"
    res1 = service.synchronize("trk0013", commit=True, request=req)
    assert res1.status == SyncStatus.SYNCED
    created_id = res1.media_row_id
    notes_1 = fake_db.rows[created_id]["Notes"]

    # 2. Second sync with existing match
    req2 = service.build_sync_request("trk0013")
    req2.tool2_decision = "EXISTING_MEDIA_MATCH"
    req2.selected_media_row_id = created_id
    res2 = service.synchronize("trk0013", commit=True, request=req2)
    assert res2.status == SyncStatus.SYNCED
    assert res2.operation == SyncOperation.NOOP
    assert fake_db.rows[created_id]["Notes"] == notes_1  # Notes not duplicated!


# ---------------------------------------------------------------------------
# Test 14: Timeout/uncertain create outcome is reconciled before retry
# ---------------------------------------------------------------------------
def test_14_timeout_uncertain_create_outcome_is_reconciled(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0014")
    fake_db = FakeBaserowWriteAdapter()
    fake_db.simulate_timeout_on_create = True

    mock_t2 = MagicMock()
    rev_cand1 = MediaDatabaseReviewResult(
        tracking_id="trk0014",
        decision=ReviewDecision.NEW_MEDIA_CANDIDATE,
        selected_media_row_id=None,
        live_read_complete=True,
        snapshot_complete=True,
        baserow_check_complete=True,
        database_state="LIVE_CURRENT",
        baserow_read_at="2026-09-17T12:00:00Z",
        database_snapshot_at="2026-09-17T12:00:00Z",
    )
    rev_cand2 = MediaDatabaseReviewResult(
        tracking_id="trk0014",
        decision=ReviewDecision.NEW_MEDIA_CANDIDATE,
        selected_media_row_id=None,
        live_read_complete=True,
        snapshot_complete=True,
        baserow_check_complete=True,
        database_state="LIVE_CURRENT",
        baserow_read_at="2026-09-17T12:00:00Z",
        database_snapshot_at="2026-09-17T12:00:00Z",
    )
    rev_after = MediaDatabaseReviewResult(
        tracking_id="trk0014",
        decision=ReviewDecision.EXISTING_MEDIA_MATCH,
        selected_media_row_id=1001,
        live_read_complete=True,
        snapshot_complete=True,
        baserow_check_complete=True,
        database_state="LIVE_CURRENT",
        baserow_read_at="2026-09-17T12:00:00Z",
        database_snapshot_at="2026-09-17T12:00:00Z",
    )
    mock_t2.review_file.side_effect = [rev_cand1, rev_cand2, rev_after]

    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=mock_t2)
    req = service.build_sync_request("trk0014")
    req.tool2_decision = "NEW_MEDIA_CANDIDATE"

    # Execute with timeout on create
    res = service.synchronize("trk0014", commit=True, request=req)
    # Reconciled without creating duplicate row!
    assert res.status == SyncStatus.SYNCED
    assert res.media_row_id == 1001
    assert "Reconciled uncertain create" in " ".join(res.diagnostic_notes)


# ---------------------------------------------------------------------------
# Test 15: Timeout/uncertain update outcome is reconciled before retry
# ---------------------------------------------------------------------------
def test_15_timeout_uncertain_update_outcome_is_reconciled(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0015", filename="renamed.mp3")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[{
        "id": 108,
        "Filename": "old.mp3",
    }])
    fake_db.simulate_timeout_on_patch = True

    service = MediaDatabaseUpdaterService(registry, fake_db)
    req = service.build_sync_request("trk0015")
    req.tool2_decision = "EXISTING_MEDIA_MATCH"
    req.selected_media_row_id = 108

    res = service.synchronize("trk0015", commit=True, request=req)
    assert res.status == SyncStatus.SYNCED
    assert "Reconciled uncertain update" in " ".join(res.diagnostic_notes)


# ---------------------------------------------------------------------------
# Test 16: Full trusted recording date writes Date as YYYY-MM-DD
# ---------------------------------------------------------------------------
def test_16_full_trusted_recording_date_writes_date_yyyy_mm_dd(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0016", date_val="2014-08-04")
    fake_db = FakeBaserowWriteAdapter()
    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=make_mock_tool2())
    req = service.build_sync_request("trk0016")
    req.tool2_decision = "NEW_MEDIA_CANDIDATE"

    res = service.synchronize("trk0016", commit=True, request=req)
    assert fake_db.rows[res.media_row_id]["Date"] == "2014-08-04"


# ---------------------------------------------------------------------------
# Test 17: Partial date leaves Date empty and writes one deterministic incomplete-date Notes marker
# ---------------------------------------------------------------------------
def test_17_partial_date_leaves_date_empty_and_writes_notes_marker(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0017", date_val="2019-09-DD")
    fake_db = FakeBaserowWriteAdapter()
    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=make_mock_tool2())
    req = service.build_sync_request("trk0017")
    req.tool2_decision = "NEW_MEDIA_CANDIDATE"

    res = service.synchronize("trk0017", commit=True, request=req)
    created = fake_db.rows[res.media_row_id]
    assert created.get("Date") is None
    assert "Incomplete recording date: 2019-09-DD" in created["Notes"]


# ---------------------------------------------------------------------------
# Test 18: Later full date fills Date and safely removes/replaces only Tool 4's incomplete-date marker
# ---------------------------------------------------------------------------
def test_18_later_full_date_fills_date_and_removes_incomplete_date_marker(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0018", date_val="2019-09-15")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[{
        "id": 109,
        "Date": None,
        "Notes": "Added from archive\nHuman comment here\nIncomplete recording date: 2019-09-DD",
    }])
    service = MediaDatabaseUpdaterService(registry, fake_db)
    req = service.build_sync_request("trk0018")
    req.tool2_decision = "EXISTING_MEDIA_MATCH"
    req.selected_media_row_id = 109

    res = service.synchronize("trk0018", commit=True, request=req)
    assert res.status == SyncStatus.SYNCED
    updated = fake_db.rows[109]
    assert updated["Date"] == "2019-09-15"
    assert "Human comment here" in updated["Notes"]
    assert "Incomplete recording date" not in updated["Notes"]


# ---------------------------------------------------------------------------
# Test 19: Provisional schedule-derived date/location is not automatically written as authoritative
# ---------------------------------------------------------------------------
def test_19_provisional_schedule_derived_date_location_not_written(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0019", date_val="2014-08-04", when_state="provisional", place="Berlin")
    fake_db = FakeBaserowWriteAdapter()
    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=make_mock_tool2())
    req = service.build_sync_request("trk0019")
    req.tool2_decision = "NEW_MEDIA_CANDIDATE"
    req.where_state = "PROVISIONAL"

    res = service.synchronize("trk0019", commit=True, request=req)
    created = fake_db.rows[res.media_row_id]
    assert created.get("Date") is None
    assert created.get("Place, location") is None


# ---------------------------------------------------------------------------
# Test 20: Title priority: filename title -> parent-folder title -> filename fallback
# ---------------------------------------------------------------------------
def test_20_title_priority_resolution():
    # 1. Filename title evidence in what_val
    req1 = MediaDbSyncRequest(
        tracking_id="t1", current_filename="f.mp3", current_path="/p/f.mp3",
        what_val="Seminar on Karma", parent_folder_context="Sunday Feasts",
    )
    assert _resolve_title(req1) == "Seminar on Karma"

    # 2. Parent folder context when what_val is scripture only
    req2 = MediaDbSyncRequest(
        tracking_id="t2", current_filename="f.mp3", current_path="/p/f.mp3",
        what_val="SB 1.3.4", parent_folder_context="Sunday Feast Lectures",
    )
    assert _resolve_title(req2) == "Sunday Feast Lectures"

    # 3. Filename stem fallback when parent folder is generic (e.g. year)
    req3 = MediaDbSyncRequest(
        tracking_id="t3", current_filename="2014-08-04_Lecture.mp3", current_path="/p/2014-08-04_Lecture.mp3",
        parent_folder_context="2014",
    )
    assert _resolve_title(req3) == "2014-08-04_Lecture"


# ---------------------------------------------------------------------------
# Test 21: Category abbreviation maps only to a valid live Category option
# ---------------------------------------------------------------------------
def test_21_category_abbreviation_maps_to_valid_live_option(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0021", what_category="Bhagavad-gita")
    fake_db = FakeBaserowWriteAdapter()
    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=make_mock_tool2())
    req = service.build_sync_request("trk0021")
    req.tool2_decision = "NEW_MEDIA_CANDIDATE"

    res = service.synchronize("trk0021", commit=True, request=req)
    assert fake_db.rows[res.media_row_id]["Category"] == "Bhagavad-gita"


# ---------------------------------------------------------------------------
# Test 22: Missing Category option does not get invented automatically
# ---------------------------------------------------------------------------
def test_22_missing_category_option_does_not_get_invented(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0022", what_category="Unknown New Category")
    fake_db = FakeBaserowWriteAdapter()
    service = MediaDatabaseUpdaterService(registry, fake_db)
    req = service.build_sync_request("trk0022")
    req.tool2_decision = "NEW_MEDIA_CANDIDATE"

    res = service.synchronize("trk0022", commit=True, request=req)
    assert res.status == SyncStatus.REVIEW_REQUIRED
    assert any("Category option" in c for c in res.conflicts)
    assert len(fake_db.rows) == 0


# ---------------------------------------------------------------------------
# Test 23: Scripture verse/reference produces the expected Tag behavior for the live field type
# ---------------------------------------------------------------------------
def test_23_scripture_verse_produces_expected_tag_behavior(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0023", what_val="SB-01-03-04", what_verse="1.3.4")
    fake_db = FakeBaserowWriteAdapter()
    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=make_mock_tool2())
    req = service.build_sync_request("trk0023")
    req.tool2_decision = "NEW_MEDIA_CANDIDATE"

    res = service.synchronize("trk0023", commit=True, request=req)
    assert fake_db.rows[res.media_row_id]["Tag"] == ["1.3.4"]


# ---------------------------------------------------------------------------
# Test 24: Existing multi-value Tags are preserved when adding an approved tag
# ---------------------------------------------------------------------------
def test_24_existing_multivalue_tags_preserved_when_adding_tag(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0024", what_val="SB-01-03-04", what_category="Srimad Bhagavatam", what_verse="1.3.4")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[{
        "id": 110,
        "Tag": ["intro"],
    }])
    service = MediaDatabaseUpdaterService(registry, fake_db)
    req = service.build_sync_request("trk0024")
    req.tool2_decision = "EXISTING_MEDIA_MATCH"
    req.selected_media_row_id = 110

    res = service.synchronize("trk0024", commit=True, request=req)
    assert res.status == SyncStatus.SYNCED
    assert fake_db.rows[110]["Tag"] == ["intro", "1.3.4"]


# ---------------------------------------------------------------------------
# Test 25: Language defaults to existing English option on new row
# ---------------------------------------------------------------------------
def test_25_language_defaults_to_existing_english_option(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0025")
    fake_db = FakeBaserowWriteAdapter()
    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=make_mock_tool2())
    req = service.build_sync_request("trk0025")
    req.tool2_decision = "NEW_MEDIA_CANDIDATE"

    res = service.synchronize("trk0025", commit=True, request=req)
    assert fake_db.rows[res.media_row_id]["Language"] == "English"


# ---------------------------------------------------------------------------
# Test 26: Status Media, Status thumb, Status Transcript default to existing Not-started option
# ---------------------------------------------------------------------------
def test_26_statuses_default_to_not_started_option(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0026")
    fake_db = FakeBaserowWriteAdapter()
    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=make_mock_tool2())
    req = service.build_sync_request("trk0026")
    req.tool2_decision = "NEW_MEDIA_CANDIDATE"

    res = service.synchronize("trk0026", commit=True, request=req)
    row = fake_db.rows[res.media_row_id]
    assert row["Status Media"] == "Not-started"
    assert row["Status thumb"] == "Not-started"
    assert row["Status Transcript"] == "Not-started"


# ---------------------------------------------------------------------------
# Test 27: Missing required status/language option blocks rather than inventing taxonomy
# ---------------------------------------------------------------------------
def test_27_missing_required_status_language_blocks_rather_than_inventing(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0027")

    # Schema missing Language option 'English'
    fake_db = FakeBaserowWriteAdapter()
    for f in fake_db.fields:
        if f["name"] == "Language":
            f["select_options"] = []
    service = MediaDatabaseUpdaterService(registry, fake_db)
    req = service.build_sync_request("trk0027")
    req.tool2_decision = "NEW_MEDIA_CANDIDATE"

    res = service.synchronize("trk0027", commit=True, request=req)
    assert res.status == SyncStatus.REVIEW_REQUIRED
    assert any("Language" in c for c in res.conflicts)


# ---------------------------------------------------------------------------
# Test 28: New legitimate country option can be added safely and then assigned
# ---------------------------------------------------------------------------
def test_28_new_legitimate_country_option_added_safely(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0028")
    fake_db = FakeBaserowWriteAdapter()
    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=make_mock_tool2())
    req = service.build_sync_request("trk0028")
    req.tool2_decision = "NEW_MEDIA_CANDIDATE"
    req.where_country = "France"

    res = service.synchronize("trk0028", commit=True, request=req)
    assert res.status == SyncStatus.SYNCED
    assert fake_db.rows[res.media_row_id]["Country"] == "France"


# ---------------------------------------------------------------------------
# Test 29: New legitimate location option can be added safely and then assigned
# ---------------------------------------------------------------------------
def test_29_new_legitimate_location_option_added_safely(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0029")
    fake_db = FakeBaserowWriteAdapter()
    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=make_mock_tool2())
    req = service.build_sync_request("trk0029")
    req.tool2_decision = "NEW_MEDIA_CANDIDATE"
    req.where_place = "Paris"

    res = service.synchronize("trk0029", commit=True, request=req)
    assert res.status == SyncStatus.SYNCED
    assert fake_db.rows[res.media_row_id]["Place, location"] == "Paris"


# ---------------------------------------------------------------------------
# Test 30: Equivalent country/location option is reused rather than duplicated
# ---------------------------------------------------------------------------
def test_30_equivalent_country_location_option_reused_rather_than_duplicated(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0030")
    fake_db = FakeBaserowWriteAdapter()
    initial_opt_count = len(fake_db.fields[-1]["select_options"])

    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=make_mock_tool2())
    req = service.build_sync_request("trk0030")
    req.tool2_decision = "NEW_MEDIA_CANDIDATE"
    req.where_place = "leipzig"  # lower case equivalent of existing "Leipzig"

    res = service.synchronize("trk0030", commit=True, request=req)
    assert res.status == SyncStatus.SYNCED
    assert fake_db.rows[res.media_row_id]["Place, location"] == "Leipzig"
    assert len(fake_db.fields[-1]["select_options"]) == initial_opt_count


# ---------------------------------------------------------------------------
# Test 31: Ambiguous similar location options route to review
# ---------------------------------------------------------------------------
def test_31_ambiguous_similar_location_options_route_to_review(tmp_path):
    fake_db = FakeBaserowWriteAdapter()
    # Add duplicate-like option
    loc_fld = [f for f in fake_db.fields if f["name"] == "Place, location"][0]
    loc_fld["select_options"].append({"id": 99, "value": "Leipzig", "color": "red"})

    with pytest.raises(AmbiguousOptionError):
        fake_db.ensure_select_option("Place, location", "Leipzig")


# ---------------------------------------------------------------------------
# Test 32: Select-option schema update preserves all existing options
# ---------------------------------------------------------------------------
def test_32_select_option_schema_update_preserves_all_existing_options(tmp_path):
    fake_db = FakeBaserowWriteAdapter()
    orig_options = [opt["value"] for opt in fake_db.fields[-1]["select_options"]]

    fake_db.ensure_select_option("Place, location", "New City")
    new_options = [opt["value"] for opt in fake_db.fields[-1]["select_options"]]

    for o in orig_options:
        assert o in new_options
    assert "New City" in new_options


# ---------------------------------------------------------------------------
# Test 33: Notes begins with Added from archive exactly once and preserves existing human notes
# ---------------------------------------------------------------------------
def test_33_notes_begins_with_added_from_archive_once_and_preserves_human_notes():
    merged1 = merge_notes("Speaker arrived late.")
    assert merged1 == "Added from archive\nSpeaker arrived late."

    # Rerun does not duplicate "Added from archive"
    merged2 = merge_notes(merged1)
    assert merged2 == "Added from archive\nSpeaker arrived late."


# ---------------------------------------------------------------------------
# Test 34: New-row timestamps/default dates are populated as specified
# ---------------------------------------------------------------------------
def test_34_new_row_timestamps_default_dates_populated(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0034")
    fake_db = FakeBaserowWriteAdapter()
    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=make_mock_tool2())
    req = service.build_sync_request("trk0034")
    req.tool2_decision = "NEW_MEDIA_CANDIDATE"

    res = service.synchronize("trk0034", commit=True, request=req)
    row = fake_db.rows[res.media_row_id]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert row["Created_on"] == today
    assert row["Last modified by"] == today
    assert row["Last modified"] == today
    assert row["imported_on"] == today


# ---------------------------------------------------------------------------
# Test 35: Existing Created_on/imported_on are not reset on ordinary rename updates
# ---------------------------------------------------------------------------
def test_35_existing_created_on_imported_on_not_reset_on_rename_update(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0035", filename="renamed.mp3")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[{
        "id": 111,
        "Filename": "old.mp3",
        "Created_on": "2018-01-01",
        "imported_on": "2018-01-01",
    }])
    service = MediaDatabaseUpdaterService(registry, fake_db)
    req = service.build_sync_request("trk0035")
    req.tool2_decision = "EXISTING_MEDIA_MATCH"
    req.selected_media_row_id = 111

    res = service.synchronize("trk0035", commit=True, request=req)
    assert res.status == SyncStatus.SYNCED
    row = fake_db.rows[111]
    assert row["Created_on"] == "2018-01-01"
    assert row["imported_on"] == "2018-01-01"


# ---------------------------------------------------------------------------
# Test 36: Last modified and Last modified by follow specified current-date write rule
# ---------------------------------------------------------------------------
def test_36_last_modified_and_last_modified_by_updated_on_write(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0036", filename="renamed.mp3")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[{
        "id": 112,
        "Filename": "old.mp3",
        "Last modified": "2018-01-01",
        "Last modified by": "2018-01-01",
    }])
    service = MediaDatabaseUpdaterService(registry, fake_db)
    req = service.build_sync_request("trk0036")
    req.tool2_decision = "EXISTING_MEDIA_MATCH"
    req.selected_media_row_id = 112

    res = service.synchronize("trk0036", commit=True, request=req)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert fake_db.rows[112]["Last modified"] == today
    assert fake_db.rows[112]["Last modified by"] == today


# ---------------------------------------------------------------------------
# Test 37: Media Archive link remains untouched/empty and is never auto-derived
# ---------------------------------------------------------------------------
def test_37_media_archive_link_remains_untouched_and_never_auto_derived(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0037")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[{
        "id": 113,
        "Filename": "f.mp3",
        "Media Archive link": "https://drive.google.com/manual_link",
    }])
    service = MediaDatabaseUpdaterService(registry, fake_db)
    req = service.build_sync_request("trk0037")
    req.tool2_decision = "EXISTING_MEDIA_MATCH"
    req.selected_media_row_id = 113

    res = service.synchronize("trk0037", commit=True, request=req)
    assert fake_db.rows[113]["Media Archive link"] == "https://drive.google.com/manual_link"


# ---------------------------------------------------------------------------
# Test 38: media_archive_path stores full current path
# ---------------------------------------------------------------------------
def test_38_media_archive_path_stores_full_current_path(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0038")
    fake_db = FakeBaserowWriteAdapter()
    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=make_mock_tool2())
    req = service.build_sync_request("trk0038")
    req.tool2_decision = "NEW_MEDIA_CANDIDATE"

    res = service.synchronize("trk0038", commit=True, request=req)
    assert fake_db.rows[res.media_row_id]["media_archive_path"] == req.current_path


# ---------------------------------------------------------------------------
# Test 39: Same tracked file rename safely updates Filename/path from old to new
# ---------------------------------------------------------------------------
def test_39_tracked_file_rename_safely_updates_filename_and_path(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0039", filename="new_name.mp3")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[{
        "id": 114,
        "Filename": "2014-08-04_KKS_BG-01-18_Leipzig-de.mp3",
        "media_archive_path": "/archive/2014-08-04_KKS_BG-01-18_Leipzig-de.mp3",
    }])
    service = MediaDatabaseUpdaterService(registry, fake_db)
    req = service.build_sync_request("trk0039")
    req.original_filename = "2014-08-04_KKS_BG-01-18_Leipzig-de.mp3"
    req.original_path = "/archive/2014-08-04_KKS_BG-01-18_Leipzig-de.mp3"
    req.tool2_decision = "EXISTING_MEDIA_MATCH"
    req.selected_media_row_id = 114

    res = service.synchronize("trk0039", commit=True, request=req)
    assert res.status == SyncStatus.SYNCED
    assert fake_db.rows[114]["Filename"] == "new_name.mp3"
    assert fake_db.rows[114]["media_archive_path"] == "/archive/new_name.mp3"


# ---------------------------------------------------------------------------
# Test 40: Different unproven archive representation does not overwrite existing path
# ---------------------------------------------------------------------------
def test_40_different_unproven_archive_representation_does_not_overwrite(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0040", filename="audio.mp3")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[{
        "id": 115,
        "Filename": "video.mp4",
        "media_archive_path": "/archive/video/video.mp4",
    }])
    service = MediaDatabaseUpdaterService(registry, fake_db)
    req = service.build_sync_request("trk0040")
    req.original_filename = "different_audio.mp3"
    req.original_path = "/archive/different_audio.mp3"
    req.tool2_decision = "EXISTING_MEDIA_MATCH"
    req.selected_media_row_id = 115

    res = service.synchronize("trk0040", commit=True, request=req)
    assert res.status == SyncStatus.REVIEW_REQUIRED
    assert any("media_archive_path collision" in c for c in res.conflicts)
    assert fake_db.rows[115]["media_archive_path"] == "/archive/video/video.mp4"  # Untouched!


# ---------------------------------------------------------------------------
# Test 41: Tool 1 successful commit records/initiates Tool 4 synchronization
# ---------------------------------------------------------------------------
def test_41_tool_1_successful_commit_initiates_tool_4_sync(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    src_file = tmp_path / "orig.mp3"
    src_file.write_text("audio content")

    target_filename = "audio_ID-trk0041.mp3"
    target_file = tmp_path / target_filename
    save_test_file(registry, tracking_id="trk0041", filename="orig.mp3", status="approved")
    # Update current_path to actual file path on disk
    registry.save_proposal(RenameProposal(
        tracking_id="trk0041",
        original_path=str(src_file),
        current_filename="orig.mp3",
        proposed_filename=target_filename,
        proposed_path=str(target_file),
        mode=RenameMode.FINALIZE,
        status="approved",
        parser_result=make_parser_result(tracking_id="trk0041", orig_filename="orig.mp3"),
    ))

    mock_updater = MagicMock()
    commit_svc = RenameCommitService(
        registry,
        mode=RenameMode.FINALIZE,
        media_db_updater_service=mock_updater,
    )

    commit_svc.commit_file("trk0041")
    assert mock_updater.synchronize.called
    sync_rec = registry.get_media_db_sync("trk0041")
    assert sync_rec is not None


# ---------------------------------------------------------------------------
# Test 42: Tool 1 mere approval/dry-run does not write Baserow
# ---------------------------------------------------------------------------
def test_42_tool_1_mere_approval_dry_run_does_not_write_baserow(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    fake_db = FakeBaserowWriteAdapter()
    save_test_file(registry, tracking_id="trk0042", status="approved")

    # Local registry review approval does not touch write adapter
    assert len(fake_db.calls) == 0


# ---------------------------------------------------------------------------
# Test 43: Tool 4 failure after rename leaves durable pending sync rather than reverting file
# ---------------------------------------------------------------------------
def test_43_tool_4_failure_after_rename_leaves_pending_sync_without_revert(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    src_file = tmp_path / "source.mp3"
    target_filename = "2014-08-04_KKS_BG-01-18_Leipzig-de.mp3"
    target_file = tmp_path / target_filename
    src_file.write_text("content")

    save_test_file(registry, tracking_id="trk0043", filename="source.mp3", status="approved")
    registry.save_proposal(RenameProposal(
        tracking_id="trk0043",
        original_path=str(src_file),
        current_filename="source.mp3",
        proposed_filename=target_filename,
        proposed_path=str(target_file),
        mode=RenameMode.FINALIZE,
        status="approved",
        parser_result=make_parser_result(tracking_id="trk0043", orig_filename="source.mp3"),
    ))

    mock_updater = MagicMock()
    mock_updater.synchronize.side_effect = BaserowUnavailableError("Network cut")
    commit_svc = RenameCommitService(
        registry,
        mode=RenameMode.FINALIZE,
        media_db_updater_service=mock_updater,
    )

    # Commit succeeds on disk despite updater failure!
    commit_svc.commit_file("trk0043")
    assert target_file.exists()
    assert not src_file.exists()

    sync_rec = registry.get_media_db_sync("trk0043")
    assert sync_rec["sync_status"] == "PENDING_SYNC"


# ---------------------------------------------------------------------------
# Test 44: Retry of pending sync uses fresh current Tool 2/Baserow state
# ---------------------------------------------------------------------------
def test_44_retry_pending_sync_uses_fresh_current_state(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0044")
    registry.save_media_db_sync(tracking_id="trk0044", sync_status="PENDING_SYNC", attempt_count=1)

    fake_db = FakeBaserowWriteAdapter()
    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=make_mock_tool2())
    # Give it a new media candidate decision
    req = service.build_sync_request("trk0044")
    req.tool2_decision = "NEW_MEDIA_CANDIDATE"

    with patch.object(service, "build_sync_request", return_value=req):
        retried = service.retry_pending(["trk0044"])
        assert len(retried) == 1
        assert retried[0].status == SyncStatus.SYNCED
        assert retried[0].attempt_count == 2


# ---------------------------------------------------------------------------
# Test 45: Portal mutating action calls service layer and revalidates live state
# ---------------------------------------------------------------------------
def test_45_portal_mutating_action_calls_service_layer(tmp_path):
    import media_archive_tooling.review_portal.app as portal_module
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0045")
    fake_db = FakeBaserowWriteAdapter()
    updater_svc = MediaDatabaseUpdaterService(registry, fake_db)

    old_service = portal_module._service
    old_commit = portal_module._commit_service
    old_updater = portal_module._media_db_updater_service
    try:
        configure_review_context(
            registry_path=tmp_path / "test.db",
            media_db_updater_service=updater_svc,
        )
        client = TestClient(app)

        resp = client.post("/file/trk0045/media-db-sync", data={"action": "preview"}, follow_redirects=True)
        assert resp.status_code == 200
        assert "Tool 4: Media Database Synchronization" in resp.text
    finally:
        portal_module._service = old_service
        portal_module._commit_service = old_commit
        portal_module._media_db_updater_service = old_updater


# ---------------------------------------------------------------------------
# Test 46: CLI dry-run performs zero mutations
# ---------------------------------------------------------------------------
def test_46_cli_dry_run_performs_zero_mutations(tmp_path, capsys):
    from media_archive_tooling.cli import run_media_db_update

    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0046")

    class Args:
        registry_path = str(tmp_path / "test.db")
        tracking_id = "trk0046"
        commit = False
        retry_pending = False
        json = False

    with patch("media_archive_tooling.cli.BaserowWriteAdapter") as mock_wa:
        fake_adapter = FakeBaserowWriteAdapter()
        mock_wa.return_value = fake_adapter
        run_media_db_update(Args())

        out = capsys.readouterr().out
        assert "DRY-RUN / PREVIEW" in out
        assert not any(c["action"] in ("create_row", "patch_row") for c in fake_adapter.calls)


# ---------------------------------------------------------------------------
# Test 47: CLI explicit commit invokes the same service used by Tool 1/portal
# ---------------------------------------------------------------------------
def test_47_cli_commit_invokes_service(tmp_path, capsys):
    from media_archive_tooling.cli import run_media_db_update

    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0047")

    class Args:
        registry_path = str(tmp_path / "test.db")
        tracking_id = "trk0047"
        commit = True
        retry_pending = False
        json = False

    fake_adapter = FakeBaserowWriteAdapter()
    mock_t2_service = make_mock_tool2(decision="NEW_MEDIA_CANDIDATE")

    with patch("media_archive_tooling.cli.BaserowWriteAdapter", return_value=fake_adapter), \
         patch("media_archive_tooling.cli.MediaDatabaseReviewService", return_value=mock_t2_service):
        with patch.object(MediaDatabaseUpdaterService, "build_sync_request") as mock_bld:
            req = MediaDbSyncRequest(
                tracking_id="trk0047",
                current_filename="test.mp3",
                current_path="/archive/test.mp3",
                tool2_decision="NEW_MEDIA_CANDIDATE",
            )
            mock_bld.return_value = req
            run_media_db_update(Args())

            out = capsys.readouterr().out
            assert "COMMIT" in out
            assert any(c["action"] == "create_row" for c in fake_adapter.calls)


# ---------------------------------------------------------------------------
# Test 48: Audit record contains before/after/provenance without credentials
# ---------------------------------------------------------------------------
def test_48_audit_record_contains_before_after_without_credentials(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0048")
    secret_token = "secret-token-xyz-987"
    fake_db = FakeBaserowWriteAdapter()
    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=make_mock_tool2())
    req = service.build_sync_request("trk0048")
    req.tool2_decision = "NEW_MEDIA_CANDIDATE"

    res = service.synchronize("trk0048", commit=True, request=req)
    res_json = res.model_dump_json()
    assert secret_token not in res_json
    assert res.field_diffs is not None
    assert len(res.field_diffs) > 0

    sync_db_rec = registry.get_media_db_sync("trk0048")
    assert sync_db_rec is not None
    assert secret_token not in str(sync_db_rec)


# ---------------------------------------------------------------------------
# Test 49: Pre-create revalidation is hard precondition; missing tool2_service blocks
# ---------------------------------------------------------------------------
def test_49_pre_create_missing_tool2_service_blocks_with_database_unavailable(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0049")
    fake_db = FakeBaserowWriteAdapter()
    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=None)
    req = service.build_sync_request("trk0049")
    req.tool2_decision = "NEW_MEDIA_CANDIDATE"

    res = service.synchronize("trk0049", commit=True, request=req)
    assert res.status == SyncStatus.DATABASE_UNAVAILABLE
    assert res.operation == SyncOperation.BLOCKED
    assert res.review_required is True
    assert not any(c["action"] == "create_row" for c in fake_db.calls)


# ---------------------------------------------------------------------------
# Test 50: Pre-create live check exception blocks safely without calling create_row
# ---------------------------------------------------------------------------
def test_50_pre_create_tool2_exception_blocks_safely(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0050")
    fake_db = FakeBaserowWriteAdapter()
    mock_t2 = MagicMock()
    mock_t2.review_file.side_effect = RuntimeError("Baserow connection timeout")
    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=mock_t2)
    req = service.build_sync_request("trk0050")
    req.tool2_decision = "NEW_MEDIA_CANDIDATE"

    res = service.synchronize("trk0050", commit=True, request=req)
    assert res.status == SyncStatus.DATABASE_UNAVAILABLE
    assert res.operation == SyncOperation.BLOCKED
    assert res.review_required is True
    assert not any(c["action"] == "create_row" for c in fake_db.calls)


# ---------------------------------------------------------------------------
# Test 51: Schema validation rejects invalid calendar date
# ---------------------------------------------------------------------------
def test_51_schema_validation_rejects_invalid_calendar_date():
    assert _is_complete_date("2024-02-29") is True
    assert _is_complete_date("2024-02-31") is False  # Feb 31 does not exist
    assert _is_complete_date("2023-02-29") is False  # 2023 is not leap year

    date_field = {"name": "Date", "type": "date"}
    with pytest.raises(BaserowSchemaError) as exc_info:
        validate_field_schema("Date", "2024-02-31", date_field)
    assert "invalid calendar date" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 52: Secret redaction removes tokens and bearer credentials
# ---------------------------------------------------------------------------
def test_52_secret_redaction_removes_tokens_and_bearer_credentials():
    raw_msg = "Error: Token secret_token_12345 failed, also Bearer secret_jwt_xyz and password=supersecret"
    clean_msg = redact_secrets(raw_msg)
    assert "secret_token_12345" not in clean_msg
    assert "secret_jwt_xyz" not in clean_msg
    assert "supersecret" not in clean_msg
    assert "[REDACTED]" in clean_msg


# ---------------------------------------------------------------------------
# Test 53: Request fingerprint is deterministic and stable
# ---------------------------------------------------------------------------
def test_53_request_fingerprint_deterministic_and_stable(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0053")
    fake_db = FakeBaserowWriteAdapter()
    service = MediaDatabaseUpdaterService(registry, fake_db)

    req1 = service.build_sync_request("trk0053")
    req2 = service.build_sync_request("trk0053")
    assert req1.request_fingerprint is not None
    assert req1.request_fingerprint == req2.request_fingerprint
    assert len(req1.request_fingerprint) == 64


# ---------------------------------------------------------------------------
# Test 54: Country mapper authoritative resolution and semantic equivalence
# ---------------------------------------------------------------------------
def test_54_country_mapper_authoritative_resolution():
    assert are_countries_equivalent("DE", "Germany") is True
    assert are_countries_equivalent("de", "germany") is True
    assert are_countries_equivalent("IN", "India") is True
    assert are_countries_equivalent("USA", "United States") is True
    assert are_countries_equivalent("Germany", "France") is False


# ---------------------------------------------------------------------------
# Test 55: Category normalization and equivalence
# ---------------------------------------------------------------------------
def test_55_category_normalization_and_equivalence():
    from media_archive_tooling.media_db_updater.engine import _normalize_category_key
    assert _normalize_category_key("BG") == "bhagavad gita"
    assert _normalize_category_key("SB") == "srimad bhagavatam"
    assert _normalize_category_key("CC") == "caitanyacaritamrta" or _normalize_category_key("CC") == "caitanya caritamrta"
    assert _normalize_category_key("Bhagavad-gita") == _normalize_category_key("BG")
    assert _normalize_category_key("Bhagavad Gita") == _normalize_category_key("BG")
    assert _normalize_category_key("Srimad-Bhagavatam") == _normalize_category_key("SB")


# ---------------------------------------------------------------------------
# Test 56 (R-013): Pre-create revalidation rejects incomplete or non-live Tool 2 results
# ---------------------------------------------------------------------------
def test_56_precreate_revalidation_rejects_incomplete_tool2_results(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0056")
    fake_db = FakeBaserowWriteAdapter()

    # Incomplete: live_read_complete is False
    t2_partial = make_mock_tool2(
        decision="NEW_MEDIA_CANDIDATE",
        live_read_complete=False,
        snapshot_complete=False,
        baserow_check_complete=False,
        database_state="LIVE_PARTIAL_OR_FAILED",
    )
    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=t2_partial)
    req = service.build_sync_request("trk0056")
    req.tool2_decision = "NEW_MEDIA_CANDIDATE"

    res = service.synchronize("trk0056", commit=True, request=req)
    assert res.status == SyncStatus.DATABASE_UNAVAILABLE
    assert res.operation == SyncOperation.BLOCKED
    assert res.review_required is True
    assert not any(c["action"] == "create_row" for c in fake_db.calls)

    # Incomplete: snapshot_complete is False
    t2_snap_fail = make_mock_tool2(decision="NEW_MEDIA_CANDIDATE", snapshot_complete=False)
    service_snap = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=t2_snap_fail)
    res_snap = service_snap.synchronize("trk0056", commit=True, request=req)
    assert res_snap.status == SyncStatus.DATABASE_UNAVAILABLE
    assert res_snap.operation == SyncOperation.BLOCKED
    assert not any(c["action"] == "create_row" for c in fake_db.calls)

    # Incomplete: database_state is OFFLINE
    t2_offline = make_mock_tool2(decision="NEW_MEDIA_CANDIDATE", database_state="OFFLINE")
    service_off = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=t2_offline)
    res_off = service_off.synchronize("trk0056", commit=True, request=req)
    assert res_off.status == SyncStatus.DATABASE_UNAVAILABLE
    assert res_off.operation == SyncOperation.BLOCKED
    assert not any(c["action"] == "create_row" for c in fake_db.calls)


# ---------------------------------------------------------------------------
# Test 57 (R-014): Parameterized semantic state positive eligibility
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("state_val,should_be_eligible", [
    ("exact", True),
    ("strong", True),
    ("provisional", False),
    ("ambiguous", False),
    ("unresolved", False),
    (None, False),
    ("", False),
    ("unexpected", False),
])
def test_57_positive_semantic_eligibility(tmp_path, state_val, should_be_eligible):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id=f"trk_{state_val}")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[{
        "id": 201,
        "Date": None,
        "Category": None,
        "Country": None,
        "Place, location": None,
        "Filename": "test.mp3",
    }])
    service = MediaDatabaseUpdaterService(registry, fake_db)
    req = service.build_sync_request(f"trk_{state_val}")
    req.tool2_decision = "EXISTING_MEDIA_MATCH"
    req.selected_media_row_id = 201

    req.when_state = state_val
    req.when_val = "2014-08-04"
    req.what_state = state_val
    req.what_category = "Bhagavad-gita"
    req.where_state = state_val
    req.where_country = "Germany"
    req.where_place = "Leipzig"

    res = service.preview(f"trk_{state_val}", request=req)
    diff_actions = {d.field_name: d.action for d in res.field_diffs}

    if should_be_eligible:
        assert diff_actions.get("Date") == FieldAction.SET
        assert diff_actions.get("Category") == FieldAction.SET
        assert diff_actions.get("Country") == FieldAction.SET
        assert diff_actions.get("Place, location") == FieldAction.SET
    else:
        assert diff_actions.get("Date") != FieldAction.SET
        assert diff_actions.get("Category") != FieldAction.SET
        assert diff_actions.get("Country") != FieldAction.SET
        assert diff_actions.get("Place, location") != FieldAction.SET


# ---------------------------------------------------------------------------
# Test 58 (R-014): Tool 1 evidence preservation in structured provenance
# ---------------------------------------------------------------------------
def test_58_tool1_evidence_preservation(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk_ev_01")
    record = registry.get_file("trk_ev_01")
    pr_dict = record["parser_result"]
    pr_dict["when"]["evidence"] = [{"source": "folder", "raw_value": "2014-08-04", "details": "parsed from folder"}]
    pr_dict["what"]["evidence"] = [{"source": "filename", "raw_value": "BG-01-18", "details": "parsed code"}]
    pr_dict["where"]["evidence"] = [{"source": "folder", "raw_value": "Leipzig-de", "details": "parsed where"}]
    with registry._get_conn() as conn:
        conn.cursor().execute("UPDATE files SET parser_result_json = ? WHERE tracking_id = ?", (json.dumps(pr_dict), "trk_ev_01"))
        conn.commit()

    fake_db = FakeBaserowWriteAdapter()
    service = MediaDatabaseUpdaterService(registry, fake_db)
    req = service.build_sync_request("trk_ev_01")

    assert req.when_provenance is not None
    assert len(req.when_provenance) == 1
    assert req.when_provenance[0]["raw_value"] == "2014-08-04"

    assert req.what_provenance is not None
    assert len(req.what_provenance) == 1
    assert req.what_provenance[0]["raw_value"] == "BG-01-18"

    assert req.where_provenance is not None
    assert len(req.where_provenance) == 1
    assert req.where_provenance[0]["raw_value"] == "Leipzig-de"


# ---------------------------------------------------------------------------
# Test 59 (R-015): Real renamer commit composition and durable synchronization
# ---------------------------------------------------------------------------
def test_59_renamer_commit_creates_configured_tool2_and_tool4(tmp_path):
    from media_archive_tooling.cli import create_media_db_updater_service

    registry = LocalRegistry(tmp_path / "test.db")
    fake_db = FakeBaserowWriteAdapter()
    mock_t2 = make_mock_tool2(decision="NEW_MEDIA_CANDIDATE")

    # Factory correctly wires tool2_service
    service = create_media_db_updater_service(
        registry=registry,
        write_adapter=fake_db,
        tool2_service=mock_t2,
    )
    assert service.tool2_service is mock_t2
    assert service.write_adapter is fake_db

    save_test_file(registry, tracking_id="trk0059")
    req = service.build_sync_request("trk0059")
    res = service.synchronize("trk0059", commit=True, request=req)
    assert res.status == SyncStatus.SYNCED
    assert any(c["action"] == "create_row" for c in fake_db.calls)

    persisted = registry.get_media_db_sync("trk0059")
    assert persisted is not None
    assert persisted["sync_status"] == "SYNCED"


# ---------------------------------------------------------------------------
# Test 60 (R-016): Field approval partial isolation, missing and stale preconditions
# ---------------------------------------------------------------------------
def test_60_field_approvals_safeguards(tmp_path):
    from media_archive_tooling.media_db_updater.models import FieldApproval, FieldApprovalAction

    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0060", date_val="2014-08-04", what_category="Bhagavad-gita")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[{
        "id": 305,
        "Date": "2015-09-09",
        "Category": "Srimad Bhagavatam",
        "Filename": "test.mp3",
    }])
    service = MediaDatabaseUpdaterService(registry, fake_db)

    # Missing precondition triggers CONFLICT and is not applied
    req = service.build_sync_request("trk0060")
    req.tool2_decision = "EXISTING_MEDIA_MATCH"
    req.selected_media_row_id = 305
    req.field_approvals = {
        "Date": {
            "action": "APPLY_CORRECTION",
            "approved_value": "2014-08-04",
            "has_reviewed_precondition": False,  # Missing precondition!
        }
    }
    res_miss = service.synchronize("trk0060", commit=True, request=req)
    assert res_miss.status == SyncStatus.REVIEW_REQUIRED
    assert fake_db.rows[305]["Date"] == "2015-09-09"

    # Stale precondition: reviewed value "2015-01-01" != DB live "2015-09-09"
    req.field_approvals = {
        "Date": FieldApproval(
            field_name="Date",
            action=FieldApprovalAction.APPLY_CORRECTION,
            approved_value="2014-08-04",
            has_reviewed_precondition=True,
            reviewed_precondition_value="2015-01-01",  # Stale!
        )
    }
    res_stale = service.synchronize("trk0060", commit=True, request=req)
    assert res_stale.status == SyncStatus.REVIEW_REQUIRED
    assert fake_db.rows[305]["Date"] == "2015-09-09"

    # Partial approval: approved Date does NOT unlock unapproved Category conflict
    req.field_approvals = {
        "Date": FieldApproval(
            field_name="Date",
            action=FieldApprovalAction.APPLY_CORRECTION,
            approved_value="2014-08-04",
            has_reviewed_precondition=True,
            reviewed_precondition_value="2015-09-09",
        )
    }
    res_part = service.synchronize("trk0060", commit=True, request=req)
    # Overall status is still REVIEW_REQUIRED due to unapproved Category conflict
    assert res_part.status == SyncStatus.REVIEW_REQUIRED
    # But Date was resolved and Category remains CONFLICT
    date_diff = next(d for d in res_part.field_diffs if d.field_name == "Date")
    cat_diff = next(d for d in res_part.field_diffs if d.field_name == "Category")
    assert date_diff.action == FieldAction.SET
    assert cat_diff.action == FieldAction.CONFLICT

    # Resolve Category with KEEP_DATABASE
    req.field_approvals["Category"] = FieldApproval(
        field_name="Category",
        action=FieldApprovalAction.KEEP_DATABASE,
        has_reviewed_precondition=True,
        reviewed_precondition_value="Srimad Bhagavatam",
    )
    res_all = service.synchronize("trk0060", commit=True, request=req)
    assert res_all.status == SyncStatus.SYNCED
    assert fake_db.rows[305]["Date"] == "2014-08-04"
    assert fake_db.rows[305]["Category"] == "Srimad Bhagavatam"


# ---------------------------------------------------------------------------
# Test 61 (R-017): Live schema mismatch handling and no partial option mutation
# ---------------------------------------------------------------------------
def test_61_schema_mismatches_and_no_partial_mutation(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0061")
    fake_db = FakeBaserowWriteAdapter()
    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=make_mock_tool2())

    # Case A: Column removed from live schema
    fake_db.fields = [f for f in fake_db.fields if f["name"] != "Date"]
    req = service.build_sync_request("trk0061")
    req.tool2_decision = "NEW_MEDIA_CANDIDATE"

    res_missing = service.synchronize("trk0061", commit=True, request=req)
    assert res_missing.status == SyncStatus.FAILED_BLOCKED
    assert "Missing field in schema" in (res_missing.error_message or "")
    assert not any(c["action"] == "create_row" for c in fake_db.calls)

    # Case B: Column type changed to incompatible type (Title changed to number)
    fake_db2 = FakeBaserowWriteAdapter()
    for f in fake_db2.fields:
        if f["name"] == "Title":
            f["type"] = "number"
    service2 = MediaDatabaseUpdaterService(registry, fake_db2, tool2_service=make_mock_tool2())
    res_type = service2.synchronize("trk0061", commit=True, request=req)
    assert res_type.status == SyncStatus.FAILED_BLOCKED
    assert "unsupported or incompatible" in (res_type.error_message or "")
    # Verify no country/place select option was created
    assert not any(c["action"] == "add_select_option" for c in fake_db2.calls)

    # Case C: Duplicate column names after normalization
    fake_db3 = FakeBaserowWriteAdapter()
    fake_db3.fields.append({"id": 999, "name": "title", "type": "text"})  # duplicate of Title
    service3 = MediaDatabaseUpdaterService(registry, fake_db3, tool2_service=make_mock_tool2())
    res_dup = service3.synchronize("trk0061", commit=True, request=req)
    assert res_dup.status == SyncStatus.FAILED_BLOCKED
    assert "Duplicate or ambiguous" in (res_dup.error_message or "")


# ---------------------------------------------------------------------------
# Test 62 (R-018): Portal retry parity and preview badge
# ---------------------------------------------------------------------------
def test_62_portal_retry_parity_and_preview_badge(tmp_path):
    try:
        registry = LocalRegistry(tmp_path / "test.db")
        save_test_file(registry, tracking_id="trk0062")
        configure_review_context(registry_path=tmp_path / "test.db")
        client = TestClient(app)

        # Save DATABASE_UNAVAILABLE state
        registry.save_media_db_sync(
            tracking_id="trk0062",
            sync_status="DATABASE_UNAVAILABLE",
            operation_type="BLOCKED",
        )
        resp = client.get("/file/trk0062")
        assert resp.status_code == 200
        assert "DATABASE_UNAVAILABLE" in resp.text
        # Retry button must be present for DATABASE_UNAVAILABLE
        assert "Retry Pending Sync" in resp.text

        # Save PREVIEW state
        registry.save_media_db_sync(
            tracking_id="trk0062",
            sync_status="PREVIEW",
            operation_type="UPDATE",
        )
        resp_prev = client.get("/file/trk0062")
        assert resp_prev.status_code == 200
        assert "PREVIEW" in resp_prev.text
        assert "#0284c7" in resp_prev.text
    finally:
        configure_review_context()


# ---------------------------------------------------------------------------
# Test 63 (R-019): Audit persistence secret redaction and fingerprint completeness
# ---------------------------------------------------------------------------
def test_63_audit_persistence_secret_redaction_and_fingerprint(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    secret_req = json.dumps({
        "tracking_id": "trk0063",
        "api_token": "Token secret_token_value_abc123",
        "nested": {"bearer": "Bearer topsecretjwt"},
    })
    secret_res = json.dumps({
        "error": "Failed with password: supersecretpass",
        "headers": "Authorization: Bearer mysecrettoken",
    })
    secret_err = "Request error on https://api.baserow.io/?key=verysecretkey"

    registry.save_media_db_sync(
        tracking_id="trk0063",
        sync_status="FAILED_BLOCKED",
        request_json=secret_req,
        result_json=secret_res,
        error_message=secret_err,
    )

    persisted = registry.get_media_db_sync("trk0063")
    assert persisted is not None
    assert "secret_token_value_abc123" not in persisted["request_json"]
    assert "topsecretjwt" not in persisted["request_json"]
    assert "supersecretpass" not in persisted["result_json"]
    assert "mysecrettoken" not in persisted["result_json"]
    assert "verysecretkey" not in persisted["error_message"]
    assert "[REDACTED]" in persisted["request_json"]
    assert "[REDACTED]" in persisted["result_json"]
    assert "[REDACTED]" in persisted["error_message"]

    # Fingerprint includes current_path
    save_test_file(registry, tracking_id="trk0063_fp1", current_path="/path1/test.mp3")
    save_test_file(registry, tracking_id="trk0063_fp2", current_path="/path2/test.mp3")
    fake_db = FakeBaserowWriteAdapter()
    service = MediaDatabaseUpdaterService(registry, fake_db)
    req1 = service.build_sync_request("trk0063_fp1")
    req2 = service.build_sync_request("trk0063_fp2")
    assert req1.request_fingerprint != req2.request_fingerprint


# ---------------------------------------------------------------------------
# Test 64 (R-020): Complete ISO-3166-1 country mapping and invalid code handling
# ---------------------------------------------------------------------------
def test_64_complete_iso_mapping_and_invalid_codes():
    from media_archive_tooling.media_db_updater.country_mapper import (
        get_country_name_for_iso,
        is_valid_country_display_name,
    )

    # Valid countries
    assert get_country_name_for_iso("GH") == "Ghana"
    assert get_country_name_for_iso("gh") == "Ghana"
    assert get_country_name_for_iso("IS") == "Iceland"
    assert get_country_name_for_iso("is") == "Iceland"
    assert get_country_name_for_iso("UK") == "United Kingdom"

    # Unknown or invalid codes
    assert get_country_name_for_iso("XX") is None
    assert get_country_name_for_iso("") is None
    assert get_country_name_for_iso(None) is None

    # Valid display names vs raw two-letter codes
    assert is_valid_country_display_name("Ghana") is True
    assert is_valid_country_display_name("Iceland") is True
    assert is_valid_country_display_name("GH") is False
    assert is_valid_country_display_name("IS") is False
    assert is_valid_country_display_name("UnknownCountry") is False


# ---------------------------------------------------------------------------
# Test 65 (R-023): Pre-create completeness verification fails closed
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kw,expected_note", [
    ({"live_read_complete": None}, "live_read_complete is not True"),
    ({"live_read_complete": False}, "live_read_complete is not True"),
    ({"snapshot_complete": None}, "snapshot_complete is not True"),
    ({"snapshot_complete": False}, "snapshot_complete is not True"),
    ({"baserow_check_complete": None}, "baserow_check_complete is not True"),
    ({"baserow_check_complete": False}, "baserow_check_complete is not True"),
    ({"database_state": None}, "database_state 'None' not in ('LIVE_CURRENT', 'LIVE_COMPLETE')"),
    ({"database_state": "LIVE_HEALTHY"}, "database_state 'LIVE_HEALTHY' not in ('LIVE_CURRENT', 'LIVE_COMPLETE')"),
    ({"database_state": "DATABASE_UNAVAILABLE"}, "database_state 'DATABASE_UNAVAILABLE' not in ('LIVE_CURRENT', 'LIVE_COMPLETE')"),
    ({"database_state": "OFFLINE"}, "database_state 'OFFLINE' not in ('LIVE_CURRENT', 'LIVE_COMPLETE')"),
    ({"database_state": "BOGUS"}, "database_state 'BOGUS' not in ('LIVE_CURRENT', 'LIVE_COMPLETE')"),
])
def test_65_r023_precreate_completeness_verification_hardened(tmp_path, kw, expected_note):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0065")
    fake_db = FakeBaserowWriteAdapter()

    # Base valid args
    rev_args = {
        "decision": "NEW_MEDIA_CANDIDATE",
        "live_read_complete": True,
        "snapshot_complete": True,
        "baserow_check_complete": True,
        "database_state": "LIVE_CURRENT",
    }
    rev_args.update(kw)
    mock_t2 = make_mock_tool2(**rev_args)

    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=mock_t2)
    res = service.synchronize("trk0065", commit=True)

    assert res.status == SyncStatus.DATABASE_UNAVAILABLE
    assert res.operation == SyncOperation.BLOCKED
    assert res.review_required is True
    assert not any(c["action"] == "create_row" for c in fake_db.calls)
    assert any(expected_note in note for note in res.diagnostic_notes)


def test_65_r023_valid_complete_contract_creates_row(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0065_ok")
    fake_db = FakeBaserowWriteAdapter()

    mock_t2 = make_mock_tool2(
        decision="NEW_MEDIA_CANDIDATE",
        live_read_complete=True,
        snapshot_complete=True,
        baserow_check_complete=True,
        database_state="LIVE_CURRENT",
    )
    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=mock_t2)
    res = service.synchronize("trk0065_ok", commit=True)
    assert res.status == SyncStatus.SYNCED
    assert any(c["action"] == "create_row" for c in fake_db.calls)


# ---------------------------------------------------------------------------
# Test 66 (R-024): Tool 2 decision update gate default-deny and association authority
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("decision", [
    "BOGUS",
    None,
    "",
    "PROBABLE_EXISTING_MEDIA",
    "MULTIPLE_CANDIDATES",
    "CONFLICT_WITH_EXISTING",
    "INSUFFICIENT_EVIDENCE",
])
def test_66_r024_non_match_decisions_cannot_update(tmp_path, decision):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0066")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[{"id": 7, "Filename": "old.mp3"}])
    service = MediaDatabaseUpdaterService(registry, fake_db)

    req = service.build_sync_request("trk0066")
    req.tool2_decision = decision
    req.selected_media_row_id = 7

    res = service.synchronize("trk0066", commit=True, request=req)
    assert res.status == SyncStatus.REVIEW_REQUIRED
    assert res.operation == SyncOperation.BLOCKED
    assert not any(c["action"] == "patch_row" for c in fake_db.calls)


def test_66_r024_arbitrary_field_approval_rejected_as_association(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0066_arb")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[{"id": 7, "Filename": "old.mp3"}])
    service = MediaDatabaseUpdaterService(registry, fake_db)

    req = service.build_sync_request("trk0066_arb")
    req.tool2_decision = "BOGUS"
    req.selected_media_row_id = 7
    # Arbitrary field approval on Title with choose_association must NOT grant association authority
    req.field_approvals = {
        "Title": {
            "action": "choose_association",
            "has_reviewed_precondition": True,
            "reviewed_precondition_value": "old.mp3",
        }
    }

    res = service.synchronize("trk0066_arb", commit=True, request=req)
    assert res.status == SyncStatus.REVIEW_REQUIRED
    assert res.operation == SyncOperation.BLOCKED
    assert not any(c["action"] == "patch_row" for c in fake_db.calls)


def test_66_r024_stale_association_approval_rejected(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0066_stale")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[{"id": 7, "Filename": "old.mp3"}])
    service = MediaDatabaseUpdaterService(registry, fake_db)

    req = service.build_sync_request("trk0066_stale")
    req.tool2_decision = "MULTIPLE_CANDIDATES"
    req.selected_media_row_id = 7
    req.association_approval = {
        "selected_media_row_id": 7,
        "reviewed_candidate_row_id": 99,  # Mismatched candidate ID
        "has_reviewed_precondition": True,
        "action": "choose_association",
        "reviewer": "human_reviewer",
    }

    res = service.synchronize("trk0066_stale", commit=True, request=req)
    assert res.status == SyncStatus.REVIEW_REQUIRED
    assert res.operation == SyncOperation.BLOCKED
    assert not any(c["action"] == "patch_row" for c in fake_db.calls)


def test_66_r024_valid_association_approval_succeeds(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0066_assoc")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[{"id": 7, "Filename": "old.mp3"}])
    service = MediaDatabaseUpdaterService(registry, fake_db)

    req = service.build_sync_request("trk0066_assoc")
    req.tool2_decision = "MULTIPLE_CANDIDATES"
    req.selected_media_row_id = 7
    req.association_approval = {
        "selected_media_row_id": 7,
        "reviewed_candidate_row_id": 7,
        "reviewed_precondition_filename": "old.mp3",
        "has_reviewed_precondition": True,
        "action": "choose_association",
        "reviewer": "human_reviewer",
    }

    res = service.synchronize("trk0066_assoc", commit=True, request=req)
    assert res.status == SyncStatus.SYNCED
    assert res.operation == SyncOperation.UPDATE
    assert any(c["action"] == "patch_row" for c in fake_db.calls)


# ---------------------------------------------------------------------------
# Test 67 (R-025): Field approval parsing fails closed and portal route tests
# ---------------------------------------------------------------------------
def test_67_r025_get_approval_fails_closed():
    from media_archive_tooling.media_db_updater.models import MediaDbSyncRequest

    # Explicit has_reviewed_precondition=False is honored even with value key present
    req = MediaDbSyncRequest(
        tracking_id="trk0067",
        current_filename="test.mp3",
        current_path="/test.mp3",
        field_approvals={
            "Title": {
                "action": "apply_correction",
                "approved_value": "New Title",
                "has_reviewed_precondition": False,
                "reviewed_precondition_value": "Old Title",
            },
            "Date": {
                "action": "INVALID_ACTION",
                "approved_value": "2015-09-09",
            },
            "Category": {
                "approved_value": "Srimad Bhagavatam",
            }
        }
    )

    appr_title = req.get_approval("Title")
    assert appr_title is not None
    assert appr_title.has_reviewed_precondition is False
    assert appr_title.reviewed_precondition_value is None

    # Invalid action string returns None (rejected)
    assert req.get_approval("Date") is None
    # Missing action returns None (rejected)
    assert req.get_approval("Category") is None


def test_67_r025_portal_post_routes(tmp_path):
    from fastapi.testclient import TestClient
    from media_archive_tooling.review_portal.app import app, configure_review_context

    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0067_p")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[{"id": 10, "Filename": "old.mp3", "Tag": ["SB 03.06.06"]}])
    service = MediaDatabaseUpdaterService(registry, fake_db)

    try:
        configure_review_context(registry_path=tmp_path / "test.db", media_db_updater_service=service)
        client = TestClient(app)

        # 1. Post keep_database
        res = client.post(
            "/file/trk0067_p/media-db-field-approval",
            data={
                "field_name": "Date",
                "action": "keep_database",
                "reviewed_precondition_value": "2015-08-27",
                "has_reviewed_precondition": "true",
            },
            follow_redirects=False,
        )
        assert res.status_code == 303

        # 2. Post apply_correction
        res = client.post(
            "/file/trk0067_p/media-db-field-approval",
            data={
                "field_name": "Title",
                "action": "apply_correction",
                "approved_value": "New Title",
                "reviewed_precondition_value": "Old Title",
                "has_reviewed_precondition": "true",
            },
            follow_redirects=False,
        )
        assert res.status_code == 303

        # 3. Post defer
        res = client.post(
            "/file/trk0067_p/media-db-field-approval",
            data={
                "field_name": "Category",
                "action": "defer",
                "has_reviewed_precondition": "true",
            },
            follow_redirects=False,
        )
        assert res.status_code == 303

        # 4. Post with structured Tag JSON precondition
        res = client.post(
            "/file/trk0067_p/media-db-field-approval",
            data={
                "field_name": "Tag",
                "action": "apply_correction",
                "approved_value": "SB 01.02.03",
                "reviewed_precondition_json": json.dumps(["SB 03.06.06"]),
                "has_reviewed_precondition": "true",
            },
            follow_redirects=False,
        )
        assert res.status_code == 303

        # Verify stored Tag approval preserved list type
        stored_sync = registry.get_media_db_sync("trk0067_p")
        tag_appr = stored_sync["request"]["field_approvals"]["Tag"]
        assert tag_appr["reviewed_precondition_value"] == ["SB 03.06.06"]

        # 5. Missing action -> 400
        res = client.post(
            "/file/trk0067_p/media-db-field-approval",
            data={"field_name": "Date"},
            follow_redirects=False,
        )
        assert res.status_code == 400

        # 6. Invalid action -> 400
        res = client.post(
            "/file/trk0067_p/media-db-field-approval",
            data={"field_name": "Date", "action": "bogus_action"},
            follow_redirects=False,
        )
        assert res.status_code == 400

        # 7. Post media-db-association route
        res = client.post(
            "/file/trk0067_p/media-db-association",
            data={
                "selected_media_row_id": 10,
                "reviewed_candidate_row_id": 10,
                "reviewed_precondition_filename": "old.mp3",
            },
            follow_redirects=False,
        )
        assert res.status_code == 303
        stored_sync = registry.get_media_db_sync("trk0067_p")
        assoc_stored = stored_sync["request"]["association_approval"]
        assert assoc_stored["selected_media_row_id"] == 10
        assert assoc_stored["reviewed_candidate_row_id"] == 10

    finally:
        configure_review_context()


# ---------------------------------------------------------------------------
# Test 68 (R-026): Key-based secret redaction and audit timestamp integrity
# ---------------------------------------------------------------------------
def test_68_r026_key_based_secret_redaction(tmp_path):
    from media_archive_tooling.media_db_updater.write_adapter import redact_secrets

    # Plain key-named sensitive fields in dictionary
    payload = {
        "api_token": "VERYSECRET",
        "password": "HUSH",
        "nested": {
            "auth_token": "TOK123",
            "normal_field": "public_data",
            "client_secret": "SHH",
        },
        "items": [
            {"access_token": "TOK456", "name": "safe"},
        ]
    }
    redacted = redact_secrets(payload)
    assert redacted["api_token"] == "[REDACTED]"
    assert redacted["password"] == "[REDACTED]"
    assert redacted["nested"]["auth_token"] == "[REDACTED]"
    assert redacted["nested"]["normal_field"] == "public_data"
    assert redacted["nested"]["client_secret"] == "[REDACTED]"
    assert redacted["items"][0]["access_token"] == "[REDACTED]"
    assert redacted["items"][0]["name"] == "safe"

    # Serialized JSON string with sensitive keys
    json_str = json.dumps({"api_token": "VERYSECRET", "password": "HUSH", "safe": 123})
    redacted_str = redact_secrets(json_str)
    parsed = json.loads(redacted_str)
    assert parsed["api_token"] == "[REDACTED]"
    assert parsed["password"] == "[REDACTED]"
    assert parsed["safe"] == 123

    # SQLite registry round-trip
    registry = LocalRegistry(tmp_path / "test.db")
    registry.save_media_db_sync(
        tracking_id="trk0068",
        sync_status="PENDING_SYNC",
        request_json=json_str,
    )
    persisted = registry.get_media_db_sync("trk0068")
    assert "VERYSECRET" not in persisted["request_json"]
    assert "HUSH" not in persisted["request_json"]
    assert "[REDACTED]" in persisted["request_json"]


def test_68_r026_live_read_timestamp_not_fabricated(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0068_ts")
    fake_db = FakeBaserowWriteAdapter()
    service = MediaDatabaseUpdaterService(registry, fake_db)

    req = service.build_sync_request("trk0068_ts")
    req.live_query_timestamp = None
    req.tool2_timestamp = "2026-09-17T10:00:00Z"
    req.tool2_database_state = "LIVE_CURRENT"

    res = service.preview("trk0068_ts", request=req)
    prov = res.audit_provenance
    assert prov["live_read_timestamp"] == "UNAVAILABLE"
    assert prov["tool2_snapshot_timestamp"] == "2026-09-17T10:00:00Z"
    assert prov["tool2_database_state"] == "LIVE_CURRENT"


# ---------------------------------------------------------------------------
# Test 69 (Q-001): Media Archive link policy on create and update
# ---------------------------------------------------------------------------
def test_69_q001_media_archive_link_policy(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, tracking_id="trk0069_c")
    save_test_file(registry, tracking_id="trk0069_u")

    fake_db = FakeBaserowWriteAdapter(initial_rows=[{
        "id": 50,
        "Filename": "test.mp3",
        "Media Archive link": "https://archive.org/details/kks-2015-08-27",
    }])
    mock_t2_create = make_mock_tool2(decision="NEW_MEDIA_CANDIDATE")
    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=mock_t2_create)

    # 1. On create, Media Archive link is omitted
    res_c = service.preview("trk0069_c")
    assert not any(d.field_name == "Media Archive link" for d in res_c.field_diffs)

    # 2. If request attempts to populate Media Archive link on create -> CONFLICT
    req_c = service.build_sync_request("trk0069_c")
    req_c.field_approvals = {
        "Media Archive link": {
            "action": "apply_correction",
            "approved_value": "https://archive.org/details/bogus",
            "has_reviewed_precondition": True,
        }
    }
    res_c_blocked = service.preview("trk0069_c", request=req_c)
    assert any("Media Archive link cannot be populated on creation" in c for c in res_c_blocked.conflicts)

    # 3. On update, existing Media Archive link is PRESERVED
    req_u = service.build_sync_request("trk0069_u")
    req_u.tool2_decision = "EXISTING_MEDIA_MATCH"
    req_u.selected_media_row_id = 50

    res_u = service.preview("trk0069_u", request=req_u)
    mal_diff = next(d for d in res_u.field_diffs if d.field_name == "Media Archive link")
    assert mal_diff.action == FieldAction.PRESERVED
    assert mal_diff.old_value == "https://archive.org/details/kks-2015-08-27"
    assert mal_diff.new_value == "https://archive.org/details/kks-2015-08-27"

    # 4. If request attempts to modify Media Archive link on update -> CONFLICT
    req_u.field_approvals = {
        "Media Archive link": {
            "action": "apply_correction",
            "approved_value": "https://new-url.org",
            "has_reviewed_precondition": True,
            "reviewed_precondition_value": "https://archive.org/details/kks-2015-08-27",
        }
    }
    res_u_mod = service.preview("trk0069_u", request=req_u)
    assert any("Media Archive link modification is unsupported" in c for c in res_u_mod.conflicts)


# ---------------------------------------------------------------------------
# Test 70: Stored match changes to blocking decisions or different row ID (R-031)
# ---------------------------------------------------------------------------
def test_70_stored_match_changes_to_blocking_decisions_or_different_row_id(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[
        {"id": 500, "Filename": "2014-08-04_kks.mp3"},
        {"id": 501, "Filename": "other.mp3"},
    ])
    save_test_file(registry, tracking_id="trk0070")

    # 1. Fresh Tool 2 returns MULTIPLE_CANDIDATES -> blocked
    mock_t2_multi = make_mock_tool2(decision="MULTIPLE_CANDIDATES", row_id=None, tracking_id="trk0070")
    svc_multi = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=mock_t2_multi)
    req_multi = svc_multi.build_sync_request("trk0070")
    req_multi.tool2_decision = "EXISTING_MEDIA_MATCH"
    req_multi.selected_media_row_id = 500
    res_multi = svc_multi.synchronize("trk0070", commit=True, request=req_multi)
    assert res_multi.status == SyncStatus.REVIEW_REQUIRED
    assert res_multi.operation == SyncOperation.CONFLICT
    assert any("TOOL2_DECISION_CHANGED" in c for c in res_multi.conflicts)

    # 2. Fresh Tool 2 returns CONFLICT_WITH_EXISTING -> blocked
    mock_t2_conf = make_mock_tool2(decision="CONFLICT_WITH_EXISTING", row_id=500, tracking_id="trk0070")
    svc_conf = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=mock_t2_conf)
    req_conf = svc_conf.build_sync_request("trk0070")
    req_conf.tool2_decision = "EXISTING_MEDIA_MATCH"
    req_conf.selected_media_row_id = 500
    res_conf = svc_conf.synchronize("trk0070", commit=True, request=req_conf)
    assert res_conf.status == SyncStatus.REVIEW_REQUIRED
    assert any("TOOL2_DECISION_CHANGED" in c for c in res_conf.conflicts)

    # 3. Fresh Tool 2 returns INSUFFICIENT_EVIDENCE -> blocked
    mock_t2_insuff = make_mock_tool2(decision="INSUFFICIENT_EVIDENCE", row_id=None, tracking_id="trk0070")
    svc_insuff = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=mock_t2_insuff)
    req_insuff = svc_insuff.build_sync_request("trk0070")
    req_insuff.tool2_decision = "EXISTING_MEDIA_MATCH"
    req_insuff.selected_media_row_id = 500
    res_insuff = svc_insuff.synchronize("trk0070", commit=True, request=req_insuff)
    assert res_insuff.status == SyncStatus.REVIEW_REQUIRED
    assert any("TOOL2_DECISION_CHANGED" in c for c in res_insuff.conflicts)

    # 4. Fresh Tool 2 returns DATABASE_UNAVAILABLE -> database unavailable
    mock_t2_unavail = make_mock_tool2(decision="DATABASE_UNAVAILABLE", row_id=500, database_state="DATABASE_UNAVAILABLE", tracking_id="trk0070")
    svc_unavail = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=mock_t2_unavail)
    req_unavail = svc_unavail.build_sync_request("trk0070")
    req_unavail.tool2_decision = "EXISTING_MEDIA_MATCH"
    req_unavail.selected_media_row_id = 500
    res_unavail = svc_unavail.synchronize("trk0070", commit=True, request=req_unavail)
    assert res_unavail.status == SyncStatus.DATABASE_UNAVAILABLE

    # 5. Fresh Tool 2 returns DIFFERENT selected row ID -> blocked
    mock_t2_diff_row = make_mock_tool2(decision="EXISTING_MEDIA_MATCH", row_id=501, tracking_id="trk0070")
    svc_diff = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=mock_t2_diff_row)
    req_diff = svc_diff.build_sync_request("trk0070")
    req_diff.tool2_decision = "EXISTING_MEDIA_MATCH"
    req_diff.selected_media_row_id = 500
    res_diff = svc_diff.synchronize("trk0070", commit=True, request=req_diff)
    assert res_diff.status == SyncStatus.REVIEW_REQUIRED
    assert any("TOOL2_SELECTED_ROW_CHANGED" in c for c in res_diff.conflicts)

    # 6. Fresh Tool 2 confirms SAME row ID 500 -> update succeeds
    mock_t2_ok = make_mock_tool2(decision="EXISTING_MEDIA_MATCH", row_id=500, tracking_id="trk0070")
    svc_ok = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=mock_t2_ok)
    req_ok = svc_ok.build_sync_request("trk0070")
    req_ok.tool2_decision = "EXISTING_MEDIA_MATCH"
    req_ok.selected_media_row_id = 500
    res_ok = svc_ok.synchronize("trk0070", commit=True, request=req_ok)
    assert res_ok.status == SyncStatus.SYNCED
    assert res_ok.operation == SyncOperation.UPDATE


# ---------------------------------------------------------------------------
# Test 71: retry_pending uses fresh Tool 2 gate (R-031)
# ---------------------------------------------------------------------------
def test_71_retry_pending_uses_fresh_tool2_gate(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[
        {"id": 550, "Filename": "2014-08-04_kks.mp3"},
    ])
    save_test_file(registry, tracking_id="trk0071")

    # Stored sync record in pending state with old match decision
    registry.save_media_db_sync(
        tracking_id="trk0071",
        sync_status="PENDING_SYNC",
        attempt_count=1,
    )

    # Live Tool 2 fresh review now reports CONFLICT_WITH_EXISTING
    mock_t2_fresh = make_mock_tool2(decision="CONFLICT_WITH_EXISTING", row_id=550, tracking_id="trk0071")
    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=mock_t2_fresh)

    results = service.retry_pending(["trk0071"])
    assert len(results) == 1
    res = results[0]
    # Fresh check must block the update rather than relying on stale cached review
    assert res.status == SyncStatus.REVIEW_REQUIRED
    assert res.operation == SyncOperation.BLOCKED


# ---------------------------------------------------------------------------
# Test 72: RenameCommitService never calls Tool 4 on INITIAL mode (R-032)
# ---------------------------------------------------------------------------
def test_72_rename_commit_service_never_calls_tool4_on_initial_mode(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    f = media_dir / "test.mp3"
    f.write_text("data")

    save_test_file(registry, tracking_id="trk0072", original_path=str(f), current_path=str(f), status="approved")
    registry.save_proposal(RenameProposal(
        tracking_id="trk0072",
        original_path=str(f),
        current_filename="test.mp3",
        proposed_filename="renamed_test_ID-trk0072.mp3",
        proposed_path=str(media_dir / "renamed_test_ID-trk0072.mp3"),
        mode=RenameMode.INITIAL,
        status="approved",
        parser_result=make_parser_result(tracking_id="trk0072", orig_filename="test.mp3"),
    ))

    mock_updater = MagicMock()

    # Case A: RenameMode.INITIAL must NOT trigger Tool 4
    svc_init = RenameCommitService(registry, mode=RenameMode.INITIAL, media_db_updater_service=mock_updater)
    svc_init.commit_file("trk0072")
    assert mock_updater.synchronize.call_count == 0
    assert registry.get_media_db_sync("trk0072") is None

    # Case B: RenameMode.FINALIZE DOES trigger Tool 4
    f_fin = media_dir / "test_fin.mp3"
    f_fin.write_text("data")
    save_test_file(registry, tracking_id="trk0072_fin", original_path=str(f_fin), current_path=str(f_fin), status="approved")
    registry.save_proposal(RenameProposal(
        tracking_id="trk0072_fin",
        original_path=str(f_fin),
        current_filename="test_fin.mp3",
        proposed_filename="renamed_fin.mp3",
        proposed_path=str(media_dir / "renamed_fin.mp3"),
        mode=RenameMode.FINALIZE,
        status="approved",
        parser_result=make_parser_result(tracking_id="trk0072_fin", orig_filename="test_fin.mp3"),
    ))
    svc_fin = RenameCommitService(registry, mode=RenameMode.FINALIZE, media_db_updater_service=mock_updater)
    svc_fin.commit_file("trk0072_fin")
    assert mock_updater.synchronize.call_count == 1
    assert registry.get_media_db_sync("trk0072_fin") is not None


# ---------------------------------------------------------------------------
# Test 73: CLI run_renamer production pipeline orchestration (R-032)
# ---------------------------------------------------------------------------
def test_73_cli_run_renamer_production_pipeline_orchestration(tmp_path):
    from media_archive_tooling.cli import run_renamer

    registry_path = tmp_path / "cli.db"
    media_dir = tmp_path / "media_cli"
    media_dir.mkdir()
    f1 = media_dir / "2014-08-04_sample.mp3"
    f1.write_text("data")

    class Args:
        pass

    args = Args()
    args.target = str(media_dir)
    args.mode = "initial"
    args.commit = False
    args.registry_path = str(registry_path)
    args.log_dir = str(tmp_path / "logs")

    # Initial mode scan runs without error and leaves zero Tool 4 sync records
    run_renamer(args)
    reg = LocalRegistry(registry_path)
    assert len(reg.list_files()) == 1
    assert len(reg.list_pending_media_db_syncs()) == 0


# ---------------------------------------------------------------------------
# Test 74: Tool 2 contract validation rejects non-models and missing timestamps (R-033)
# ---------------------------------------------------------------------------
def test_74_tool2_contract_validation_rejects_non_models_and_missing_timestamps(tmp_path):
    from media_archive_tooling.media_db_updater.engine import validate_tool2_review_result

    # 1. Arbitrary non-model object without contract -> rejected
    class FakeBag:
        tracking_id = "trk0074"
        decision = "NEW_MEDIA_CANDIDATE"
        live_read_complete = True
        snapshot_complete = True
        baserow_check_complete = True
        database_state = "LIVE_CURRENT"

    res, err = validate_tool2_review_result(FakeBag(), "trk0074")
    assert res is None
    assert "not MediaDatabaseReviewResult" in err

    # 2. Missing/empty live read timestamp -> rejected
    real_res_no_ts = MediaDatabaseReviewResult(
        tracking_id="trk0074",
        decision=ReviewDecision.NEW_MEDIA_CANDIDATE,
        live_read_complete=True,
        snapshot_complete=True,
        baserow_check_complete=True,
        database_state="LIVE_CURRENT",
        baserow_read_at="",
        database_snapshot_at="",
    )
    res_ts, err_ts = validate_tool2_review_result(real_res_no_ts, "trk0074")
    assert res_ts is None
    assert "live-read timestamp is missing" in err_ts

    # 3. Valid model with timestamp -> accepted
    real_res_ok = MediaDatabaseReviewResult(
        tracking_id="trk0074",
        decision=ReviewDecision.NEW_MEDIA_CANDIDATE,
        live_read_complete=True,
        snapshot_complete=True,
        baserow_check_complete=True,
        database_state="LIVE_CURRENT",
        baserow_read_at="2026-09-17T12:00:00Z",
    )
    res_ok, err_ok = validate_tool2_review_result(real_res_ok, "trk0074")
    assert res_ok is not None
    assert err_ok is None


# ---------------------------------------------------------------------------
# Test 75: Association approval precondition filename validation (R-033)
# ---------------------------------------------------------------------------
def test_75_association_approval_precondition_filename_validation(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[
        {"id": 600, "Filename": "expected_file.mp3"},
    ])
    save_test_file(registry, tracking_id="trk0075")

    mock_t2 = make_mock_tool2(decision="CONFLICT_WITH_EXISTING", row_id=600, tracking_id="trk0075")
    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=mock_t2)

    # 1. Missing reviewed_precondition_filename -> blocked
    res_missing = service.apply_association_approval(
        tracking_id="trk0075",
        selected_media_row_id=600,
        reviewed_candidate_row_id=600,
        reviewed_precondition_filename=None,
        commit=False,
    )
    assert res_missing.status == SyncStatus.REVIEW_REQUIRED
    assert any("ASSOCIATION_PRECONDITION_FAILED" in c for c in res_missing.conflicts)

    # 2. Mismatched reviewed_precondition_filename -> blocked
    res_mismatch = service.apply_association_approval(
        tracking_id="trk0075",
        selected_media_row_id=600,
        reviewed_candidate_row_id=600,
        reviewed_precondition_filename="different_file.mp3",
        commit=False,
    )
    assert res_mismatch.status == SyncStatus.REVIEW_REQUIRED
    assert any("ASSOCIATION_PRECONDITION_FAILED" in c for c in res_mismatch.conflicts)

    # 3. Matching reviewed_precondition_filename -> allowed
    res_match = service.apply_association_approval(
        tracking_id="trk0075",
        selected_media_row_id=600,
        reviewed_candidate_row_id=600,
        reviewed_precondition_filename="expected_file.mp3",
        commit=True,
    )
    assert res_match.status == SyncStatus.SYNCED
    assert res_match.operation == SyncOperation.UPDATE
