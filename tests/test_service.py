import tempfile
import shutil
import sqlite3
from pathlib import Path
import pytest

from media_archive_tooling.renamer.registry.registry import LocalRegistry
from media_archive_tooling.renamer.planner.planner import RenamePlanner
from media_archive_tooling.renamer.service import RenamerApplicationService
from media_archive_tooling.renamer.models import (
    ParserResult, Identity, Context, WhenResult, WhatResult, WhereResult,
    ResolutionState, RenameProposal, RenameMode, FileMetadata
)


@pytest.fixture
def service_env():
    temp_dir = Path(tempfile.mkdtemp())
    reg_db = temp_dir / "service_test.db"
    registry = LocalRegistry(reg_db)
    planner = RenamePlanner(mode=RenameMode.INITIAL)
    service = RenamerApplicationService(registry=registry, planner=planner)

    # Seed an initial proposal needing review
    tid = "a1b2c3d4"
    identity = Identity(
        tracking_id=tid,
        original_filename="sample_audio.mp3",
        original_path=f"/archive/sample_audio.mp3",
        current_filename="sample_audio.mp3",
        extension=".mp3"
    )
    res = ParserResult(
        identity=identity,
        context=Context(),
        when=WhenResult(selected_value="YYYY-MM-DD", precision="none", state=ResolutionState.UNRESOLVED),
        who="KKS",
        what=WhatResult(selected_value=None, state=ResolutionState.UNRESOLVED),
        where=WhereResult(place_location=None, state=ResolutionState.UNRESOLVED),
        file_metadata=FileMetadata(),
        unclassified_text=["sample", "audio"],
        conflicts=[],
        review_reasons=["WHAT is unresolved", "WHEN is unresolved"]
    )
    proposal = RenameProposal(
        tracking_id=tid,
        original_path=f"/archive/sample_audio.mp3",
        current_filename="sample_audio.mp3",
        proposed_filename=f"sample_audio_ID-{tid}.mp3",
        proposed_path=f"/archive/sample_audio_ID-{tid}.mp3",
        mode=RenameMode.INITIAL,
        needs_review=True,
        review_reasons=["WHAT is unresolved", "WHEN is unresolved"],
        parser_result=res
    )
    registry.save_proposal(proposal)

    yield {
        "service": service,
        "registry": registry,
        "reg_db": reg_db,
        "tracking_id": tid,
    }
    shutil.rmtree(temp_dir)


def test_service_validation_rejects_invalid_date(service_env):
    service = service_env["service"]
    tid = service_env["tracking_id"]

    with pytest.raises(ValueError) as exc:
        service.apply_review_action(
            tracking_id=tid,
            action="approve",
            when_val="invalid-date-format"
        )
    assert "Invalid date format" in str(exc.value)


def test_service_apply_review_action_regenerates_proposal_and_audits(service_env):
    service = service_env["service"]
    registry = service_env["registry"]
    reg_db = service_env["reg_db"]
    tid = service_env["tracking_id"]

    updated = service.apply_review_action(
        tracking_id=tid,
        action="approve",
        when_val="2015-08-15",
        what_val="Kirtan",
        where_val="Vrindavan-in",
        reviewer="reviewer_alice"
    )

    # Status must be updated
    assert updated["status"] == "approved"
    assert updated["needs_review"] == 0
    assert updated["when_val"] == "2015-08-15"
    assert updated["what_val"] == "Kirtan"
    assert updated["where_val"] == "Vrindavan-in"

    # Proposal must be regenerated through naming planner
    assert updated["proposed_filename"] == f"2015-08-15_KKS_Kirtan_Vrindavan-in_ID-{tid}.mp3"

    # Verify audit history in review_actions table
    with sqlite3.connect(str(reg_db)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT tracking_id, action, reviewer, changes_json FROM review_actions WHERE tracking_id = ?", (tid,))
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == tid
        assert row[1] == "approve"
        assert row[2] == "reviewer_alice"
        assert "2015-08-15" in row[3]

