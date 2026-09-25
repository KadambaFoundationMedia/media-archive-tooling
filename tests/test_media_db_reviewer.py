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
        assert snapshot.state in ("LIVE_CURRENT", "LIVE_COMPLETE")
        assert snapshot.media_rows[0]["Title"] == "Fresh Live Title"


# ---------------------------------------------------------------------------
# Test 5: Stale cache not producing complete no-match/new-item
# ---------------------------------------------------------------------------
def test_05_stale_cache_not_producing_complete_no_match(tmp_path):
    snap_file = tmp_path / "snapshot.json"
    cached = BaserowSnapshot(
        snapshot_at="2025-01-01T00:00:00Z",
        state="DATABASE_UNAVAILABLE",
        complete=False,
        media_rows=[{"id": 99, "Date": "2020-01-01", "Title": "Something else"}],
    )
    snap_file.write_text(cached.model_dump_json())

    provider = BaserowSnapshotProvider(snapshot_path=snap_file)
    snapshot = provider.load_snapshot()
    assert snapshot.state == "DATABASE_UNAVAILABLE"

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


def test_candidate_retains_live_raw_fields_for_console_preview():
    raw_row = {
        "id": 201,
        "Date": "2014-08-04",
        "What": "BG-01-18",
        "Place, location": "Leipzig",
        "Country": "Germany",
        "Title": "Love in the Spiritual World",
        "Filename": "old-source.mp3",
        "Notes": "Original filename: old-source.mp3",
        "media_archive_path": "/archive/old-source.mp3",
    }
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-13T00:00:00Z", state="LIVE_CURRENT",
        complete=True, media_rows=[raw_row],
    )
    result = MediaDatabaseReconciliationEngine().reconcile(make_parser_result(), snapshot)
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.normalized_row["title"] == raw_row["Title"]
    assert candidate.raw_row["Notes"] == raw_row["Notes"]
    assert candidate.raw_row["media_archive_path"] == raw_row["media_archive_path"]


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
    assert result.renamer_enrichment.where_val == "Leipzig-de"


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


def test_confirmed_location_alias_preserves_tool1_canonical_filename_value():
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-19T00:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=[{
            "id": 3234,
            "Date": "2003-10-25",
            "Title": "SB 4.9.11",
            "Category": "Srimad-bhagavatam",
            "Place, location": "Farma-Krishna-Dvur",
            "Country": "Czech-republic",
        }],
    )
    parser_result = make_parser_result(
        tracking_id="prague3234",
        date_val="2003-10-25",
        what_val="SB-4-9-11",
        what_category="Srimad Bhagavatam",
        place="Krsna-Dvur",
        country="cz",
    )

    result = MediaDatabaseReconciliationEngine().reconcile(parser_result, snapshot)

    assert result.decision == ReviewDecision.EXISTING_MEDIA_MATCH
    assert result.selected_media_row_id == 3234
    assert result.renamer_enrichment.where_val == "Krsna-Dvur-cz"


def test_20_country_only_evidence_excludes_foreign_partial_date_noise():
    assert _compare_places(None, "cz", "Amsterdam", "Netherlands") == (
        FieldComparisonState.CONFLICT,
        "Country conflict: local 'cz' (CZ) contradicts database 'Netherlands' (NL)",
    )

    assert _compare_places(
        "Krsna-Dvur",
        "cz",
        "Farma-Krishna-Dvur",
        "Czech-republic",
    ) == (FieldComparisonState.AGREES, None)

    parser_result = make_parser_result(
        tracking_id="duben02",
        orig_filename="02 KKS. SB. 3.1.20.mp3",
        date_val="2008-04-DD",
        what_val="SB-3-1-20",
        what_category="Srimad Bhagavatam",
        place=None,
        country="cz",
    )
    parser_result.when.precision = "month"
    parser_result.when.state = ResolutionState.STRONG
    parser_result.where = WhereResult(
        place_location=None,
        country="Czech Republic",
        country_iso2="cz",
        state=ResolutionState.STRONG,
    )
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-17T00:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=[
            {
                "id": 3142,
                "Date": "2008-04-30",
                "Title": "Queensday",
                "Category": "Unknown",
                "Place, location": "ISKCON-Amsterdam",
                "Country": "Netherlands",
            },
            {
                "id": 3144,
                "Date": "2008-04-25",
                "Title": "Leicester",
                "Category": "Unknown",
                "Place, location": "Leicester",
                "Country": "United Kingdom",
            },
        ],
    )

    result = MediaDatabaseReconciliationEngine().reconcile(parser_result, snapshot)

    assert result.decision == ReviewDecision.NEW_MEDIA_CANDIDATE
    assert result.candidates == []
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

    fake_provider = BaserowSnapshotProvider(
        api_url="https://api.baserow.io",
        api_token="test_mock_token",
        media_table_id="100",
        snapshot_path=tmp_path / "mock_snapshot.json",
    )
    configure_review_context(registry_path=reg_db, media_db_provider=fake_provider)
    client = TestClient(app)

    # GET file detail - should display Tool 2 card
    resp = client.get("/file/portal01")
    assert resp.status_code == 200
    assert "Tool 2 — Baserow Media Database Reconciliation" in resp.text
    assert "EXISTING_MEDIA_MATCH" in resp.text
    assert "#501" in resp.text

    # POST human action
    with patch("httpx.Client.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": 501,
            "Title": "Love in the Spiritual World",
            "Date": "2014-08-04",
            "Place": "Leipzig",
            "Country": "Germany",
            "What": "BG-01-18",
        }
        mock_get.return_value = mock_resp

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

    updated_rev = registry.get_media_db_review("portal01")
    assert updated_rev is not None
    assert updated_rev["decision"] == ReviewDecision.EXISTING_MEDIA_MATCH.value
    assert updated_rev["selected_media_row_id"] == 501
    enrichment = updated_rev["result"]["renamer_enrichment"]
    assert enrichment["confirmed"] is True
    assert enrichment["title_full"] == "Love in the Spiritual World"
    assert enrichment["live_read_complete"] is True


# ---------------------------------------------------------------------------
# Test 32: Live revalidation race conditions (R-001)
# ---------------------------------------------------------------------------
def test_32_live_revalidation_race_collaborator_edit_or_delete(tmp_path):
    reg_db = tmp_path / "reg.db"
    registry = LocalRegistry(reg_db)
    parser_res = make_parser_result(
        tracking_id="race01",
        orig_filename="2014-08-04_KKS_BG-01-18_Leipzig-de.mp3",
        date_val="2014-08-04",
        what_val="BG-01-18",
        place="Leipzig",
    )
    prop = RenameProposal(
        tracking_id="race01",
        original_path="/archive/sample.mp3",
        current_filename="sample.mp3",
        proposed_filename="sample_ID-race01.mp3",
        proposed_path="/archive/sample_ID-race01.mp3",
        mode=RenameMode.INITIAL,
        parser_result=parser_res,
    )
    registry.save_proposal(prop)

    rev_res = MediaDatabaseReviewResult(
        tracking_id="race01",
        database_state="LIVE_CURRENT",
        database_snapshot_at="2026-09-14T00:00:00Z",
        snapshot_complete=True,
        baserow_check_complete=True,
        decision=ReviewDecision.EXISTING_MEDIA_MATCH,
        selected_media_row_id=777,
        selected_field_evidence={
            "local_date": "2014-08-04",
            "local_what": "BG-01-18",
            "local_place": "Leipzig",
            "local_country": "DE",
        },
        candidates=[
            MediaCandidate(
                media_row_id=777,
                raw_row={"id": 777, "Title": "Original Title"},
                normalized_row={"id": 777, "title": "Original Title", "date": "2014-08-04", "what": "BG-01-18"},
                retrieval_reasons=["Direct match"],
                score=100.0,
            )
        ],
    )
    registry.save_media_db_review(
        tracking_id="race01",
        decision=rev_res.decision.value,
        database_state="LIVE_CURRENT",
        snapshot_timestamp=rev_res.database_snapshot_at,
        result_json=rev_res.model_dump_json(),
        selected_media_row_id=777,
        review_required=False,
    )

    # Mock provider where row 777 was deleted by collaborator
    mock_provider = MagicMock(spec=BaserowSnapshotProvider)
    mock_provider.fetch_media_row_live.return_value = None

    service = MediaDatabaseReviewService(registry=registry, provider=mock_provider)
    with pytest.raises(RuntimeError, match="no longer exists in Baserow"):
        service.confirm_existing(tracking_id="race01", media_row_id=777)

    # Race condition on confirm_new: collaborator inserted matching row
    mock_provider.search_media_candidates_live.return_value = [
        {"id": 999, "title": "Collaborator Inserted", "date": "2014-08-04", "what": "BG-01-18"}
    ]
    with pytest.raises(RuntimeError, match="live search discovered matching row"):
        service.confirm_new(tracking_id="race01")


# ---------------------------------------------------------------------------
# Test 33: Pure boolean predicate (R-002) - high score without predicate does not confirm
# ---------------------------------------------------------------------------
def test_33_negative_test_high_score_without_explicit_predicates_does_not_auto_confirm():
    engine = MediaDatabaseReconciliationEngine()
    parser_res = make_parser_result(
        date_val="2014-08-04",
        what_val="Bhakti",  # Specific topic, not scripture reference
        place="London",
    )
    # Candidate with partial overlap (place match, topic overlap) but missing date -> unconfirmed
    candidate_row = {
        "id": 10,
        "Date": "",  # Missing in database
        "Place": "London",
        "Title": "London Seminar on Bhakti",
        "Category": "Seminar",
    }
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-14T00:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=[candidate_row],
    )
    result = engine.reconcile(parser_res, snapshot)
    # Even if scoring assigns points, decision must NOT be EXISTING_MEDIA_MATCH
    assert result.decision != ReviewDecision.EXISTING_MEDIA_MATCH
    assert result.decision == ReviewDecision.PROBABLE_EXISTING_MEDIA
    assert result.review_required is True


# ---------------------------------------------------------------------------
# Test 34: Country contradiction flags conflict (R-003)
# ---------------------------------------------------------------------------
def test_34_country_contradiction_flags_conflict():
    engine = MediaDatabaseReconciliationEngine()
    parser_res = make_parser_result(
        date_val="2014-08-04",
        what_val="BG-01-18",
        place="Paris",
        country="United States",  # Paris, Texas / US
    )
    candidate_row = {
        "id": 20,
        "Date": "2014-08-04",
        "Place": "Paris",
        "Country": "France",  # Paris, France
        "What": "BG-01-18",
        "Title": "Bhagavad-gita 1.18",
    }
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-14T00:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=[candidate_row],
    )
    result = engine.reconcile(parser_res, snapshot)
    assert result.decision != ReviewDecision.EXISTING_MEDIA_MATCH
    assert len(result.candidates) > 0
    cand = result.candidates[0]
    places_comp = cand.field_comparisons.get("place")
    assert places_comp is not None
    assert places_comp.state == FieldComparisonState.CONFLICT
    assert any("Country conflict" in c for c in cand.conflicts)


# ---------------------------------------------------------------------------
# Test 35: Structural scripture reference matching (R-004)
# ---------------------------------------------------------------------------
def test_35_structural_scripture_reference_matching():
    engine = MediaDatabaseReconciliationEngine()
    # Negative test: BG-01-01 vs BG-01-10
    p1 = make_parser_result(date_val="2014-08-04", what_val="BG-01-01", place="Leipzig")
    c1 = {
        "id": 31,
        "Date": "2014-08-04",
        "Place": "Leipzig",
        "What": "BG-01-10",
        "Title": "BG 1.10",
    }
    snap1 = BaserowSnapshot(
        snapshot_at="2026-09-14T00:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=[c1],
    )
    res1 = engine.reconcile(p1, snap1)
    assert res1.decision != ReviewDecision.EXISTING_MEDIA_MATCH
    what_comp = res1.candidates[0].field_comparisons.get("what")
    assert what_comp.state == FieldComparisonState.CONFLICT

    # Multi-verse range test: BG-01-01-02 vs BG-01-01 (partial overlap does NOT establish identity)
    p2 = make_parser_result(date_val="2014-08-04", what_val="BG-01-01-02", place="Leipzig")
    c2 = {
        "id": 32,
        "Date": "2014-08-04",
        "Place": "Leipzig",
        "What": "BG-01-01",
        "Title": "BG 1.1",
    }
    snap2 = BaserowSnapshot(
        snapshot_at="2026-09-14T00:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=[c2],
    )
    res2 = engine.reconcile(p2, snap2)
    cand2 = res2.candidates[0]
    assert cand2.field_comparisons["what"].state == FieldComparisonState.CONFLICT
    assert "partial verse overlap" in (cand2.field_comparisons["what"].details or "")
    assert res2.decision != ReviewDecision.EXISTING_MEDIA_MATCH


# ---------------------------------------------------------------------------
# Test 36: Tool 1 partial date matching (R-005)
# ---------------------------------------------------------------------------
def test_36_tool1_partial_date_matching():
    engine = MediaDatabaseReconciliationEngine()
    # Partial date YYYY-MM-DD where day is unknown
    p1 = make_parser_result(date_val="2015-02-DD", what_val="BG-01-18", place="Leipzig")
    c1 = {"id": 41, "Date": "2015-02-15", "Place": "Leipzig", "What": "BG-01-18", "Title": "Lecture"}
    snap1 = BaserowSnapshot(
        snapshot_at="2026-09-14T00:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=[c1],
    )
    res1 = engine.reconcile(p1, snap1)
    cand1 = res1.candidates[0]
    date_comp = cand1.field_comparisons.get("date")
    assert date_comp is not None
    assert date_comp.state == FieldComparisonState.AGREES
    assert "Partial date match" in (date_comp.details or "")

    # Different month -> CONFLICT
    c2 = {"id": 42, "Date": "2015-03-15", "Place": "Leipzig", "What": "BG-01-18", "Title": "Lecture"}
    snap2 = BaserowSnapshot(
        snapshot_at="2026-09-14T00:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=[c2],
    )
    res2 = engine.reconcile(p1, snap2)
    cand2 = res2.candidates[0]
    assert cand2.field_comparisons["date"].state == FieldComparisonState.CONFLICT


# ---------------------------------------------------------------------------
# Test 37: Bounded travel schedule corroboration (R-006)
# ---------------------------------------------------------------------------
def test_37_bounded_travel_schedule_corroboration():
    engine = MediaDatabaseReconciliationEngine()
    travel_row = {
        "id": 1,
        "City": "Leipzig",
        "Country": "Germany",
        "Start Date": "2014-08-01",
        "End Date": "2014-08-05",
    }
    candidate_row = {
        "id": 51,
        "Date": "2014-08-03",
        "Place": "",  # missing in media row
        "What": "BG-01-18",
        "Title": "BG 1.18",
    }
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-14T00:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=[candidate_row],
        travel_schedule_rows=[travel_row],
    )
    # Inside travel range: corroborates
    p_inside = make_parser_result(date_val="2014-08-03", what_val="BG-01-18", place="Leipzig")
    res_inside = engine.reconcile(p_inside, snapshot)
    cand_inside = res_inside.candidates[0]
    assert any("Travel schedule records 'Leipzig'" in c for c in cand_inside.travel_schedule_context)
    assert any("Travel schedule corroborates" in r for r in cand_inside.retrieval_reasons)

    # Outside travel range (same month): does NOT corroborate
    p_outside = make_parser_result(date_val="2014-08-25", what_val="BG-01-18", place="Leipzig")
    cand_outside_row = dict(candidate_row, id=52, Date="2014-08-25")
    snap_outside = BaserowSnapshot(
        snapshot_at="2026-09-14T00:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=[cand_outside_row],
        travel_schedule_rows=[travel_row],
    )
    res_outside = engine.reconcile(p_outside, snap_outside)
    cand_outside = res_outside.candidates[0]
    assert not any("Travel schedule records 'Leipzig'" in c for c in cand_outside.travel_schedule_context)


# ---------------------------------------------------------------------------
# Test 38: Progressive conflict routing (R-007)
# ---------------------------------------------------------------------------
def test_38_progressive_conflict_routing_location_vs_identity():
    engine = MediaDatabaseReconciliationEngine()
    # Case 1: Location-only conflict -> routed downstream to Tool 3, review_required_now is False
    p_loc = make_parser_result(date_val="2014-08-04", what_val="BG-01-18", place="Berlin")
    c_loc = {"id": 61, "Date": "2014-08-04", "What": "BG-01-18", "Place": "Leipzig", "Title": "BG 1.18"}
    snap1 = BaserowSnapshot(snapshot_at="2026-09-14T00:00:00Z", state="LIVE_CURRENT", complete=True, media_rows=[c_loc])
    res1 = engine.reconcile(p_loc, snap1)
    assert res1.review_required is True
    assert res1.review_required_now is False
    assert any("tool_3" in r.lower() for r in res1.downstream_routing)

    # Case 2: Direct-identity contradiction (same filename, conflicting date) -> review_required_now is True
    p_ident = make_parser_result(
        date_val="2014-08-04",
        what_val="BG-01-18",
        place="Leipzig",
        orig_filename="2014-08-04_KKS_BG-01-18_Leipzig-de.mp3",
    )
    c_ident = {
        "id": 62,
        "Date": "2014-08-05",  # Date conflict!
        "What": "BG-01-18",
        "Place": "Leipzig",
        "Title": "BG 1.18",
        "Filename": "2014-08-04_KKS_BG-01-18_Leipzig-de.mp3",  # Direct identity match!
    }
    snap2 = BaserowSnapshot(snapshot_at="2026-09-14T00:00:00Z", state="LIVE_CURRENT", complete=True, media_rows=[c_ident])
    res2 = engine.reconcile(p_ident, snap2)
    assert res2.review_required_now is True
    assert res2.review_required is True


# ---------------------------------------------------------------------------
# Test 39: Renamer enrichment carries live provenance (R-001)
# ---------------------------------------------------------------------------
def test_39_renamer_enrichment_carries_live_provenance():
    engine = MediaDatabaseReconciliationEngine()
    parser_res = make_parser_result(
        date_val="2014-08-04",
        what_val="BG-01-18",
        place="Leipzig",
    )
    candidate_row = {
        "id": 71,
        "Date": "2014-08-04",
        "Place": "Leipzig",
        "Country": "Germany",
        "What": "BG-01-18",
        "Title": "Love in the Spiritual World",
    }
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-14T10:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=[candidate_row],
    )
    result = engine.reconcile(parser_res, snapshot)
    assert result.decision == ReviewDecision.EXISTING_MEDIA_MATCH
    enrichment = result.renamer_enrichment
    assert enrichment.confirmed is True
    assert enrichment.media_row_id == 71
    assert enrichment.baserow_read_at == "2026-09-14T10:00:00Z"
    assert enrichment.live_read_complete is True


# ---------------------------------------------------------------------------
# Test 40: Portal media DB action fails without live provider in production (R-010)
# ---------------------------------------------------------------------------
def test_40_portal_media_db_action_fails_without_live_provider_in_production(tmp_path, monkeypatch):
    """Proves that when Baserow credentials are absent in production, live authority prevents false confirmation."""
    from fastapi.testclient import TestClient
    from media_archive_tooling.review_portal.app import app, configure_review_context
    from media_archive_tooling.config import AppConfig

    reg_db = tmp_path / "registry.db"
    registry = LocalRegistry(reg_db)

    parser_res = make_parser_result(
        tracking_id="noprov01",
        orig_filename="2014-08-04_KKS_BG-01-18_Leipzig-de.mp3",
        date_val="2014-08-04",
        what_val="BG-01-18",
        place="Leipzig",
    )
    prop = RenameProposal(
        tracking_id="noprov01",
        original_path="/archive/sample.mp3",
        current_filename="sample.mp3",
        proposed_filename="sample_ID-noprov01.mp3",
        proposed_path="/archive/sample_ID-noprov01.mp3",
        mode=RenameMode.INITIAL,
        parser_result=parser_res,
    )
    registry.save_proposal(prop)

    # Configure portal without any injected provider/service
    configure_review_context(registry_path=reg_db, media_db_service=None, media_db_provider=None)

    # Ensure environment has no Baserow credentials
    monkeypatch.delenv("BASEROW_API_TOKEN", raising=False)
    monkeypatch.delenv("BASEROW_MEDIA_TABLE_ID", raising=False)

    with patch("media_archive_tooling.review_portal.app.load_config", return_value=AppConfig()):
        client = TestClient(app)
        resp = client.post(
            "/file/noprov01/media-db-action",
            data={
                "action": "confirm_existing",
                "media_row_id": "501",
                "notes": "Attempt in unconfigured env",
                "reviewer": "web_operator",
            },
        )
        assert resp.status_code == 400
        assert "live database is unavailable" in resp.json().get("detail", "")


# ---------------------------------------------------------------------------
# Test 41: Portal media DB action supports injected service (R-010)
# ---------------------------------------------------------------------------
def test_41_portal_media_db_action_supports_injected_service(tmp_path):
    """Proves that custom media_db_service can be directly injected into review portal context."""
    from fastapi.testclient import TestClient
    from media_archive_tooling.review_portal.app import app, configure_review_context

    reg_db = tmp_path / "registry.db"
    registry = LocalRegistry(reg_db)

    parser_res = make_parser_result(
        tracking_id="servinj01",
        orig_filename="2014-08-04_KKS_BG-01-18_Leipzig-de.mp3",
        date_val="2014-08-04",
        what_val="BG-01-18",
        place="Leipzig",
    )
    prop = RenameProposal(
        tracking_id="servinj01",
        original_path="/archive/sample.mp3",
        current_filename="sample.mp3",
        proposed_filename="sample_ID-servinj01.mp3",
        proposed_path="/archive/sample_ID-servinj01.mp3",
        mode=RenameMode.INITIAL,
        parser_result=parser_res,
    )
    registry.save_proposal(prop)

    mock_service = MagicMock()
    configure_review_context(registry_path=reg_db, media_db_service=mock_service)
    client = TestClient(app)

    resp = client.post(
        "/file/servinj01/media-db-action",
        data={
            "action": "confirm_new",
            "notes": "Confirmed via injected service",
            "reviewer": "web_operator",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    mock_service.apply_human_decision.assert_called_once_with(
        tracking_id="servinj01",
        action="confirm_new",
        media_row_id=None,
        notes="Confirmed via injected service",
        reviewer="review_portal",
    )
    mock_service.apply_enrichment_to_renamer.assert_not_called()


# ---------------------------------------------------------------------------
# Test 42: Sequential reviews see live Baserow updates (R-001)
# ---------------------------------------------------------------------------
def test_42_sequential_reviews_see_live_baserow_updates(tmp_path):
    """Proves two sequential reviews query live Baserow afresh without cached snapshot reuse."""
    reg_db = tmp_path / "registry.db"
    registry = LocalRegistry(reg_db)

    parser_res = make_parser_result(
        tracking_id="seq01",
        date_val="2014-08-04",
        what_val="BG-01-18",
        place="Leipzig",
    )
    registry.save_proposal(RenameProposal(
        tracking_id="seq01",
        original_path="p1",
        current_filename="f1",
        proposed_filename="f1",
        proposed_path="p1",
        mode=RenameMode.INITIAL,
        parser_result=parser_res,
    ))

    # Dynamic provider simulating collaborator update between decisions
    snap1 = BaserowSnapshot(
        snapshot_at="2026-09-14T10:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=[],
    )
    snap2 = BaserowSnapshot(
        snapshot_at="2026-09-14T10:05:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=[
            {
                "id": 901,
                "Date": "2014-08-04",
                "What": "BG-01-18",
                "Place": "Leipzig",
                "Title": "Live Added Title",
            }
        ],
    )

    call_count = 0

    def dynamic_load_snapshot(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return snap1 if call_count == 1 else snap2

    provider = MagicMock()
    provider.load_snapshot.side_effect = dynamic_load_snapshot

    service = MediaDatabaseReviewService(registry=registry, provider=provider)

    # First review: sees empty table
    r1 = service.review_file("seq01")
    assert r1.decision != ReviewDecision.EXISTING_MEDIA_MATCH
    assert r1.selected_media_row_id is None

    # Second review: sees collaborator update immediately
    r2 = service.review_file("seq01")
    assert r2.decision == ReviewDecision.EXISTING_MEDIA_MATCH
    assert r2.selected_media_row_id == 901
    assert r2.renamer_enrichment.title_full == "Live Added Title"
    assert call_count == 2


# ---------------------------------------------------------------------------
# Test 43: Batch review uses per-decision live query (R-001)
# ---------------------------------------------------------------------------
def test_43_batch_review_uses_per_decision_live_query(tmp_path):
    """Proves batch review performs per-decision queries and does not reuse a batch-wide snapshot."""
    reg_db = tmp_path / "registry.db"
    registry = LocalRegistry(reg_db)

    p1 = make_parser_result(tracking_id="b01", date_val="2014-08-04", what_val="BG-01-18", place="Leipzig")
    p2 = make_parser_result(tracking_id="b02", date_val="2015-05-10", what_val="SB-01-01-01", place="London")
    registry.save_proposal(RenameProposal(
        tracking_id="b01", original_path="p1", current_filename="f1", proposed_filename="f1", proposed_path="p1", mode=RenameMode.INITIAL, parser_result=p1
    ))
    registry.save_proposal(RenameProposal(
        tracking_id="b02", original_path="p2", current_filename="f2", proposed_filename="f2", proposed_path="p2", mode=RenameMode.INITIAL, parser_result=p2
    ))

    queries_received = []

    def mock_load_snapshot(parser_result=None, force_refresh=False):
        queries_received.append(parser_result)
        tid = getattr(getattr(parser_result, "identity", None), "tracking_id", "")
        if tid == "b01":
            return BaserowSnapshot(
                snapshot_at="2026-09-14T10:00:00Z",
                state="LIVE_CURRENT",
                complete=True,
                media_rows=[{"id": 101, "Date": "2014-08-04", "What": "BG-01-18", "Place": "Leipzig", "Title": "Title 101"}],
            )
        else:
            return BaserowSnapshot(
                snapshot_at="2026-09-14T10:01:00Z",
                state="LIVE_CURRENT",
                complete=True,
                media_rows=[{"id": 202, "Date": "2015-05-10", "What": "SB-01-01-01", "Place": "London", "Title": "Title 202"}],
            )

    provider = MagicMock()
    provider.load_snapshot.side_effect = mock_load_snapshot

    service = MediaDatabaseReviewService(registry=registry, provider=provider)
    results = service.review_batch(["b01", "b02"])

    assert len(results) == 2
    assert len(queries_received) == 2
    assert results[0].selected_media_row_id == 101
    assert results[1].selected_media_row_id == 202


# ---------------------------------------------------------------------------
# Test 44: Scripture overlapping ranges conflict and grammar enforced (R-004)
# ---------------------------------------------------------------------------
def test_44_scripture_overlapping_ranges_conflict_and_grammar_enforced():
    """Proves scripture parser rejects malformed/descending ranges and range overlap conflicts."""
    from media_archive_tooling.media_db_reviewer.engine import parse_scripture_reference, _compare_what

    # 1. Grammar enforcement: malformed extra components and descending ranges rejected
    assert parse_scripture_reference("BG 13.8.12") is None
    assert parse_scripture_reference("BG 1.12-8") is None
    assert parse_scripture_reference("SB 1.2.10-5") is None
    assert parse_scripture_reference("CC Adi 1.15-10") is None

    # Valid scripture parsing
    bg1 = parse_scripture_reference("BG 1.1-3")
    bg2 = parse_scripture_reference("BG 1.3-5")
    assert bg1 == {"book": "BG", "canto": None, "chapter": 1, "v_start": 1, "v_end": 3}
    assert bg2 == {"book": "BG", "canto": None, "chapter": 1, "v_start": 3, "v_end": 5}

    # 2. Overlapping ranges without exact identity must CONFLICT
    state, detail = _compare_what("BG 1.1-3", None, "BG 1.3-5", "BG 1.3-5", None)
    assert state == FieldComparisonState.CONFLICT
    assert "partial verse overlap" in (detail or "")

    sb_st, sb_det = _compare_what("SB 1.1.1-3", None, "SB 1.1.2-4", "SB 1.1.2-4", None)
    assert sb_st == FieldComparisonState.CONFLICT
    assert "partial verse overlap" in (sb_det or "")

    # Exact identity AGREES
    eq_st, eq_det = _compare_what("BG 1.1-3", None, "BG 1.1-3", "BG 1.1-3", None)
    assert eq_st == FieldComparisonState.AGREES
    assert eq_det is None

    # 3. Engine reconciliation: overlapping range must not auto-confirm
    engine = MediaDatabaseReconciliationEngine()
    p = make_parser_result(date_val="2014-08-04", what_val="BG-01-01-03", place="Leipzig")
    cand = {"id": 88, "Date": "2014-08-04", "What": "BG 1.3-5", "Place": "Leipzig", "Title": "Lecture"}
    snap = BaserowSnapshot(snapshot_at="2026-09-14T00:00:00Z", state="LIVE_CURRENT", complete=True, media_rows=[cand])
    res = engine.reconcile(p, snap)
    assert res.decision != ReviewDecision.EXISTING_MEDIA_MATCH
    assert res.candidates[0].field_comparisons["what"].state == FieldComparisonState.CONFLICT


# ---------------------------------------------------------------------------
# Test 45: confirm_new fails when live search is unavailable (R-011)
# ---------------------------------------------------------------------------
def test_45_confirm_new_fails_when_live_search_unavailable(tmp_path):
    """Proves confirm_new raises RuntimeError and cannot finalize when live search is unavailable."""
    from media_archive_tooling.media_db_reviewer.baserow_provider import BaserowSnapshotProvider, BaserowUnavailableError

    reg_db = tmp_path / "registry.db"
    registry = LocalRegistry(reg_db)

    p = make_parser_result(tracking_id="newunavail01", date_val="2014-08-04", what_val="BG-01-18", place="Leipzig")
    registry.save_proposal(RenameProposal(
        tracking_id="newunavail01", original_path="p", current_filename="f", proposed_filename="f", proposed_path="p", mode=RenameMode.INITIAL, parser_result=p
    ))

    # Provider with no credentials
    provider = BaserowSnapshotProvider(api_token=None, media_table_id=None)

    # Directly check provider method
    with pytest.raises(BaserowUnavailableError):
        provider.search_media_candidates_live("BG-01-18")

    # Service confirm_new must fail and refuse to confirm
    service = MediaDatabaseReviewService(registry=registry, provider=provider)
    with pytest.raises(RuntimeError) as exc_info:
        service.apply_human_decision("newunavail01", action="confirm_new", reviewer="human_editor")

    assert "live database search is unavailable" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 46: Explicit 404 vs database unavailable distinction (R-011)
# ---------------------------------------------------------------------------
def test_46_explicit_404_vs_database_unavailable_distinction(tmp_path):
    """Proves explicit 404 reports row absence while transport/HTTP 500 reports database unavailable."""
    from media_archive_tooling.media_db_reviewer.baserow_provider import BaserowSnapshotProvider

    reg_db = tmp_path / "registry.db"
    registry = LocalRegistry(reg_db)

    p = make_parser_result(tracking_id="dist01", date_val="2014-08-04", what_val="BG-01-18", place="Leipzig")
    registry.save_proposal(RenameProposal(
        tracking_id="dist01", original_path="p", current_filename="f", proposed_filename="f", proposed_path="p", mode=RenameMode.INITIAL, parser_result=p
    ))

    provider = BaserowSnapshotProvider(api_token="valid_token", media_table_id="123")
    service = MediaDatabaseReviewService(registry=registry, provider=provider)

    # Case 1: Explicit 404 -> Row no longer exists
    mock_resp_404 = MagicMock()
    mock_resp_404.status_code = 404
    with patch("httpx.Client.get", return_value=mock_resp_404):
        assert provider.fetch_media_row_live(999) is None
        with pytest.raises(RuntimeError) as exc404:
            service.apply_human_decision("dist01", action="confirm_existing", media_row_id=999)
        assert "no longer exists in Baserow" in str(exc404.value)

    # Case 2: HTTP 500 error -> Live database is unavailable
    mock_resp_500 = MagicMock()
    mock_resp_500.status_code = 500
    with patch("httpx.Client.get", return_value=mock_resp_500):
        with pytest.raises(RuntimeError) as exc500:
            service.apply_human_decision("dist01", action="confirm_existing", media_row_id=999)
        assert "live database is unavailable" in str(exc500.value)


# ---------------------------------------------------------------------------
# Test 47: Negative regression: Partial-date agreement does not auto-confirm (R-002)
# ---------------------------------------------------------------------------
def test_47_negative_partial_date_agreement_does_not_auto_confirm():
    """Rule 2/3 require exact full date; compatible partial date (e.g. 2015-02-DD) cannot auto-confirm."""
    engine = MediaDatabaseReconciliationEngine()
    parser_res = make_parser_result(
        date_val="2015-02-DD",
        what_val="BG-01-18",
        place="Leipzig",
    )
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-14T00:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=[
            {
                "id": 101,
                "Date": "2015-02-15",
                "What": "BG-01-18",
                "Place": "Leipzig",
            }
        ],
    )
    res = engine.reconcile(parser_res, snapshot)
    assert res.decision == ReviewDecision.PROBABLE_EXISTING_MEDIA
    assert res.decision != ReviewDecision.EXISTING_MEDIA_MATCH
    assert res.renamer_enrichment.confirmed is False


# ---------------------------------------------------------------------------
# Test 48: Negative regression: Fuzzy/substring location does not auto-confirm (R-002)
# ---------------------------------------------------------------------------
def test_48_negative_fuzzy_location_agreement_does_not_auto_confirm():
    """Rule 2 requires exact normalized place; fuzzy/substring match cannot auto-confirm."""
    engine = MediaDatabaseReconciliationEngine()
    parser_res = make_parser_result(
        date_val="2015-02-15",
        what_val="BG-01-18",
        place="New York",
    )
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-14T00:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=[
            {
                "id": 102,
                "Date": "2015-02-15",
                "What": "BG-01-18",
                "Place": "New York City",
            }
        ],
    )
    res = engine.reconcile(parser_res, snapshot)
    assert res.decision == ReviewDecision.PROBABLE_EXISTING_MEDIA
    assert res.decision != ReviewDecision.EXISTING_MEDIA_MATCH
    assert res.renamer_enrichment.confirmed is False


# ---------------------------------------------------------------------------
# Test 49: Negative regression: Generic WHAT does not auto-confirm (R-002)
# ---------------------------------------------------------------------------
def test_49_negative_generic_what_does_not_auto_confirm():
    """Rule 2/3 require a specific WHAT; generic tokens (Lecture, Class, Bhajan) cannot auto-confirm."""
    engine = MediaDatabaseReconciliationEngine()
    for generic_token in ("Lecture", "Class", "Bhajan", "Kirtan", "Morning Lecture"):
        parser_res = make_parser_result(
            date_val="2015-02-15",
            what_val=generic_token,
            place="Leipzig",
        )
        snapshot = BaserowSnapshot(
            snapshot_at="2026-09-14T00:00:00Z",
            state="LIVE_CURRENT",
            complete=True,
            media_rows=[
                {
                    "id": 103,
                    "Date": "2015-02-15",
                    "What": generic_token,
                    "Place": "Leipzig",
                }
            ],
        )
        res = engine.reconcile(parser_res, snapshot)
        assert res.decision == ReviewDecision.PROBABLE_EXISTING_MEDIA, f"Token '{generic_token}' must not auto-confirm"
        assert res.decision != ReviewDecision.EXISTING_MEDIA_MATCH
        assert res.renamer_enrichment.confirmed is False


# ---------------------------------------------------------------------------
# Test 50: Negative regression: Unrelated non-empty title does not corroborate (R-002)
# ---------------------------------------------------------------------------
def test_50_negative_unrelated_non_empty_title_does_not_corroborate():
    """Rule 3 requires genuine corroboration; an unrelated non-empty title does not satisfy Rule 3."""
    engine = MediaDatabaseReconciliationEngine()
    parser_res = make_parser_result(
        date_val="2015-02-15",
        what_val="BG-01-18",
        what_category="Bhagavad-gita",
        place=None,
    )
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-14T00:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=[
            {
                "id": 104,
                "Date": "2015-02-15",
                "What": "BG-01-18",
                "Title": "Morning Breakfast with Guests",
                "Category": None,
            }
        ],
    )
    res = engine.reconcile(parser_res, snapshot)
    assert res.decision == ReviewDecision.PROBABLE_EXISTING_MEDIA
    assert res.decision != ReviewDecision.EXISTING_MEDIA_MATCH
    assert res.renamer_enrichment.confirmed is False


# ---------------------------------------------------------------------------
# Test 51: Positive regression: Genuinely corroborated Rule 3 evidence confirms (R-002)
# ---------------------------------------------------------------------------
def test_51_positive_genuinely_corroborated_rule3_confirms():
    """Rule 3 confirms when exact full date + specific WHAT is genuinely corroborated by title or category."""
    engine = MediaDatabaseReconciliationEngine()

    # Case A: Title mentions scripture/topic
    p1 = make_parser_result(date_val="2015-02-15", what_val="BG-01-18", what_category="Bhagavad-gita", place=None)
    s1 = BaserowSnapshot(
        snapshot_at="2026-09-14T00:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=[
            {
                "id": 105,
                "Date": "2015-02-15",
                "What": "BG-01-18",
                "Title": "Discourse on Bhagavad Gita 1.18",
            }
        ],
    )
    res1 = engine.reconcile(p1, s1)
    assert res1.decision == ReviewDecision.EXISTING_MEDIA_MATCH
    assert res1.selected_media_row_id == 105
    assert res1.renamer_enrichment.confirmed is True

    # Case B: Category genuinely matches
    p2 = make_parser_result(date_val="2015-02-15", what_val="BG-01-18", what_category="Bhagavad-gita", place=None)
    s2 = BaserowSnapshot(
        snapshot_at="2026-09-14T00:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=[
            {
                "id": 106,
                "Date": "2015-02-15",
                "What": "BG-01-18",
                "Category": "Bhagavad-gita",
            }
        ],
    )
    res2 = engine.reconcile(p2, s2)
    assert res2.decision == ReviewDecision.EXISTING_MEDIA_MATCH
    assert res2.selected_media_row_id == 106
    assert res2.renamer_enrichment.confirmed is True


# ---------------------------------------------------------------------------
# Test 52: Pagination: candidate on page 2 of search results is discovered (R-012)
# ---------------------------------------------------------------------------
def test_52_pagination_candidate_on_page_2_is_discovered():
    """Live candidate search must follow next URL and discover rows on page 2+."""
    from media_archive_tooling.media_db_reviewer.baserow_provider import BaserowSnapshotProvider

    provider = BaserowSnapshotProvider(
        api_url="https://api.baserow.io",
        api_token="test_tok",
        media_table_id="111",
    )

    page1_resp = MagicMock()
    page1_resp.status_code = 200
    page1_resp.json.return_value = {
        "count": 2,
        "next": "https://api.baserow.io/api/database/rows/table/111/?page=2",
        "results": [{"id": 1, "Date": "2014-08-04", "What": "Other"}],
    }

    page2_resp = MagicMock()
    page2_resp.status_code = 200
    page2_resp.json.return_value = {
        "count": 2,
        "next": None,
        "results": [{"id": 2, "Date": "2014-08-04", "What": "BG-01-18"}],
    }

    def side_effect(url, **kwargs):
        if "page=2" in url:
            return page2_resp
        return page1_resp

    with patch("httpx.Client.get", side_effect=side_effect):
        results = provider.search_media_candidates_live("BG-01-18")
        assert len(results) == 2
        assert any(r["id"] == 2 for r in results)


# ---------------------------------------------------------------------------
# Test 53: Candidate discoverable by date/place but not WHAT is retrieved (R-012)
# ---------------------------------------------------------------------------
def test_53_candidate_discoverable_by_date_and_place_without_what():
    """Targeted querying must search by place and date so candidates without WHAT are retrieved."""
    from media_archive_tooling.media_db_reviewer.baserow_provider import BaserowSnapshotProvider

    p = make_parser_result(
        date_val="2015-02-15",
        what_val=None,  # Unresolved WHAT
        place="Leipzig",
    )

    provider = BaserowSnapshotProvider(
        api_url="https://api.baserow.io",
        api_token="test_tok",
        media_table_id="111",
    )

    captured_urls = []

    def mock_get(url, **kwargs):
        captured_urls.append(url)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"next": None, "results": []}
        return resp

    with patch("httpx.Client.get", side_effect=mock_get):
        with httpx.Client() as client:
            provider._fetch_targeted_media_rows(client, p)

    # Must have queried both 2015-02-15 and Leipzig
    has_date_query = any("search=2015-02-15" in u for u in captured_urls)
    has_place_query = any("search=Leipzig" in u for u in captured_urls)
    assert has_date_query, f"Expected date search query in {captured_urls}"
    assert has_place_query, f"Expected place search query in {captured_urls}"


# ---------------------------------------------------------------------------
# Test 54: Partial date + place without specific WHAT yields INSUFFICIENT_EVIDENCE (R-012)
# ---------------------------------------------------------------------------
def test_54_partial_date_and_place_without_specific_what_returns_insufficient_evidence():
    """Partial date (e.g. 2015-02-DD) + place without specific WHAT cannot produce NEW_MEDIA_CANDIDATE."""
    engine = MediaDatabaseReconciliationEngine()
    parser_res = make_parser_result(
        date_val="2015-02-DD",
        what_val=None,  # No specific WHAT
        place="Leipzig",
    )
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-14T00:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=[],
    )
    res = engine.reconcile(parser_res, snapshot)
    assert res.decision == ReviewDecision.INSUFFICIENT_EVIDENCE
    assert res.decision != ReviewDecision.NEW_MEDIA_CANDIDATE


# ---------------------------------------------------------------------------
# Test 55: Missing media_table_id yields DATABASE_UNAVAILABLE (R-012)
# ---------------------------------------------------------------------------
def test_55_missing_media_table_id_yields_database_unavailable(tmp_path):
    """Missing media_table_id must yield DATABASE_UNAVAILABLE and reject confirm_new."""
    from media_archive_tooling.media_db_reviewer.baserow_provider import BaserowSnapshotProvider

    reg_db = tmp_path / "registry.db"
    registry = LocalRegistry(reg_db)

    p = make_parser_result(tracking_id="notable01", date_val="2015-02-15", what_val="BG-01-18", place="Leipzig")
    registry.save_proposal(RenameProposal(
        tracking_id="notable01", original_path="p", current_filename="f", proposed_filename="f", proposed_path="p", mode=RenameMode.INITIAL, parser_result=p
    ))

    # Media table ID is missing, only category table ID configured
    provider = BaserowSnapshotProvider(
        api_token="valid_token",
        media_table_id=None,
        category_table_id="cat_123",
    )

    snap = provider.load_snapshot()
    assert snap.state == "DATABASE_UNAVAILABLE"
    assert snap.complete is False

    service = MediaDatabaseReviewService(registry=registry, provider=provider)
    rev_res = service.review_file("notable01")
    assert rev_res.decision == ReviewDecision.DATABASE_UNAVAILABLE
    assert rev_res.baserow_check_complete is False

    with pytest.raises(RuntimeError) as exc_info:
        service.confirm_new("notable01")
    assert "live database search is unavailable" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 56: Confirmed match automatically enriches Renamer proposal (R-013)
# ---------------------------------------------------------------------------
def test_56_confirmed_match_automatically_enriches_renamer_proposal(tmp_path):
    """Tool 1 initial proposal -> Tool 2 live confirmed existing row -> automatic Renamer proposal contains title and baserow_check_complete=True."""
    reg_db = tmp_path / "registry.db"
    registry = LocalRegistry(reg_db)

    # Tool 1 creates initial proposal
    p = make_parser_result(
        tracking_id="enr01",
        date_val="2014-08-04",
        what_val="BG-01-18",
        place="Leipzig",
        country="de",
    )
    initial_proposal = RenameProposal(
        tracking_id="enr01",
        original_path="sample-files/audio/2014-08-04_BG-01-18_Leipzig.mp3",
        current_filename="2014-08-04_BG-01-18_Leipzig.mp3",
        proposed_filename="2014-08-04_KKS_BG-01-18_Leipzig-de_ID-enr01.mp3",
        proposed_path="sample-files/audio/2014-08-04_KKS_BG-01-18_Leipzig-de_ID-enr01.mp3",
        mode=RenameMode.INITIAL,
        parser_result=p,
    )
    registry.save_proposal(initial_proposal)

    # Baserow has matching row with confirmed title
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-14T10:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=[
            {
                "id": 501,
                "Date": "2014-08-04",
                "What": "BG-01-18",
                "Place": "Leipzig",
                "Country": "Germany",
                "Title": "Conquering the Mind",
            }
        ],
    )
    provider = MagicMock()
    provider.load_snapshot.return_value = snapshot

    service = MediaDatabaseReviewService(registry=registry, provider=provider)
    result = service.review_file("enr01")

    assert result.decision == ReviewDecision.EXISTING_MEDIA_MATCH
    assert result.selected_media_row_id == 501
    assert result.renamer_enrichment.confirmed is True
    assert result.renamer_enrichment.title_full == "Conquering the Mind"

    # Check updated Tool 1 registry record
    rec = registry.get_file("enr01")
    assert rec is not None
    assert rec["status"] == "enriched"
    assert rec["proposed_filename"] == "2014-08-04_KKS_BG-01-18-Conquering-the-Mind_Leipzig-de_ID-enr01.mp3"
    assert rec["what_val"] == "BG-01-18-Conquering-the-Mind"
    assert rec["parser_result"]["file_metadata"]["baserow_check_complete"] is True


# ---------------------------------------------------------------------------
# Test 57: Unconfirmed states do not alter Tool 1 proposal (R-013)
# ---------------------------------------------------------------------------
def test_57_unconfirmed_states_do_not_alter_tool_1_proposal(tmp_path):
    """Probable, multiple, conflicting, insufficient, and unavailable states do NOT copy candidate metadata into proposed filename."""
    reg_db = tmp_path / "registry.db"
    registry = LocalRegistry(reg_db)

    cases = [
        ("prob01", "2014-08-04", "BG-01-18", None, [{"id": 601, "Date": "2014-08-04", "What": "BG-01-18", "Title": "Probable Candidate"}], ReviewDecision.PROBABLE_EXISTING_MEDIA),
        ("mult01", "2014-08-04", "BG-01-18", "Leipzig", [
            {"id": 602, "Date": "2014-08-04", "What": "BG-01-18", "Place": "Leipzig", "Country": "Germany", "Title": "Candidate One"},
            {"id": 603, "Date": "2014-08-04", "What": "BG-01-18", "Place": "Leipzig", "Country": "Germany", "Title": "Candidate Two"},
        ], ReviewDecision.MULTIPLE_CANDIDATES),
        ("conf01", "2014-08-04", "BG-01-18", "Leipzig", [{"id": 604, "Date": "2014-08-04", "What": "BG-01-18", "Place": "Moscow", "Country": "Russia", "Title": "Conflict Candidate"}], ReviewDecision.CONFLICT_WITH_EXISTING),
        ("insuff01", "2014-08-DD", "Lecture", None, [], ReviewDecision.INSUFFICIENT_EVIDENCE),
    ]

    for tid, dt, wt, pl, rows, expected_dec in cases:
        p = make_parser_result(tracking_id=tid, date_val=dt, what_val=wt, place=pl)
        initial_name = f"initial_{tid}.mp3"
        registry.save_proposal(RenameProposal(
            tracking_id=tid, original_path=f"p/{initial_name}", current_filename=initial_name,
            proposed_filename=initial_name, proposed_path=f"p/{initial_name}", mode=RenameMode.INITIAL, parser_result=p,
        ))

        snapshot = BaserowSnapshot(
            snapshot_at="2026-09-14T10:00:00Z",
            state="LIVE_CURRENT",
            complete=True,
            media_rows=rows,
        )
        provider = MagicMock()
        provider.load_snapshot.return_value = snapshot

        service = MediaDatabaseReviewService(registry=registry, provider=provider)
        res = service.review_file(tid)

        assert res.decision == expected_dec
        assert res.renamer_enrichment.confirmed is False

        # Proposal in registry must NOT be enriched or modified with candidate titles
        rec = registry.get_file(tid)
        assert rec["proposed_filename"] == initial_name
        assert rec["status"] == "pending"
        for row in rows:
            if "Title" in row and row["Title"]:
                assert row["Title"] not in rec["proposed_filename"]
                assert row["Title"] not in (rec["what_val"] or "")


# ---------------------------------------------------------------------------
# Test 58: Completed live no-match marks Baserow check complete without invented metadata (R-013)
# ---------------------------------------------------------------------------
def test_58_completed_live_no_match_marks_baserow_check_complete_without_invented_metadata(tmp_path):
    """Completed live no-match marks baserow_check_complete=True and clears _edited suffix without inventing title/location."""
    reg_db = tmp_path / "registry.db"
    registry = LocalRegistry(reg_db)

    # Initial proposal with edited=True, producing an _edited suffix
    p = make_parser_result(
        tracking_id="nomatch01",
        date_val="2015-02-15",
        what_val="BG-01-18",
        place="Leipzig",
        country="de",
    )
    p.file_metadata.edited = True
    initial_prop = RenameProposal(
        tracking_id="nomatch01",
        original_path="audio/file.mp3",
        current_filename="file.mp3",
        proposed_filename="2015-02-15_KKS_BG-01-18_Leipzig-de_edited_ID-nomatch01.mp3",
        proposed_path="audio/2015-02-15_KKS_BG-01-18_Leipzig-de_edited_ID-nomatch01.mp3",
        mode=RenameMode.INITIAL,
        parser_result=p,
    )
    registry.save_proposal(initial_prop)

    # Empty complete live snapshot
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-14T10:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=[],
    )
    provider = MagicMock()
    provider.load_snapshot.return_value = snapshot

    service = MediaDatabaseReviewService(registry=registry, provider=provider)
    result = service.review_file("nomatch01")

    assert result.decision == ReviewDecision.NEW_MEDIA_CANDIDATE
    assert result.baserow_check_complete is True
    assert result.renamer_enrichment.confirmed is False

    # Check updated Tool 1 registry record
    rec = registry.get_file("nomatch01")
    assert rec["status"] == "enriched"
    # Baserow check complete clears _edited suffix per Tool 1 lifecycle
    assert rec["proposed_filename"] == "2015-02-15_KKS_BG-01-18_Leipzig-de_ID-nomatch01.mp3"
    assert "_edited" not in rec["proposed_filename"]
    # No invented title
    assert rec["what_val"] == "BG-01-18"
    assert rec["parser_result"]["file_metadata"]["baserow_check_complete"] is True


# ---------------------------------------------------------------------------
# Test 59: Supported CLI/batch path exercises the bridge (R-013)
# ---------------------------------------------------------------------------
def test_59_cli_and_batch_review_exercises_enrichment_bridge(tmp_path):
    """The supported CLI run_media_db_review command and batch review automatically trigger the enrichment bridge."""
    from media_archive_tooling.cli import run_media_db_review
    import argparse

    reg_db = tmp_path / "registry.db"
    registry = LocalRegistry(reg_db)

    # Create 2 proposals: 1 confirmed match, 1 new media candidate
    p1 = make_parser_result(tracking_id="cli01", date_val="2014-08-04", what_val="BG-01-18", place="Leipzig", country="de")
    registry.save_proposal(RenameProposal(
        tracking_id="cli01", original_path="p1", current_filename="f1",
        proposed_filename="2014-08-04_KKS_BG-01-18_Leipzig-de_ID-cli01.mp3", proposed_path="p1",
        mode=RenameMode.INITIAL, parser_result=p1,
    ))

    p2 = make_parser_result(tracking_id="cli02", date_val="2015-02-15", what_val="SB-01-01-01", place="Vrindavan", country="in")
    registry.save_proposal(RenameProposal(
        tracking_id="cli02", original_path="p2", current_filename="f2",
        proposed_filename="2015-02-15_KKS_SB-01-01-01_Vrindavan-in_ID-cli02.mp3", proposed_path="p2",
        mode=RenameMode.INITIAL, parser_result=p2,
    ))

    # Mock Baserow provider in CLI execution
    rows = [
        {"id": 701, "Date": "2014-08-04", "What": "BG-01-18", "Place": "Leipzig", "Country": "Germany", "Title": "CLI Bridge Enriched Title"}
    ]
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-14T10:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=rows,
    )

    with patch("media_archive_tooling.media_db_reviewer.baserow_provider.BaserowSnapshotProvider.load_snapshot", return_value=snapshot):
        args = argparse.Namespace(
            registry_path=str(reg_db),
            snapshot_path=None,
            refresh_snapshot=False,
            auto_enrich=True,
            tracking_id=None,
            json=False,
        )
        run_media_db_review(args)

    rec1 = registry.get_file("cli01")
    assert rec1["status"] == "enriched"
    assert "CLI-Bridge-Enriched-Title" in rec1["proposed_filename"]
    assert rec1["parser_result"]["file_metadata"]["baserow_check_complete"] is True

    rec2 = registry.get_file("cli02")
    assert rec2["status"] == "enriched"
    assert rec2["parser_result"]["file_metadata"]["baserow_check_complete"] is True


# ---------------------------------------------------------------------------
# Test 60: Rerunning review is idempotent and does not duplicate title (R-013)
# ---------------------------------------------------------------------------
def test_60_rerunning_review_is_idempotent_and_does_not_duplicate_title(tmp_path):
    """Rerunning review/enrichment multiple times is idempotent and never duplicates title or scripture text."""
    reg_db = tmp_path / "registry.db"
    registry = LocalRegistry(reg_db)

    # 1. Standard scripture + title case
    p = make_parser_result(tracking_id="idem01", date_val="2014-08-04", what_val="BG-01-18", place="Leipzig", country="de")
    registry.save_proposal(RenameProposal(
        tracking_id="idem01", original_path="p", current_filename="f",
        proposed_filename="2014-08-04_KKS_BG-01-18_Leipzig-de_ID-idem01.mp3", proposed_path="p",
        mode=RenameMode.INITIAL, parser_result=p,
    ))

    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-14T10:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=[{"id": 801, "Date": "2014-08-04", "What": "BG-01-18", "Place": "Leipzig", "Country": "Germany", "Title": "The Great Armies"}],
    )
    provider = MagicMock()
    provider.load_snapshot.return_value = snapshot

    service = MediaDatabaseReviewService(registry=registry, provider=provider)

    # Run 1
    service.review_file("idem01")
    rec1 = registry.get_file("idem01")
    expected_filename = "2014-08-04_KKS_BG-01-18-The-Great-Armies_Leipzig-de_ID-idem01.mp3"
    assert rec1["proposed_filename"] == expected_filename
    assert rec1["what_val"] == "BG-01-18-The-Great-Armies"

    # Run 2
    service.review_file("idem01")
    rec2 = registry.get_file("idem01")
    assert rec2["proposed_filename"] == expected_filename
    assert rec2["what_val"] == "BG-01-18-The-Great-Armies"
    assert "The-Great-Armies-The-Great-Armies" not in rec2["proposed_filename"]

    # Run 3
    service.review_file("idem01")
    rec3 = registry.get_file("idem01")
    assert rec3["proposed_filename"] == expected_filename
    assert rec3["what_val"] == "BG-01-18-The-Great-Armies"

    # 2. Long title subject to 128-char budget compaction
    long_title = "Observations of All Great Kings and Warriors Gathered on the Holy Field of Kuruksetra Before the Great War"
    p_long = make_parser_result(tracking_id="idem02", date_val="2014-08-04", what_val="BG-01-18", place="Leipzig-very-long-location-name", country="de")
    registry.save_proposal(RenameProposal(
        tracking_id="idem02", original_path="p2", current_filename="f2",
        proposed_filename="2014-08-04_KKS_BG-01-18_Leipzig-very-long-location-name-de_ID-idem02.mp3", proposed_path="p2",
        mode=RenameMode.INITIAL, parser_result=p_long,
    ))
    snapshot_long = BaserowSnapshot(
        snapshot_at="2026-09-14T10:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=[{"id": 802, "Date": "2014-08-04", "What": "BG-01-18", "Place": "Leipzig-very-long-location-name", "Country": "Germany", "Title": long_title}],
    )
    provider_long = MagicMock()
    provider_long.load_snapshot.return_value = snapshot_long
    service_long = MediaDatabaseReviewService(registry=registry, provider=provider_long)

    # Run 1 with long title
    service_long.review_file("idem02")
    rec_long_1 = registry.get_file("idem02")
    fname_long_1 = rec_long_1["proposed_filename"]
    assert len(fname_long_1) <= 128

    # Run 2 with long title
    service_long.review_file("idem02")
    rec_long_2 = registry.get_file("idem02")
    assert rec_long_2["proposed_filename"] == fname_long_1
    assert len(rec_long_2["proposed_filename"]) <= 128


# ---------------------------------------------------------------------------
# Test 61: Portal confirmation produces single enrichment audit event (R-014)
# ---------------------------------------------------------------------------
def test_61_r014_portal_confirmation_produces_single_enrichment_audit_event(tmp_path):
    """Portal confirmation route invokes apply_human_decision as single owner and records exactly one enrich audit action."""
    from fastapi.testclient import TestClient
    from media_archive_tooling.review_portal.app import app, configure_review_context

    reg_db = tmp_path / "registry.db"
    registry = LocalRegistry(reg_db)

    p = make_parser_result(tracking_id="portalevt01", orig_filename="2014-08-04_KKS_BG-01-18_Leipzig-de.mp3", date_val="2014-08-04", what_val="BG-01-18", place="Leipzig", country="de")
    registry.save_proposal(RenameProposal(
        tracking_id="portalevt01",
        original_path="path/orig.mp3",
        current_filename="2014-08-04_KKS_BG-01-18_Leipzig-de.mp3",
        proposed_filename="2014-08-04_KKS_BG-01-18_Leipzig-de_ID-portalevt01.mp3",
        proposed_path="path/2014-08-04_KKS_BG-01-18_Leipzig-de_ID-portalevt01.mp3",
        mode=RenameMode.INITIAL,
        parser_result=p,
    ))

    # Initial probable review
    cand_row = {"id": 901, "date": "2014-08-04", "what": "BG-01-18", "place": "Leipzig", "country": "Germany", "title": "Divine Heritage"}
    rev_res = MediaDatabaseReviewResult(
        tracking_id="portalevt01",
        decision=ReviewDecision.PROBABLE_EXISTING_MEDIA,
        decision_state="Probable candidate found",
        review_required=True,
        review_required_now=False,
        selected_field_evidence={"local_date": "2014-08-04", "local_what": "BG-01-18", "local_place": "Leipzig", "local_country": "de"},
        candidates=[MediaCandidate(media_row_id=901, score=85.0, normalized_row=cand_row, retrieval_reasons=["matching date"])],
        proposed_tool4_action=Tool4Action.NEEDS_REVIEW,
        database_state="LIVE_CURRENT",
        baserow_read_at="2026-09-14T10:00:00Z",
        database_snapshot_at="2026-09-14T10:00:00Z",
        live_read_complete=True,
    )
    registry.save_media_db_review(
        tracking_id="portalevt01",
        decision=rev_res.decision.value,
        database_state="LIVE_CURRENT",
        snapshot_timestamp=rev_res.database_snapshot_at,
        result_json=rev_res.model_dump_json(),
        selected_media_row_id=None,
        review_required=True,
    )

    fake_provider = BaserowSnapshotProvider(
        api_url="https://api.baserow.io",
        api_token="test_mock_token",
        media_table_id="100",
        snapshot_path=tmp_path / "mock_snapshot.json",
    )
    configure_review_context(registry_path=reg_db, media_db_provider=fake_provider)
    client = TestClient(app)

    with patch("httpx.Client.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": 901,
            "Title": "Divine Heritage",
            "Date": "2014-08-04",
            "Place": "Leipzig",
            "Country": "Germany",
            "What": "BG-01-18",
        }
        mock_get.return_value = mock_resp

        resp = client.post(
            "/file/portalevt01/media-db-action",
            data={
                "action": "confirm_existing",
                "media_row_id": "901",
                "notes": "Confirmed via single-owner portal",
                "reviewer": "web_operator",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    actions = registry.get_review_actions("portalevt01")
    enrich_actions = [a for a in actions if a["action"] == "enrich"]
    portal_actions = [a for a in actions if a["action"] == "media_db_confirm_existing"]

    # Exactly ONE enrich action and ONE portal decision action
    assert len(enrich_actions) == 1, f"Expected exactly 1 enrich audit action, got {len(enrich_actions)}: {enrich_actions}"
    assert len(portal_actions) == 1
    assert enrich_actions[0]["reviewer"] == "tool_2_media_database_review"
    assert portal_actions[0]["reviewer"] == "review_portal"

    # Enriched proposal
    rec = registry.get_file("portalevt01")
    assert rec["status"] == "enriched"
    assert "Divine-Heritage" in rec["proposed_filename"]


# ---------------------------------------------------------------------------
# Test 62: Defer on confirmed association is rejected as contradictory (R-014)
# ---------------------------------------------------------------------------
def test_62_r014_defer_on_confirmed_association_is_rejected_as_contradictory(tmp_path):
    """Attempting to defer a confirmed existing media match raises ValueError and portal returns 400 without modifying proposal."""
    from fastapi.testclient import TestClient
    from media_archive_tooling.review_portal.app import app, configure_review_context

    reg_db = tmp_path / "registry.db"
    registry = LocalRegistry(reg_db)

    p = make_parser_result(tracking_id="defconf01", date_val="2014-08-04", what_val="BG-01-18", place="Leipzig", country="de")
    registry.save_proposal(RenameProposal(
        tracking_id="defconf01",
        original_path="p",
        current_filename="f.mp3",
        proposed_filename="2014-08-04_KKS_BG-01-18_Leipzig-de_ID-defconf01.mp3",
        proposed_path="p",
        mode=RenameMode.INITIAL,
        parser_result=p,
    ))

    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-14T10:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=[{"id": 902, "Date": "2014-08-04", "What": "BG-01-18", "Place": "Leipzig", "Country": "Germany", "Title": "Sacred Assembly"}],
    )
    provider = MagicMock()
    provider.load_snapshot.return_value = snapshot
    provider.fetch_media_row_live.return_value = {"id": 902, "date": "2014-08-04", "what": "BG-01-18", "place": "Leipzig", "country": "Germany", "title": "Sacred Assembly"}

    service = MediaDatabaseReviewService(registry=registry, provider=provider)

    # 1. Review confirms match and applies enrichment
    service.review_file("defconf01")
    rec_before = registry.get_file("defconf01")
    assert rec_before["status"] == "enriched"
    assert "Sacred-Assembly" in rec_before["proposed_filename"]
    actions_before = registry.get_review_actions("defconf01")
    assert any(a["action"] == "enrich" for a in actions_before)

    # 2. Service-level defer attempt on confirmed association must raise ValueError
    with pytest.raises(ValueError, match="Cannot defer tracking ID 'defconf01': media association is already confirmed"):
        service.apply_human_decision("defconf01", action="defer", reviewer="tester")

    # 3. Verify registry proposal and actions are completely unchanged
    rec_after = registry.get_file("defconf01")
    assert rec_after["status"] == "enriched"
    assert rec_after["proposed_filename"] == rec_before["proposed_filename"]
    assert len(registry.get_review_actions("defconf01")) == len(actions_before)

    # 4. Portal-level defer attempt returns HTTP 400
    configure_review_context(registry_path=reg_db, media_db_provider=provider)
    client = TestClient(app)
    resp = client.post(
        "/file/defconf01/media-db-action",
        data={"action": "defer", "notes": "Try deferring confirmed"},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "Cannot defer tracking ID 'defconf01'" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Test 63: Defer on unconfirmed stored review resets enrichment state (R-014)
# ---------------------------------------------------------------------------
def test_63_r014_defer_on_unconfirmed_stored_review_resets_enrichment_state(tmp_path):
    """Deferring an unconfirmed candidate review sets INSUFFICIENT_EVIDENCE, clears candidate selection, and never triggers Renamer enrichment."""
    reg_db = tmp_path / "registry.db"
    registry = LocalRegistry(reg_db)

    p = make_parser_result(
        tracking_id="defunconf01",
        orig_filename="2014-08-04_KKS_Class_Leipzig.mp3",
        date_val="2014-08-04",
        what_val="Class",
        place="Leipzig",
        country="de",
    )
    p.review_reasons = ["WHAT is unresolved"]
    orig_proposed = "2014-08-04_KKS_Class_Leipzig-de_ID-defunconf01.mp3"
    registry.save_proposal(RenameProposal(
        tracking_id="defunconf01",
        original_path="p",
        current_filename="2014-08-04_KKS_Class_Leipzig.mp3",
        proposed_filename=orig_proposed,
        proposed_path="p",
        mode=RenameMode.INITIAL,
        parser_result=p,
        needs_review=True,
    ))

    # Stored review with probable candidate and candidate metadata
    cand_row = {"id": 903, "date": "2014-08-04", "what": "Class", "place": "Leipzig", "country": "Germany", "title": "Stale Title To Avoid"}
    stored_res = MediaDatabaseReviewResult(
        tracking_id="defunconf01",
        decision=ReviewDecision.PROBABLE_EXISTING_MEDIA,
        decision_state="Probable match found",
        review_required=True,
        review_required_now=False,
        selected_field_evidence={"local_date": "2014-08-04", "local_what": "Class", "local_place": "Leipzig", "local_country": "de"},
        selected_media_row_id=903,
        candidates=[MediaCandidate(media_row_id=903, score=70.0, normalized_row=cand_row, retrieval_reasons=["matching date"])],
        proposed_tool4_action=Tool4Action.NEEDS_REVIEW,
        database_state="LIVE_CURRENT",
        baserow_read_at="2026-09-14T10:00:00Z",
        database_snapshot_at="2026-09-14T10:00:00Z",
        live_read_complete=True,
        renamer_enrichment=RenamerEnrichment(
            confirmed=False,
            media_row_id=903,
            what_val="Class-Stale-Title-To-Avoid",
            title_full="Stale Title To Avoid",
            evidence=["stored_candidate:903"],
        ),
    )
    registry.save_media_db_review(
        tracking_id="defunconf01",
        decision=stored_res.decision.value,
        database_state="LIVE_CURRENT",
        snapshot_timestamp=stored_res.database_snapshot_at,
        result_json=stored_res.model_dump_json(),
        selected_media_row_id=903,
        review_required=True,
    )

    provider = MagicMock()
    service = MediaDatabaseReviewService(registry=registry, provider=provider)

    # Defer decision
    result = service.apply_human_decision("defunconf01", action="defer", notes="Needs more research", reviewer="operator_jane")

    # Verify review result state
    assert result.decision == ReviewDecision.INSUFFICIENT_EVIDENCE
    assert result.selected_media_row_id is None
    assert result.baserow_check_complete is False
    assert result.renamer_enrichment.confirmed is False
    assert result.renamer_enrichment.what_val is None
    assert result.renamer_enrichment.title_full is None
    assert "deferred_by_operator_jane:defunconf01" in result.renamer_enrichment.evidence

    # Verify stored review in registry
    stored_after = registry.get_media_db_review("defunconf01")
    assert stored_after["decision"] == ReviewDecision.INSUFFICIENT_EVIDENCE.value
    assert stored_after["selected_media_row_id"] is None
    res_after = stored_after["result"]
    assert res_after["renamer_enrichment"]["confirmed"] is False
    assert res_after["baserow_check_complete"] is False

    # Verify Renamer proposal remained untouched (no enrichment, no stale title)
    rec = registry.get_file("defunconf01")
    assert rec["status"] == "pending"
    assert rec["needs_review"] == 1 or rec["needs_review"] is True
    assert rec["proposed_filename"] == orig_proposed
    assert "Stale" not in rec["proposed_filename"]

    # Verify review actions: only media_db_defer, exactly zero enrich actions
    actions = registry.get_review_actions("defunconf01")
    assert len(actions) == 1
    assert actions[0]["action"] == "media_db_defer"
    assert not any(a["action"] == "enrich" for a in actions)

    # Verify apply_enrichment_to_renamer returns None (defense-in-depth)
    assert service.apply_enrichment_to_renamer("defunconf01") is None


def test_adjacent_daily_verse_is_related_series_not_conflicting_duplicate():
    engine = MediaDatabaseReconciliationEngine()
    parser_result = make_parser_result(
        orig_filename="KKS_S.B. 1.19.30_28.8.11_Oslo_ .WMA",
        date_val="2011-08-28",
        what_val="SB-1-19-30",
        what_category="Srimad Bhagavatam",
        place="Oslo",
        country="NO",
    )
    snapshot = BaserowSnapshot(
        snapshot_at="2026-09-19T00:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=[{
            "id": 3231,
            "Date": "2011-08-29",
            "Place": "Oslo",
            "Country": "Norway",
            "Title": "SB 1.19.31",
            "Category": "Srimad-bhagavatam",
        }],
    )

    result = engine.reconcile(parser_result, snapshot)

    assert result.decision == ReviewDecision.NEW_MEDIA_CANDIDATE
    assert result.proposed_tool4_action == Tool4Action.CREATE_NEW
    assert result.review_required is False
    assert result.candidates == []
    assert result.selected_field_evidence["related_series"] == [{
        "media_row_id": 3231,
        "date": "2011-08-29",
        "what": "",
        "title": "SB 1.19.31",
        "place": "Oslo",
        "country": "Norway",
        "relationship": "next_class",
    }]
    assert "related_series_row:3231" in result.evidence


# ---------------------------------------------------------------------------
# Test 64: Regression R-016: Generic WHAT (e.g. Class) excluded from Baserow duplicate matching
# ---------------------------------------------------------------------------
def test_64_r016_generic_what_class_does_not_create_baserow_candidates_nor_multiple_candidates():
    """Models the 94-row class-only scenario for tracking ID 26f17dfa (05 SOKENDA LEKCE STEREO JET.mp3).

    1. Incompatible date/place rows must not be retained as Baserow candidates merely because both sides say 'Class'.
    2. The result must not be MULTIPLE_CANDIDATES.
    3. Generic local WHAT returns NOT_COMPARABLE with diagnostic note and never shows as agreeing.
    4. Makes no automatic Baserow mutation (tool4_action == NO_WRITE, renamer_enrichment unconfirmed).
    5. Tool 3 remains INSUFFICIENT_EVIDENCE when travel schedule context is sparse.
    6. Targeted querying does not search Baserow for generic 'Class'.
    7. Specific WHAT matching still works.
    """
    from media_archive_tooling.media_db_reviewer.engine import _compare_what, is_specific_what
    from media_archive_tooling.media_db_reviewer.baserow_provider import BaserowSnapshotProvider
    from media_archive_tooling.travel_reviewer.engine import TravelScheduleEngine
    from media_archive_tooling.travel_reviewer.models import TravelScheduleManifest, TravelReviewDecision

    # 1. Verify is_specific_what classification
    assert is_specific_what("Class") is False
    assert is_specific_what("LEKCE") is False
    assert is_specific_what("SB 3.6.6") is True

    # 2. Verify _compare_what returns NOT_COMPARABLE for generic local WHAT
    w_st, w_det = _compare_what("Class", "Class", "Class", "Daily Class", "Class")
    assert w_st == FieldComparisonState.NOT_COMPARABLE
    assert "Generic local WHAT 'Class' excluded from duplicate matching" in (w_det or "")

    # 3. Model the 94-row class-only scenario: 94 Baserow rows with category/title "Class" and mismatched dates/places
    engine = MediaDatabaseReconciliationEngine()
    parser_res = make_parser_result(
        tracking_id="26f17dfa",
        orig_filename="05 SOKENDA LEKCE STEREO JET.mp3",
        date_val="2008-04-DD",
        what_val="Class",
        what_category="Class",
        place=None,
        country="cz",
    )

    rows_94 = []
    countries = ["Sweden", "Germany", "United Kingdom", "India", "Netherlands", "Poland"]
    for i in range(1, 95):
        rows_94.append({
            "id": 1000 + i,
            "Date": f"2015-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
            "Place": f"City_{i}",
            "Country": countries[i % len(countries)],
            "What": "Class" if i % 2 == 0 else "",
            "Title": f"Class {i}" if i % 2 == 1 else "Morning Class",
            "Category": "Class",
        })

    snapshot_94 = BaserowSnapshot(
        snapshot_at="2026-09-22T00:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=rows_94,
    )

    res = engine.reconcile(parser_res, snapshot_94)

    # Incompatible date/place rows must NOT become candidates
    assert len(res.candidates) == 0, f"Expected 0 candidates, got {len(res.candidates)}"
    assert res.decision != ReviewDecision.MULTIPLE_CANDIDATES
    assert res.decision == ReviewDecision.INSUFFICIENT_EVIDENCE
    assert res.proposed_tool4_action == Tool4Action.NO_WRITE
    assert res.renamer_enrichment.confirmed is False

    # Also test full date 2008-04-05 without place: remains INSUFFICIENT_EVIDENCE, 0 candidates
    parser_res_fulldt = make_parser_result(
        tracking_id="26f17dfa",
        orig_filename="05 SOKENDA LEKCE STEREO JET.mp3",
        date_val="2008-04-05",
        what_val="Class",
        what_category="Class",
        place=None,
        country="cz",
    )
    res_fulldt = engine.reconcile(parser_res_fulldt, snapshot_94)
    assert len(res_fulldt.candidates) == 0
    assert res_fulldt.decision != ReviewDecision.MULTIPLE_CANDIDATES
    assert res_fulldt.decision == ReviewDecision.INSUFFICIENT_EVIDENCE
    assert res_fulldt.proposed_tool4_action == Tool4Action.NO_WRITE

    # 4. Verify targeted Baserow querying does NOT query for generic "Class"
    captured_urls = []

    def mock_get(url, **kwargs):
        captured_urls.append(url)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"next": None, "results": []}
        return resp

    provider = BaserowSnapshotProvider(
        api_url="https://api.baserow.io",
        api_token="test_tok",
        media_table_id="111",
    )
    with patch("httpx.Client.get", side_effect=mock_get):
        with httpx.Client() as client:
            provider._fetch_targeted_media_rows(client, parser_res)

    assert not any("search=Class" in u or "search=class" in u for u in captured_urls), (
        f"Generic WHAT 'Class' must not be queried in Baserow: {captured_urls}"
    )

    # 5. Verify Tool 3 remains INSUFFICIENT_EVIDENCE for this sparse file
    manifest = TravelScheduleManifest(
        format_version="1.0",
        source_table_id="123",
        retrieved_at="2026-09-14T00:00:00Z",
        complete=True,
        row_count=0,
        canonical_sha256="",
        normalized_rows=[],
    )
    travel_engine = TravelScheduleEngine(manifest)
    travel_res = travel_engine.evaluate(parser_res, tool2_context=res)
    assert travel_res.decision == TravelReviewDecision.INSUFFICIENT_EVIDENCE
    assert len(travel_res.candidates) == 0

    # 6. Verify specific WHAT matching still works
    specific_parser_res = make_parser_result(
        tracking_id="spec01",
        orig_filename="2015-08-27_SB-3-6-6_Sweden.mp3",
        date_val="2015-08-27",
        what_val="SB 3.6.6",
        what_category="Srimad Bhagavatam",
        place="Sweden",
        country="se",
    )
    specific_snapshot = BaserowSnapshot(
        snapshot_at="2026-09-22T00:00:00Z",
        state="LIVE_CURRENT",
        complete=True,
        media_rows=[{
            "id": 2335,
            "Date": "2015-08-27",
            "Place": "Sweden",
            "Country": "Sweden",
            "What": "SB 3.6.6",
            "Title": "SB 3.6.6 class",
            "Category": "Srimad Bhagavatam",
        }],
    )
    spec_res = engine.reconcile(specific_parser_res, specific_snapshot)
    assert spec_res.decision == ReviewDecision.EXISTING_MEDIA_MATCH
    assert spec_res.selected_media_row_id == 2335
    assert spec_res.renamer_enrichment.confirmed is True
    assert len(spec_res.candidates) == 1
    assert spec_res.candidates[0].field_comparisons["what"].state == FieldComparisonState.AGREES
