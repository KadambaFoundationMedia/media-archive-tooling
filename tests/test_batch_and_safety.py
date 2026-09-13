import shutil
import tempfile
from pathlib import Path
import pytest

from media_archive_tooling.renamer.models import RenameMode
from media_archive_tooling.renamer.registry.registry import LocalRegistry
from media_archive_tooling.renamer.logging.logger import RenamerLogger
from media_archive_tooling.renamer.planner.executor import BatchExecutor


@pytest.fixture
def temp_environment():
    temp_dir = Path(tempfile.mkdtemp())
    source_dir = temp_dir / "media"
    source_dir.mkdir()
    reg_db = temp_dir / "registry.db"
    log_dir = temp_dir / "logs"

    # Create dummy media files
    f1 = source_dir / "KKS Bhajans vrindavan sep 2019.mp3"
    f1.write_text("audio data 1")
    f2 = source_dir / "R09_0004.MP3"
    f2.write_text("audio data 2")

    registry = LocalRegistry(reg_db)
    logger = RenamerLogger(log_dir)
    executor = BatchExecutor(registry=registry, logger=logger, mode=RenameMode.INITIAL)

    yield {
        "temp_dir": temp_dir,
        "source_dir": source_dir,
        "registry": registry,
        "logger": logger,
        "executor": executor,
        "f1": f1,
        "f2": f2,
    }
    shutil.rmtree(temp_dir)


def test_dry_run_mode(temp_environment):
    env = temp_environment
    executor = env["executor"]
    source_dir = env["source_dir"]

    # Run analysis (dry-run)
    proposals = executor.scan_directory(source_dir)
    assert len(proposals) == 2

    # Verify files on disk were NOT modified
    assert env["f1"].exists()
    assert env["f2"].exists()

    # Verify registry records
    files_in_reg = env["registry"].list_files()
    assert len(files_in_reg) == 2


def test_commit_mode(temp_environment):
    env = temp_environment
    executor = env["executor"]
    source_dir = env["source_dir"]

    proposals = executor.scan_directory(source_dir)
    committed = executor.commit_proposals(proposals)

    # Both files should be committed
    assert all(p.status == "committed" for p in committed)

    # Verify original names do not exist, and new names exist
    assert not env["f1"].exists()
    assert not env["f2"].exists()

    for p in committed:
        target = Path(p.proposed_path)
        assert target.exists()
        assert target.read_text().startswith("audio data")


def test_idempotency(temp_environment):
    env = temp_environment
    executor = env["executor"]
    source_dir = env["source_dir"]

    # First run: commit
    proposals = executor.scan_directory(source_dir)
    committed = executor.commit_proposals(proposals)
    tracking_ids_1 = {p.tracking_id for p in committed}

    # Second run on the renamed files
    proposals_2 = executor.scan_directory(source_dir)
    tracking_ids_2 = {p.tracking_id for p in proposals_2}

    # Must preserve exact same tracking IDs
    assert tracking_ids_1 == tracking_ids_2
    # Proposed names should match current names (no renames required)
    for p in proposals_2:
        assert not p.changes_detected


def test_safety_never_silently_overwrite(temp_environment):
    env = temp_environment
    executor = env["executor"]
    source_dir = env["source_dir"]

    proposals = executor.scan_directory(source_dir)
    target_proposal = proposals[0]

    # Pre-create the destination file to simulate collision/conflict
    dest_path = Path(target_proposal.proposed_path)
    dest_path.write_text("existing file that must not be overwritten")

    # Attempt commit
    committed = executor.commit_proposals(proposals)

    # The colliding proposal should fail safely
    failed_prop = next(p for p in committed if p.tracking_id == target_proposal.tracking_id)
    assert failed_prop.status == "failed"
    assert "will not be overwritten" in failed_prop.error
    assert dest_path.read_text() == "existing file that must not be overwritten"
