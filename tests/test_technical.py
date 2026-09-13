import pytest
from media_archive_tooling.renamer.parser.technical import (
    extract_or_generate_tracking_id, extract_technical_metadata
)


def test_tracking_id_generation_and_reuse():
    # New ID generated
    id1, clean1, was_existing1 = extract_or_generate_tracking_id("R09_0004.MP3")
    assert len(id1) == 8
    assert not was_existing1
    assert clean1 == "R09_0004.MP3"

    # Existing ID recognized and reused
    filename_with_id = f"2019-09-DD_KKS_Kirtan_Vrindavan-in_ID-{id1}.mp3"
    id2, clean2, was_existing2 = extract_or_generate_tracking_id(filename_with_id)
    assert id2 == id1
    assert was_existing2
    assert f"_ID-{id1}" not in clean2


def test_edited_flag_extraction():
    cleaned, meta = extract_technical_metadata("2012-05-13_KKS_BG-8-19-Sundayfeast_Sydney_edited")
    assert meta.edited is True
    assert "_edited" not in cleaned


def test_source_id_and_combination():
    cleaned, meta = extract_technical_metadata("A019 03-10-23 BG 3.12 Praha")
    assert meta.source_sequence_id == "A019"

    cleaned2, meta2 = extract_technical_metadata("JRM and class 24/5/11 villa vrindavan")
    assert meta2.possible_combination is True
