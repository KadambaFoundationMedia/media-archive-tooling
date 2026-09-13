import shutil
import tempfile
from pathlib import Path
import pytest
from unittest.mock import patch

from media_archive_tooling.cli import main, run_review


@pytest.fixture
def cli_test_env():
    temp_dir = Path(tempfile.mkdtemp())
    source_dir = temp_dir / "media"
    source_dir.mkdir()
    reg_db = temp_dir / "reg.db"
    log_dir = temp_dir / "logs"

    f1 = source_dir / "KKS Bhajans vrindavan sep 2019.mp3"
    f1.write_text("dummy audio")

    yield {
        "temp_dir": temp_dir,
        "source_dir": source_dir,
        "reg_db": reg_db,
        "log_dir": log_dir,
        "f1": f1,
    }
    shutil.rmtree(temp_dir)


def test_cli_bare_invocation_is_dry_run(cli_test_env):
    """Prove that bare invocation `media-archive renamer <path>` does not rename files."""
    env = cli_test_env
    test_args = [
        "media-archive",
        "renamer",
        str(env["source_dir"]),
        "--registry-path", str(env["reg_db"]),
        "--log-dir", str(env["log_dir"]),
    ]
    with patch("sys.argv", test_args):
        main()

    # The original file MUST still exist on disk unchanged
    assert env["f1"].exists(), "Bare invocation mutated file on disk! Expected dry-run by default."


def test_cli_explicit_dry_run_flag(cli_test_env):
    """Test that explicit --dry-run also leaves files untouched."""
    env = cli_test_env
    test_args = [
        "media-archive",
        "renamer",
        str(env["source_dir"]),
        "--dry-run",
        "--registry-path", str(env["reg_db"]),
        "--log-dir", str(env["log_dir"]),
    ]
    with patch("sys.argv", test_args):
        main()

    assert env["f1"].exists()


def test_cli_explicit_commit_flag(cli_test_env):
    """Test that only explicit --commit applies renames to the filesystem."""
    env = cli_test_env
    test_args = [
        "media-archive",
        "renamer",
        str(env["source_dir"]),
        "--commit",
        "--registry-path", str(env["reg_db"]),
        "--log-dir", str(env["log_dir"]),
    ]
    with patch("sys.argv", test_args):
        main()

    # Original filename should no longer exist, renamed file should exist
    assert not env["f1"].exists()
    renamed_files = list(env["source_dir"].glob("*.mp3"))
    assert len(renamed_files) == 1
    assert "2019-09-DD_KKS_Kirtan_Vrindavan-in" in renamed_files[0].name


def test_cli_review_rejects_non_loopback():
    """Test that review command rejects binding to non-loopback host."""
    class DummyArgs:
        host = "0.0.0.0"
        port = 8000

    with pytest.raises(SystemExit) as exc_info:
        run_review(DummyArgs())
    assert exc_info.value.code == 1

