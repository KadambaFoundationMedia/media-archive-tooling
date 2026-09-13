from pathlib import Path

import pytest

from media_archive_tooling.renamer.commit_service import RenameCommitService
from media_archive_tooling.renamer.models import (
    Context,
    Identity,
    ParserResult,
    RenameMode,
    RenameProposal,
    ResolutionState,
    WhatResult,
    WhenResult,
    WhereResult,
)
from media_archive_tooling.renamer.registry.registry import LocalRegistry


def make_proposal(source: Path, *, needs_review: bool = False) -> RenameProposal:
    tracking_id = "deadbeef"
    parser = ParserResult(
        identity=Identity(
            tracking_id=tracking_id,
            original_filename=source.name,
            original_path=str(source),
            current_filename=source.name,
            extension=source.suffix.lower(),
        ),
        context=Context(parent_folder=source.parent.name),
        when=WhenResult(selected_value="2015-07-25", precision="day", state=ResolutionState.EXACT),
        what=WhatResult(selected_value="SB-7-2-16", state=ResolutionState.EXACT),
        where=WhereResult(
            place_location="Radhadesh",
            country="Belgium",
            country_iso2="be",
            state=ResolutionState.EXACT,
        ),
        review_reasons=["manual check"] if needs_review else [],
    )
    proposed_filename = "2015-07-25_KKS_SB-7-2-16_Radhadesh-be_ID-deadbeef.mp3"
    return RenameProposal(
        tracking_id=tracking_id,
        original_path=str(source),
        current_filename=source.name,
        proposed_filename=proposed_filename,
        proposed_path=str(source.with_name(proposed_filename)),
        mode=RenameMode.INITIAL,
        needs_review=needs_review,
        review_reasons=list(parser.review_reasons),
        changes_detected=True,
        parser_result=parser,
    )


def test_commit_service_renames_reviewed_proposal_safely(tmp_path):
    source = tmp_path / "original.mp3"
    source.write_bytes(b"media")
    registry = LocalRegistry(tmp_path / "registry.db")
    proposal = make_proposal(source)
    registry.save_proposal(proposal)

    service = RenameCommitService(registry)
    updated = service.commit_file("deadbeef", reviewer="test")

    target = tmp_path / proposal.proposed_filename
    assert not source.exists()
    assert target.read_bytes() == b"media"
    assert updated["status"] == "committed"
    assert updated["current_path"] == str(target)
    assert updated["current_filename"] == proposal.proposed_filename
    actions = registry.get_review_actions("deadbeef")
    assert actions[-1]["action"] == "commit"
    assert actions[-1]["changes"]["filesystem_rename"] is True


def test_commit_service_refuses_unresolved_review(tmp_path):
    source = tmp_path / "original.mp3"
    source.write_bytes(b"media")
    registry = LocalRegistry(tmp_path / "registry.db")
    registry.save_proposal(make_proposal(source, needs_review=True))

    service = RenameCommitService(registry)
    with pytest.raises(ValueError, match="still requires human review"):
        service.commit_file("deadbeef")

    assert source.exists()


def test_commit_service_never_overwrites_existing_target(tmp_path):
    source = tmp_path / "original.mp3"
    source.write_bytes(b"source")
    registry = LocalRegistry(tmp_path / "registry.db")
    proposal = make_proposal(source)
    registry.save_proposal(proposal)
    target = tmp_path / proposal.proposed_filename
    target.write_bytes(b"existing")

    service = RenameCommitService(registry)
    with pytest.raises(ValueError, match="will not be overwritten"):
        service.commit_file("deadbeef")

    assert source.read_bytes() == b"source"
    assert target.read_bytes() == b"existing"
