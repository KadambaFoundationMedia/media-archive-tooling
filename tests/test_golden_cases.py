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
    assert prop.proposed_filename.startswith("2011-05-24_KKS_Jaya-Radha-Madhava_Villa-Vrindavan-it")


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
    assert res.where.place_location == "Sydney"
    assert res.where.country_iso2 == "au"


def test_golden_case_prague_collection(parser_and_planner):
    # Prague-Oct-2003/Lekce/A019 03-10-23 BG 3.12 Praha.mp3
    parser, planner = parser_and_planner
    file_path = Path("/archive/Prague-Oct-2003/Lekce/A019 03-10-23 BG 3.12 Praha.mp3")
    res = parser.parse_file(file_path)
    prop = planner.plan_rename(res)

    assert res.when.selected_value == "2003-10-23"
    assert res.what.selected_value == "BG-3-12"
    assert res.where.place_location == "Praha"
    assert res.where.country_iso2 == "cz"
    assert prop.proposed_filename.startswith("2003-10-23_KKS_BG-3-12_Praha-cz")
