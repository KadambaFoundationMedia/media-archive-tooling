import pytest
from media_archive_tooling.renamer.parser.when import (
    expand_two_digit_year, is_valid_archive_year, parse_when
)
from media_archive_tooling.renamer.models import ResolutionState


def test_two_digit_year_expansion():
    # 93-99 -> 1993-1999
    assert expand_two_digit_year(93) == 1993
    assert expand_two_digit_year(99) == 1999
    # 00-23 -> 2000-2023
    assert expand_two_digit_year(0) == 2000
    assert expand_two_digit_year(10) == 2010
    assert expand_two_digit_year(23) == 2023
    # 24-92 are invalid
    assert expand_two_digit_year(24) is None
    assert expand_two_digit_year(50) is None
    assert expand_two_digit_year(92) is None


def test_archive_year_boundaries():
    assert is_valid_archive_year(1993)
    assert is_valid_archive_year(2023)
    assert not is_valid_archive_year(1992)
    assert not is_valid_archive_year(2024)


def test_iso_date_parsing():
    res, rem = parse_when("2010-09-10_KKS_SB-1-4-5.mp3")
    assert res.selected_value == "2010-09-10"
    assert res.state == ResolutionState.EXACT
    assert res.precision == "day"

    # Explicit partials
    res2, _ = parse_when("2019-09-DD_KKS_Kirtan.mp3")
    assert res2.selected_value == "2019-09-DD"
    assert res2.precision == "partial"


def test_multilingual_month_parsing():
    # Czech: duben = April (04)
    res_cz, _ = parse_when("KKS DUBEN 2008.mp3")
    assert res_cz.selected_value == "2008-04-DD"
    assert res_cz.precision == "month"

    # English: sep 2019
    res_en, _ = parse_when("KKS Bhajans vrindavan sep 2019.mp3")
    assert res_en.selected_value == "2019-09-DD"

    # With day: 27_8_15
    res_day, _ = parse_when("HH Kadamba Kanana Swami - SB 3.6.6 - Sweden - 27_8_15.mp3")
    assert res_day.selected_value == "2015-08-27"


def test_ambiguous_numeric_date():
    # 10-9-10 can be 2010-09-10 or 2010-10-09
    res, _ = parse_when("KKS-10-9-10 - SB 1.4.5.mp3", us_context=False)
    assert res.selected_value == "2010-09-10"
    assert "2010-10-09" in res.alternatives
    assert res.state == ResolutionState.PROVISIONAL


def test_dotted_scripture_numbers_do_not_hide_later_explicit_date():
    result, _ = parse_when(
        "KKS_S.B. 1.19.30_28.8.11_Oslo_ .WMA",
        parent_folder="From JVD (8.9.11)",
    )

    assert result.selected_value == "2011-08-28"
    assert result.state == ResolutionState.STRONG
    assert any(e.raw_value == "28.8.11" for e in result.evidence)
    folder_evidence = [e for e in result.evidence if e.source == "folder_numeric_context"]
    assert len(folder_evidence) == 1
    assert folder_evidence[0].raw_value == "From JVD (8.9.11)"
