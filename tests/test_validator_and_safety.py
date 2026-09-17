import tempfile
import shutil
from pathlib import Path
import pytest

from media_archive_tooling.renamer.validator import (
    validate_calendar_date,
    validate_iso2_country,
    validate_canonical_filename,
)
from media_archive_tooling.common.ascii_latin import to_ascii_latin
from media_archive_tooling.renamer.parser.technical import extract_or_generate_tracking_id
from media_archive_tooling.renamer.planner.executor import is_ignored_file
from media_archive_tooling.renamer.planner.planner import RenamePlanner
from media_archive_tooling.renamer.models import (
    ParserResult,
    Identity,
    Context,
    WhenResult,
    WhatResult,
    WhereResult,
    FileMetadata,
    RenameMode,
    ResolutionState,
)


def test_validator_date_bounds_and_validity():
    # Valid dates
    assert validate_calendar_date("2011-05-24")[0] is True
    assert validate_calendar_date("2000-02-29")[0] is True  # Leap year
    assert validate_calendar_date("2019-09-DD")[0] is True  # Incomplete day
    assert validate_calendar_date("YYYY-MM-DD")[0] is True

    # Invalid dates
    assert validate_calendar_date("2011-02-29")[0] is False  # Non-leap year Feb 29
    assert validate_calendar_date("2011-02-30")[0] is False  # Impossible date
    assert validate_calendar_date("2011-04-31")[0] is False  # April has 30 days
    assert validate_calendar_date("1990-01-01")[0] is False  # Pre-1993 outside archive bounds
    assert validate_calendar_date("2025-01-01")[0] is False  # Post-2023 outside archive bounds
    assert validate_calendar_date("not-a-date")[0] is False


def test_validator_iso2_country():
    assert validate_iso2_country("in")[0] is True
    assert validate_iso2_country("CZ")[0] is True
    assert validate_iso2_country("us")[0] is True
    assert validate_iso2_country("xx")[0] is False  # Unknown code
    assert validate_iso2_country("usa")[0] is False  # Not 2 chars


def test_validator_canonical_filename():
    # Valid initial filename
    ok, errors = validate_canonical_filename("2011-05-24_KKS_SB-1-1-1_Praha-cz_ID-a1b2c3d4.mp3", mode=RenameMode.INITIAL, tracking_id="a1b2c3d4")
    assert ok is True

    # Missing extension
    ok, errors = validate_canonical_filename("2011-05-24_KKS_SB-1-1-1_Praha-cz_ID-a1b2c3d4")
    assert ok is False
    assert any("extension" in e for e in errors)

    # Violating one-dot rule
    ok, errors = validate_canonical_filename("2011-05-24_KKS_SB-1.1.1_Praha-cz_ID-a1b2c3d4.mp3")
    assert ok is False
    assert any("one-dot rule" in e for e in errors)

    # Spaces in filename
    ok, errors = validate_canonical_filename("2011-05-24 KKS SB-1-1-1_Praha-cz_ID-a1b2c3d4.mp3")
    assert ok is False
    assert any("spaces" in e for e in errors)

    # Illegal characters
    ok, errors = validate_canonical_filename("2011-05-24_KKS_SB-1-1-1_Praha:cz_ID-a1b2c3d4.mp3")
    assert ok is False
    assert any("illegal" in e for e in errors)

    # Archive convention forbids punctuation even when the host filesystem permits it.
    ok, errors = validate_canonical_filename(
        "2011-05-24_KKS_SB-1-1-1(with-JRM)_Praha-cz_ID-a1b2c3d4.mp3",
        mode=RenameMode.INITIAL,
        tracking_id="a1b2c3d4",
    )
    assert ok is False
    assert any("illegal" in e and "(" in e for e in errors)


def test_transliteration_cyrillic_and_devanagari():
    # Cyrillic
    cyr = "Лекция по Бхагавад-гите"
    trans_cyr = to_ascii_latin(cyr)
    assert "Lektsiya" in trans_cyr
    assert "Bkhagavad" in trans_cyr
    assert not any(ord(c) > 127 for c in trans_cyr)

    # Devanagari
    dev = "कीर्तन"
    trans_dev = to_ascii_latin(dev)
    assert any(sub in trans_dev for sub in ["keertn", "kirt"])
    assert not any(ord(c) > 127 for c in trans_dev)


def test_ignored_system_files():
    assert is_ignored_file(Path(".DS_Store")) is True
    assert is_ignored_file(Path("Thumbs.db")) is True
    assert is_ignored_file(Path("~$mydoc.docx")) is True
    assert is_ignored_file(Path(".git/config")) is True
    assert is_ignored_file(Path("normal_lecture.mp3")) is False
    assert is_ignored_file(Path("notes.txt")) is False
    assert is_ignored_file(Path("transcript.srt")) is False


def test_extension_agnostic_handling():
    """Verify that renamer handles non-audio extensions such as .txt, .srt, .pdf."""
    planner = RenamePlanner(mode=RenameMode.INITIAL)
    for ext in [".txt", ".srt", ".pdf", ".mp4"]:
        res = ParserResult(
            identity=Identity(
                tracking_id="deadbeef",
                original_filename=f"2011-05-24_Lecture{ext}",
                original_path=f"/archive/2011-05-24_Lecture{ext}",
                current_filename=f"2011-05-24_Lecture{ext}",
                extension=ext
            ),
            context=Context(parent_folder="archive", ancestor_folders=[]),
            when=WhenResult(selected_value="2011-05-24", state=ResolutionState.EXACT),
            who="KKS",
            what=WhatResult(selected_value="SB-1-1-1", state=ResolutionState.EXACT),
            where=WhereResult(place_location="Praha", country_iso2="cz", state=ResolutionState.EXACT),
            file_metadata=FileMetadata()
        )
        prop = planner.plan_rename(res)
        assert prop.proposed_filename == f"2011-05-24_KKS_SB-1-1-1_Praha-cz_ID-deadbeef{ext}"


def test_tracking_id_collision_retry():
    """Verify extract_or_generate_tracking_id checks registry and retries on collision."""
    existing_ids = {"11111111", "22222222"}

    class DummyRegistry:
        def get_file(self, tid: str):
            return {"tracking_id": tid} if tid in existing_ids else None

    dummy_reg = DummyRegistry()
    tid, _, was_existing = extract_or_generate_tracking_id("test_file.mp3", registry=dummy_reg)
    assert was_existing is False
    assert tid not in existing_ids
    assert len(tid) == 8


def test_overlength_filename_abbreviation_and_flagging():
    """Verify names over 128 characters use safe abbreviations and flag review if still overlength."""
    planner = RenamePlanner(mode=RenameMode.INITIAL)
    very_long_title = "Sri-Isopanisad-Lecture-On-Invocation-And-Full-Explanation-Of-Everything-In-The-Material-World-And-Transcendental-Abode-Of-Lord-Krishna-In-Full-Detail"
    res = ParserResult(
        identity=Identity(
            tracking_id="12345678",
            original_filename="long.mp3",
            original_path="/archive/long.mp3",
            current_filename="long.mp3",
            extension=".mp3"
        ),
        context=Context(parent_folder="archive", ancestor_folders=[]),
        when=WhenResult(selected_value="2011-05-24", state=ResolutionState.EXACT),
        who="KKS",
        what=WhatResult(selected_value=very_long_title, state=ResolutionState.EXACT),
        where=WhereResult(place_location="Praha", country_iso2="cz", state=ResolutionState.EXACT),
        file_metadata=FileMetadata()
    )
    prop = planner.plan_rename(res)
    assert prop.needs_review is True
    assert any("128 characters" in r for r in prop.review_reasons)
