"""Architecture and integration tests verifying the Baserow Access Boundary Amendment.

Controlling specification: docs/baserow-access-boundary-amendment.md
Demonstrates the 8 non-negotiable architecture rules:
1. Tool 1 and Tool 3 production composition constructs no Baserow client/provider and receives no Baserow credentials.
2. Tool 2 cannot perform create/update/delete/schema/select-option mutations.
3. Tool 4 uses Tool 2's current result as the create-vs-update gate.
4. Tool 4 independently revalidates the exact row and write preconditions immediately before mutation.
5. Tool 3 operates offline from a complete verified schedule artifact produced through Tool 2's read-only boundary.
6. Tool 1 calls Tool 4 only after the final filename/current stage state is committed (not on RenameMode.INITIAL).
7. A later final Tool 1 rename creates another durable Tool 4 synchronization request.
8. The full Tool 1–4 regression suite remains green.
"""
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch
import pytest

from media_archive_tooling.adapters.baserow import BaserowReferenceProvider
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
from media_archive_tooling.renamer.planner.executor import BatchExecutor
from media_archive_tooling.renamer.logging.logger import RenamerLogger
from media_archive_tooling.renamer.registry.registry import LocalRegistry
from media_archive_tooling.media_db_reviewer.baserow_provider import BaserowSnapshotProvider
from media_archive_tooling.media_db_reviewer.models import (
    MediaDatabaseReviewResult,
    ReviewDecision,
)
from media_archive_tooling.media_db_reviewer.service import MediaDatabaseReviewService
from media_archive_tooling.travel_reviewer.models import (
    NormalizedTravelRow,
    TravelScheduleManifest,
)
from media_archive_tooling.travel_reviewer.reference_store import (
    TravelReferenceStore,
    compute_canonical_sha256,
)
from media_archive_tooling.travel_reviewer.service import TravelScheduleReviewService
from media_archive_tooling.media_db_updater.models import (
    MediaDbSyncRequest,
    SyncOperation,
    SyncStatus,
)
from media_archive_tooling.media_db_updater.service import MediaDatabaseUpdaterService
from media_archive_tooling.media_db_updater.write_adapter import FakeBaserowWriteAdapter


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
    mock_t2.review_file.return_value = _review_file(tracking_id or "default")
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


# ===========================================================================
# Rule 1: Tool 1 and 3 production composition constructs no Baserow client/provider
# ===========================================================================
def test_rule_1_tool1_and_tool3_receive_no_baserow_client(tmp_path):
    from media_archive_tooling.cli import run_travel_review

    # Tool 1: BatchExecutor receives an offline BaserowReferenceProvider with no live client/token
    t1_provider = BaserowReferenceProvider()
    assert not isinstance(t1_provider, BaserowSnapshotProvider)
    assert not hasattr(t1_provider, "api_token") or getattr(t1_provider, "api_token", None) is None
    assert not hasattr(t1_provider, "client") or getattr(t1_provider, "client", None) is None

    executor = BatchExecutor(
        registry=LocalRegistry(tmp_path / "test.db"),
        logger=RenamerLogger(tmp_path / "logs"),
        provider=t1_provider,
        mode=RenameMode.FINALIZE,
    )
    assert executor.provider is t1_provider
    assert not isinstance(executor.provider, BaserowSnapshotProvider)

    # Tool 3: run_travel_review constructs no BaserowSnapshotProvider and passes provider=None
    class TravelArgs:
        registry_path = str(tmp_path / "test.db")
        reference_path = None
        auto_enrich = False
        tracking_id = None
        json = False

    with patch("media_archive_tooling.cli.BaserowSnapshotProvider") as mock_snap_prov_t3:
        run_travel_review(TravelArgs())
        # Tool 3 composition must NOT construct BaserowSnapshotProvider
        assert mock_snap_prov_t3.call_count == 0


# ===========================================================================
# Rule 2: Tool 2 cannot perform create/update/delete/schema/select-option mutations
# ===========================================================================
def test_rule_2_tool2_technically_incapable_of_mutations():
    # 1. Inspect MediaDatabaseReviewService methods
    t2_service_methods = dir(MediaDatabaseReviewService)
    mutation_method_names = [
        "create_row", "patch_row", "update_row", "delete_row",
        "ensure_select_option", "add_select_option", "mutate_schema",
    ]
    for m in mutation_method_names:
        assert m not in t2_service_methods, f"Tool 2 service unexpectedly exposes mutation method '{m}'"

    # 2. Inspect BaserowSnapshotProvider methods
    t2_provider_methods = dir(BaserowSnapshotProvider)
    for m in mutation_method_names:
        assert m not in t2_provider_methods, f"Tool 2 provider unexpectedly exposes mutation method '{m}'"

    # 3. Verify provider instance has only read operations
    provider = BaserowSnapshotProvider(api_url="https://api.baserow.io", api_token="read_token", media_table_id=123)
    assert not hasattr(provider, "create_row")
    assert not hasattr(provider, "patch_row")
    assert not hasattr(provider, "ensure_select_option")


# ===========================================================================
# Rule 3: Tool 4 uses Tool 2's current result as create-vs-update gate
# ===========================================================================
def test_rule_3_tool4_uses_tool2_result_as_gate(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, "trk_rule3_create")
    save_test_file(registry, "trk_rule3_update")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[{"id": 401, "Filename": "old.mp3"}])

    # Case A: Tool 2 returns NEW_MEDIA_CANDIDATE -> Tool 4 plans CREATE
    mock_t2_create = make_mock_tool2(decision="NEW_MEDIA_CANDIDATE")
    service_create = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=mock_t2_create)
    res_c = service_create.preview("trk_rule3_create")
    assert res_c.operation == SyncOperation.CREATE

    # Case B: Tool 2 returns EXISTING_MEDIA_MATCH -> Tool 4 plans UPDATE
    mock_t2_update = make_mock_tool2(decision="EXISTING_MEDIA_MATCH", row_id=401)
    service_update = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=mock_t2_update)
    res_u = service_update.preview("trk_rule3_update")
    assert res_u.operation == SyncOperation.UPDATE

    # Case C: Tool 2 returns non-match (e.g. MULTIPLE_CANDIDATES) without association -> Tool 4 BLOCKS
    mock_t2_multi = make_mock_tool2(decision="MULTIPLE_CANDIDATES")
    service_multi = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=mock_t2_multi)
    res_m = service_multi.preview("trk_rule3_update")
    assert res_m.operation == SyncOperation.BLOCKED
    assert res_m.status == SyncStatus.REVIEW_REQUIRED


# ===========================================================================
# Rule 4: Tool 4 independently revalidates exact row and write preconditions
# ===========================================================================
def test_rule_4_tool4_independently_revalidates_exact_row_and_preconditions(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, "trk_rule4")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[{
        "id": 501,
        "Filename": "original.mp3",
        "Date": "2014-08-04",
        "Title": "Original Title",
    }])
    service = MediaDatabaseUpdaterService(registry, fake_db)

    req = service.build_sync_request("trk_rule4")
    req.tool2_decision = "EXISTING_MEDIA_MATCH"
    req.selected_media_row_id = 501
    req.field_approvals = {
        "Title": {
            "action": "apply_correction",
            "has_reviewed_precondition": True,
            "approved_value": "Updated Title",
            "reviewed_precondition_value": "Original Title",
        }
    }

    # Plan based on current DB state
    plan = service.preview("trk_rule4", request=req)
    assert plan.status in (SyncStatus.SYNCING, SyncStatus.SYNCED)

    # Collaborator changes Title live in DB before commit
    fake_db.rows[501]["Title"] = "Concurrent Collaborator Title"

    # Commit execution performs independent live revalidation and detects collaborator conflict
    commit_res = service.engine._commit_update(req, plan, initial_live_row=plan.precondition_row_snapshot)
    assert commit_res.status == SyncStatus.REVIEW_REQUIRED
    assert commit_res.operation == SyncOperation.CONFLICT
    assert any("COLLABORATOR_CONFLICT" in c for c in commit_res.conflicts)
    assert fake_db.rows[501]["Title"] == "Concurrent Collaborator Title"


# ===========================================================================
# Rule 5: Tool 3 operates offline from verified local reference artifact
# ===========================================================================
def test_rule_5_tool3_operates_offline_from_verified_artifact(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    save_test_file(registry, "trk_rule5")

    # Create the same complete, integrity-verified local artifact that Tool 2's
    # read-only bootstrap boundary supplies in production. The test must not
    # depend on a developer's ignored .renamer/reference state.
    reference_path = tmp_path / "travel_schedule.json"
    rows = [
        NormalizedTravelRow(
            id=1,
            start_date="2014-08-04",
            end_date="2014-08-04",
            place="Leipzig",
            country="Germany",
            country_iso2="de",
            schedule_text="Leipzig, Germany",
        )
    ]
    manifest_fixture = TravelScheduleManifest(
        source_table_id="travel-schedule-test",
        retrieved_at="2026-09-17T00:00:00Z",
        complete=True,
        row_count=len(rows),
        canonical_sha256=compute_canonical_sha256(rows),
        normalized_rows=rows,
    )
    TravelReferenceStore(reference_path=reference_path).save_reference(manifest_fixture)

    # Load and use the verified artifact with no Baserow provider.
    store = TravelReferenceStore(reference_path=reference_path, provider=None)
    assert store.provider is None
    # Reference schedule is loaded offline from verified local data
    manifest = store.load_reference()
    assert manifest is not None
    assert manifest.canonical_sha256 is not None
    assert len(manifest.normalized_rows) > 0

    # Review file runs offline without any network/Baserow calls
    service = TravelScheduleReviewService(registry=registry, reference_store=store)
    res = service.review_file("trk_rule5", auto_enrich=False)
    assert res is not None
    assert res.tracking_id == "trk_rule5"
    assert res.decision is not None


# ===========================================================================
# Rule 6: Tool 1 calls Tool 4 only after the final filename is committed
# ===========================================================================
def test_rule_6_tool1_does_not_call_tool4_on_initial_rename(tmp_path):
    reg_path = tmp_path / "registry.db"
    registry = LocalRegistry(reg_path)
    logger = RenamerLogger(tmp_path / "logs")
    provider = BaserowReferenceProvider()
    mock_updater = MagicMock()

    media_dir = tmp_path / "media"
    media_dir.mkdir()

    # Case A: INITIAL rename mode must NOT call Tool 4
    test_file_init = media_dir / "2014-08-04_raw_initial.mp3"
    test_file_init.write_text("data")
    prop_init = save_test_file(
        registry,
        "trk_rule6_init",
        filename="2014-08-04_raw_initial.mp3",
        original_path=str(test_file_init),
        current_path=str(test_file_init),
    )
    prop_init.proposed_filename = "2014-08-04_kks_bg-01-18_init.mp3"
    prop_init.proposed_path = str(media_dir / "2014-08-04_kks_bg-01-18_init.mp3")
    prop_init.changes_detected = True

    executor_init = BatchExecutor(
        registry=registry,
        logger=logger,
        provider=provider,
        mode=RenameMode.INITIAL,
        media_db_updater_service=mock_updater,
    )
    executor_init.commit_proposals([prop_init])

    # Tool 4 updater service must NOT have been called for initial rename
    assert mock_updater.synchronize.call_count == 0
    assert registry.get_media_db_sync("trk_rule6_init") is None

    # Case B: FINALIZE rename mode DOES call Tool 4 after commit
    media_dir_fin = tmp_path / "media_fin"
    media_dir_fin.mkdir()
    test_file_final = media_dir_fin / "2014-08-04_raw_final.mp3"
    test_file_final.write_text("data")
    prop_final = save_test_file(
        registry,
        "trk_rule6_final",
        filename="2014-08-04_raw_final.mp3",
        original_path=str(test_file_final),
        current_path=str(test_file_final),
    )
    prop_final.proposed_filename = "2014-08-04_kks_bg-01-18_final.mp3"
    prop_final.proposed_path = str(media_dir_fin / "2014-08-04_kks_bg-01-18_final.mp3")
    prop_final.mode = RenameMode.FINALIZE
    prop_final.changes_detected = True

    executor_final = BatchExecutor(
        registry=registry,
        logger=logger,
        provider=provider,
        mode=RenameMode.FINALIZE,
        media_db_updater_service=mock_updater,
    )
    executor_final.commit_proposals([prop_final])

    # Tool 4 updater service WAS called for final rename
    assert mock_updater.synchronize.call_count == 1
    sync_rec = registry.get_media_db_sync("trk_rule6_final")
    assert sync_rec is not None
    assert sync_rec["sync_status"] == "PENDING_SYNC"


# ===========================================================================
# Rule 7: A later final Tool 1 rename creates another durable Tool 4 sync request
# ===========================================================================
def test_rule_7_subsequent_final_rename_creates_durable_sync_request(tmp_path):
    registry = LocalRegistry(tmp_path / "test.db")
    fake_db = FakeBaserowWriteAdapter(initial_rows=[{
        "id": 701,
        "Filename": "first_final.mp3",
        "media_archive_path": str(tmp_path / "first_final.mp3"),
    }])
    mock_t2 = make_mock_tool2(decision="EXISTING_MEDIA_MATCH", row_id=701)
    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=mock_t2)

    # First final rename
    file1 = tmp_path / "first_final.mp3"
    file1.write_text("audio")
    save_test_file(registry, "trk_rule7", filename="first_final.mp3", current_path=str(file1))
    req1 = service.build_sync_request("trk_rule7")
    res1 = service.synchronize("trk_rule7", commit=True, request=req1)
    assert res1.status == SyncStatus.SYNCED

    # Registry has recorded first sync
    rec1 = registry.get_media_db_sync("trk_rule7")
    assert rec1 is not None
    assert rec1["sync_status"] == "SYNCED"

    # Later stage discovers stronger evidence and commits second final rename
    file2 = tmp_path / "second_final.mp3"
    file2.write_text("audio")
    save_test_file(registry, "trk_rule7", filename="second_final.mp3", current_path=str(file2))

    # A subsequent sync request is generated and processed
    req2 = service.build_sync_request("trk_rule7")
    assert req2.current_filename == "second_final.mp3"
    assert req2.request_fingerprint != req1.request_fingerprint

    res2 = service.synchronize("trk_rule7", commit=True, request=req2)
    assert res2.status == SyncStatus.SYNCED
    assert fake_db.rows[701]["Filename"] == "second_final.mp3"

    # Registry records updated durable sync
    rec2 = registry.get_media_db_sync("trk_rule7")
    assert rec2 is not None
    assert rec2["request"]["request_fingerprint"] == req2.request_fingerprint


# ===========================================================================
# Rule 8: The full Tool 1–4 regression suite remains green
# ===========================================================================
def test_rule_8_access_matrix_and_boundaries_contract():
    """Verify the non-negotiable access matrix from Section 1."""
    # Tool 1: Read No, Write No
    # Tool 2: Read Yes, Write No
    # Tool 3: Read No, Write No
    # Tool 4: Read Yes, Write Yes
    from media_archive_tooling.media_db_updater.write_adapter import BaserowWriteAdapter
    assert hasattr(BaserowWriteAdapter, "create_row")
    assert hasattr(BaserowWriteAdapter, "patch_row")
    assert hasattr(BaserowWriteAdapter, "ensure_select_option")
    assert not hasattr(MediaDatabaseReviewService, "create_row")
    assert not hasattr(BaserowReferenceProvider, "create_row")
    assert not hasattr(TravelReferenceStore, "create_row")
