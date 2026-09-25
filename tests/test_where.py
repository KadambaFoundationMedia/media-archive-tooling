import pytest
from media_archive_tooling.renamer.parser.where import WhereResolver
from media_archive_tooling.renamer.parser.engine import RenamerParser
from media_archive_tooling.renamer.models import ResolutionState
from media_archive_tooling.common.ascii_latin import to_ascii_latin
from pathlib import Path


def test_ascii_latin_transliteration():
    assert to_ascii_latin("Zürich") == "Zurich"
    assert to_ascii_latin("Průhonice") == "Pruhonice"
    assert to_ascii_latin("Málaga") == "Malaga"
    assert to_ascii_latin("Česká") == "Ceska"


def test_where_canonical_and_iso2():
    resolver = WhereResolver()

    # Vrindavan -> India -> in
    res, _ = resolver.resolve("vrindavan sep 2019.mp3")
    assert res.place_location == "Vrindavan"
    assert res.country_iso2 == "in"
    assert res.state == ResolutionState.EXACT

    # Villa Vrindavan -> Italy -> it
    res2, _ = resolver.resolve("villa vrindavan.mp3")
    assert res2.place_location == "Villa-Vrindavan"
    assert res2.country_iso2 == "it"

    # Praha -> Czech Republic -> cz
    res3, _ = resolver.resolve("BG 3.12 Praha.mp3")
    assert res3.place_location == "Praha"
    assert res3.country_iso2 == "cz"


def test_where_from_folder_context():
    resolver = WhereResolver()
    res, _ = resolver.resolve("class.mp3", parent_folder="Stockholm visit March 2015")
    assert res.place_location == "Stockholm"
    assert res.country_iso2 == "se"
    assert res.state == ResolutionState.STRONG


def test_czech_month_folder_context_sets_country_without_inventing_location():
    resolver = WhereResolver()
    res, remaining = resolver.resolve(
        "02 KKS. SB. 3.1.20.mp3",
        parent_folder="KKS DUBEN 2008 MP3",
    )

    assert res.place_location is None
    assert res.country == "Czech Republic"
    assert res.country_iso2 == "cz"
    assert res.state == ResolutionState.STRONG
    assert res.evidence[0].source == "czech_language_context"
    assert remaining == "02 KKS. SB. 3.1.20.mp3"


def test_bounded_fuzzy_where():
    resolver = WhereResolver()
    # Typo "PRUHONICCE" fuzzy matches "Pruhonice"
    res, _ = resolver.resolve("07 KKS PRUHONICCE.mp3")
    assert res.place_location == "Pruhonice"
    assert res.country_iso2 == "cz"
    assert res.state == ResolutionState.PROVISIONAL


def test_filename_country_suffix_constrains_location_before_online_lookup():
    class WrongCountryLookup:
        def __init__(self):
            self.calls = 0

        def lookup(self, query):
            self.calls += 1
            return {"canonical_place": "杉並区", "country": "Japan", "country_iso2": "jp"}

    lookup = WrongCountryLookup()
    resolver = WhereResolver(location_lookup_provider=lookup)

    result, remaining = resolver.resolve("Simhachalam_de")
    assert result.place_location == "Simhachalam"
    assert result.country == "Germany"
    assert result.country_iso2 == "de"
    assert result.state == ResolutionState.EXACT
    assert remaining == ""
    assert lookup.calls == 0

    unknown, remaining_unknown = resolver.resolve("UnlistedVillage_de")
    assert unknown.place_location is None
    assert unknown.country_iso2 == "de"
    assert unknown.state == ResolutionState.STRONG
    assert remaining_unknown == "UnlistedVillage"
    assert lookup.calls == 0

    conflicting, _ = resolver.resolve("Oslo_de")
    assert conflicting.place_location is None
    assert conflicting.country_iso2 == "de"
    assert conflicting.state == ResolutionState.AMBIGUOUS
    assert "Oslo-no" in conflicting.alternatives
    assert lookup.calls == 0


def test_cc_talk_sample_country_does_not_follow_unconstrained_geocoder():
    media = Path("sample-files/cutting-samples/2012-01-02_KKS_CC-Talk_Simhachalam_de.mp3")
    result = RenamerParser().parse_file(media)
    assert result.what.evidence[0].source == "category_title_terms"
    assert result.where.place_location == "Simhachalam"
    assert result.where.country_iso2 == "de"
    assert result.where.state == ResolutionState.EXACT
