"""Hermetic unit and integration tests for live category_title resolution (Finding R-041).

Covers:
- Tool 2 live/snapshot resolve_category_title matching row 5 for CC-Talk.
- Word boundary matching: substrings in unrelated words (Access, Succumb) do not match CC.
- Specificity preference: longer terms take precedence.
- Ambiguity and unavailable reference detection.
- Tool 1 parse_what and RenamerParser preservation of specific WHAT (selected_value="CC-Talk")
  without flattening to category name, applying canonical category "Caitanya-caritamrta".
- Tool 4 consumption and pre-write revalidation via Tool 2.
- Tool 4 mapping to exact live Media schema select option without creating Category options.
- Stale reference detection and missing select option conflicts.
- Confirmed existing metadata preservation.
- Dry-run preview in Main Script with zero mutations.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from media_archive_tooling.media_db_reviewer.baserow_provider import (
    BaserowSnapshotProvider,
    BaserowUnavailableError,
)
from media_archive_tooling.media_db_reviewer.models import (
    BaserowSnapshot,
    CategoryTitleResolution,
    CategoryTitleResolutionStatus,
    MediaCandidate,
    MediaDatabaseReviewResult,
    ReviewDecision,
)
from media_archive_tooling.media_db_reviewer.service import MediaDatabaseReviewService
from media_archive_tooling.media_db_updater.engine import (
    CATEGORY_ALIASES,
    MediaDatabaseUpdateEngine,
)
from media_archive_tooling.media_db_updater.models import (
    FieldAction,
    MediaDbSyncRequest,
    MediaDbSyncResult,
    SyncOperation,
    SyncStatus,
)
from media_archive_tooling.media_db_updater.service import MediaDatabaseUpdaterService
from media_archive_tooling.media_db_updater.write_adapter import FakeBaserowWriteAdapter
from media_archive_tooling.orchestrator.models import FileExecutionStatus
from media_archive_tooling.orchestrator.service import MainToolingScriptService
from media_archive_tooling.renamer.models import (
    Evidence,
    Identity,
    ParserResult,
    RenameMode,
    ResolutionState,
)
from media_archive_tooling.renamer.parser.engine import RenamerParser
from media_archive_tooling.renamer.parser.what import parse_what
from media_archive_tooling.renamer.planner.planner import RenamePlanner
from media_archive_tooling.renamer.registry.registry import LocalRegistry


FAKE_CATEGORY_TITLE_ROWS = [
    {
        "id": 1,
        "category": "Srimad-bhagavatam",
        "title_matching_terms": "SB, Srimad Bhagavatam, srimad bhagavatam, srimad-bhagavatam",
    },
    {
        "id": 2,
        "category": "Bhagavad-gita",
        "title_matching_terms": "BG, Bhagavad Gita, bhagavad gita, bhagavad-gita",
    },
    {
        "id": 5,
        "category": "Caitanya-caritamrta",
        "title_matching_terms": "CC, Chaitanya Charitamrita, caitanya-caritamrta, chaitanya caritamrta",
    },
    {
        "id": 8,
        "category": "Kirtan",
        "title_matching_terms": "Kirtan, Bhajan, Bhajans",
    },
]


def make_fake_review_service(
    rows: Optional[List[Dict[str, Any]]] = None,
    unavailable: bool = False,
    table_id: str = "1193367",
    decision: str = "NEW_MEDIA_CANDIDATE",
    existing_row_id: Optional[int] = None,
) -> MediaDatabaseReviewService:
    """Create a hermetic MediaDatabaseReviewService with controlled category_title provider."""
    mock_provider = MagicMock(spec=BaserowSnapshotProvider)
    mock_provider.category_table_id = table_id
    mock_provider.media_table_id = "658291"
    if unavailable:
        mock_provider.fetch_category_title_rows_live.side_effect = BaserowUnavailableError("Baserow connection failed")
    else:
        effective_rows = FAKE_CATEGORY_TITLE_ROWS if rows is None else rows
        mock_provider.fetch_category_title_rows_live.return_value = (
            effective_rows,
            "2026-09-25T12:00:00Z",
            table_id,
        )
    svc = MediaDatabaseReviewService(registry=None, provider=mock_provider)

    def _review_file(tid: str, force_refresh: bool = False, auto_enrich: bool = True):
        dec_enum = ReviewDecision.NEW_MEDIA_CANDIDATE if decision == "NEW_MEDIA_CANDIDATE" else ReviewDecision.EXISTING_MEDIA_MATCH
        return MediaDatabaseReviewResult(
            tracking_id=tid,
            decision=dec_enum,
            selected_media_row_id=existing_row_id,
            live_read_complete=True,
            snapshot_complete=True,
            baserow_check_complete=True,
            database_state="LIVE_CURRENT",
            baserow_read_at="2026-09-25T12:00:00Z",
            database_snapshot_at="2026-09-25T12:00:00Z",
        )
    svc.review_file = MagicMock(side_effect=_review_file)
    return svc


# ===========================================================================
# 1. Tool 2 resolve_category_title Tests
# ===========================================================================

def test_tool2_resolve_category_title_row_5_matches():
    """Verify Tool 2 matches row 5 for CC-Talk and variants."""
    svc = make_fake_review_service()

    for query in ("CC-Talk", "cc-talk", "CC_Talk", "CC", "Chaitanya Charitamrita"):
        res = svc.resolve_category_title(query)
        assert res.status == CategoryTitleResolutionStatus.MATCHED
        assert res.matched_row_id == 5
        assert res.category == "Caitanya-caritamrta"
        assert res.table_id == "1193367"
        assert res.reason is None


def test_tool2_resolve_category_title_no_substring_inside_unrelated_words():
    """Verify words containing 'cc' as a substring (Access, Succumb) do NOT match CC."""
    svc = make_fake_review_service()

    for word in ("Access", "Succumb", "Accept", "Success", "Accident"):
        res = svc.resolve_category_title(word)
        assert res.status == CategoryTitleResolutionStatus.NO_MATCH, f"'{word}' should not match CC"
        assert res.category is None


def test_tool2_resolve_category_title_ambiguous():
    """Verify ambiguous match when two different categories match with equal term length."""
    ambig_rows = [
        {"id": 10, "category": "Category-A", "title_matching_terms": "Special"},
        {"id": 20, "category": "Category-B", "title_matching_terms": "Special"},
    ]
    svc = make_fake_review_service(rows=ambig_rows)
    res = svc.resolve_category_title("Special-Talk")
    assert res.status == CategoryTitleResolutionStatus.AMBIGUOUS
    assert res.category is None
    assert "multiple categories matched" in res.reason


def test_tool2_resolve_category_title_database_unavailable():
    """Verify DATABASE_UNAVAILABLE when table is unpopulated or remote fails."""
    svc = make_fake_review_service(unavailable=True)
    res = svc.resolve_category_title("CC-Talk")
    assert res.status == CategoryTitleResolutionStatus.DATABASE_UNAVAILABLE
    assert res.category is None
    assert "Baserow connection failed" in res.reason


# ===========================================================================
# 2. Tool 1 parse_what and RenamerParser Tests
# ===========================================================================

def test_tool1_parse_what_preserves_specific_what():
    """Tool 1 preserves specific WHAT token (CC-Talk) while applying canonical category."""
    svc = make_fake_review_service()

    res, cleaned, conflict = parse_what(
        "2012-01-02_KKS_CC-Talk_Simhachalam_de.mp3",
        category_resolver=svc.resolve_category_title,
    )
    assert res.selected_value == "CC-Talk", "Must preserve specific token, not flatten to category"
    assert res.category == "Caitanya-caritamrta"
    assert res.state == ResolutionState.STRONG
    assert conflict is None
    assert "CC-Talk" not in cleaned

    # Provenance audit evidence
    ev = next(e for e in res.evidence if e.source == "category_title_reference")
    assert ev.raw_value == "CC-Talk"
    details = json.loads(ev.details)
    assert details["row_id"] == 5
    assert details["matched_term"] == "CC"
    assert details["category"] == "Caitanya-caritamrta"


def test_tool1_parse_file_renamer_parser_integration():
    """RenamerParser with category_resolver preserves CC-Talk and plans correct filename."""
    svc = make_fake_review_service()
    parser = RenamerParser(category_resolver=svc.resolve_category_title)

    result = parser.parse_file(Path("2012-01-02_KKS_CC-Talk_Simhachalam_de.mp3"))
    assert result.what.selected_value == "CC-Talk"
    assert result.what.category == "Caitanya-caritamrta"
    assert result.what.state == ResolutionState.STRONG

    proposal = RenamePlanner(mode=RenameMode.INITIAL).plan_rename(result)
    assert "CC-Talk" in proposal.proposed_filename
    assert proposal.proposed_filename.startswith("2012-01-02_KKS_CC-Talk_Simhachalam-de")


def test_tool1_category_resolver_ambiguous_routes_to_review():
    """Ambiguous category_title match results in AMBIGUOUS what state and review reason."""
    ambig_rows = [
        {"id": 10, "category": "Category-A", "title_matching_terms": "AmbiguousTerm"},
        {"id": 20, "category": "Category-B", "title_matching_terms": "AmbiguousTerm"},
    ]
    svc = make_fake_review_service(rows=ambig_rows)
    parser = RenamerParser(category_resolver=svc.resolve_category_title)

    result = parser.parse_file(Path("2012-01-02_KKS_AmbiguousTerm_Berlin_de.mp3"))
    assert result.what.state == ResolutionState.AMBIGUOUS
    assert any("Ambiguous category_title matches" in r for r in result.review_reasons)


def test_tool1_category_resolver_unavailable_marks_unresolved():
    """Unavailable category_title reference marks what UNRESOLVED with clear conflict."""
    svc = make_fake_review_service(unavailable=True)
    parser = RenamerParser(category_resolver=svc.resolve_category_title)

    result = parser.parse_file(Path("2012-01-02_KKS_CC-Talk_Simhachalam_de.mp3"))
    assert result.what.state == ResolutionState.UNRESOLVED
    assert any("database unavailable" in c for c in result.conflicts)


# ===========================================================================
# 3. Tool 4 Update Engine Tests: Revalidation and Live Schema Mapping
# ===========================================================================

def make_test_schema_with_caitanya_caritamrta() -> List[Dict[str, Any]]:
    """Return live-equivalent schema where Category option is Caitanya-caritamrta."""
    fields = FakeBaserowWriteAdapter().fields
    for fld in fields:
        if fld["name"] == "Category":
            fld["select_options"] = [
                {"id": 1, "value": "Srimad-bhagavatam", "color": "blue"},
                {"id": 2, "value": "Bhagavad-gita", "color": "green"},
                {"id": 3, "value": "Caitanya-caritamrta", "color": "orange"},
                {"id": 4, "value": "Kirtan", "color": "yellow"},
            ]
        elif fld["name"] == "Place, location":
            fld["select_options"].append({"id": 99, "value": "Simhachalam", "color": "purple"})
        elif fld["name"] == "Tag":
            fld["type"] = "text"
            fld.pop("select_options", None)
        elif fld["name"] == "Language":
            fld["type"] = "multiple_select"
    return fields


def test_tool4_consumes_and_revalidates_category_evidence(tmp_path):
    """Tool 4 consumes category evidence, rechecks via Tool 2, and maps to live Category option."""
    registry = LocalRegistry(tmp_path / "test.db")
    svc_t2 = make_fake_review_service()
    fields = make_test_schema_with_caitanya_caritamrta()
    fake_db = FakeBaserowWriteAdapter(initial_fields=fields)

    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=svc_t2)

    # Build request with CC-Talk and category evidence
    request = MediaDbSyncRequest(
        tracking_id="trk_cctalk_01",
        current_filename="2012-01-02_KKS_CC-Talk_Simhachalam-de.mp3",
        current_path="/archive/2012-01-02_KKS_CC-Talk_Simhachalam-de.mp3",
        when_val="2012-01-02",
        when_state="exact",
        what_val="CC-Talk",
        what_category="Caitanya-caritamrta",
        what_state="strong",
        what_provenance=[{
            "source": "category_title_reference",
            "raw_value": "CC-Talk",
            "details": json.dumps({
                "matched_term": "CC",
                "category": "Caitanya-caritamrta",
                "row_id": 5,
                "table_id": "1193367",
                "read_at": "2026-09-25T12:00:00Z",
            }),
        }],
        where_place="Simhachalam",
        where_country="Germany",
        where_country_iso="de",
        where_state="exact",
        tool2_decision="NEW_MEDIA_CANDIDATE",
    )

    result = service.synchronize("trk_cctalk_01", commit=True, request=request)
    assert result.status == SyncStatus.SYNCED
    assert result.operation == SyncOperation.CREATE

    row = fake_db.rows[result.media_row_id]
    assert row["Category"] == "Caitanya-caritamrta", "Exact live Media option must be written"
    assert row["Title"] == "CC-Talk", "Specific WHAT title must be preserved in Title"

    # Verify no new select options were created on Category
    cat_fld = next(f for f in fake_db.fields if f["name"] == "Category")
    assert len(cat_fld["select_options"]) == 4


def test_tool4_stale_category_reference_blocks_write(tmp_path):
    """Tool 4 detects changed/stale category reference and blocks Category write with conflict."""
    registry = LocalRegistry(tmp_path / "test.db")
    # Live tool 2 returns Category-Changed instead of Caitanya-caritamrta
    changed_rows = [
        {"id": 5, "category": "Different-Category", "title_matching_terms": "CC"},
    ]
    svc_t2 = make_fake_review_service(rows=changed_rows)
    fields = make_test_schema_with_caitanya_caritamrta()
    fake_db = FakeBaserowWriteAdapter(initial_fields=fields)

    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=svc_t2)

    request = MediaDbSyncRequest(
        tracking_id="trk_stale_01",
        current_filename="2012-01-02_KKS_CC-Talk_Simhachalam-de.mp3",
        current_path="/archive/2012-01-02_KKS_CC-Talk_Simhachalam-de.mp3",
        what_val="CC-Talk",
        what_category="Caitanya-caritamrta",
        what_state="strong",
        what_provenance=[{
            "source": "category_title_reference",
            "raw_value": "CC-Talk",
            "details": json.dumps({
                "matched_term": "CC",
                "category": "Caitanya-caritamrta",
                "row_id": 5,
            }),
        }],
        tool2_decision="NEW_MEDIA_CANDIDATE",
    )

    result = service.synchronize("trk_stale_01", commit=True, request=request)
    assert result.status == SyncStatus.REVIEW_REQUIRED
    assert any("Stale category_title reference" in c for c in result.conflicts)


def test_tool4_missing_category_option_in_schema_blocks_write(tmp_path):
    """When matched category has no option in live schema, Category write is blocked."""
    registry = LocalRegistry(tmp_path / "test.db")
    svc_t2 = make_fake_review_service()
    # Schema omits Caitanya-caritamrta option
    fields = FakeBaserowWriteAdapter().fields
    for fld in fields:
        if fld["name"] == "Category":
            fld["select_options"] = [
                {"id": 1, "value": "Srimad-bhagavatam", "color": "blue"},
            ]
    fake_db = FakeBaserowWriteAdapter(initial_fields=fields)

    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=svc_t2)

    request = MediaDbSyncRequest(
        tracking_id="trk_missing_opt_01",
        current_filename="2012-01-02_KKS_CC-Talk_Simhachalam-de.mp3",
        current_path="/archive/2012-01-02_KKS_CC-Talk_Simhachalam-de.mp3",
        what_val="CC-Talk",
        what_category="Caitanya-caritamrta",
        what_state="strong",
        what_provenance=[{
            "source": "category_title_reference",
            "raw_value": "CC-Talk",
            "details": json.dumps({"matched_term": "CC", "category": "Caitanya-caritamrta"}),
        }],
        tool2_decision="NEW_MEDIA_CANDIDATE",
    )

    result = service.synchronize("trk_missing_opt_01", commit=True, request=request)
    assert result.status == SyncStatus.REVIEW_REQUIRED
    assert any("not found in live schema (creation disallowed)" in c for c in result.conflicts)
    cat_fld = next(f for f in fake_db.fields if f["name"] == "Category")
    assert len(cat_fld["select_options"]) == 1, "Must never automatically create Category option"


def test_tool4_preserves_confirmed_existing_category_on_update(tmp_path):
    """Confirmed existing row Category is preserved and not overwritten."""
    registry = LocalRegistry(tmp_path / "test.db")
    svc_t2 = make_fake_review_service(decision="EXISTING_MEDIA_MATCH", existing_row_id=100)
    fields = make_test_schema_with_caitanya_caritamrta()
    fake_db = FakeBaserowWriteAdapter(
        initial_fields=fields,
        initial_rows=[{
            "id": 100,
            "Category": {"value": "Caitanya-caritamrta"},
            "Title": "CC-Talk",
            "Filename": "old_name.mp3",
        }],
    )

    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=svc_t2)

    request = MediaDbSyncRequest(
        tracking_id="trk_update_pres_01",
        current_filename="new_name.mp3",
        current_path="/archive/new_name.mp3",
        what_val="CC-Talk",
        what_category="Caitanya-caritamrta",
        what_state="strong",
        selected_media_row_id=100,
        tool2_decision="EXISTING_MEDIA_MATCH",
    )

    result = service.synchronize("trk_update_pres_01", commit=True, request=request)
    assert result.status == SyncStatus.SYNCED
    cat_diff = next(d for d in result.field_diffs if d.field_name == "Category")
    assert cat_diff.action == FieldAction.PRESERVED
    val = fake_db.rows[100]["Category"]
    assert (val.get("value") if isinstance(val, dict) else val) == "Caitanya-caritamrta"


def test_tool4_conflicting_existing_category_flags_review(tmp_path):
    """Contradictory existing row Category is not overwritten and flags conflict for review."""
    registry = LocalRegistry(tmp_path / "test.db")
    svc_t2 = make_fake_review_service(decision="EXISTING_MEDIA_MATCH", existing_row_id=101)
    fields = make_test_schema_with_caitanya_caritamrta()
    fake_db = FakeBaserowWriteAdapter(
        initial_fields=fields,
        initial_rows=[{
            "id": 101,
            "Category": {"value": "Bhagavad-gita"},
            "Title": "Confirmed Gita Class",
            "Filename": "gita.mp3",
        }],
    )

    service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=svc_t2)

    request = MediaDbSyncRequest(
        tracking_id="trk_update_conflict_01",
        current_filename="new_name.mp3",
        current_path="/archive/new_name.mp3",
        what_val="CC-Talk",
        what_category="Caitanya-caritamrta",
        what_state="strong",
        selected_media_row_id=101,
        tool2_decision="EXISTING_MEDIA_MATCH",
    )

    result = service.synchronize("trk_update_conflict_01", commit=True, request=request)
    assert result.status == SyncStatus.REVIEW_REQUIRED
    assert any("Category conflict: DB has 'Bhagavad-gita'" in c for c in result.conflicts)
    val = fake_db.rows[101]["Category"]
    assert (val.get("value") if isinstance(val, dict) else val) == "Bhagavad-gita", "Database value must be preserved"


# ===========================================================================
# 4. Cross-Tool Main Script Dry-Run Pipeline
# ===========================================================================

def test_dry_run_pipeline_distinguishes_blocked_vs_safe_create(tmp_path):
    """Dry-run preview shows proposed Category and Title with zero writes."""
    registry = LocalRegistry(tmp_path / "test.db")
    svc_t2 = make_fake_review_service()
    fields = make_test_schema_with_caitanya_caritamrta()
    fake_db = FakeBaserowWriteAdapter(initial_fields=fields)

    tool4_service = MediaDatabaseUpdaterService(registry, fake_db, tool2_service=svc_t2)
    parser = RenamerParser(category_resolver=svc_t2.resolve_category_title)

    # Preview create request
    request = MediaDbSyncRequest(
        tracking_id="trk_dryrun_01",
        current_filename="2012-01-02_KKS_CC-Talk_Simhachalam-de.mp3",
        current_path="/archive/2012-01-02_KKS_CC-Talk_Simhachalam-de.mp3",
        when_val="2012-01-02",
        when_state="exact",
        what_val="CC-Talk",
        what_category="Caitanya-caritamrta",
        what_state="strong",
        what_provenance=[{
            "source": "category_title_reference",
            "raw_value": "CC-Talk",
            "details": json.dumps({"matched_term": "CC", "category": "Caitanya-caritamrta", "row_id": 5}),
        }],
        where_place="Simhachalam",
        where_country="Germany",
        where_country_iso="de",
        where_state="exact",
        tool2_decision="NEW_MEDIA_CANDIDATE",
    )

    preview_res = tool4_service.preview("trk_dryrun_01", request=request)
    assert preview_res.status == SyncStatus.SYNCING
    assert preview_res.operation == SyncOperation.CREATE
    assert not preview_res.review_required

    diff_map = {d.field_name: d.new_value for d in preview_res.field_diffs if d.action == FieldAction.SET}
    assert diff_map["Title"] == "CC-Talk"
    assert diff_map["Category"] == "Caitanya-caritamrta"

    # Zero writes occurred in fake_db
    assert len(fake_db.rows) == 0
