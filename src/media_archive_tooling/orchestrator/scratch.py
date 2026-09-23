"""Scratch space lifecycle and preflight verification for archive-scale runs."""
import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional, Union

# Common prefixes for temporary files generated across tools
ABANDONED_SCRATCH_PREFIXES = (
    ".tmp_extract_",
    ".tmp_whisper_",
    "tmp_main_",
)


def compute_file_sha256(path: Union[str, Path]) -> str:
    """Compute streaming SHA-256 for a file."""
    p = Path(path)
    if not p.exists() or not p.is_file():
        return ""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _resolve_existing_ancestor(path: Path) -> Path:
    """Resolve the closest existing directory ancestor of path."""
    curr = path.resolve()
    while not curr.exists() and curr != curr.parent:
        curr = curr.parent
    return curr if curr.exists() else Path(tempfile.gettempdir())


def check_available_scratch_space(path: Union[str, Path], required_bytes: int) -> bool:
    """Verify that the filesystem containing path has at least required_bytes free space."""
    target = Path(path)
    existing = _resolve_existing_ancestor(target)
    try:
        usage = shutil.disk_usage(existing)
        return usage.free >= required_bytes
    except Exception:
        # Fallback if disk_usage fails
        return True


def ensure_sufficient_scratch_space(
    path: Union[str, Path],
    required_bytes: int,
    operation_name: str = "media processing",
) -> None:
    """Preflight check raising RuntimeError if disk space is insufficient."""
    target = Path(path)
    existing = _resolve_existing_ancestor(target)
    try:
        usage = shutil.disk_usage(existing)
        if usage.free < required_bytes:
            raise RuntimeError(
                f"Insufficient disk space for {operation_name} on {existing}: "
                f"required {required_bytes} bytes ({required_bytes / (1024 * 1024):.1f} MB), "
                f"but only {usage.free} bytes ({usage.free / (1024 * 1024):.1f} MB) available."
            )
    except OSError:
        # If filesystem does not support disk_usage, proceed
        pass


def is_scratch_artifact(path: Path) -> bool:
    """Verify whether a path strictly matches owned temporary artifact conventions."""
    name = path.name
    for prefix in ABANDONED_SCRATCH_PREFIXES:
        if name.startswith(prefix):
            return True
    if name.endswith(".tmp") and (name.startswith(".") or name.startswith("tmp_")):
        return True
    return False


def clean_abandoned_scratch(
    scratch_dir: Optional[Union[str, Path]] = None,
    target_roots: Optional[List[Union[str, Path]]] = None,
) -> List[Path]:
    """Clean up owned temporary scratch files from interrupted prior runs.
    
    Safety rule: only removes files or directories matching owned scratch patterns.
    Never removes media originals or arbitrary files.
    """
    cleaned: List[Path] = []
    dirs_to_scan: List[Path] = []

    if scratch_dir:
        p = Path(scratch_dir).resolve()
        if p.exists() and p.is_dir():
            dirs_to_scan.append(p)

    if target_roots:
        for root in target_roots:
            rp = Path(root).resolve()
            if rp.exists() and rp.is_dir() and rp not in dirs_to_scan:
                dirs_to_scan.append(rp)

    for base in dirs_to_scan:
        try:
            for item in base.iterdir():
                if is_scratch_artifact(item):
                    try:
                        if item.is_file() or item.is_symlink():
                            item.unlink(missing_ok=True)
                            cleaned.append(item)
                        elif item.is_dir():
                            shutil.rmtree(item, ignore_errors=True)
                            cleaned.append(item)
                    except Exception:
                        pass
        except Exception:
            pass

    return cleaned


class ScratchTracker:
    """Context manager and tracker for temporary scratch files created during file execution."""

    def __init__(self, scratch_dir: Optional[Union[str, Path]] = None):
        self.scratch_dir = Path(scratch_dir).resolve() if scratch_dir else None
        self.tracked_paths: List[Path] = []

    def register(self, path: Union[str, Path]) -> Path:
        """Register an existing or about-to-be-created scratch path for lifecycle cleanup."""
        p = Path(path).resolve()
        if p not in self.tracked_paths:
            self.tracked_paths.append(p)
        return p

    def create_scratch_file(self, prefix: str = "tmp_main_", suffix: str = ".tmp") -> Path:
        """Create a tracked temporary scratch file."""
        if self.scratch_dir:
            self.scratch_dir.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=self.scratch_dir)
        else:
            fd, name = tempfile.mkstemp(prefix=prefix, suffix=suffix)
        os.close(fd)
        p = Path(name).resolve()
        self.register(p)
        return p

    def cleanup(self) -> None:
        """Remove all tracked scratch files and directories."""
        for p in self.tracked_paths:
            try:
                if p.is_file() or p.is_symlink():
                    p.unlink(missing_ok=True)
                elif p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
            except Exception:
                pass
        self.tracked_paths.clear()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
