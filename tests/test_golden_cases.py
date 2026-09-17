import pytest
from pathlib import Path
from media_archive_tooling.renamer.parser.engine import RenamerParser
from media_archive_tooling.renamer.planner.planner import RenamePlanner
from media_archive_tooling.renamer.models import RenameMode, ResolutionState


@pytest.fixture
def parser_and_planner():
    parser = RenamerParser()
    planner = RenamePlanner(mode=RenameMode.INITIAL)
    return parser, planner


def test_golden_case_1(parser_and_planner):
    # KKS-10-9-10 - SB 1.4.5.mp3
    parser, planner = parser_and_planner
    res = parser.parse_file(Path("/archive/KKS-10-9-10 - SB 1.4.5.mp3"))
    prop = planner.plan_rename(res)

    assert res.when.selected_value == "2010-09-10"
    assert "2010-10-09" in res.when.alternatives
    assert res.what.selected_value == "SB-1-4-5"
    assert prop.proposed_filename.startswith("2010-09-10_KKS_SB-1-4-5")
    assert f"_ID-{res.identity.tracking_id}.mp3" in prop.proposed_filename


def test_golden_case_2(parser_and_planner):
    # KKS Bhajans vrindavan sep 2019.mp3
    parser, planner = parser_and_planner
    res = parser.parse_file(Path("/archive/KKS Bhajans vrindavan sep 2019.mp3"))
    prop = planner.plan_rename(res)

    assert res.when.selected_value == "2019-09-DD"
    assert res.what.selected_value == "Kirtan"
    assert res.where.place_location == "Vrindavan"
    assert res.where.country_iso2 == "in"
    assert prop.proposed_filename.startswith("2019-09-DD_KKS_Kirtan_Vrindavan-in")


def test_golden_case_3(parser_and_planner):
    # JRM and class 24/5/11 villa vrindavan.mp3
    parser, planner = parser_and_planner
    res = parser.parse_file(Path("/archive/jrm.mp3"), raw_filename="JRM and class 24/5/11 villa vrindavan.mp3")
    prop = planner.plan_rename(res)

    assert res.when.selected_value == "2011-05-24"
    assert res.what.selected_value == "Jaya-Radha-Madhava"
    assert res.where.place_location == "Villa-Vrindavan"
    assert res.where.country_iso2 == "it"
    assert res.file_metadata.possible_combination is True
    # A secondary combination marker must not discard valid primary-class evidence.
    assert prop.proposed_filename == f"2011-05-24_KKS_Jaya-Radha-Madhava_Villa-Vrindavan-it_ID-{res.identity.tracking_id}.mp3"


def test_dotted_sb_combination_uses_primary_class_for_canonical_filename():
    source = Path(
        "/archive/From JVD (8.9.11)/"
        "KKS_S.B. 1.19.31(with Radha Madhava)_Oslo_29.8.11.WMA"
    )
    parser = RenamerParser()
    result = parser.parse_file(source)
    proposal = RenamePlanner(mode=RenameMode.FINALIZE).plan_rename(result)

    assert result.when.selected_value == "2011-08-29"
    assert result.what.selected_value == "SB-1-19-31"
    assert result.what.category == "Srimad Bhagavatam"
    assert result.where.place_location == "Oslo"
    assert result.where.country_iso2 == "no"
    assert result.file_metadata.possible_combination is True
    assert proposal.proposed_filename == "2011-08-29_KKS_SB-1-19-31_Oslo-no.wma"


def test_golden_case_4_recorder_unresolved(parser_and_planner):
    # R09_0004.MP3
    parser, planner = parser_and_planner
    res = parser.parse_file(Path("/archive/R09_0004.MP3"))
    prop = planner.plan_rename(res)

    # Must retain useful identifier and not invent dates
    assert "R09-0004" in prop.proposed_filename or "R09_0004" in prop.proposed_filename
    assert f"_ID-{res.identity.tracking_id}.mp3" in prop.proposed_filename


def test_golden_case_5_edited(parser_and_planner):
    # 2012-05-13_KKS_BG-8-19-Sundayfeast_Sydney_edited.mp3
    parser, planner = parser_and_planner
    res = parser.parse_file(Path("/archive/2012-05-13_KKS_BG-8-19-Sundayfeast_Sydney_edited.mp3"))
    prop = planner.plan_rename(res)

    assert res.when.selected_value == "2012-05-13"
    assert res.file_metadata.edited is True
    assert res.what.selected_value == "BG-8-19-Sundayfeast"
    assert res.where.place_location == "Sydney"
    assert res.where.country_iso2 == "au"
    # Prior to Baserow check, _edited MUST be preserved
    assert "_edited_ID-" in prop.proposed_filename

    # After Baserow check is complete, _edited is removed
    res.file_metadata.baserow_check_complete = True
    prop_after = planner.plan_rename(res)
    assert "_edited" not in prop_after.proposed_filename
    assert prop_after.proposed_filename.startswith("2012-05-13_KKS_BG-8-19-Sundayfeast_Sydney-au_ID-")


def test_unresolved_what_does_not_fabricate_recording(parser_and_planner):
    # Known WHEN and WHERE, but unresolved WHAT
    parser, planner = parser_and_planner
    res = parser.parse_file(Path("/archive/2012-05-13_Sydney.mp3"))
    prop = planner.plan_rename(res)

    assert res.what.selected_value is None
    assert res.what.state == ResolutionState.UNRESOLVED
    assert "Recording" not in prop.proposed_filename
    # Must preserve useful current wording + tracking ID
    assert prop.proposed_filename == f"2012-05-13_Sydney_ID-{res.identity.tracking_id}.mp3"


def test_golden_case_prague_collection(parser_and_planner):
    from media_archive_tooling.renamer.parser.collection import CollectionGrammar
    parser, planner = parser_and_planner
    dir_path = Path("/archive/Prague-Oct-2003/Lekce")
    filenames = [
        "A019 03-10-23 BG 3.12 Praha.mp3",
        "A020 03-10-24 SB 9.23.22 Praha.mp3",
        "A022F 03-10-25 SB 4.9.11 Nezkracena Farma KD.mp3",
    ]
    grammar = CollectionGrammar(dir_path, filenames)

    # 1. A019
    res1 = parser.parse_file(dir_path / filenames[0], collection_grammar=grammar)
    prop1 = planner.plan_rename(res1)
    assert res1.file_metadata.source_sequence_id == "A019"
    assert res1.when.selected_value == "2003-10-23"
    assert res1.what.selected_value == "BG-3-12"
    assert res1.where.place_location == "Praha"
    assert res1.where.country_iso2 == "cz"
    assert prop1.proposed_filename.startswith("2003-10-23_KKS_BG-3-12_Praha-cz")

    # 2. A020
    res2 = parser.parse_file(dir_path / filenames[1], collection_grammar=grammar)
    prop2 = planner.plan_rename(res2)
    assert res2.file_metadata.source_sequence_id == "A020"
    assert res2.when.selected_value == "2003-10-24"
    assert res2.what.selected_value == "SB-9-23-22"
    assert res2.where.place_location == "Praha"
    assert prop2.proposed_filename.startswith("2003-10-24_KKS_SB-9-23-22_Praha-cz")

    # 3. A022F
    res3 = parser.parse_file(dir_path / filenames[2], collection_grammar=grammar)
    prop3 = planner.plan_rename(res3)
    assert res3.file_metadata.source_sequence_id == "A022F"
    assert res3.when.selected_value == "2003-10-25"
    assert res3.what.selected_value == "SB-4-9-11"
    # R-014: Direct filename evidence 'Farma KD' outranks folder context 'Praha' -> Krsna-Dvur-cz
    assert res3.where.place_location == "Krsna-Dvur"
    assert res3.where.country_iso2 == "cz"
    assert prop3.proposed_filename.startswith("2003-10-25_KKS_SB-4-9-11_Krsna-Dvur-cz")
    assert "Nezkracena" in res3.unclassified_text


def test_golden_case_duben_2008_folder_grammar(parser_and_planner):
    """Sequence indices like 07, 08 must not become calendar days."""
    from media_archive_tooling.renamer.parser.collection import CollectionGrammar
    parser, planner = parser_and_planner
    dir_path = Path("/archive/KKS DUBEN 2008 MP3")
    filenames = [
        "07 KKS PRUHON.mp3",
        "08 KKS SB 3.1.26.mp3",
    ]
    grammar = CollectionGrammar(dir_path, filenames)

    # File 07: sequence index 07, not 7th of April!
    res07 = parser.parse_file(dir_path / filenames[0], collection_grammar=grammar)
    prop07 = planner.plan_rename(res07)
    assert res07.file_metadata.source_sequence_id == "07"
    assert res07.when.selected_value == "2008-04-DD"
    assert res07.where.place_location == "Pruhonice"
    assert res07.where.country_iso2 == "cz"
    assert res07.what.selected_value is None
    assert "Recording" not in prop07.proposed_filename
    # Under R-022/R-023, incomplete-but-safe files continue without human review; generic unresolved WHAT routes to Tools 2 and 5
    assert prop07.needs_review is False
    assert "tool_7_class_classification" not in res07.downstream_routing
    assert "tool_2_media_database_review" in res07.downstream_routing
    assert "tool_5_content_discovery" in res07.downstream_routing
    assert "WHAT is unresolved" in res07.diagnostic_notes

    # File 08: sequence index 08, not 8th of April!
    res08 = parser.parse_file(dir_path / filenames[1], collection_grammar=grammar)
    prop08 = planner.plan_rename(res08)
    assert res08.file_metadata.source_sequence_id == "08"
    assert res08.when.selected_value == "2008-04-DD"
    assert res08.what.selected_value == "SB-3-1-26"
    assert res08.where.place_location is None
    assert res08.where.country_iso2 == "cz"
    assert prop08.proposed_filename.startswith("2008-04-DD_KKS_SB-3-1-26_cz")

    # File 02: dotted SB syntax plus Czech month context establishes country,
    # while location correctly remains open for later processing.
    filename02 = "02 KKS. SB. 3.1.20.mp3"
    res02 = parser.parse_file(dir_path / filename02, collection_grammar=grammar)
    prop02 = RenamePlanner(mode=RenameMode.FINALIZE).plan_rename(res02)
    assert res02.file_metadata.source_sequence_id == "02"
    assert res02.when.selected_value == "2008-04-DD"
    assert res02.what.selected_value == "SB-3-1-20"
    assert res02.where.place_location is None
    assert res02.where.country == "Czech Republic"
    assert res02.where.country_iso2 == "cz"
    assert prop02.proposed_filename == "2008-04-DD_KKS_SB-3-1-20_cz.mp3"
