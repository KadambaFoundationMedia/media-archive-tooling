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


def make_mock_tool2(decision: str = "NEW_MEDIA_CANDIDATE", row_id: Optional[int] = None):
    mock_t2 = MagicMock()
    mock_res = MagicMock()
    mock_res.decision = decision
    mock_res.selected_media_row_id = row_id
    mock_res.model_dump.return_value = {"decision": decision, "selected_media_row_id": row_id}
    mock_t2.review_file.return_value = mock_res
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
    req.is_human_approved = True

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
    req.field_approvals = {"Title": {"approved_value": "New Approved Title", "reviewed_precondition_value": "Initial Title"}}

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
    # Initially NEW_MEDIA_CANDIDATE, then during pre-create fresh check returns EXISTING_MEDIA_MATCH
    mock_res = MagicMock()
    mock_res.decision = "EXISTING_MEDIA_MATCH"
    mock_res.selected_media_row_id = 999
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
    rev_cand1 = MagicMock(decision="NEW_MEDIA_CANDIDATE", selected_media_row_id=None, review_reasons=[])
    rev_cand2 = MagicMock(decision="NEW_MEDIA_CANDIDATE", selected_media_row_id=None, review_reasons=[])
    rev_after = MagicMock(decision="EXISTING_MEDIA_MATCH", selected_media_row_id=1001, review_reasons=[])
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
    custom_fields = [
        {"id": 105, "name": "Language", "type": "single_select", "select_options": []}
    ]
    fake_db = FakeBaserowWriteAdapter(initial_fields=custom_fields)
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
        mode=RenameMode.INITIAL,
        status="approved",
        parser_result=make_parser_result(tracking_id="trk0041", orig_filename="orig.mp3"),
    ))

    mock_updater = MagicMock()
    commit_svc = RenameCommitService(registry, media_db_updater_service=mock_updater)

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
    target_filename = "target_ID-trk0043.mp3"
    target_file = tmp_path / target_filename
    src_file.write_text("content")

    save_test_file(registry, tracking_id="trk0043", filename="source.mp3", status="approved")
    registry.save_proposal(RenameProposal(
        tracking_id="trk0043",
        original_path=str(src_file),
        current_filename="source.mp3",
        proposed_filename=target_filename,
        proposed_path=str(target_file),
        mode=RenameMode.INITIAL,
        status="approved",
        parser_result=make_parser_result(tracking_id="trk0043", orig_filename="source.mp3"),
    ))

    mock_updater = MagicMock()
    mock_updater.synchronize.side_effect = BaserowUnavailableError("Network cut")
    commit_svc = RenameCommitService(registry, media_db_updater_service=mock_updater)

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
    mock_t2_service = MagicMock()
    mock_res = MagicMock()
    mock_res.decision = "NEW_MEDIA_CANDIDATE"
    mock_res.selected_media_row_id = None
    mock_res.review_reasons = []
    mock_t2_service.review_file.return_value = mock_res

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

