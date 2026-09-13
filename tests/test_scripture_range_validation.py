from media_archive_tooling.renamer.models import ResolutionState
from media_archive_tooling.renamer.parser.what import parse_what


class RecordingValidator:
    def __init__(self, valid_refs=None):
        self.refs = []
        self.valid_refs = set(valid_refs or [])

    def validate_scripture_reference(self, scripture_ref):
        self.refs.append(scripture_ref)
        if scripture_ref in self.valid_refs:
            return True, "validated"
        return False, "not_found"


def test_bg_range_validates_full_vedabase_range_key():
    validator = RecordingValidator({"BG-13-8-12"})
    result, _, conflict = parse_what(
        "HH KKS BG 13.8-12. 2008_04_09.mp3",
        vedabase_validator=validator,
    )

    assert result.selected_value == "BG-13-8-12"
    assert result.state == ResolutionState.EXACT
    assert conflict is None
    assert validator.refs == ["BG-13-8-12"]


def test_sb_range_validates_full_vedabase_range_key():
    validator = RecordingValidator({"SB-1-19-31-34"})
    result, _, conflict = parse_what(
        "SB 1.19.31-34.mp3",
        vedabase_validator=validator,
    )

    assert result.selected_value == "SB-1-19-31-34"
    assert result.state == ResolutionState.EXACT
    assert conflict is None
    assert validator.refs == ["SB-1-19-31-34"]


def test_descriptive_suffix_is_not_sent_to_vedabase():
    validator = RecordingValidator({"BG-8-19"})
    result, _, conflict = parse_what(
        "BG-8-19-Sundayfeast.mp3",
        vedabase_validator=validator,
        specific_titles_ref=[
            {
                "canonical_what": "Sundayfeast",
                "category": "Sunday Feast",
                "terms": ["Sundayfeast"],
            }
        ],
    )

    assert result.selected_value == "BG-8-19-Sundayfeast"
    assert result.state == ResolutionState.EXACT
    assert conflict is None
    assert validator.refs == ["BG-8-19"]
