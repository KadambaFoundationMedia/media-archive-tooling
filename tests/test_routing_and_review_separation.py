import pytest
from pathlib import Path

from media_archive_tooling.renamer.parser.engine import RenamerParser
from media_archive_tooling.renamer.planner.planner import RenamePlanner
from media_archive_tooling.renamer.logging.logger import RenamerLogger
from media_archive_tooling.renamer.models import (
    RenameMode,
    ResolutionState,
    ParserResult,
    Identity,
    Context,
    WhenResult,
    WhatResult,
    WhereResult,
    FileMetadata,
    RenameProposal,
)


@pytest.fixture
def parser_and_planner():
    parser = RenamerParser()
    planner = RenamePlanner(mode=RenameMode.INITIAL)
    return parser, planner


def test_incomplete_but_safe_files_continue_without_human_review(parser_and_planner):
    """Verify that files with missing WHERE or generic WHAT continue safely without human review (R-022, R-023)."""
    parser, planner = parser_and_planner

    # 1. Missing WHERE: continues safely to downstream enrichment (Tool 2/3)
    res_no_where = parser.parse_file(Path("/archive/2012-05-13_KKS_BG-8-19.mp3"))
    prop_no_where = planner.plan_rename(res_no_where)
    assert prop_no_where.needs_review is False
    assert "WHERE is unresolved" in res_no_where.diagnostic_notes
    assert "tool_2_3_media_enrichment" in res_no_where.downstream_routing

    # 2. Generic unresolved WHAT: retains useful wording + ID and routes to Tools 2 and 5, NOT Tool 7 (R-023)
    res_no_what = parser.parse_file(Path("/archive/2012-05-13_Sydney.mp3"))
    prop_no_what = planner.plan_rename(res_no_what)
    assert prop_no_what.needs_review is False
    assert "WHAT is unresolved" in res_no_what.diagnostic_notes
    assert "tool_7_class_classification" not in res_no_what.downstream_routing
    assert "tool_2_media_database_review" in res_no_what.downstream_routing
    assert "tool_5_content_discovery" in res_no_what.downstream_routing
    assert prop_no_what.proposed_filename.startswith("2012-05-13_Sydney_ID-")

    # 3. Combination candidate: retains source stem + ID and routes to Tools 5/6
    res_comb = parser.parse_file(Path("/archive/jrm.mp3"), raw_filename="JRM and class 24-5-11 villa vrindavan.mp3")
    prop_comb = planner.plan_rename(res_comb)
    assert prop_comb.needs_review is False
    assert "tool_5_6_split_combination" in res_comb.downstream_routing
    assert "retaining source stem + ID for splitting" in res_comb.diagnostic_notes[0]
    assert f"_ID-{res_comb.identity.tracking_id}.mp3" in prop_comb.proposed_filename


def test_unresolved_class_what_routing_when_class_evidence_exists(parser_and_planner, tmp_path):
    """Verify that when an item is already established as a class, unresolved class WHAT routes to Tool 7 (R-023)."""
    parser, planner = parser_and_planner

    # 1. Folder context indicates class
    res_folder_class = parser.parse_file(Path("/archive/Classes/2012-05-13_Sydney.mp3"))
    prop_folder_class = planner.plan_rename(res_folder_class)
    assert prop_folder_class.needs_review is False
    assert "tool_7_class_classification" in res_folder_class.downstream_routing
    assert any("Class WHAT is unresolved" in note for note in res_folder_class.diagnostic_notes)

    # 2. Filename token indicates class/lecture
    res_lecture = parser.parse_file(Path("/archive/2011-05-24_KKS_Lecture_Sydney.mp3"))
    prop_lecture = planner.plan_rename(res_lecture)
    assert prop_lecture.needs_review is False
    assert "tool_7_class_classification" in res_lecture.downstream_routing
    assert any("Unidentified class WHAT" in note for note in res_lecture.diagnostic_notes)

    # 3. Service enrichment with what_category="Class" routes to Tool 7
    from media_archive_tooling.renamer.service import RenamerApplicationService
    from media_archive_tooling.renamer.registry.registry import LocalRegistry
    from media_archive_tooling.renamer.models import EnrichmentEvidence

    registry = LocalRegistry(db_path=tmp_path / "registry.db")
    res_raw = parser.parse_file(Path("/archive/2012-05-13_Sydney.mp3"))
    prop_raw = planner.plan_rename(res_raw)
    registry.save_proposal(prop_raw)

    service = RenamerApplicationService(registry=registry, planner=planner)
    enriched_prop = service.apply_enrichment(
        EnrichmentEvidence(
            tracking_id=prop_raw.tracking_id,
            what_category="Class",
            source_tool="tool_5_content_discovery"
        )
    )
    assert bool(enriched_prop["needs_review"]) is False
    import json
    parsed_json = json.loads(enriched_prop["parser_result_json"])
    assert "tool_7_class_classification" in parsed_json["downstream_routing"]


def test_genuine_conflicts_and_ambiguities_require_human_review(parser_and_planner):
    """Verify that actual contradictions, unresolvable ambiguities, and corruptions enter human queue."""
    parser, planner = parser_and_planner

    # 1. Filename date vs folder year conflict
    res_conflict = parser.parse_file(Path("/archive/2012-Folder/2011-12-30_KKS_Lecture.mp3"))
    prop_conflict = planner.plan_rename(res_conflict)
    assert prop_conflict.needs_review is True
    assert any("conflicts with folder year" in r for r in prop_conflict.review_reasons)

    # 2. Filename WHAT vs folder category contradiction
    res_what_conflict = parser.parse_file(Path("/archive/Kirtan/SB-1-1-1.mp3"))
    prop_what_conflict = planner.plan_rename(res_what_conflict)
    assert prop_what_conflict.needs_review is True
    assert any("contradicts parent folder category 'Kirtan'" in r for r in prop_what_conflict.review_reasons)

    # 3. Corrupted file
    res_corrupted = parser.parse_file(Path("/archive/2011-05-24_corrupted_lecture.mp3"))
    prop_corrupted = planner.plan_rename(res_corrupted)
    assert prop_corrupted.needs_review is True
    assert any("CORRUPTED" in r for r in prop_corrupted.review_reasons)


def test_logger_evaluation_categories(tmp_path):
    """Verify objective evaluation categories in RenamerLogger._categorize_proposal."""
    logger = RenamerLogger(log_dir=tmp_path)

    # 1. Safe automatic
    prop_auto = RenameProposal(
        tracking_id="11111111",
        original_path="/a.mp3",
        current_filename="a.mp3",
        proposed_filename="2011-05-24_KKS_SB-1-1-1_Praha-cz_ID-11111111.mp3",
        proposed_path="/a.mp3",
        mode=RenameMode.INITIAL,
        needs_review=False,
        parser_result=ParserResult(
            identity=Identity(tracking_id="11111111", original_filename="a.mp3", original_path="/a.mp3", current_filename="a.mp3", extension=".mp3"),
            context=Context(),
            when=WhenResult(selected_value="2011-05-24", state=ResolutionState.EXACT),
            what=WhatResult(selected_value="SB-1-1-1", state=ResolutionState.EXACT),
            where=WhereResult(place_location="Praha", country_iso2="cz", state=ResolutionState.EXACT),
            file_metadata=FileMetadata()
        )
    )
    assert logger._categorize_proposal(prop_auto) == "safe_automatic"

    # 2. Downstream split
    prop_split = RenameProposal(
        tracking_id="22222222",
        original_path="/b.mp3",
        current_filename="b.mp3",
        proposed_filename="JRM and class_ID-22222222.mp3",
        proposed_path="/b.mp3",
        mode=RenameMode.INITIAL,
        needs_review=False,
        parser_result=ParserResult(
            identity=Identity(tracking_id="22222222", original_filename="b.mp3", original_path="/b.mp3", current_filename="b.mp3", extension=".mp3"),
            context=Context(),
            when=WhenResult(selected_value="2011-05-24", state=ResolutionState.EXACT),
            what=WhatResult(selected_value="Jaya-Radha-Madhava", state=ResolutionState.EXACT),
            where=WhereResult(place_location="Praha", country_iso2="cz", state=ResolutionState.EXACT),
            file_metadata=FileMetadata(possible_combination=True)
        )
    )
    assert logger._categorize_proposal(prop_split) == "downstream_split"

    # 3. Downstream enrichment
    prop_enrich = RenameProposal(
        tracking_id="33333333",
        original_path="/c.mp3",
        current_filename="c.mp3",
        proposed_filename="2011-05-24_KKS_SB-1-1-1_ID-33333333.mp3",
        proposed_path="/c.mp3",
        mode=RenameMode.INITIAL,
        needs_review=False,
        parser_result=ParserResult(
            identity=Identity(tracking_id="33333333", original_filename="c.mp3", original_path="/c.mp3", current_filename="c.mp3", extension=".mp3"),
            context=Context(),
            when=WhenResult(selected_value="2011-05-24", state=ResolutionState.EXACT),
            what=WhatResult(selected_value="SB-1-1-1", state=ResolutionState.EXACT),
            where=WhereResult(state=ResolutionState.UNRESOLVED),
            file_metadata=FileMetadata()
        )
    )
    assert logger._categorize_proposal(prop_enrich) == "downstream_enrichment"

    # 4. Human review required
    prop_review = RenameProposal(
        tracking_id="44444444",
        original_path="/d.mp3",
        current_filename="d.mp3",
        proposed_filename="d_ID-44444444.mp3",
        proposed_path="/d.mp3",
        mode=RenameMode.INITIAL,
        needs_review=True,
        review_reasons=["Filename date '2011-12-30' conflicts with folder year '2012'"],
        parser_result=ParserResult(
            identity=Identity(tracking_id="44444444", original_filename="d.mp3", original_path="/d.mp3", current_filename="d.mp3", extension=".mp3"),
            context=Context(),
            when=WhenResult(selected_value="2011-12-30", state=ResolutionState.EXACT),
            what=WhatResult(selected_value="SB-1-1-1", state=ResolutionState.EXACT),
            where=WhereResult(place_location="Praha", country_iso2="cz", state=ResolutionState.EXACT),
            file_metadata=FileMetadata()
        )
    )
    assert logger._categorize_proposal(prop_review) == "human_review_required"
