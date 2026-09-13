"""Comprehensive unit and integration tests for Tool 2 (Media Database Reviewer).

Covers all 30 required test cases from Section 33 of docs/tool-2-media-database-reviewer-build-plan.md,
plus CLI and review portal integration tests.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from typing import Any, Dict, List, Optional
from unittest.mock import patch, MagicMock

import httpx
import pytest

from media_archive_tooling.media_db_reviewer.models import (
    BaserowSnapshot,
    FieldComparisonState,
    MediaCandidate,
    MediaDatabaseReviewResult,
    RenamerEnrichment,
    ReviewDecision,
    Tool4Action,
)
from media_archive_tooling.media_db_reviewer.baserow_provider import (
    BaserowSnapshotProvider,
    normalize_category_title_row,
    normalize_media_row,
    normalize_travel_schedule_row,
)
from media_archive_tooling.media_db_reviewer.engine import (
    MediaDatabaseReconciliationEngine,
    _compare_dates,
    _compare_places,
    _compare_what,
)
from media_archive_tooling.media_db_reviewer.service import MediaDatabaseReviewService
from media_archive_tooling.media_db_reviewer.title_compaction import compact_title_for_filename
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
from media_archive_tooling.renamer.planner.planner import RenamePlanner
from media_archive_tooling.renamer.registry.registry import LocalRegistry
from media_archive_tooling.renamer.service import RenamerApplicationService


def make_parser_result(
    tracking_id: str = "test0001",
    orig_filename: str = "2014-08-04_KKS_BG-01-18_Leipzig-de.mp3",
    date_val: Optional[str] = "2014-08-04",
    what_val: Optional[str] = "BG-01-18",
    what_category: Optional[str] = "Bhagavad-gita",
    place: Optional[str] = "Leipzig",
    country: Optional[str] = "DE",
    source_ids: Optional[List[str]] = None,
    technical_flags: Optional[List[str]] = None,
) -> ParserResult:
    """Helper to build a realistic ParserResult."""
    return ParserResult(
        identity=Identity(
            tracking_id=tracking_id,
            original_filename=orig_filename,
            original_path=f"/archive/{orig_filename}",
            current_filename=orig_filename,
            extension=Path(orig_filename).suffix,
        ),
        context=Context(),
        when=WhenResult(
            selected_value=date_val or "YYYY-MM-DD",
            precision="exact" if date_val else "none",
            state=ResolutionState.EXACT if date_val else ResolutionState.UNRESOLVED,
        ),
        who="KKS",
        what=WhatResult(
            selected_value=what_val,
            category=what_category,
            state=ResolutionState.EXACT if what_val else ResolutionState.UNRESOLVED,
        ),
        where=WhereResult(
            place_location=place,
            country_iso2=country,
            state=ResolutionState.EXACT if place else ResolutionState.UNRESOLVED,
        ),
        file_metadata=FileMetadata(
            source_sequence_id=source_ids[0] if source_ids else None,
            edited=("edited" in (technical_flags or [])),
        ),
        review_reasons=[],
    )


# ---------------------------------------------------------------------------
# Test 1: Read-only Baserow adapter behavior using config without exposing secrets
# ---------------------------------------------------------------------------
def test_01_read_only_baserow_adapter_behavior_without_secrets():
    secret_token = "secret-token-xyz-123"
    provider = BaserowSnapshotProvider(
        api_token=secret_token,
        media_table_id="101",
        category_table_id="102",
        travel_schedule_table_id="103",
    )
    # Ensure token is not leaked in str or repr
    rep = repr(provider)
    assert secret_token not in rep
    s = str(provider)
    assert secret_token not in s

    # Ensure provider only performs GET requests, no mutations
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"count": 0, "next": None, "results": []}
    mock_client.get.return_value = mock_resp

    rows = provider._fetch_table_rows(mock_client, "101")
    assert mock_client.get.called
    assert rows == []


# ---------------------------------------------------------------------------
# Test 2: Complete pagination for all required tables
# ---------------------------------------------------------------------------
def test_02_complete_pagination_for_all_tables():
    media_p1 = {"count": 2, "next": "https://api.baserow.io/api/database/rows/table/101/?page=2", "results": [{"id": 1, "Date": "2014-08-04"}]}
    media_p2 = {"count": 2, "next": None, "results": [{"id": 2, "Date": "2014-08-05"}]}
    cat_p1 = {"count": 2, "next": "https://api.baserow.io/api/database/rows/table/102/?page=2", "results": [{"id": 10, "category": "Kirtan"}]}
    cat_p2 = {"count": 2, "next": None, "results": [{"id": 11, "category": "Japa"}]}
    travel_p1 = {"count": 2, "next": "https://api.baserow.io/api/database/rows/table/103/?page=2", "results": [{"id": 20, "Place": "Leipzig"}]}
    travel_p2 = {"count": 2, "next": None, "results": [{"id": 21, "Place": "Berlin"}]}

    def mock_get(url, headers=None):
        u = str(url)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        if "table/101/" in u:
            mock_resp.json.return_value = media_p2 if "page=2" in u else media_p1
        elif "table/102/" in u:
            mock_resp.json.return_value = cat_p2 if "page=2" in u else cat_p1
        elif "table/103/" in u:
            mock_resp.json.return_value = travel_p2 if "page=2" in u else travel_p1
        else:
            mock_resp.status_code = 404
            mock_resp.json.return_value = {}
        return mock_resp

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.get.side_effect = mock_get

    provider = BaserowSnapshotProvider(
        api_token="dummy",
        media_table_id="101",
        category_table_id="102",
        travel_schedule_table_id="103",
    )

    with patch("httpx.Client", return_value=mock_client):
        snapshot = provider._fetch_live_snapshot()
        assert len(snapshot.media_rows) == 2
        assert len(snapshot.category_title_rows) == 2
        assert len(snapshot.travel_schedule_rows) == 2
        assert snapshot.complete is True


# ---------------------------------------------------------------------------
# Test 3: Schema mapping for media, category_title, travel_schedule
# ---------------------------------------------------------------------------
def test_03_schema_mapping_media_category_travel():
    raw_media = {
        "id": 999,
        "Date": "2014/08/04",
        "Title": "Love in the Spiritual World",
        "What": "BG 1.18",
        "Category": "Bhagavad-gita",
        "Place, location": "Leipzig",
        "Country": "Germany",
        "Filename": "2014-08-04_Leipzig.mp3",
        "Source ID": "AUD-999",
        "URL": "https://media.org/aud999",
        "Files": [{"name": "2014-08-04_Leipzig.mp3"}],
        "Format": "Audio",
        "Fact Checked": "True",
    }
    norm_m = normalize_media_row(raw_media)
    assert norm_m["id"] == 999
    assert norm_m["date"] == "2014-08-04"
    assert norm_m["title"] == "Love in the Spiritual World"
    assert norm_m["what"] == "BG 1.18"
    assert norm_m["place"] == "Leipzig"
    assert norm_m["country"] == "Germany"
    assert "AUD-999" in norm_m["source_ids"]
    assert "2014-08-04_Leipzig.mp3" in norm_m["attachments"]
    assert norm_m["fact_checked"] is True

    raw_cat = {
        "id": 50,
        "category": "Sunday Feast",
        "title_matching_terms": "sunday feast, feast lecture",
        "folder_path": "Sunday Feast",
        "color": "#8e44ad",
    }
    norm_c = normalize_category_title_row(raw_cat)
    assert norm_c["category"] == "Sunday Feast"
    assert "sunday feast" in norm_c["title_matching_terms"]
    assert "feast lecture" in norm_c["title_matching_terms"]

    raw_travel = {
        "id": 70,
        "Start Date": "2014/08/01",
        "End Date": "2014/08/10",
        "Place": "Leipzig",
        "Country": "Germany",
        "Schedule text": "Annual Ratha Yatra festival",
    }
    norm_t = normalize_travel_schedule_row(raw_travel)
    assert norm_t["start_date"] == "2014-08-01"
    assert norm_t["end_date"] == "2014-08-10"
    assert norm_t["place"] == "Leipzig"
    assert norm_t["country"] == "Germany"


# ---------------------------------------------------------------------------
# Test 4: Live-over-cache authority
# ---------------------------------------------------------------------------
def test_04_live_over_cache_authority(tmp_path):
    snap_file = tmp_path / "snapshot.json"
    cached = BaserowSnapshot(
        snapshot_at="2026-01-01T00:00:00Z",
        state="LIVE_COMPLETE",
        complete=True,
        media_rows=[{"id": 1, "Title": "Old Cached Title"}],
    )
    snap_file.write_text(cached.model_dump_json())

    provider = BaserowSnapshotProvider(
        api_token="token",
        media_table_id="101",
        snapshot_path=snap_file,
    )

    live_resp = {"count": 1, "next": None, "results": [{"id": 1, "Title": "Fresh Live Title"}]}
    with patch("httpx.Client.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = live_resp
        mock_get.return_value = mock_resp

        snapshot = provider.load_snapshot(force_refresh=True)
        assert snapshot.state == "LIVE_COMPLETE"
        assert snapshot.media_rows[0]["Title"] == "Fresh Live Title"


# ---------------------------------------------------------------------------
# Test 5: Stale cache not producing complete no-match/new-item
# ---------------------------------------------------------------------------
def test_05_stale_cache_not_producing_complete_no_match(tmp_path):
    snap_file = tmp_path / "snapshot.json"
    cached = BaserowSnapshot(
        snapshot_at="2025-01-01T00:00:00Z",
        state="CACHED_STALE",
        complete=False,
        media_rows=[{"id": 99, "Date": "2020-01-01", "Title": "Something else"}],
    )
    snap_file.write_text(cached.model_dump_json())

    provider = BaserowSnapshotProvider(snapshot_path=snap_file)
    snapshot = provider.load_snapshot()
    assert snapshot.state == "CACHED_STALE"

    engine = MediaDatabaseReconciliationEngine()
    parser_res = make_parser_result(date_val="2021-05-05", what_val="BG-02-02", place="Zurich")

    result = engine.reconcile(parser_res, snapshot)
    assert result.decision != ReviewDecision.NEW_MEDIA_CANDIDATE
    assert result.decision == ReviewDecision.DATABASE_UNAVAILABLE
    assert result.baserow_check_complete is False


# ---------------------------------------------------------------------------
# Test 6: Unique direct identity -> EXISTING_MEDIA_MATCH
# ---------------------------------------------------------------------------
def test_06_unique_direct_identity_produces_existing_media_match():
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-13T00:00:00Z",
        state="LIVE_COMPLETE",
        complete=True,
        media_rows=[
            {
                "id": 105,
                "Title": "Identity Matched Lecture",
                "Source ID": "TRACK-777",
                "Date": "2015-06-01",
            }
        ],
    )
    engine = MediaDatabaseReconciliationEngine()
    parser_res = make_parser_result(source_ids=["TRACK-777"], date_val="2015-06-01")

    result = engine.reconcile(parser_res, snapshot)
    assert result.decision == ReviewDecision.EXISTING_MEDIA_MATCH
    assert result.selected_media_row_id == 105
    assert result.renamer_enrichment.confirmed is True


# ---------------------------------------------------------------------------
# Test 7: Exact full date + scripture WHAT + place unique candidate -> confirmed match
# ---------------------------------------------------------------------------
def test_07_exact_full_date_scripture_what_place_unique_candidate():
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-13T00:00:00Z",
        state="LIVE_COMPLETE",
        complete=True,
        media_rows=[
            {
                "id": 201,
                "Date": "2014-08-04",
                "What": "BG-01-18",
                "Place, location": "Leipzig",
                "Country": "Germany",
                "Title": "Love in the Spiritual World",
            }
        ],
    )
    engine = MediaDatabaseReconciliationEngine()
    parser_res = make_parser_result(
        date_val="2014-08-04",
        what_val="BG-01-18",
        place="Leipzig",
        country="DE",
    )
    result = engine.reconcile(parser_res, snapshot)
    assert result.decision == ReviewDecision.EXISTING_MEDIA_MATCH
    assert result.selected_media_row_id == 201
    assert result.renamer_enrichment.confirmed is True
    assert result.renamer_enrichment.title_full == "Love in the Spiritual World"


# ---------------------------------------------------------------------------
# Test 8: Exact date + specific WHAT + corroborating field unique candidate -> confirmed match
# ---------------------------------------------------------------------------
def test_08_exact_date_specific_what_corroborating_field_unique_candidate():
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-13T00:00:00Z",
        state="LIVE_COMPLETE",
        complete=True,
        media_rows=[
            {
                "id": 202,
                "Date": "2018-09-10",
                "What": "SB-01-02-03",
                "Title": "Glories of Srimad Bhagavatam",
            }
        ],
    )
    engine = MediaDatabaseReconciliationEngine()
    parser_res = make_parser_result(
        date_val="2018-09-10",
        what_val="SB-01-02-03",
        place=None,
    )
    result = engine.reconcile(parser_res, snapshot)
    assert result.decision == ReviewDecision.EXISTING_MEDIA_MATCH
    assert result.selected_media_row_id == 202
    assert result.renamer_enrichment.confirmed is True


# ---------------------------------------------------------------------------
# Test 9: Two equally plausible rows -> MULTIPLE_CANDIDATES, never first-match-wins
# ---------------------------------------------------------------------------
def test_09_two_equally_plausible_rows_multiple_candidates_no_first_match_wins():
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-13T00:00:00Z",
        state="LIVE_COMPLETE",
        complete=True,
        media_rows=[
            {
                "id": 301,
                "Date": "2014-08-04",
                "Place": "Leipzig",
                "Title": "Morning Class",
            },
            {
                "id": 302,
                "Date": "2014-08-04",
                "Place": "Leipzig",
                "Title": "Evening Class",
            },
        ],
    )
    engine = MediaDatabaseReconciliationEngine()
    parser_res = make_parser_result(date_val="2014-08-04", place="Leipzig", what_val=None)

    result = engine.reconcile(parser_res, snapshot)
    assert result.decision == ReviewDecision.MULTIPLE_CANDIDATES
    assert result.selected_media_row_id is None
    assert result.renamer_enrichment.confirmed is False
    assert result.proposed_tool4_action == Tool4Action.NEEDS_REVIEW
    assert "tool_3_travel_schedule_review" in result.downstream_routing


# ---------------------------------------------------------------------------
# Test 10: Partial candidate -> PROBABLE_EXISTING_MEDIA, no confirmed enrichment
# ---------------------------------------------------------------------------
def test_10_partial_candidate_probable_existing_media_no_confirmed_enrichment():
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-13T00:00:00Z",
        state="LIVE_COMPLETE",
        complete=True,
        media_rows=[
            {
                "id": 401,
                "Date": "2014-08-04",
                "Place": "Leipzig",
            }
        ],
    )
    engine = MediaDatabaseReconciliationEngine()
    parser_res = make_parser_result(date_val="2014-08-04", place="Leipzig", what_val=None)

    result = engine.reconcile(parser_res, snapshot)
    assert result.decision == ReviewDecision.PROBABLE_EXISTING_MEDIA
    assert result.renamer_enrichment.confirmed is False


# ---------------------------------------------------------------------------
# Test 11: Leipzig example finds correct candidate and returns full title
# ---------------------------------------------------------------------------
def test_11_leipzig_example_finds_correct_candidate_and_returns_full_title():
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-13T00:00:00Z",
        state="LIVE_COMPLETE",
        complete=True,
        media_rows=[
            {
                "id": 501,
                "Date": "2014-08-04",
                "What": "BG-01-18",
                "Place, location": "Leipzig",
                "Country": "Germany",
                "Title": "Love in the Spiritual World",
            }
        ],
    )
    engine = MediaDatabaseReconciliationEngine()
    parser_res = make_parser_result(
        orig_filename="2014-08-04_KKS_BG-1-18_Leipzig-de.mp3",
        date_val="2014-08-04",
        what_val="BG-01-18",
        place="Leipzig",
        country="DE",
    )
    result = engine.reconcile(parser_res, snapshot)
    assert result.decision == ReviewDecision.EXISTING_MEDIA_MATCH
    assert result.selected_media_row_id == 501
    assert result.renamer_enrichment.confirmed is True
    assert result.renamer_enrichment.title_full == "Love in the Spiritual World"


# ---------------------------------------------------------------------------
# Test 12: Later Renamer enrichment renders BG-1-18-Love-in-the-Spiritual-World correctly
# ---------------------------------------------------------------------------
def test_12_later_renamer_enrichment_renders_bg_1_18_love_in_the_spiritual_world(tmp_path):
    reg_db = tmp_path / "registry.db"
    registry = LocalRegistry(reg_db)
    planner = RenamePlanner(mode=RenameMode.ENRICH)
    renamer = RenamerApplicationService(registry=registry, planner=planner)

    parser_res = make_parser_result(
        tracking_id="lep01",
        orig_filename="2014-08-04_KKS_BG-01-18_Leipzig-de.mp3",
        date_val="2014-08-04",
        what_val="BG-01-18",
        place="Leipzig",
        country="DE",
    )
    proposal = RenameProposal(
        tracking_id="lep01",
        original_path="/archive/2014-08-04_KKS_BG-01-18_Leipzig-de.mp3",
        current_filename="2014-08-04_KKS_BG-01-18_Leipzig-de.mp3",
        proposed_filename="2014-08-04_KKS_BG-01-18_Leipzig-de_ID-lep01.mp3",
        proposed_path="/archive/2014-08-04_KKS_BG-01-18_Leipzig-de_ID-lep01.mp3",
        mode=RenameMode.INITIAL,
        parser_result=parser_res,
    )
    registry.save_proposal(proposal)

    evidence = EnrichmentEvidence(
        tracking_id="lep01",
        when_val="2014-08-04",
        what_val="BG-01-18-Love-in-the-Spiritual-World",
        where_val="Leipzig-de",
        what_category="Bhagavad-gita",
        baserow_check_complete=True,
        source_tool="tool_2_media_database_review",
        details="Confirmed Baserow Media row 501",
    )

    enriched_prop = renamer.apply_enrichment(evidence)
    assert enriched_prop is not None
    assert "BG-01-18-Love-in-the-Spiritual-World" in enriched_prop["proposed_filename"]
    assert "Leipzig-de" in enriched_prop["proposed_filename"]


# ---------------------------------------------------------------------------
# Test 13: Long confirmed Baserow title shortened at word boundaries for length
# ---------------------------------------------------------------------------
def test_13_long_confirmed_baserow_title_shortened_at_word_boundaries_for_length(tmp_path):
    reg_db = tmp_path / "registry.db"
    registry = LocalRegistry(reg_db)
    planner = RenamePlanner(mode=RenameMode.ENRICH)
    renamer = RenamerApplicationService(registry=registry, planner=planner)

    long_title = "The Most Extraordinary Elaborate Comprehensive Exposition on Transcendent Spiritual Realities Beyond Matter and Duality in Every Dimension"
    parser_res = make_parser_result(
        tracking_id="long01",
        orig_filename="2014-08-04_KKS_BG-01-18_Leipzig-de.mp3",
        date_val="2014-08-04",
        what_val="BG-01-18",
        place="Leipzig",
        country="DE",
    )
    proposal = RenameProposal(
        tracking_id="long01",
        original_path="/archive/sample.mp3",
        current_filename="sample.mp3",
        proposed_filename="sample_ID-long01.mp3",
        proposed_path="/archive/sample_ID-long01.mp3",
        mode=RenameMode.INITIAL,
        parser_result=parser_res,
    )
    registry.save_proposal(proposal)

    evidence = EnrichmentEvidence(
        tracking_id="long01",
        what_val=f"BG-01-18-{long_title}",
        where_val="Leipzig-de",
        baserow_check_complete=True,
    )
    enriched = renamer.apply_enrichment(evidence)
    assert enriched is not None
    assert len(enriched["proposed_filename"]) <= 128
    assert "BG-01-18-" in enriched["proposed_filename"]
    stem = Path(enriched["proposed_filename"]).stem
    assert not stem.endswith("-")


# ---------------------------------------------------------------------------
# Test 14: Title shortening is deterministic across reruns
# ---------------------------------------------------------------------------
def test_14_title_shortening_is_deterministic_across_reruns():
    title = "Comprehensive Elucidation of the Science of Bhakti Yoga and Renunciation"
    budget = 40
    res1, c1, reason1 = compact_title_for_filename(title, budget)
    res2, c2, reason2 = compact_title_for_filename(title, budget)
    assert res1 == res2
    assert c1 == c2
    assert reason1 == reason2
    assert len(res1) <= budget


# ---------------------------------------------------------------------------
# Test 15: Date conflict -> explicit conflict and no automatic title enrichment
# ---------------------------------------------------------------------------
def test_15_date_conflict_explicit_conflict_and_no_automatic_title_enrichment():
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-13T00:00:00Z",
        state="LIVE_COMPLETE",
        complete=True,
        media_rows=[
            {
                "id": 601,
                "Date": "2014-04-08",
                "Place": "Leipzig",
                "What": "BG-01-18",
                "Title": "Leipzig Lecture",
            }
        ],
    )
    engine = MediaDatabaseReconciliationEngine()
    parser_res = make_parser_result(
        date_val="2014-08-04",
        what_val="BG-01-18",
        place="Leipzig",
    )
    result = engine.reconcile(parser_res, snapshot)
    assert result.decision == ReviewDecision.CONFLICT_WITH_EXISTING
    assert result.renamer_enrichment.confirmed is False
    assert any("conflict" in c.lower() for c in result.conflicts)


# ---------------------------------------------------------------------------
# Test 16: Blank database field is DATABASE_MISSING, not conflict
# ---------------------------------------------------------------------------
def test_16_blank_database_field_is_database_missing_not_conflict():
    d_st, d_det = _compare_dates("2014-08-04", None)
    assert d_st == FieldComparisonState.DATABASE_MISSING

    p_st, p_det = _compare_places("Leipzig", "DE", None, None)
    assert p_st == FieldComparisonState.DATABASE_MISSING

    w_st, w_det = _compare_what("BG-01-18", "Bhagavad-gita", None, None, None)
    assert w_st == FieldComparisonState.DATABASE_MISSING


# ---------------------------------------------------------------------------
# Test 17: Local missing field is LOCAL_MISSING and may be enrichment when confirmed
# ---------------------------------------------------------------------------
def test_17_local_missing_field_is_local_missing_and_enriches_when_confirmed():
    d_st, d_det = _compare_dates(None, "2014-08-04")
    assert d_st == FieldComparisonState.LOCAL_MISSING

    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-13T00:00:00Z",
        state="LIVE_COMPLETE",
        complete=True,
        media_rows=[
            {
                "id": 701,
                "Date": "2014-08-04",
                "Place": "Leipzig",
                "Country": "Germany",
                "What": "BG-01-18",
                "Title": "Love in the Spiritual World",
                "Source ID": "DIRECT-701",
            }
        ],
    )
    engine = MediaDatabaseReconciliationEngine()
    parser_res = make_parser_result(
        date_val="2014-08-04",
        place=None,
        country=None,
        source_ids=["DIRECT-701"],
    )
    result = engine.reconcile(parser_res, snapshot)
    assert result.decision == ReviewDecision.EXISTING_MEDIA_MATCH
    assert result.renamer_enrichment.where_val == "Leipzig-Germany"


# ---------------------------------------------------------------------------
# Test 18: category_title corroborates without flattening specific WHAT
# ---------------------------------------------------------------------------
def test_18_category_title_corroborates_without_flattening_specific_what():
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-13T00:00:00Z",
        state="LIVE_COMPLETE",
        complete=True,
        media_rows=[
            {
                "id": 801,
                "Date": "2019-01-01",
                "What": "BG-02-01",
                "Category": "Bhagavad-gita",
                "Title": "New Year Lecture",
            }
        ],
        category_title_rows=[
            {
                "id": 1,
                "category": "Bhagavad-gita",
                "title_matching_terms": "gita, bg",
            }
        ],
    )
    engine = MediaDatabaseReconciliationEngine()
    parser_res = make_parser_result(
        orig_filename="2019-01-01_KKS_BG-02-01_New-Year.mp3",
        date_val="2019-01-01",
        what_val="BG-02-01",
        what_category=None,
    )
    result = engine.reconcile(parser_res, snapshot)
    assert result.decision == ReviewDecision.EXISTING_MEDIA_MATCH
    assert "BG-02-01" in result.renamer_enrichment.what_val


# ---------------------------------------------------------------------------
# Test 19: travel_schedule corroborates candidate without continuous presence assumption
# ---------------------------------------------------------------------------
def test_19_travel_schedule_corroborates_candidate_without_continuous_presence_assumption():
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-13T00:00:00Z",
        state="LIVE_COMPLETE",
        complete=True,
        media_rows=[
            {
                "id": 901,
                "Date": "2014-08-04",
                "Place": "Leipzig",
                "Title": "Special Program",
            }
        ],
        travel_schedule_rows=[
            {
                "id": 10,
                "Start Date": "2014-08-04",
                "Place": "Leipzig",
                "Schedule text": "Ratha Yatra Tour",
            }
        ],
    )
    engine = MediaDatabaseReconciliationEngine()
    parser_res = make_parser_result(date_val="2014-08-04", place="Leipzig", what_val=None)

    result = engine.reconcile(parser_res, snapshot)
    candidate = result.candidates[0]
    assert any("Travel schedule" in ctx for ctx in candidate.travel_schedule_context)


# ---------------------------------------------------------------------------
# Test 20: Valid complete no-match -> NEW_MEDIA_CANDIDATE when discriminating
# ---------------------------------------------------------------------------
def test_20_valid_complete_no_match_new_media_candidate_when_discriminating():
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-13T00:00:00Z",
        state="LIVE_COMPLETE",
        complete=True,
        media_rows=[],
    )
    engine = MediaDatabaseReconciliationEngine()
    parser_res = make_parser_result(
        date_val="2022-03-15",
        what_val="SB-07-09-11",
        place="Mayapur",
    )
    result = engine.reconcile(parser_res, snapshot)
    assert result.decision == ReviewDecision.NEW_MEDIA_CANDIDATE
    assert result.proposed_tool4_action == Tool4Action.CREATE_NEW


# ---------------------------------------------------------------------------
# Test 21: Sparse no-match -> INSUFFICIENT_EVIDENCE
# ---------------------------------------------------------------------------
def test_21_sparse_no_match_insufficient_evidence():
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-13T00:00:00Z",
        state="LIVE_COMPLETE",
        complete=True,
        media_rows=[],
    )
    engine = MediaDatabaseReconciliationEngine()
    parser_res = make_parser_result(
        orig_filename="kirtan_recording.mp3",
        date_val=None,
        what_val=None,
        place=None,
    )
    result = engine.reconcile(parser_res, snapshot)
    assert result.decision == ReviewDecision.INSUFFICIENT_EVIDENCE
    assert result.proposed_tool4_action == Tool4Action.NO_WRITE


# ---------------------------------------------------------------------------
# Test 22: Database unavailable -> DATABASE_UNAVAILABLE and baserow_check_complete=false
# ---------------------------------------------------------------------------
def test_22_database_unavailable_decision_and_baserow_check_complete_false():
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-13T00:00:00Z",
        state="UNAVAILABLE",
        complete=False,
        media_rows=[],
    )
    engine = MediaDatabaseReconciliationEngine()
    parser_res = make_parser_result()
    result = engine.reconcile(parser_res, snapshot)
    assert result.decision == ReviewDecision.DATABASE_UNAVAILABLE
    assert result.baserow_check_complete is False


# ---------------------------------------------------------------------------
# Test 23: Valid complete no-match -> baserow_check_complete=true
# ---------------------------------------------------------------------------
def test_23_valid_complete_no_match_baserow_check_complete_true():
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-13T00:00:00Z",
        state="LIVE_COMPLETE",
        complete=True,
        media_rows=[],
    )
    engine = MediaDatabaseReconciliationEngine()
    parser_res = make_parser_result(
        date_val="2023-01-01",
        what_val="BG-09-26",
        place="London",
    )
    result = engine.reconcile(parser_res, snapshot)
    assert result.decision == ReviewDecision.NEW_MEDIA_CANDIDATE
    assert result.baserow_check_complete is True


# ---------------------------------------------------------------------------
# Test 24: _edited lifecycle handoff to Renamer
# ---------------------------------------------------------------------------
def test_24_edited_lifecycle_handoff_to_renamer(tmp_path):
    reg_db = tmp_path / "registry.db"
    registry = LocalRegistry(reg_db)
    planner = RenamePlanner(mode=RenameMode.ENRICH)
    renamer = RenamerApplicationService(registry=registry, planner=planner)

    parser_res = make_parser_result(
        tracking_id="ed01",
        orig_filename="2014-08-04_KKS_BG-01-18_Leipzig-de_edited.mp3",
        date_val="2014-08-04",
        what_val="BG-01-18",
        place="Leipzig",
        country="DE",
        technical_flags=["edited"],
    )
    proposal = RenameProposal(
        tracking_id="ed01",
        original_path="/archive/2014-08-04_KKS_BG-01-18_Leipzig-de_edited.mp3",
        current_filename="2014-08-04_KKS_BG-01-18_Leipzig-de_edited.mp3",
        proposed_filename="2014-08-04_KKS_BG-01-18_Leipzig-de_edited_ID-ed01.mp3",
        proposed_path="/archive/2014-08-04_KKS_BG-01-18_Leipzig-de_edited_ID-ed01.mp3",
        mode=RenameMode.INITIAL,
        parser_result=parser_res,
    )
    registry.save_proposal(proposal)

    # 1. Before Baserow check is complete (baserow_check_complete=False), _edited is preserved
    evidence_incomplete = EnrichmentEvidence(
        tracking_id="ed01",
        what_val="BG-01-18-Love-in-the-Spiritual-World",
        baserow_check_complete=False,
    )
    enriched_incomplete = renamer.apply_enrichment(evidence_incomplete)
    assert enriched_incomplete is not None
    assert "_edited" in enriched_incomplete["proposed_filename"]

    # 2. When Tool 2 completes usable Baserow check (baserow_check_complete=True), Renamer drops _edited
    evidence_complete = EnrichmentEvidence(
        tracking_id="ed01",
        what_val="BG-01-18-Love-in-the-Spiritual-World",
        baserow_check_complete=True,
    )
    enriched_complete = renamer.apply_enrichment(evidence_complete)
    assert enriched_complete is not None
    assert "_edited" not in enriched_complete["proposed_filename"]
    assert "Love-in-the-Spiritual-World" in enriched_complete["proposed_filename"]


# ---------------------------------------------------------------------------
# Test 25: Related format maps to same logical Media row (LINK_EXISTING)
# ---------------------------------------------------------------------------
def test_25_related_format_maps_to_same_logical_media_row_link_existing():
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-13T00:00:00Z",
        state="LIVE_COMPLETE",
        complete=True,
        media_rows=[
            {
                "id": 1001,
                "Date": "2014-08-04",
                "What": "BG-01-18",
                "Place": "Leipzig",
                "Country": "Germany",
                "Format": "Audio",
                "Title": "Love in the Spiritual World",
            }
        ],
    )
    engine = MediaDatabaseReconciliationEngine()
    parser_res = make_parser_result(
        orig_filename="2014-08-04_KKS_BG-01-18_Leipzig-de.mp4",
        date_val="2014-08-04",
        what_val="BG-01-18",
        place="Leipzig",
        country="DE",
    )
    result = engine.reconcile(parser_res, snapshot)
    assert result.decision == ReviewDecision.EXISTING_MEDIA_MATCH
    assert result.proposed_tool4_action == Tool4Action.LINK_EXISTING


# ---------------------------------------------------------------------------
# Test 26: Human confirms candidate with audit provenance
# ---------------------------------------------------------------------------
def test_26_human_confirms_candidate_with_audit_provenance(tmp_path):
    reg_db = tmp_path / "registry.db"
    registry = LocalRegistry(reg_db)

    parser_res = make_parser_result(tracking_id="h01", date_val="2014-08-04", place="Leipzig", what_val=None)
    prop = RenameProposal(
        tracking_id="h01",
        original_path="/archive/sample.mp3",
        current_filename="sample.mp3",
        proposed_filename="sample_ID-h01.mp3",
        proposed_path="/archive/sample_ID-h01.mp3",
        mode=RenameMode.INITIAL,
        parser_result=parser_res,
    )
    registry.save_proposal(prop)

    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-13T00:00:00Z",
        state="LIVE_COMPLETE",
        complete=True,
        media_rows=[
            {
                "id": 1101,
                "Date": "2014-08-04",
                "Place": "Leipzig",
                "Title": "Human Selected Title",
            }
        ],
    )
    provider = MagicMock()
    provider.load_snapshot.return_value = snapshot

    service = MediaDatabaseReviewService(registry=registry, provider=provider)
    res_init = service.review_file("h01")
    assert res_init.decision == ReviewDecision.PROBABLE_EXISTING_MEDIA

    res_conf = service.apply_human_decision(
        tracking_id="h01",
        action="confirm_existing",
        media_row_id=1101,
        reviewer="editor1",
        notes="Verified audio recording against archive log",
    )
    assert res_conf.decision == ReviewDecision.EXISTING_MEDIA_MATCH
    assert res_conf.renamer_enrichment.confirmed is True
    assert res_conf.renamer_enrichment.title_full == "Human Selected Title"

    audit_history = registry.get_review_actions("h01")
    assert any(a["action"] == "media_db_confirm_existing" for a in audit_history)


# ---------------------------------------------------------------------------
# Test 27: Human confirms new media candidate locally without writing Baserow
# ---------------------------------------------------------------------------
def test_27_human_confirms_new_media_candidate_locally_no_baserow_write(tmp_path):
    reg_db = tmp_path / "registry.db"
    registry = LocalRegistry(reg_db)

    parser_res = make_parser_result(tracking_id="h02")
    prop = RenameProposal(
        tracking_id="h02",
        original_path="/archive/sample.mp3",
        current_filename="sample.mp3",
        proposed_filename="sample_ID-h02.mp3",
        proposed_path="/archive/sample_ID-h02.mp3",
        mode=RenameMode.INITIAL,
        parser_result=parser_res,
    )
    registry.save_proposal(prop)

    provider = MagicMock()
    provider.load_snapshot.return_value = BaserowSnapshot(
        snapshot_at="2026-09-13T00:00:00Z",
        state="LIVE_COMPLETE",
        complete=True,
    )

    service = MediaDatabaseReviewService(registry=registry, provider=provider)
    service.review_file("h02")

    res = service.apply_human_decision(
        tracking_id="h02",
        action="confirm_new",
        reviewer="editor2",
    )
    assert res.decision == ReviewDecision.NEW_MEDIA_CANDIDATE
    assert res.proposed_tool4_action == Tool4Action.CREATE_NEW
    audit = registry.get_review_actions("h02")
    assert any(a["action"] == "media_db_confirm_new" for a in audit)


# ---------------------------------------------------------------------------
# Test 28: Idempotent rerun against same snapshot
# ---------------------------------------------------------------------------
def test_28_idempotent_rerun_against_same_snapshot(tmp_path):
    reg_db = tmp_path / "registry.db"
    registry = LocalRegistry(reg_db)

    parser_res = make_parser_result(
        tracking_id="idemp01",
        orig_filename="2014-08-04_KKS_BG-01-18_Leipzig-de.mp3",
        date_val="2014-08-04",
        what_val="BG-01-18",
        place="Leipzig",
    )
    prop = RenameProposal(
        tracking_id="idemp01",
        original_path="/archive/sample.mp3",
        current_filename="sample.mp3",
        proposed_filename="sample_ID-idemp01.mp3",
        proposed_path="/archive/sample_ID-idemp01.mp3",
        mode=RenameMode.INITIAL,
        parser_result=parser_res,
    )
    registry.save_proposal(prop)

    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-13T00:00:00Z",
        state="LIVE_COMPLETE",
        complete=True,
        media_rows=[
            {
                "id": 1201,
                "Date": "2014-08-04",
                "What": "BG-01-18",
                "Place": "Leipzig",
                "Title": "Stable Title",
            }
        ],
    )
    provider = MagicMock()
    provider.load_snapshot.return_value = snapshot

    service = MediaDatabaseReviewService(registry=registry, provider=provider)
    r1 = service.review_file("idemp01")
    r2 = service.review_file("idemp01")

    assert r1.decision == r2.decision
    assert r1.selected_media_row_id == r2.selected_media_row_id
    assert r1.renamer_enrichment.title_full == r2.renamer_enrichment.title_full


# ---------------------------------------------------------------------------
# Test 29: Batch continues when one item errors/ambiguous
# ---------------------------------------------------------------------------
def test_29_batch_continues_when_one_item_errors_or_ambiguous(tmp_path):
    reg_db = tmp_path / "registry.db"
    registry = LocalRegistry(reg_db)

    p1 = make_parser_result(tracking_id="b01", date_val="2014-08-04", what_val="BG-01-18", place="Leipzig")
    registry.save_proposal(RenameProposal(
        tracking_id="b01", original_path="p1", current_filename="f1", proposed_filename="f1", proposed_path="p1", mode=RenameMode.INITIAL, parser_result=p1
    ))
    p3 = make_parser_result(tracking_id="b03", date_val="2015-01-01", what_val="SB-01-01-01", place="Vrindavan")
    registry.save_proposal(RenameProposal(
        tracking_id="b03", original_path="p3", current_filename="f3", proposed_filename="f3", proposed_path="p3", mode=RenameMode.INITIAL, parser_result=p3
    ))

    provider = MagicMock()
    provider.load_snapshot.return_value = BaserowSnapshot(
        snapshot_at="2026-09-13T00:00:00Z",
        state="LIVE_COMPLETE",
        complete=True,
    )
    service = MediaDatabaseReviewService(registry=registry, provider=provider)

    results = service.review_batch(tracking_ids=["b01", "non_existent_id", "b03"])
    assert len(results) == 2
    assert {r.tracking_id for r in results} == {"b01", "b03"}


# ---------------------------------------------------------------------------
# Test 30: Tool 4 handoff carries row ID and snapshot timestamp
# ---------------------------------------------------------------------------
def test_30_tool_4_handoff_carries_row_id_and_snapshot_timestamp():
    snap_ts = "2026-09-13T12:34:56Z"
    snapshot = BaserowSnapshot(
        snapshot_at=snap_ts,
        state="LIVE_COMPLETE",
        complete=True,
        media_rows=[
            {
                "id": 1301,
                "Date": "2014-08-04",
                "What": "BG-01-18",
                "Place": "Leipzig",
                "Title": "Tool 4 Target",
            }
        ],
    )
    engine = MediaDatabaseReconciliationEngine()
    parser_res = make_parser_result(
        date_val="2014-08-04",
        what_val="BG-01-18",
        place="Leipzig",
    )
    result = engine.reconcile(parser_res, snapshot)
    assert result.selected_media_row_id == 1301
    assert result.database_snapshot_at == snap_ts
    assert result.database_state == "LIVE_COMPLETE"
    assert result.proposed_tool4_action == Tool4Action.ENRICH_EXISTING


# ---------------------------------------------------------------------------
# Test 31: Portal integration & human confirmation endpoints
# ---------------------------------------------------------------------------
def test_31_portal_media_db_endpoints(tmp_path):
    from fastapi.testclient import TestClient
    from media_archive_tooling.review_portal.app import app, configure_review_context

    reg_db = tmp_path / "registry.db"
    registry = LocalRegistry(reg_db)

    parser_res = make_parser_result(
        tracking_id="portal01",
        orig_filename="2014-08-04_KKS_BG-01-18_Leipzig-de.mp3",
        date_val="2014-08-04",
        what_val="BG-01-18",
        place="Leipzig",
    )
    prop = RenameProposal(
        tracking_id="portal01",
        original_path="/archive/sample.mp3",
        current_filename="sample.mp3",
        proposed_filename="sample_ID-portal01.mp3",
        proposed_path="/archive/sample_ID-portal01.mp3",
        mode=RenameMode.INITIAL,
        parser_result=parser_res,
    )
    registry.save_proposal(prop)

    rev_res = MediaDatabaseReviewResult(
        tracking_id="portal01",
        database_state="LIVE_COMPLETE",
        database_snapshot_at="2026-09-13T00:00:00Z",
        snapshot_complete=True,
        baserow_check_complete=True,
        decision=ReviewDecision.EXISTING_MEDIA_MATCH,
        selected_media_row_id=501,
        candidates=[
            MediaCandidate(
                media_row_id=501,
                raw_row={"id": 501, "Title": "Love in the Spiritual World"},
                normalized_row={"id": 501, "title": "Love in the Spiritual World", "date": "2014-08-04", "place": "Leipzig", "country": "Germany"},
                retrieval_reasons=["Exact full date + WHAT + WHERE high-specificity signature"],
                score=115.0,
            )
        ],
    )
    registry.save_media_db_review(
        tracking_id="portal01",
        decision=rev_res.decision.value,
        database_state="LIVE_COMPLETE",
        snapshot_timestamp=rev_res.database_snapshot_at,
        result_json=rev_res.model_dump_json(),
        selected_media_row_id=501,
        review_required=False,
    )

    configure_review_context(registry_path=reg_db)
    client = TestClient(app)

    # GET file detail - should display Tool 2 card
    resp = client.get("/file/portal01")
    assert resp.status_code == 200
    assert "Tool 2 — Baserow Media Database Reconciliation" in resp.text
    assert "EXISTING_MEDIA_MATCH" in resp.text
    assert "#501" in resp.text

    # POST human action
    resp2 = client.post(
        "/file/portal01/media-db-action",
        data={
            "action": "confirm_existing",
            "media_row_id": "501",
            "notes": "Verified in portal",
            "reviewer": "web_operator",
        },
        follow_redirects=True,
    )
    assert resp2.status_code == 200
    assert "Tool 2 — Baserow Media Database Reconciliation" in resp2.text
