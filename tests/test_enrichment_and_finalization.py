import tempfile
import shutil
from pathlib import Path
import pytest

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
    EnrichmentEvidence,
)
from media_archive_tooling.renamer.registry.registry import LocalRegistry
from media_archive_tooling.renamer.planner.planner import RenamePlanner
from media_archive_tooling.renamer.service import RenamerApplicationService


@pytest.fixture
def enrich_env():
    temp_dir = Path(tempfile.mkdtemp())
    reg_db = temp_dir / "enrich_reg.db"
    registry = LocalRegistry(reg_db)

    # Register an unresolved combination file
    res = ParserResult(
        identity=Identity(
            tracking_id="c0ffee01",
            original_filename="JRM and class 24/5/11 villa vrindavan.mp3",
            original_path=str(temp_dir / "jrm.mp3"),
            current_filename="JRM and class 24/5/11 villa vrindavan.mp3",
            extension=".mp3"
        ),
        context=Context(parent_folder="archive", ancestor_folders=[]),
        when=WhenResult(selected_value="2011-05-24", state=ResolutionState.EXACT),
        who="KKS",
        what=WhatResult(selected_value="Jaya-Radha-Madhava", state=ResolutionState.EXACT),
        where=WhereResult(place_location="Villa-Vrindavan", country_iso2="it", state=ResolutionState.EXACT),
        file_metadata=FileMetadata(possible_combination=True),
        review_reasons=["File has combination clue (possible multiple recordings)"]
    )
    planner = RenamePlanner(mode=RenameMode.INITIAL)
    prop = planner.plan_rename(res)
    registry.save_proposal(prop)

    service = RenamerApplicationService(registry=registry)
    yield {
        "temp_dir": temp_dir,
        "registry": registry,
        "service": service,
        "tracking_id": "c0ffee01",
    }
    shutil.rmtree(temp_dir)


def test_enrichment_updates_combination_and_reasons(enrich_env):
    service = enrich_env["service"]
    tid = enrich_env["tracking_id"]

    # Initial proposed name is unsplit combination (stem + ID)
    f_init = service.get_file(tid)
    assert "_ID-c0ffee01" in f_init["proposed_filename"]
    assert "JRM and class" in f_init["proposed_filename"]
    assert f_init["needs_review"] == 1

    # Apply enrichment from Tool 5 (Audio Splitter) marking split complete
    evidence = EnrichmentEvidence(
        tracking_id=tid,
        source_tool="tool_5_splitter",
        what_val="SB-1-2-3",
        possible_combination=False,
        details="Split into individual recording"
    )
    updated = service.apply_enrichment(evidence)

    assert updated["status"] == "enriched"
    assert updated["what_val"] == "SB-1-2-3"
    assert updated["needs_review"] == 0
    # Now it forms canonical name with ID
    assert updated["proposed_filename"].startswith("2011-05-24_KKS_SB-1-2-3_Villa-Vrindavan-it_ID-c0ffee01.mp3")

    # Verify audit history
    history = enrich_env["registry"].get_history(tid)
    assert len(history) == 1
    assert history[0]["reviewer"] == "tool_5_splitter"
    assert history[0]["changes"]["what_val"] == "SB-1-2-3"


def test_finalization_strips_id_and_resolves_disk_collision(enrich_env):
    temp_dir = enrich_env["temp_dir"]
    planner_fin = RenamePlanner(mode=RenameMode.FINALIZE)

    # 1. Non-colliding finalization strips ID
    res1 = ParserResult(
        identity=Identity(tracking_id="aaaa1111", original_filename="lec1.mp3", original_path=str(temp_dir / "lec1.mp3"), current_filename="lec1.mp3", extension=".mp3"),
        context=Context(parent_folder="archive", ancestor_folders=[]),
        when=WhenResult(selected_value="2011-05-24", state=ResolutionState.EXACT),
        who="KKS",
        what=WhatResult(selected_value="SB-1-1-1", state=ResolutionState.EXACT),
        where=WhereResult(place_location="Praha", country_iso2="cz", state=ResolutionState.EXACT),
        file_metadata=FileMetadata(baserow_check_complete=True)
    )
    prop1 = planner_fin.plan_rename(res1)
    assert prop1.proposed_filename == "2011-05-24_KKS_SB-1-1-1_Praha-cz.mp3"

    # 2. Collision with file existing on disk assigns next counter
    # Create the target file on disk
    target_disk = temp_dir / "2011-05-24_KKS_SB-1-1-1_Praha-cz.mp3"
    target_disk.touch()

    # Create dummy source file
    source_disk = temp_dir / "lec1.mp3"
    source_disk.touch()

    # Another recording resolved to the same base name
    res2 = ParserResult(
        identity=Identity(tracking_id="bbbb2222", original_filename="lec2.mp3", original_path=str(source_disk), current_filename="lec2.mp3", extension=".mp3"),
        context=Context(parent_folder="archive", ancestor_folders=[]),
        when=WhenResult(selected_value="2011-05-24", state=ResolutionState.EXACT),
        who="KKS",
        what=WhatResult(selected_value="SB-1-1-1", state=ResolutionState.EXACT),
        where=WhereResult(place_location="Praha", country_iso2="cz", state=ResolutionState.EXACT),
        file_metadata=FileMetadata(baserow_check_complete=True)
    )
    prop2 = planner_fin.plan_rename(res2)
    resolved_props = planner_fin.resolve_batch_collisions([prop2])
    # Must detect pre-existing file on disk and assign -02
    assert resolved_props[0].proposed_filename == "2011-05-24_KKS_SB-1-1-1_Praha-cz-02.mp3"
