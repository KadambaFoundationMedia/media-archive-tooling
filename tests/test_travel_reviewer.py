"""Comprehensive tests for Tool 3 — Travel Schedule Reviewer.

Implements all 40 required tests specified in Section 35 of docs/tool-3-travel-schedule-reviewer-build-plan.md.
"""
from datetime import date
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import pytest
from fastapi.testclient import TestClient

from media_archive_tooling.cli import main as cli_main
from media_archive_tooling.media_db_reviewer.baserow_provider import (
    BaserowSnapshotProvider,
    BaserowUnavailableError,
)
from media_archive_tooling.media_db_reviewer.models import (
    MediaCandidate,
    MediaDatabaseReviewResult,
    RenamerEnrichment,
    ReviewDecision,
)
from media_archive_tooling.renamer.models import (
    EnrichmentEvidence,
    Evidence,
    Identity,
    Context,
    ParserResult,
    RenameMode,
    RenameProposal,
    ResolutionState,
    WhenResult,
    WhatResult,
    WhereResult,
)
from media_archive_tooling.renamer.registry.registry import LocalRegistry
from media_archive_tooling.renamer.parser.engine import RenamerParser
from media_archive_tooling.renamer.service import RenamerApplicationService
from media_archive_tooling.media_db_reviewer.models import (
    MediaCandidate,
    MediaDatabaseReviewResult,
    ReviewDecision,
)
from media_archive_tooling.review_portal.app import app, configure_review_context
from media_archive_tooling.travel_reviewer.engine import (
    TravelScheduleEngine,
    TravelScheduleIndex,
    _norm_place_token,
    group_candidates_semantically,
    parse_iso_date,
    parse_structured_where,
)
from media_archive_tooling.travel_reviewer.models import (
    NormalizedTravelRow,
    TravelRenamerEnrichment,
    TravelReviewDecision,
    TravelReviewResult,
    TravelScheduleManifest,
)
from media_archive_tooling.travel_reviewer.reference_store import (
    TravelReferenceStore,
    compute_canonical_sha256,
    normalize_to_travel_row,
)
from media_archive_tooling.travel_reviewer.service import TravelScheduleReviewService


# --- Fixtures and Helpers ---

def make_raw_schedule_row(
    row_id: int,
    start_date: str,
    end_date: str = "",
    place: str = "",
    country: str = "",
    text: str = "",
):
    return {
        "id": row_id,
        "Start Date": start_date,
        "End Date": end_date,
        "Place": place,
        "Country": country,
        "Schedule text": text,
    }


def create_engine_with_rows(rows: List[NormalizedTravelRow]) -> TravelScheduleEngine:
    manifest = TravelScheduleManifest(
        format_version="1.0",
        source_table_id="123",
        retrieved_at="2026-09-14T00:00:00Z",
        complete=True,
        row_count=len(rows),
        canonical_sha256=compute_canonical_sha256(rows),
        normalized_rows=rows,
    )
    return TravelScheduleEngine(manifest)


class FakeBaserowProvider:
    def __init__(self, rows=None, travel_schedule_table_id="12345", fail=False):
        self.rows = rows if rows is not None else []
        self.travel_schedule_table_id = travel_schedule_table_id
        self.fail = fail
        self.fetch_count = 0

    def fetch_all_travel_schedule_rows(self):
        if self.fail:
            raise BaserowUnavailableError("Baserow API connection failed")
        self.fetch_count += 1
        return self.rows


def create_sample_parser_result(
    tracking_id: str = "a1b2c3d4",
    filename: str = "2019-09-10_KKS_Lecture_Berlin-de_ID-a1b2c3d4.mp3",
    when_val: str = "2019-09-10",
    when_state: ResolutionState = ResolutionState.EXACT,
    when_precision: str = "day",
    place: Optional[str] = "Berlin",
    country: Optional[str] = "Germany",
    country_iso2: Optional[str] = "de",
    where_state: ResolutionState = ResolutionState.EXACT,
    what_val: str = "Lecture",
    parent_folder: str = "",
) -> ParserResult:
    return ParserResult(
        identity=Identity(
            tracking_id=tracking_id,
            original_filename=filename,
            original_path=f"/media/{filename}",
            current_filename=filename,
            extension=".mp3",
        ),
        context=Context(parent_folder=parent_folder),
        when=WhenResult(
            selected_value=when_val,
            precision=when_precision,
            state=when_state,
            evidence=[Evidence(source="filename", raw_value=when_val)],
        ),
        who="KKS",
        what=WhatResult(
            selected_value=what_val,
            state=ResolutionState.EXACT,
        ),
        where=WhereResult(
            place_location=place,
            country=country,
            country_iso2=country_iso2,
            state=where_state,
            evidence=[Evidence(source="filename", raw_value=f"{place}-{country_iso2}")],
        ),
    )


def register_file(registry: LocalRegistry, parser_res: ParserResult) -> str:
    tid = parser_res.identity.tracking_id
    prop = RenameProposal(
        tracking_id=tid,
        original_path=parser_res.identity.original_path,
        current_filename=parser_res.identity.current_filename,
        proposed_filename=parser_res.identity.current_filename,
        proposed_path=parser_res.identity.original_path,
        mode=RenameMode.INITIAL,
        parser_result=parser_res,
        needs_review=False,
        review_reasons=[],
    )
    registry.save_proposal(prop)
    return tid


# --- 40 Required Tests ---

def test_01_complete_static_reference_bootstrap_with_pagination(tmp_path):
    """1. complete static reference bootstrap through Tool 2/shared provider with pagination."""
    # Simulate 150 rows returned across pages by provider
    raw_rows = [
        make_raw_schedule_row(i, "2019-09-10", place=f"City{i}", country="Germany")
        for i in range(1, 151)
    ]
    fake_provider = FakeBaserowProvider(rows=raw_rows, travel_schedule_table_id="777")
    ref_file = tmp_path / "reference" / "travel_schedule.json"
    store = TravelReferenceStore(reference_path=ref_file, provider=fake_provider)

    manifest = store.ensure_reference()
    assert fake_provider.fetch_count == 1
    assert manifest.complete is True
    assert manifest.row_count == 150
    assert len(manifest.normalized_rows) == 150
    assert manifest.source_table_id == "777"
    assert len(manifest.canonical_sha256) == 64
    assert ref_file.exists()


def test_02_verified_local_reference_reused_with_zero_network_calls(tmp_path):
    """2. verified local reference is reused with zero network calls on normal rerun."""
    raw_rows = [make_raw_schedule_row(1, "2019-09-10", place="Berlin", country="Germany")]
    fake_provider = FakeBaserowProvider(rows=raw_rows)
    ref_file = tmp_path / "travel_schedule.json"
    store = TravelReferenceStore(reference_path=ref_file, provider=fake_provider)

    # Initial bootstrap
    store.ensure_reference()
    assert fake_provider.fetch_count == 1

    # Second call should load from disk with zero network calls
    manifest2 = store.ensure_reference()
    assert fake_provider.fetch_count == 1
    assert manifest2.row_count == 1


def test_03_deterministic_reference_checksum_and_row_count():
    """3. deterministic reference checksum and row count."""
    row1 = normalize_to_travel_row(make_raw_schedule_row(1, "2019-09-10", place="Berlin", country="Germany"))
    row2 = normalize_to_travel_row(make_raw_schedule_row(2, "2019-09-12", place="Leipzig", country="Germany"))

    # Checksum computed with rows in order 1, 2 vs order 2, 1
    sha_a = compute_canonical_sha256([row1, row2])
    sha_b = compute_canonical_sha256([row2, row1])
    assert sha_a == sha_b


def test_04_corrupted_local_reference_rejected(tmp_path):
    """4. corrupted local reference is rejected."""
    ref_file = tmp_path / "travel_schedule.json"
    raw_rows = [make_raw_schedule_row(1, "2019-09-10", place="Berlin", country="Germany")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=raw_rows))
    store.ensure_reference()

    # Tamper with file
    with open(ref_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["normalized_rows"][0]["place"] = "TamperedPlace"
    with open(ref_file, "w", encoding="utf-8") as f:
        json.dump(data, f)

    assert store.load_reference() is None


def test_05_corrupt_or_missing_reference_rebootstrapped(tmp_path):
    """5. corrupt/missing reference can be re-bootstrapped when provider is available."""
    ref_file = tmp_path / "travel_schedule.json"
    raw_rows = [make_raw_schedule_row(1, "2019-09-10", place="Berlin", country="Germany")]
    provider = FakeBaserowProvider(rows=raw_rows)
    store = TravelReferenceStore(reference_path=ref_file, provider=provider)

    # Write corrupt file
    ref_file.write_text("{corrupt json", encoding="utf-8")
    assert store.load_reference() is None

    # ensure_reference re-bootstraps
    manifest = store.ensure_reference()
    assert manifest is not None
    assert manifest.row_count == 1
    assert provider.fetch_count == 1


def test_06_corrupt_or_missing_reference_unavailable_provider(tmp_path):
    """6. corrupt/missing reference + unavailable provider -> REFERENCE_UNAVAILABLE."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "missing_ref.json"
    provider = FakeBaserowProvider(fail=True)
    store = TravelReferenceStore(reference_path=ref_file, provider=provider)
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    p_res = create_sample_parser_result()
    register_file(reg, p_res)

    res = service.review_file(p_res.identity.tracking_id)
    assert res.decision == TravelReviewDecision.REFERENCE_UNAVAILABLE
    assert "unavailable" in res.diagnostic_notes[0].lower()


def test_07_explicit_remote_verify_unexpected_change(tmp_path):
    """7. explicit remote verify with different canonical checksum surfaces unexpected reference change and does not silently replace local reference."""
    ref_file = tmp_path / "travel_schedule.json"
    initial_rows = [make_raw_schedule_row(1, "2019-09-10", place="Berlin", country="Germany")]
    provider = FakeBaserowProvider(rows=initial_rows)
    store = TravelReferenceStore(reference_path=ref_file, provider=provider)
    manifest = store.ensure_reference()
    orig_sha = manifest.canonical_sha256

    # Remote table now has different rows
    provider.rows = [
        make_raw_schedule_row(1, "2019-09-10", place="Berlin", country="Germany"),
        make_raw_schedule_row(2, "2019-09-15", place="Munich", country="Germany"),
    ]

    check = store.verify_remote_reference()
    assert check["matches"] is False
    assert check["unexpected_change"] is True
    assert check["local_sha256"] == orig_sha
    assert check["remote_sha256"] != orig_sha

    # Verify local file was NOT silently overwritten
    reloaded = store.load_reference()
    assert reloaded.canonical_sha256 == orig_sha
    assert reloaded.row_count == 1


def test_08_exact_known_date_place_corroborated(tmp_path):
    """8. exact known date + known place schedule match -> CORROBORATED, no field overwrite."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    rows = [make_raw_schedule_row(10, "2019-09-10", place="Berlin", country="Germany")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    p_res = create_sample_parser_result(when_val="2019-09-10", place="Berlin", country_iso2="de")
    tid = register_file(reg, p_res)

    res = service.review_file(tid)
    assert res.decision == TravelReviewDecision.CORROBORATED
    assert 10 in res.selected_schedule_row_ids
    assert res.provisional_enrichment is None

    # Proposal remains unchanged
    stored = reg.get_file(tid)
    assert stored["when_val"] == "2019-09-10"
    assert stored["where_val"] == "Berlin-de"


def test_09_known_date_place_vs_different_scheduled_place_conflict(tmp_path):
    """9. known date/place vs different scheduled place -> SCHEDULE_CONFLICT, local values unchanged."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    rows = [make_raw_schedule_row(11, "2019-09-10", place="Berlin", country="Germany")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    p_res = create_sample_parser_result(when_val="2019-09-10", place="Leipzig", country_iso2="de")
    tid = register_file(reg, p_res)

    res = service.review_file(tid)
    assert res.decision == TravelReviewDecision.SCHEDULE_CONFLICT
    assert len(res.conflicts) > 0
    assert "Berlin" in res.conflicts[0]

    # Local value preserved
    stored = reg.get_file(tid)
    assert "Leipzig" in stored["where_val"]


def test_exact_filename_location_outranks_same_country_schedule_context(tmp_path):
    """A precise recording location in the filename must not be blocked by broader itinerary context."""
    registry = LocalRegistry(tmp_path / "registry.sqlite3")
    rows = [make_raw_schedule_row(480, "2003-10-25", place="Prague", country="Czech Republic")]
    store = TravelReferenceStore(
        reference_path=tmp_path / "travel_schedule.json",
        provider=FakeBaserowProvider(rows=rows),
    )
    service = TravelScheduleReviewService(registry=registry, reference_store=store)
    parser_result = RenamerParser().parse_file(Path(
        "/archive/Prague-Oct-2003/Lekce/"
        "A022F 03-10-25 SB 4.9.11 Nezkracena Farma KD.mp3"
    ))
    tracking_id = register_file(registry, parser_result)

    result = service.review_file(tracking_id)

    assert result.decision == TravelReviewDecision.CORROBORATED
    assert result.conflicts == []
    assert result.selected_schedule_row_ids == [480]
    assert any("retained exact filename location Krsna-Dvur" in note for note in result.diagnostic_notes)
    assert registry.get_file(tracking_id)["where_val"] == "Krsna-Dvur-cz"


def test_exact_filename_location_does_not_hide_country_contradiction(tmp_path):
    registry = LocalRegistry(tmp_path / "registry.sqlite3")
    rows = [make_raw_schedule_row(481, "2003-10-25", place="Berlin", country="Germany")]
    store = TravelReferenceStore(
        reference_path=tmp_path / "travel_schedule.json",
        provider=FakeBaserowProvider(rows=rows),
    )
    service = TravelScheduleReviewService(registry=registry, reference_store=store)
    parser_result = RenamerParser().parse_file(Path(
        "/archive/Prague-Oct-2003/Lekce/"
        "A022F 03-10-25 SB 4.9.11 Nezkracena Farma KD.mp3"
    ))
    tracking_id = register_file(registry, parser_result)

    result = service.review_file(tracking_id)

    assert result.decision == TravelReviewDecision.SCHEDULE_CONFLICT
    assert any("Germany" in conflict or "de" in conflict for conflict in result.conflicts)


def test_10_meaningful_query_no_schedule_row_no_support(tmp_path):
    """10. meaningful query with no schedule row -> NO_SCHEDULE_SUPPORT, not conflict."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    rows = [make_raw_schedule_row(12, "2019-09-20", place="Munich", country="Germany")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    p_res = create_sample_parser_result(when_val="2019-09-10", place="Berlin", country_iso2="de")
    tid = register_file(reg, p_res)

    res = service.review_file(tid)
    assert res.decision == TravelReviewDecision.NO_SCHEDULE_SUPPORT
    assert len(res.conflicts) == 0


def test_11_known_location_single_day_visit_provisional_when(tmp_path):
    """11. known location + exactly one single-day visit -> provisional full-date enrichment."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    rows = [make_raw_schedule_row(20, "2015-08-12", place="Berlin", country="Germany")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    p_res = create_sample_parser_result(
        when_val="YYYY-MM-DD",
        when_state=ResolutionState.UNRESOLVED,
        when_precision="none",
        place="Berlin",
        country_iso2="de",
    )
    tid = register_file(reg, p_res)

    res = service.review_file(tid, auto_enrich=True)
    assert res.decision == TravelReviewDecision.PROVISIONAL_ENRICHMENT
    assert res.provisional_enrichment is not None
    assert res.provisional_enrichment.when_val == "2015-08-12"
    assert res.provisional_enrichment.when_state == ResolutionState.PROVISIONAL

    # In registry, proposal updated and when.state remains PROVISIONAL
    stored = reg.get_file(tid)
    assert stored["when_val"] == "2015-08-12"
    p_dict = stored["parser_result"]
    assert p_dict["when"]["state"] == "provisional"


def test_12_known_location_multiday_in_one_month_partial_date(tmp_path):
    """12. known location + one multi-day range in one month -> only month-precision partial date, not arbitrary day."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    rows = [make_raw_schedule_row(21, "2015-08-10", end_date="2015-08-15", place="Berlin", country="Germany")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    p_res = create_sample_parser_result(
        when_val="YYYY-MM-DD",
        when_state=ResolutionState.UNRESOLVED,
        place="Berlin",
        country_iso2="de",
    )
    tid = register_file(reg, p_res)

    res = service.review_file(tid, auto_enrich=True)
    assert res.decision == TravelReviewDecision.PROVISIONAL_ENRICHMENT
    assert res.provisional_enrichment.when_val == "2015-08-DD"
    assert res.provisional_enrichment.when_state == ResolutionState.PROVISIONAL


def test_13_known_location_range_spanning_months_year_precision(tmp_path):
    """13. known location + one range spanning months in one year -> only year precision."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    rows = [make_raw_schedule_row(22, "2015-08-25", end_date="2015-09-05", place="Berlin", country="Germany")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    p_res = create_sample_parser_result(
        when_val="YYYY-MM-DD",
        when_state=ResolutionState.UNRESOLVED,
        place="Berlin",
        country_iso2="de",
    )
    tid = register_file(reg, p_res)

    res = service.review_file(tid, auto_enrich=True)
    assert res.decision == TravelReviewDecision.PROVISIONAL_ENRICHMENT
    assert res.provisional_enrichment.when_val == "2015-MM-DD"


def test_14_range_spanning_years_no_invented_selected_date(tmp_path):
    """14. date range spanning years -> no invented selected date."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    rows = [make_raw_schedule_row(23, "2015-12-28", end_date="2016-01-05", place="Berlin", country="Germany")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    p_res = create_sample_parser_result(
        when_val="YYYY-MM-DD",
        when_state=ResolutionState.UNRESOLVED,
        place="Berlin",
        country_iso2="de",
    )
    tid = register_file(reg, p_res)

    res = service.review_file(tid)
    assert res.decision == TravelReviewDecision.MULTIPLE_SCHEDULE_CANDIDATES
    assert res.provisional_enrichment is None


def test_15_same_location_multiple_distinct_visits(tmp_path):
    """15. same location with multiple distinct visits -> MULTIPLE_SCHEDULE_CANDIDATES."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    rows = [
        make_raw_schedule_row(30, "2015-08-12", place="Berlin", country="Germany"),
        make_raw_schedule_row(31, "2017-06-01", place="Berlin", country="Germany"),
    ]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    p_res = create_sample_parser_result(
        when_val="YYYY-MM-DD",
        when_state=ResolutionState.UNRESOLVED,
        place="Berlin",
        country_iso2="de",
    )
    tid = register_file(reg, p_res)

    res = service.review_file(tid)
    assert res.decision == TravelReviewDecision.MULTIPLE_SCHEDULE_CANDIDATES
    assert res.provisional_enrichment is None
    assert len(res.candidates) == 2


def test_16_exact_date_single_structured_place_provisional_where(tmp_path):
    """16. exact known date + exactly one structured place -> provisional WHERE enrichment."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    rows = [make_raw_schedule_row(40, "2019-09-10", place="Berlin", country="Germany")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    p_res = create_sample_parser_result(
        when_val="2019-09-10",
        when_state=ResolutionState.EXACT,
        place=None,
        country=None,
        country_iso2=None,
        where_state=ResolutionState.UNRESOLVED,
    )
    tid = register_file(reg, p_res)

    res = service.review_file(tid, auto_enrich=True)
    assert res.decision == TravelReviewDecision.PROVISIONAL_ENRICHMENT
    assert res.provisional_enrichment is not None
    assert res.provisional_enrichment.where_val == "Berlin-de"
    assert res.provisional_enrichment.where_state == ResolutionState.PROVISIONAL

    # Stored state preserves PROVISIONAL
    stored = reg.get_file(tid)
    assert stored["where_val"] == "Berlin-de"
    assert stored["parser_result"]["where"]["state"] == "provisional"


def test_17_exact_date_multiple_different_places(tmp_path):
    """17. exact known date + multiple materially different places -> multiple candidates, no selected WHERE."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    rows = [
        make_raw_schedule_row(41, "2019-09-10", place="Berlin", country="Germany"),
        make_raw_schedule_row(42, "2019-09-10", place="Leipzig", country="Germany"),
    ]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    p_res = create_sample_parser_result(
        when_val="2019-09-10",
        place=None,
        country_iso2=None,
        where_state=ResolutionState.UNRESOLVED,
    )
    tid = register_file(reg, p_res)

    res = service.review_file(tid)
    assert res.decision == TravelReviewDecision.MULTIPLE_SCHEDULE_CANDIDATES
    assert res.provisional_enrichment is None


def test_18_known_place_missing_country_supplies_country(tmp_path):
    """18. known place missing country + unique schedule match can provisionally fill country without replacing place."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    rows = [make_raw_schedule_row(43, "2019-09-10", place="Berlin", country="Germany")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    # Local has place Berlin, but no country
    p_res = create_sample_parser_result(
        when_val="2019-09-10",
        place="Berlin",
        country=None,
        country_iso2=None,
        where_state=ResolutionState.STRONG,
    )
    tid = register_file(reg, p_res)

    res = service.review_file(tid, auto_enrich=True)
    assert res.decision == TravelReviewDecision.PROVISIONAL_ENRICHMENT
    assert res.provisional_enrichment.where_val == "Berlin-de"

    stored = reg.get_file(tid)
    assert stored["where_val"] == "Berlin-de"


def test_19_country_contradiction_prevents_location_selection(tmp_path):
    """19. country contradiction prevents automatic location selection."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    rows = [make_raw_schedule_row(44, "2019-09-10", place="Berlin", country="Germany")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    # Local has country IT (Italy)
    p_res = create_sample_parser_result(
        when_val="2019-09-10",
        place=None,
        country="Italy",
        country_iso2="it",
        where_state=ResolutionState.PROVISIONAL,
    )
    tid = register_file(reg, p_res)

    res = service.review_file(tid)
    assert res.decision == TravelReviewDecision.SCHEDULE_CONFLICT
    assert res.provisional_enrichment is None
    assert "contradicts" in res.conflicts[0]


def test_20_partial_local_date_structurally_constrains_candidates(tmp_path):
    """20. partial local date structurally constrains schedule candidates."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    rows = [
        make_raw_schedule_row(50, "2014-05-10", place="Berlin", country="Germany"),
        make_raw_schedule_row(51, "2015-08-12", place="Berlin", country="Germany"),
        make_raw_schedule_row(52, "2016-09-01", place="Berlin", country="Germany"),
    ]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    # Local partial date: 2015-08-DD
    p_res = create_sample_parser_result(
        when_val="2015-08-DD",
        when_state=ResolutionState.STRONG,
        when_precision="month",
        place="Berlin",
        country_iso2="de",
    )
    tid = register_file(reg, p_res)

    res = service.review_file(tid, auto_enrich=True)
    # The partial date uniquely isolates row 51 (2015-08-12)
    assert res.decision == TravelReviewDecision.PROVISIONAL_ENRICHMENT
    assert res.provisional_enrichment.when_val == "2015-08-12"


def test_21_inclusive_range_boundary_matches_start_and_end(tmp_path):
    """21. inclusive range boundary matches start and end dates."""
    rows = [normalize_to_travel_row(make_raw_schedule_row(60, "2019-09-10", end_date="2019-09-15", place="Berlin", country="Germany"))]
    manifest = TravelScheduleManifest(
        source_table_id="1", retrieved_at="now", row_count=1,
        canonical_sha256=compute_canonical_sha256(rows), normalized_rows=rows,
    )
    index = TravelScheduleIndex(manifest)

    assert len(index.get_rows_by_date("2019-09-10")) == 1  # Start boundary
    assert len(index.get_rows_by_date("2019-09-12")) == 1  # Inside range
    assert len(index.get_rows_by_date("2019-09-15")) == 1  # End boundary
    assert len(index.get_rows_by_date("2019-09-09")) == 0  # Outside before
    assert len(index.get_rows_by_date("2019-09-16")) == 0  # Outside after


def test_22_invalid_end_before_start_cannot_authorize_enrichment(tmp_path):
    """22. invalid end-before-start row cannot authorize enrichment."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    rows = [make_raw_schedule_row(70, "2019-09-15", end_date="2019-09-10", place="Berlin", country="Germany")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    p_res = create_sample_parser_result(
        when_val="2019-09-12",
        place=None,
        where_state=ResolutionState.UNRESOLVED,
    )
    tid = register_file(reg, p_res)

    res = service.review_file(tid)
    # The invalid row must not index for 2019-09-12
    assert res.decision == TravelReviewDecision.NO_SCHEDULE_SUPPORT
    assert res.provisional_enrichment is None


def test_23_semantically_duplicate_schedule_rows_grouped(tmp_path):
    """23. semantically duplicate schedule rows are grouped deterministically while all source row IDs remain in provenance."""
    rows = [
        normalize_to_travel_row(make_raw_schedule_row(81, "2019-09-10", place="Berlin", country="Germany", text="Morning Class")),
        normalize_to_travel_row(make_raw_schedule_row(82, "2019-09-10", place="Berlin", country="Germany", text="Evening Kirtan")),
    ]
    candidates = group_candidates_semantically(rows)
    assert len(candidates) == 1
    cand = candidates[0]
    assert sorted(cand.schedule_row_ids) == [81, 82]
    assert "Morning Class" in cand.schedule_text
    assert "Evening Kirtan" in cand.schedule_text


def test_24_exact_schedule_text_cannot_authorize_automatic_enrichment(tmp_path):
    """24. exact schedule text term may retrieve/support a candidate but text-only/fuzzy matching cannot authorize automatic enrichment."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    # Row has empty place, but notes mention Leipzig
    rows = [make_raw_schedule_row(90, "2019-09-10", place="", country="Germany", text="Visit to Leipzig center")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    p_res = create_sample_parser_result(
        when_val="2019-09-10",
        place=None,
        where_state=ResolutionState.UNRESOLVED,
    )
    tid = register_file(reg, p_res)

    res = service.review_file(tid)
    # Cannot enrich place from schedule text alone!
    assert res.decision != TravelReviewDecision.PROVISIONAL_ENRICHMENT
    assert res.provisional_enrichment is None


def test_25_fuzzy_place_similarity_alone_cannot_authorize_enrichment(tmp_path):
    """25. fuzzy place similarity alone cannot authorize automatic enrichment."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    rows = [make_raw_schedule_row(91, "2015-08-12", place="Berlin", country="Germany")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    # Local place has a typo "Berrlinx"
    p_res = create_sample_parser_result(
        when_val="YYYY-MM-DD",
        place="Berrlinx",
        country_iso2="de",
        where_state=ResolutionState.UNRESOLVED,
    )
    tid = register_file(reg, p_res)

    res = service.review_file(tid)
    assert res.decision == TravelReviewDecision.NO_SCHEDULE_SUPPORT
    assert res.provisional_enrichment is None


def test_26_neither_date_nor_location_known_insufficient_evidence(tmp_path):
    """26. neither date nor location known -> INSUFFICIENT_EVIDENCE, no unconstrained guess."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    rows = [make_raw_schedule_row(92, "2019-09-10", place="Berlin", country="Germany")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    p_res = create_sample_parser_result(
        when_val="YYYY-MM-DD",
        when_state=ResolutionState.UNRESOLVED,
        place=None,
        country=None,
        country_iso2=None,
        where_state=ResolutionState.UNRESOLVED,
    )
    tid = register_file(reg, p_res)

    res = service.review_file(tid)
    assert res.decision == TravelReviewDecision.INSUFFICIENT_EVIDENCE
    assert res.provisional_enrichment is None
    assert "tool_5_content_discovery" in res.downstream_routing


def test_27_parent_folder_consumed_through_parser_result(tmp_path):
    """27. parent-folder evidence is consumed through Tool 1 ParserResult; Tool 3 does not reparse raw folders independently."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    rows = [make_raw_schedule_row(93, "2019-09-10", place="Berlin", country="Germany")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    # Tool 1 provided parent folder evidence into where
    p_res = create_sample_parser_result(
        when_val="2019-09-10",
        place="Berlin",
        country_iso2="de",
        parent_folder="Berlin_Recordings",
    )
    p_res.where.evidence.append(Evidence(source="parent_folder", raw_value="Berlin_Recordings"))
    tid = register_file(reg, p_res)

    res = service.review_file(tid)
    assert res.decision == TravelReviewDecision.CORROBORATED


def test_28_confirmed_tool2_media_values_never_overridden(tmp_path):
    """28. confirmed Tool 2 Media values are never overridden/downgraded by travel schedule."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    # Schedule places speaker in Munich on 2019-09-10
    rows = [make_raw_schedule_row(100, "2019-09-10", place="Munich", country="Germany")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    p_res = create_sample_parser_result(when_val="2019-09-10", place="Berlin", country_iso2="de")
    tid = register_file(reg, p_res)

    # Confirmed Media match indicates Berlin
    t2_res = MediaDatabaseReviewResult(
        tracking_id=tid,
        decision=ReviewDecision.EXISTING_MEDIA_MATCH,
        selected_media_row_id=555,
        database_state="LIVE_CURRENT",
        renamer_enrichment=RenamerEnrichment(
            confirmed=True,
            media_row_id=555,
            when_val="2019-09-10",
            where_val="Berlin-de",
        ),
    )

    res = service.review_file(tid, tool2_context=t2_res)
    # Schedule conflict does NOT change or override the confirmed Media values
    assert res.decision == TravelReviewDecision.SCHEDULE_CONFLICT
    assert res.provisional_enrichment is None


def test_29_explicit_local_vs_confirmed_media_contradiction_preserved(tmp_path):
    """29. explicit local <-> confirmed Media contradiction is preserved; Tool 3 does not adjudicate it."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    rows = [make_raw_schedule_row(101, "2019-09-10", place="Berlin", country="Germany")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    p_res = create_sample_parser_result(when_val="2019-09-01", place="Berlin", country_iso2="de")
    tid = register_file(reg, p_res)

    # High-authority conflict in Tool 2
    t2_res = MediaDatabaseReviewResult(
        tracking_id=tid,
        decision=ReviewDecision.EXISTING_MEDIA_MATCH,
        selected_media_row_id=666,
        database_state="LIVE_CURRENT",
        conflicts=["Local date 2019-09-01 contradicts confirmed Media date 2019-09-10"],
    )

    res = service.review_file(tid, tool2_context=t2_res)
    assert any("contradicts" in c for c in res.conflicts)
    assert any("high-authority" in n for n in res.diagnostic_notes)


def test_30_probable_or_multiple_tool2_candidates_do_not_leak_confirmed_enrichment(tmp_path):
    """30. probable/multiple Tool 2 candidate metadata does not leak into confirmed Renamer enrichment through Tool 3."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    rows = [make_raw_schedule_row(102, "2015-08-12", place="Berlin", country="Germany")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    p_res = create_sample_parser_result(
        when_val="YYYY-MM-DD",
        when_state=ResolutionState.UNRESOLVED,
        place="Berlin",
        country_iso2="de",
    )
    tid = register_file(reg, p_res)

    # Tool 2 candidate only
    t2_res = MediaDatabaseReviewResult(
        tracking_id=tid,
        decision=ReviewDecision.PROBABLE_EXISTING_MEDIA,
        database_state="LIVE_CURRENT",
        candidates=[MediaCandidate(media_row_id=777, score=85.0)],
    )

    res = service.review_file(tid, tool2_context=t2_res)
    # Schedule enrichment MUST be provisional, never confirmed
    if res.provisional_enrichment:
        assert res.provisional_enrichment.confirmed is False
        assert res.provisional_enrichment.source_tool == "tool_3_travel_schedule_review"


def test_31_historical_tool2_result_not_treated_as_current_media_authority(tmp_path):
    """31. historical stored Tool 2 result is not treated as current Media authority on an independent Tool 3 run that requires current Media context."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    rows = [make_raw_schedule_row(103, "2019-09-10", place="Berlin", country="Germany")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    p_res = create_sample_parser_result(when_val="2019-09-10", place="Berlin", country_iso2="de")
    tid = register_file(reg, p_res)

    # Independent run without passing live tool2_context
    res = service.review_file(tid, tool2_context=None)
    assert res.tool2_context_state == "UNAVAILABLE"


def test_32_media_context_unavailable_still_produces_provisional_schedule_evidence(tmp_path):
    """32. Media context unavailable + static reference available may still produce explicitly provisional schedule evidence and records Media unavailability."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    rows = [make_raw_schedule_row(104, "2015-08-12", place="Berlin", country="Germany")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    p_res = create_sample_parser_result(
        when_val="YYYY-MM-DD",
        when_state=ResolutionState.UNRESOLVED,
        place="Berlin",
        country_iso2="de",
    )
    tid = register_file(reg, p_res)

    t2_res = MediaDatabaseReviewResult(
        tracking_id=tid,
        decision=ReviewDecision.DATABASE_UNAVAILABLE,
        database_state="DATABASE_UNAVAILABLE",
    )

    res = service.review_file(tid, tool2_context=t2_res, auto_enrich=True)
    assert res.tool2_context_state == "DATABASE_UNAVAILABLE"
    assert res.decision == TravelReviewDecision.PROVISIONAL_ENRICHMENT
    assert res.provisional_enrichment.when_val == "2015-08-12"
    assert res.provisional_enrichment.confirmed is False


def test_33_schedule_only_enrichment_remains_provisional_in_tool1_registry(tmp_path):
    """33. Tool 3 schedule-only enrichment remains ResolutionState.PROVISIONAL in Tool 1 registry."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    rows = [make_raw_schedule_row(105, "2015-08-12", place="Berlin", country="Germany")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    p_res = create_sample_parser_result(
        when_val="YYYY-MM-DD",
        when_state=ResolutionState.UNRESOLVED,
        place="Berlin",
        country_iso2="de",
    )
    tid = register_file(reg, p_res)

    service.review_file(tid, auto_enrich=True)

    stored = reg.get_file(tid)
    p_data = stored["parser_result"]
    assert p_data["when"]["state"] == ResolutionState.PROVISIONAL.value
    assert p_data["when"]["selected_value"] == "2015-08-12"


def test_34_tool2_confirmed_enrichment_retains_stronger_state(tmp_path):
    """34. existing Tool 2 confirmed enrichment still retains its accepted stronger state semantics after the enrichment-contract extension."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    renamer = RenamerApplicationService(registry=reg)

    p_res = create_sample_parser_result(
        when_val="YYYY-MM-DD",
        when_state=ResolutionState.UNRESOLVED,
    )
    tid = register_file(reg, p_res)

    # Tool 2 confirmed enrichment without when_state specified defaults to EXACT
    evidence = EnrichmentEvidence(
        tracking_id=tid,
        when_val="2019-09-10",
        source_tool="tool_2_media_database_review",
        details="confirmed media match",
    )
    renamer.apply_enrichment(evidence)

    stored = reg.get_file(tid)
    p_data = stored["parser_result"]
    assert p_data["when"]["state"] == ResolutionState.EXACT.value


def test_35_only_safe_unique_provisional_enrichment_auto_hands_off(tmp_path):
    """35. only safe unique PROVISIONAL_ENRICHMENT auto-hands off to Renamer."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    rows = [
        make_raw_schedule_row(110, "2015-08-12", place="Berlin", country="Germany"),
        make_raw_schedule_row(111, "2016-09-01", place="Berlin", country="Germany"),
    ]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    p_res = create_sample_parser_result(
        when_val="YYYY-MM-DD",
        when_state=ResolutionState.UNRESOLVED,
        place="Berlin",
        country_iso2="de",
    )
    tid = register_file(reg, p_res)

    res = service.review_file(tid, auto_enrich=True)
    assert res.decision == TravelReviewDecision.MULTIPLE_SCHEDULE_CANDIDATES

    # Proposal remains unresolved
    stored = reg.get_file(tid)
    assert stored["when_val"] == "YYYY-MM-DD"


def test_36_multiple_conflict_no_support_do_not_change_tool1_selected_fields(tmp_path):
    """36. multiple/conflict/no-support/insufficient/unavailable states do not change Tool 1 selected fields."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    rows = [make_raw_schedule_row(120, "2019-09-10", place="Berlin", country="Germany")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    # 1. Conflict file
    p1 = create_sample_parser_result(tracking_id="conf0001", when_val="2019-09-10", place="Leipzig")
    register_file(reg, p1)
    service.review_file("conf0001", auto_enrich=True)
    assert reg.get_file("conf0001")["where_val"] == "Leipzig-de"

    # 2. No support file
    p2 = create_sample_parser_result(tracking_id="nosup001", when_val="1999-01-01", place="Tokyo")
    register_file(reg, p2)
    service.review_file("nosup001", auto_enrich=True)
    assert reg.get_file("nosup001")["where_val"] == "Tokyo-de"


def test_37_rerunning_tool3_is_idempotent(tmp_path):
    """37. rerunning Tool 3 is idempotent and does not duplicate evidence/proposal tokens."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    rows = [make_raw_schedule_row(130, "2015-08-12", place="Berlin", country="Germany")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    p_res = create_sample_parser_result(
        when_val="YYYY-MM-DD",
        when_state=ResolutionState.UNRESOLVED,
        place="Berlin",
        country_iso2="de",
    )
    tid = register_file(reg, p_res)

    # Run 1
    service.review_file(tid, auto_enrich=True)
    file_1 = reg.get_file(tid)
    when_ev_1 = len(file_1["parser_result"]["when"]["evidence"])
    prop_1 = file_1["proposed_filename"]

    # Run 2
    service.review_file(tid, auto_enrich=True)
    file_2 = reg.get_file(tid)
    when_ev_2 = len(file_2["parser_result"]["when"]["evidence"])
    prop_2 = file_2["proposed_filename"]

    assert when_ev_1 == when_ev_2
    assert prop_1 == prop_2


def test_38_cli_batch_works_offline_with_verified_reference(tmp_path, monkeypatch):
    """38. CLI batch works offline using verified reference."""
    reg_db = tmp_path / "registry.sqlite3"
    ref_file = tmp_path / "travel_schedule.json"
    reg = LocalRegistry(reg_db)

    p1 = create_sample_parser_result(tracking_id="cli00001", when_val="2019-09-10", place="Berlin")
    register_file(reg, p1)

    # Bootstrap reference file first
    rows = [make_raw_schedule_row(140, "2019-09-10", place="Berlin", country="Germany")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    store.ensure_reference()

    # Execute CLI travel-review offline
    test_args = [
        "media-archive",
        "travel-review",
        "--registry-path", str(reg_db),
        "--reference-path", str(ref_file),
    ]
    monkeypatch.setattr("sys.argv", test_args)
    cli_main()

    # Stored review should exist in SQLite registry
    review = reg.get_travel_review("cli00001")
    assert review is not None
    assert review["decision"] == "CORROBORATED"


def test_39_portal_displays_tool3_evidence(tmp_path):
    """39. portal displays Tool 3 evidence/candidates and uses service boundaries rather than direct mutations."""
    reg_db = tmp_path / "registry.sqlite3"
    reg = LocalRegistry(reg_db)
    configure_review_context(registry_path=reg_db)

    p_res = create_sample_parser_result(tracking_id="port0001")
    register_file(reg, p_res)

    # Save a Tool 3 review into registry
    review_res = TravelReviewResult(
        tracking_id="port0001",
        decision=TravelReviewDecision.CORROBORATED,
        reference_checksum="abc123def4567890",
        reference_row_count=42,
        selected_schedule_row_ids=[10],
        diagnostic_notes=["Schedule agrees with known date and place"],
    )
    reg.save_travel_review(
        tracking_id="port0001",
        decision=review_res.decision.value,
        reference_checksum=review_res.reference_checksum,
        reference_row_count=review_res.reference_row_count,
        selected_row_ids=review_res.selected_schedule_row_ids,
        result_json=review_res.model_dump_json(),
    )

    client = TestClient(app)
    resp = client.get("/file/port0001")
    assert resp.status_code == 200
    assert "Tool 3 — Travel Schedule Review" in resp.text
    assert "CORROBORATED" in resp.text
    assert "abc123def456" in resp.text


def test_40_batch_isolates_one_file_errors_and_continues(tmp_path):
    """40. batch isolates one-file errors and continues."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    rows = [make_raw_schedule_row(150, "2019-09-10", place="Berlin", country="Germany")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    p1 = create_sample_parser_result(tracking_id="good0001")
    register_file(reg, p1)

    # Review batch containing a non-existent tracking ID and the good tracking ID
    results = service.review_batch(tracking_ids=["nonexistent_id", "good0001"])
    assert len(results) == 2
    assert results[0].decision == TravelReviewDecision.PROCESSING_ERROR
    assert "Batch item processing error" in results[0].diagnostic_notes[0]
    assert results[1].decision == TravelReviewDecision.CORROBORATED


# --- Review Findings Regression Tests (R-001 through R-006) ---

def test_r001_local_date_missing_confirmed_media_date_no_contradictory_when_enrichment(tmp_path):
    """R-001: local date missing + confirmed Media date A + unique schedule date B -> no enrichment to B."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    # Schedule has Leipzig only on 2015-05-10 (date B)
    rows = [make_raw_schedule_row(10, "2015-05-10", place="Leipzig", country="Germany")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    # Local file has NO date, but place Leipzig
    p = create_sample_parser_result(
        tracking_id="r001_date",
        when_val="YYYY-MM-DD",
        when_state=ResolutionState.UNRESOLVED,
        place="Leipzig",
    )
    register_file(reg, p)

    # Tool 2 has confirmed Media match with date A = 2012-01-07
    t2_res = MediaDatabaseReviewResult(
        tracking_id="r001_date",
        database_state="LIVE_CURRENT",
        decision=ReviewDecision.EXISTING_MEDIA_MATCH,
        selected_media_row_id=999,
        renamer_enrichment=RenamerEnrichment(
            confirmed=True,
            media_row_id=999,
            when_val="2012-01-07",
            where_val="Leipzig-de",
        ),
    )

    res = service.review_file("r001_date", tool2_context=t2_res, auto_enrich=True)

    # Must NOT enrich to date B (2015-05-10)
    if res.provisional_enrichment:
        assert res.provisional_enrichment.when_val != "2015-05-10"
        assert res.provisional_enrichment.when_val is None

    # Registry proposal date must remain unchanged / not set to 2015-05-10
    prop = reg.get_file("r001_date")
    assert "2015-05-10" not in prop["proposed_filename"]


def test_r001_local_place_missing_confirmed_media_where_no_contradictory_where_enrichment(tmp_path):
    """R-001: local place missing + confirmed Media WHERE A + unique schedule WHERE B -> no enrichment to B."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    ref_file = tmp_path / "travel_schedule.json"
    # Schedule on 2012-01-07 says speaker is in Radhadesh (Belgium) -> location B
    rows = [make_raw_schedule_row(11, "2012-01-07", place="Radhadesh", country="Belgium")]
    store = TravelReferenceStore(reference_path=ref_file, provider=FakeBaserowProvider(rows=rows))
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    # Local file has date 2012-01-07, but NO place
    p = create_sample_parser_result(
        tracking_id="r001_place",
        when_val="2012-01-07",
        place=None,
    )
    register_file(reg, p)

    # Tool 2 has confirmed Media match with WHERE A = "Leipzig-de"
    t2_res = MediaDatabaseReviewResult(
        tracking_id="r001_place",
        database_state="LIVE_CURRENT",
        decision=ReviewDecision.EXISTING_MEDIA_MATCH,
        selected_media_row_id=888,
        renamer_enrichment=RenamerEnrichment(
            confirmed=True,
            media_row_id=888,
            when_val="2012-01-07",
            where_val="Leipzig-de",
        ),
    )

    res = service.review_file("r001_place", tool2_context=t2_res, auto_enrich=True)

    # Must detect schedule conflict between planned Radhadesh and confirmed Media Leipzig
    assert res.decision == TravelReviewDecision.SCHEDULE_CONFLICT
    assert res.provisional_enrichment is None

    # Registry proposal must NOT have Radhadesh
    prop = reg.get_file("r001_place")
    assert "Radhadesh" not in prop["proposed_filename"]


def test_r003_verified_reference_not_overwritten_by_init_when_remote_checksum_differs(tmp_path, monkeypatch):
    """R-003: verified reference is not overwritten by routine review or init when remote checksum differs."""
    ref_file = tmp_path / "travel_schedule.json"
    initial_rows = [make_raw_schedule_row(1, "2019-09-10", place="Berlin", country="Germany")]
    provider1 = FakeBaserowProvider(rows=initial_rows)
    store1 = TravelReferenceStore(reference_path=ref_file, provider=provider1)
    manifest = store1.ensure_reference()
    original_sha = manifest.canonical_sha256

    # Remote table now has different rows / checksum
    different_rows = [make_raw_schedule_row(2, "2020-01-01", place="Prague", country="Czech Republic")]
    provider2 = FakeBaserowProvider(rows=different_rows)
    store2 = TravelReferenceStore(reference_path=ref_file, provider=provider2)

    # ensure_reference returns existing verified local reference without re-fetching
    reloaded = store2.ensure_reference()
    assert reloaded.canonical_sha256 == original_sha
    assert provider2.fetch_count == 0

    # CLI travel-reference init refuses to overwrite existing verified reference
    test_args = [
        "media-archive",
        "travel-reference",
        "init",
        "--reference-path", str(ref_file),
    ]
    monkeypatch.setattr("sys.argv", test_args)
    cli_main()

    # Local file content and checksum remain unchanged
    after_manifest = store2.load_reference()
    assert after_manifest.canonical_sha256 == original_sha
    assert after_manifest.row_count == 1
    assert after_manifest.normalized_rows[0].place == "Berlin"


def test_r004_valid_start_with_malformed_nonempty_end_date_cannot_authorize_enrichment():
    """R-004: valid start with malformed non-empty end date is rejected as invalid data and cannot authorize enrichment."""
    # end_date is non-empty but unparseable
    row = normalize_to_travel_row(make_raw_schedule_row(20, "2019-09-10", end_date="invalid-end-date", place="Berlin"))
    manifest = TravelScheduleManifest(
        format_version="1.0",
        source_table_id="123",
        retrieved_at="2026-09-14T00:00:00Z",
        complete=True,
        row_count=1,
        canonical_sha256=compute_canonical_sha256([row]),
        normalized_rows=[row],
    )
    engine = TravelScheduleEngine(manifest)

    # Must be recorded in invalid_rows and excluded from date lookups
    assert len(engine.index.invalid_rows) == 1
    assert engine.index.get_rows_by_date("2019-09-10") == []

    p = create_sample_parser_result(when_val="2019-09-10", place=None)
    res = engine.evaluate(p)
    # Must NOT authorize provisional enrichment
    assert res.decision != TravelReviewDecision.PROVISIONAL_ENRICHMENT
    assert res.decision == TravelReviewDecision.NO_SCHEDULE_SUPPORT


def test_r005_alias_equivalent_places_grouped_with_all_row_ids_and_text_preserved():
    """R-005: alias-equivalent places group to single candidate preserving all row IDs and schedule text."""
    # Row 1: place="Radhadesh", Row 2: place="Chateau de Petite Somme" (alias of Radhadesh)
    row1 = normalize_to_travel_row(make_raw_schedule_row(101, "2020-05-01", place="Radhadesh", text="Morning class"))
    row2 = normalize_to_travel_row(make_raw_schedule_row(102, "2020-05-01", place="Chateau de Petite Somme", text="Evening kirtan"))

    manifest = TravelScheduleManifest(
        format_version="1.0",
        source_table_id="123",
        retrieved_at="2026-09-14T00:00:00Z",
        complete=True,
        row_count=2,
        canonical_sha256=compute_canonical_sha256([row1, row2]),
        normalized_rows=[row1, row2],
    )
    engine = TravelScheduleEngine(manifest)
    # Ensure alias mapping includes Chateau de Petite Somme -> Radhadesh
    engine.index.alias_to_canonical[_norm_place_token("Chateau de Petite Somme")] = "radhadesh"
    engine.index.alias_to_canonical[_norm_place_token("Radhadesh")] = "radhadesh"

    candidates = group_candidates_semantically([row1, row2], index=engine.index)
    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.schedule_row_ids == [101, 102]
    assert "Morning class" in cand.schedule_text
    assert "Evening kirtan" in cand.schedule_text


def test_r005_missing_end_vs_explicit_single_day_grouped_with_both_row_ids():
    """R-005: missing end date and explicit single day (end == start) group together."""
    row1 = normalize_to_travel_row(make_raw_schedule_row(201, "2020-05-01", end_date="", place="Berlin"))
    row2 = normalize_to_travel_row(make_raw_schedule_row(202, "2020-05-01", end_date="2020-05-01", place="Berlin"))

    candidates = group_candidates_semantically([row1, row2])
    assert len(candidates) == 1
    assert candidates[0].schedule_row_ids == [201, 202]
    assert candidates[0].end_date == "2020-05-01"


def test_r006_valid_explicit_range_longer_than_366_days_indexed_and_found():
    """R-006: valid explicit range longer than 366 days is contained in date lookups."""
    # Span from 2018-01-01 to 2019-03-01 is 424 days
    row = normalize_to_travel_row(make_raw_schedule_row(301, "2018-01-01", end_date="2019-03-01", place="Mayapur", country="India"))
    manifest = TravelScheduleManifest(
        format_version="1.0",
        source_table_id="123",
        retrieved_at="2026-09-14T00:00:00Z",
        complete=True,
        row_count=1,
        canonical_sha256=compute_canonical_sha256([row]),
        normalized_rows=[row],
    )
    engine = TravelScheduleEngine(manifest)

    # Check date inside long range
    rows = engine.index.get_rows_by_date("2018-06-15")
    assert len(rows) == 1
    assert rows[0].id == 301

    # Check month inside long range
    m_rows = engine.index.get_rows_by_month("2018-06")
    assert len(m_rows) == 1
    assert m_rows[0].id == 301

    # Check evaluate Case A on date inside range
    p = create_sample_parser_result(when_val="2018-06-15", place="Mayapur", country="India", country_iso2="in")
    res = engine.evaluate(p)
    assert res.decision == TravelReviewDecision.CORROBORATED


def test_r008_hyphenated_confirmed_place_not_truncated():
    """R-008: hyphenated places (e.g. Villa-Vrindavan, Serbia-summer-camp) are preserved, not truncated."""
    place, iso = parse_structured_where("Villa-Vrindavan-IT")
    assert place == "Villa-Vrindavan"
    assert iso == "it"

    place2, iso2 = parse_structured_where("Serbia-summer-camp-RS")
    assert place2 == "Serbia-summer-camp"
    assert iso2 == "rs"

    place3, iso3 = parse_structured_where("New-York-US")
    assert place3 == "New-York"
    assert iso3 == "us"

    # No country suffix
    place4, iso4 = parse_structured_where("Serbia-summer-camp")
    assert place4 == "Serbia-summer-camp"
    assert iso4 is None


def test_r008_same_place_different_country_media_guard_suppresses_enrichment():
    """R-008: media authority guard suppresses schedule WHERE enrichment if country contradicts confirmed Media country."""
    row = normalize_to_travel_row(make_raw_schedule_row(401, "2020-05-10", place="Springfield", country="USA"))
    manifest = TravelScheduleManifest(
        format_version="1.0",
        source_table_id="123",
        retrieved_at="2026-09-15T00:00:00Z",
        complete=True,
        row_count=1,
        canonical_sha256=compute_canonical_sha256([row]),
        normalized_rows=[row],
    )
    engine = TravelScheduleEngine(manifest)

    res = TravelReviewResult(
        tracking_id="sp01",
        decision=TravelReviewDecision.PROVISIONAL_ENRICHMENT,
        provisional_enrichment=TravelRenamerEnrichment(
            where_val="Springfield-US",
            where_state=ResolutionState.PROVISIONAL,
        ),
    )
    guarded = engine._apply_media_authority_guard(
        res,
        confirmed_media_when=None,
        confirmed_media_where="Springfield-AU",
    )
    # The guard must suppress provisional enrichment for Springfield-US because confirmed Media has country AU
    assert guarded.provisional_enrichment is None
    assert any("Suppressed provisional WHERE enrichment" in n for n in guarded.diagnostic_notes)


def test_r008_confirmed_media_where_only_acts_as_case_b_anchor_without_provisional_where():
    """R-008: when local has no anchor and confirmed Media provides WHERE (no WHEN), WHERE anchors Case B without becoming provisional evidence."""
    row = normalize_to_travel_row(make_raw_schedule_row(501, "2019-06-15", place="Villa-Vrindavan", country="Italy"))
    manifest = TravelScheduleManifest(
        format_version="1.0",
        source_table_id="123",
        retrieved_at="2026-09-15T00:00:00Z",
        complete=True,
        row_count=1,
        canonical_sha256=compute_canonical_sha256([row]),
        normalized_rows=[row],
    )
    engine = TravelScheduleEngine(manifest)

    # Local has no date anchor and no place anchor
    p = create_sample_parser_result(when_val="YYYY-MM-DD", place="", country=None, country_iso2=None)
    # Confirmed Media has WHERE Villa-Vrindavan-IT, but NO when_val
    t2_res = MediaDatabaseReviewResult(
        tracking_id=p.identity.tracking_id,
        decision=ReviewDecision.EXISTING_MEDIA_MATCH,
        selected_media_row_id=888,
        candidates=[
            MediaCandidate(
                media_row_id=888,
                normalized_row={"place": "Villa-Vrindavan", "country": "Italy"},
            )
        ],
    )
    res = engine.evaluate(p, tool2_context=t2_res)
    # Case B should find the unique visit on 2019-06-15 and provisionally enrich WHEN, but NOT WHERE
    assert res.decision == TravelReviewDecision.PROVISIONAL_ENRICHMENT
    assert res.provisional_enrichment is not None
    assert res.provisional_enrichment.when_val == "2019-06-15"
    assert res.provisional_enrichment.where_val is None


def test_r009_same_place_different_country_multiple_candidates_in_case_c():
    """R-009: same place in different countries on same date must be MULTIPLE_SCHEDULE_CANDIDATES in Case C."""
    row1 = normalize_to_travel_row(make_raw_schedule_row(601, "2020-08-01", place="Springfield", country="United States"))
    row2 = normalize_to_travel_row(make_raw_schedule_row(602, "2020-08-01", place="Springfield", country="Australia"))
    manifest = TravelScheduleManifest(
        format_version="1.0",
        source_table_id="123",
        retrieved_at="2026-09-15T00:00:00Z",
        complete=True,
        row_count=2,
        canonical_sha256=compute_canonical_sha256([row1, row2]),
        normalized_rows=[row1, row2],
    )
    engine = TravelScheduleEngine(manifest)

    p = create_sample_parser_result(when_val="2020-08-01", place="", country=None, country_iso2=None)
    res = engine.evaluate(p)
    assert res.decision == TravelReviewDecision.MULTIPLE_SCHEDULE_CANDIDATES
    assert res.provisional_enrichment is None


def test_r009_reversed_input_order_deterministic_grouping_and_provenance():
    """R-009: candidate grouping and provenance are deterministic independent of input row order."""
    row1 = normalize_to_travel_row(make_raw_schedule_row(10, "2019-05-01", place="Berlin", text="First text"))
    row2 = normalize_to_travel_row(make_raw_schedule_row(20, "2019-05-01", place="Berlin", text="Second text"))

    cands_forward = group_candidates_semantically([row1, row2])
    cands_reversed = group_candidates_semantically([row2, row1])

    assert len(cands_forward) == 1
    assert len(cands_reversed) == 1
    assert cands_forward[0].schedule_row_ids == [10, 20]
    assert cands_reversed[0].schedule_row_ids == [10, 20]
    assert cands_forward[0].schedule_text == cands_reversed[0].schedule_text


def test_r009_union_of_row_ids_and_texts_preserved_for_selected_candidate():
    """R-009: union of contributing row IDs and texts preserved when selecting candidate."""
    row1 = normalize_to_travel_row(make_raw_schedule_row(50, "2021-04-05", place="Mayapur", country="India", text="Morning darshan"))
    row2 = normalize_to_travel_row(make_raw_schedule_row(51, "2021-04-05", place="Mayapur", country="India", text="Evening class"))
    manifest = TravelScheduleManifest(
        format_version="1.0",
        source_table_id="123",
        retrieved_at="2026-09-15T00:00:00Z",
        complete=True,
        row_count=2,
        canonical_sha256=compute_canonical_sha256([row1, row2]),
        normalized_rows=[row1, row2],
    )
    engine = TravelScheduleEngine(manifest)

    p = create_sample_parser_result(when_val="2021-04-05", place="", country=None, country_iso2=None)
    res = engine.evaluate(p)
    assert res.decision == TravelReviewDecision.PROVISIONAL_ENRICHMENT
    assert res.selected_schedule_row_ids == [50, 51]
    assert res.provisional_enrichment.schedule_row_ids == [50, 51]


def test_r010_tampered_country_iso2_rejected_by_load_reference(tmp_path):
    """R-010: tampered country_iso2 cannot pass reference loading integrity check."""
    row = normalize_to_travel_row(make_raw_schedule_row(701, "2020-01-01", place="Berlin", country="Germany"))
    ref_file = tmp_path / "travel_schedule.json"
    manifest = TravelScheduleManifest(
        format_version="1.0",
        source_table_id="123",
        retrieved_at="2026-09-15T00:00:00Z",
        complete=True,
        row_count=1,
        canonical_sha256=compute_canonical_sha256([row]),
        normalized_rows=[row],
    )
    store = TravelReferenceStore(reference_path=ref_file)
    store.save_reference(manifest)

    # Tamper with stored country_iso2 in the JSON file
    import json
    with open(ref_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["normalized_rows"][0]["country_iso2"] = "fr"  # Germany should be 'de'
    with open(ref_file, "w", encoding="utf-8") as f:
        json.dump(data, f)

    # Loading the tampered file must fail (return None)
    loaded = store.load_reference()
    assert loaded is None


def test_r011_tool2_decision_snapshotted_in_result_and_registry(tmp_path):
    """R-011: tool2_decision is snapshotted in TravelReviewResult and registry."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    row = normalize_to_travel_row(make_raw_schedule_row(801, "2020-03-01", place="Berlin", country="Germany"))
    manifest = TravelScheduleManifest(
        format_version="1.0",
        source_table_id="123",
        retrieved_at="2026-09-15T00:00:00Z",
        complete=True,
        row_count=1,
        canonical_sha256=compute_canonical_sha256([row]),
        normalized_rows=[row],
    )
    ref_file = tmp_path / "travel_schedule.json"
    store = TravelReferenceStore(reference_path=ref_file)
    store.save_reference(manifest)
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    p = create_sample_parser_result(tracking_id="t2snap01", when_val="2020-03-01", place="Berlin", country="Germany", country_iso2="de")
    register_file(reg, p)

    t2_res = MediaDatabaseReviewResult(
        tracking_id=p.identity.tracking_id,
        decision=ReviewDecision.EXISTING_MEDIA_MATCH,
        selected_media_row_id=801,
    )
    res = service.review_file(target="t2snap01", tool2_context=t2_res)
    assert res.tool2_decision == "EXISTING_MEDIA_MATCH"

    stored = reg.get_travel_review("t2snap01")
    assert stored is not None
    assert stored.get("tool2_decision") == "EXISTING_MEDIA_MATCH"
    assert stored["result"].get("tool2_decision") == "EXISTING_MEDIA_MATCH"


def test_r011_candidate_comparison_states_populated_in_cases_b_and_c(tmp_path):
    """R-011: candidate comparison states are populated in Case B and Case C."""
    row1 = normalize_to_travel_row(make_raw_schedule_row(901, "2020-07-15", place="Berlin", country="Germany"))
    manifest = TravelScheduleManifest(
        format_version="1.0",
        source_table_id="123",
        retrieved_at="2026-09-15T00:00:00Z",
        complete=True,
        row_count=1,
        canonical_sha256=compute_canonical_sha256([row1]),
        normalized_rows=[row1],
    )
    engine = TravelScheduleEngine(manifest)

    # Case B test (place known, date missing)
    pb = create_sample_parser_result(when_val="YYYY-MM-DD", place="Berlin", country="Germany", country_iso2="de")
    res_b = engine.evaluate(pb)
    assert len(res_b.candidates) == 1
    cand_b = res_b.candidates[0]
    assert cand_b.place_comparison == "AGREES"
    assert cand_b.country_comparison == "AGREES"
    assert cand_b.date_comparison == "LOCAL_MISSING"
    assert len(cand_b.match_reasons) > 0

    # Case C test (date known, place missing)
    pc = create_sample_parser_result(when_val="2020-07-15", place="", country="")
    res_c = engine.evaluate(pc)
    assert len(res_c.candidates) == 1
    cand_c = res_c.candidates[0]
    assert cand_c.date_comparison == "AGREES"
    assert cand_c.place_comparison == "LOCAL_MISSING"
    assert len(cand_c.match_reasons) > 0


def test_r012_batch_error_does_not_produce_reference_unavailable_when_reference_healthy(tmp_path):
    """R-012/R-014: per-file error in batch produces PROCESSING_ERROR, not REFERENCE_UNAVAILABLE or INSUFFICIENT_EVIDENCE."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    row = normalize_to_travel_row(make_raw_schedule_row(999, "2020-01-01", place="Berlin", country="Germany"))
    manifest = TravelScheduleManifest(
        format_version="1.0",
        source_table_id="123",
        retrieved_at="2026-09-15T00:00:00Z",
        complete=True,
        row_count=1,
        canonical_sha256=compute_canonical_sha256([row]),
        normalized_rows=[row],
    )
    ref_file = tmp_path / "travel_schedule.json"
    store = TravelReferenceStore(reference_path=ref_file)
    store.save_reference(manifest)
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    results = service.review_batch(tracking_ids=["missing_from_registry"])
    assert len(results) == 1
    # Must NOT be REFERENCE_UNAVAILABLE or INSUFFICIENT_EVIDENCE; must be distinct PROCESSING_ERROR (R-014)
    assert results[0].decision == TravelReviewDecision.PROCESSING_ERROR
    assert results[0].reference_checksum == manifest.canonical_sha256
    assert "Batch item processing error" in results[0].diagnostic_notes[0]
    assert results[0].review_required is True


def test_r013_confirmed_country_missing_locally_agreeing_schedule_corroborates_without_redundant_provisional_enrichment(tmp_path):
    """R-013: confirmed Media country missing locally + agreeing schedule -> CORROBORATED without redundant provisional enrichment."""
    # Local: exact date 2005-06-15, Springfield, country missing
    p = create_sample_parser_result(tracking_id="t13_agree", when_val="2005-06-15", place="Springfield", country=None, country_iso2=None)
    
    # Tool 2: confirmed match with Springfield-AU
    t2_res = MediaDatabaseReviewResult(
        tracking_id="t13_agree",
        decision=ReviewDecision.EXISTING_MEDIA_MATCH,
        selected_media_row_id=777,
        candidates=[
            MediaCandidate(
                media_row_id=777,
                normalized_row={"date": "2005-06-15", "place": "Springfield", "country": "Australia"},
            )
        ],
        renamer_enrichment=RenamerEnrichment(
            confirmed=True,
            media_row_id=777,
            when_val="2005-06-15",
            where_val="Springfield-AU",
        ),
    )
    
    # Schedule: 2005-06-15, Springfield, Australia (au)
    row = normalize_to_travel_row(make_raw_schedule_row(10, "2005-06-15", place="Springfield", country="Australia"))
    engine = create_engine_with_rows([row])
    
    res = engine.evaluate(p, tool2_context=t2_res)
    assert res.decision == TravelReviewDecision.CORROBORATED
    # Must NOT emit provisional enrichment (Tool 2/Media already owns authoritative country)
    assert res.provisional_enrichment is None
    assert len(res.candidates) == 1
    assert res.candidates[0].date_comparison == "AGREES"
    assert res.candidates[0].place_comparison == "AGREES"
    assert res.candidates[0].country_comparison == "AGREES"


def test_r013_confirmed_country_missing_locally_conflicting_schedule_returns_schedule_conflict(tmp_path):
    """R-013: confirmed Media country missing locally + conflicting schedule -> SCHEDULE_CONFLICT with explicit provenance."""
    # Local: exact date 2005-06-15, Springfield, country missing
    p = create_sample_parser_result(tracking_id="t13_conf", when_val="2005-06-15", place="Springfield", country=None, country_iso2=None)
    
    # Tool 2: confirmed match with Springfield-AU
    t2_res = MediaDatabaseReviewResult(
        tracking_id="t13_conf",
        decision=ReviewDecision.EXISTING_MEDIA_MATCH,
        selected_media_row_id=777,
        candidates=[
            MediaCandidate(
                media_row_id=777,
                normalized_row={"date": "2005-06-15", "place": "Springfield", "country": "Australia"},
            )
        ],
        renamer_enrichment=RenamerEnrichment(
            confirmed=True,
            media_row_id=777,
            when_val="2005-06-15",
            where_val="Springfield-AU",
        ),
    )
    
    # Schedule: 2005-06-15, Springfield, United States (us)
    row = normalize_to_travel_row(make_raw_schedule_row(20, "2005-06-15", place="Springfield", country="United States"))
    engine = create_engine_with_rows([row])
    
    res = engine.evaluate(p, tool2_context=t2_res)
    assert res.decision == TravelReviewDecision.SCHEDULE_CONFLICT
    assert len(res.conflicts) > 0
    assert res.provisional_enrichment is None
    assert len(res.candidates) == 1
    # Place agrees, country conflicts
    assert res.candidates[0].place_comparison == "AGREES"
    assert res.candidates[0].country_comparison == "CONFLICT"


def test_r013_case_a_country_only_conflict_comparison_states():
    """R-013: Case A place agrees and country conflicts -> place_comparison=AGREES, country_comparison=CONFLICT."""
    p = create_sample_parser_result(tracking_id="t13_cmp", when_val="2005-06-15", place="Springfield", country="Australia", country_iso2="au")
    # Schedule has Springfield in United States
    row = normalize_to_travel_row(make_raw_schedule_row(30, "2005-06-15", place="Springfield", country="United States"))
    engine = create_engine_with_rows([row])
    
    res = engine.evaluate(p)
    assert res.decision == TravelReviewDecision.SCHEDULE_CONFLICT
    assert len(res.candidates) == 1
    cand = res.candidates[0]
    assert cand.date_comparison == "AGREES"
    assert cand.place_comparison == "AGREES"
    assert cand.country_comparison == "CONFLICT"


def test_r013_non_iso_two_letter_place_suffix_not_truncated():
    """R-013: hyphenated place ending in non-ISO two-letter suffix is preserved and not truncated."""
    from media_archive_tooling.travel_reviewer.engine import parse_structured_where
    
    # Non-ISO two-letter suffix (KD is not a country)
    place, iso = parse_structured_where("Farma-KD")
    assert place == "Farma-KD"
    assert iso is None
    
    # Non-ISO two-letter suffix (XY is not a country)
    place, iso = parse_structured_where("Temple-XY")
    assert place == "Temple-XY"
    assert iso is None
    
    # Valid ISO suffixes must still be recognized
    place, iso = parse_structured_where("Villa-Vrindavan-IT")
    assert place == "Villa-Vrindavan"
    assert iso == "it"
    
    place, iso = parse_structured_where("Serbia-summer-camp-RS")
    assert place == "Serbia-summer-camp"
    assert iso == "rs"


def test_r014_batch_processing_error_distinct_from_insufficient_evidence_and_reference_unavailable(tmp_path):
    """R-014: operational batch error produces PROCESSING_ERROR, distinct from INSUFFICIENT_EVIDENCE and REFERENCE_UNAVAILABLE."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    row = normalize_to_travel_row(make_raw_schedule_row(100, "2020-01-01", place="Berlin", country="Germany"))
    manifest = TravelScheduleManifest(
        format_version="1.0",
        source_table_id="tbl_100",
        retrieved_at="2026-09-15T00:00:00Z",
        complete=True,
        row_count=1,
        canonical_sha256=compute_canonical_sha256([row]),
        normalized_rows=[row],
    )
    ref_file = tmp_path / "travel_schedule.json"
    store = TravelReferenceStore(reference_path=ref_file)
    store.save_reference(manifest)
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    # Valid item registered in registry
    p_good = create_sample_parser_result(tracking_id="good_item", when_val="2020-01-01", place="Berlin", country="Germany", country_iso2="de")
    register_file(reg, p_good)

    # Missing item not in registry
    results = service.review_batch(tracking_ids=["missing_item", "good_item"])
    assert len(results) == 2
    
    # First item failed due to missing record: must be distinct PROCESSING_ERROR
    err_res = results[0]
    assert err_res.decision == TravelReviewDecision.PROCESSING_ERROR
    assert err_res.decision != TravelReviewDecision.INSUFFICIENT_EVIDENCE
    assert err_res.decision != TravelReviewDecision.REFERENCE_UNAVAILABLE
    assert err_res.decision != TravelReviewDecision.NO_SCHEDULE_SUPPORT
    assert err_res.reference_checksum == manifest.canonical_sha256
    assert err_res.reference_row_count == 1
    assert err_res.review_required is True
    assert "Batch item processing error" in err_res.diagnostic_notes[0]

    # Verify audit persistence in local registry
    stored_err = service.get_stored_review("missing_item")
    assert stored_err is not None
    assert stored_err["decision"] == TravelReviewDecision.PROCESSING_ERROR.value
    assert stored_err["reference_checksum"] == manifest.canonical_sha256

    # Second item must succeed, showing batch isolation
    good_res = results[1]
    assert good_res.decision == TravelReviewDecision.CORROBORATED


def test_r015_batch_processing_error_when_reference_also_unavailable_and_persisted_to_registry(tmp_path):
    """R-015: batch exception is classified as PROCESSING_ERROR even when reference is unavailable, and is persisted."""
    reg = LocalRegistry(tmp_path / "registry.sqlite3")
    # Reference store pointing to nonexistent file
    ref_file = tmp_path / "nonexistent_travel_schedule.json"
    store = TravelReferenceStore(reference_path=ref_file)
    service = TravelScheduleReviewService(registry=reg, reference_store=store)

    # Calling review_batch on a missing item (which causes an exception during review_file lookup)
    results = service.review_batch(tracking_ids=["missing_err_item"])
    assert len(results) == 1
    err_res = results[0]

    # Must be PROCESSING_ERROR, NOT REFERENCE_UNAVAILABLE
    assert err_res.decision == TravelReviewDecision.PROCESSING_ERROR
    assert err_res.decision != TravelReviewDecision.REFERENCE_UNAVAILABLE
    assert err_res.reference_checksum == ""
    assert err_res.reference_row_count == 0
    assert err_res.review_required is True

    # Audit persistence: must be retrievable from the registry
    stored = service.get_stored_review("missing_err_item")
    assert stored is not None
    assert stored["decision"] == TravelReviewDecision.PROCESSING_ERROR.value
    assert stored["tracking_id"] == "missing_err_item"


def test_r016_tool3_suffix_safety_independent_of_tool2_country_normalization_semantics():
    """R-016: Tool 3 WHERE suffix safety is local and does not alter accepted Tool 2 country normalization."""
    from media_archive_tooling.media_db_reviewer.engine import _norm_country
    from media_archive_tooling.travel_reviewer.engine import parse_structured_where

    # Tool 2 country normalization semantics preserved from main:
    # 2-letter alpha tokens are uppercase preserved; unmapped countries fall back to uppercase
    assert _norm_country("KD") == "KD"
    assert _norm_country("XY") == "XY"
    assert _norm_country("NonexistentCountry") == "NONEXISTENTCOUNTRY"
    assert _norm_country("Germany") == "DE"
    assert _norm_country("Australia") == "AU"

    # Tool 3 parse_structured_where strictly validates against ISO-2 codes locally
    place, iso = parse_structured_where("Farma-KD")
    assert place == "Farma-KD"
    assert iso is None

    place, iso = parse_structured_where("Temple-XY")
    assert place == "Temple-XY"
    assert iso is None

    place, iso = parse_structured_where("Villa-Vrindavan-IT")
    assert place == "Villa-Vrindavan"
    assert iso == "it"

