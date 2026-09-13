import pytest
from media_archive_tooling.renamer.parser.what import parse_what
from media_archive_tooling.renamer.models import ResolutionState


def test_scripture_sb_parsing():
    res, rem, conflict = parse_what("SB 1.4.5.mp3")
    assert res.selected_value == "SB-1-4-5"
    assert res.category == "Srimad Bhagavatam"
    assert res.state == ResolutionState.EXACT
    assert conflict is None

    res2, _, _ = parse_what("SB 9.23.22.mp3")
    assert res2.selected_value == "SB-9-23-22"

    res3, _, _ = parse_what("Srimad Bhagavatam 3.1.26.mp3")
    assert res3.selected_value == "SB-3-1-26"

    range_res, _, _ = parse_what("SB 1.1.2-4.mp3")
    assert range_res.selected_value == "SB-1-1-2-4"


def test_scripture_bg_parsing():
    res, _, _ = parse_what("BG 3.12.mp3")
    assert res.selected_value == "BG-3-12"
    assert res.category == "Bhagavad Gita"

    res2, _, _ = parse_what("BG 4.38 Praha.mp3")
    assert res2.selected_value == "BG-4-38"

    range_res, _, _ = parse_what("BG 1.1-3.mp3")
    assert range_res.selected_value == "BG-1-1-3"


def test_scripture_cc_range_parsing():
    res, _, _ = parse_what("CC Adi 9.48-50.mp3")
    assert res.selected_value == "CC-Adi-9-48-50"
    assert res.category == "Chaitanya Charitamrita"


def test_dotted_extra_numeric_component_is_not_silently_treated_as_range():
    # The book/category token may still be recognized, but the malformed numeric
    # structure must not become a canonical scripture reference.
    bg, _, _ = parse_what("BG 13.8.12")
    assert bg.selected_value != "BG-13-8-12"
    assert bg.state != ResolutionState.EXACT

    sb, _, _ = parse_what("SB 1.1.2.4")
    assert sb.selected_value != "SB-1-1-2-4"
    assert sb.state != ResolutionState.EXACT


def test_specific_preservation_over_category():
    # JRM should resolve to Jaya-Radha-Madhava
    res, _, _ = parse_what("JRM.mp3")
    assert res.selected_value == "Jaya-Radha-Madhava"
    assert res.state == ResolutionState.STRONG


def test_folder_category_conflict():
    # File has SB scripture, but folder is Kirtany
    res, _, conflict = parse_what("SB 9.23.22.mp3", parent_folder="Kirtany")
    assert res.selected_value == "SB-9-23-22"
    assert conflict is not None
    assert "contradicts parent folder" in conflict
