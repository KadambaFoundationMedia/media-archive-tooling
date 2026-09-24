"""Scratch space lifecycle and preflight verification for archive-scale runs."""
import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, List, Optional, Union

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
    registry: Optional[Any] = None,
    dry_run: bool = False,
    target_roots: Optional[List[Union[str, Path]]] = None,
) -> List[Path]:
    """Clean up owned temporary scratch files from interrupted prior runs (R-054).

    Safety rules:
    - Dry-run must delete nothing.
    - Clean ONLY scratch artifacts proved owned by durable registry identity
      and strictly confined to the tool's designated scratch area.
    - Never sweep arbitrary target roots or perform directory-wide prefix deletion.
    """
    if dry_run or not scratch_dir or registry is None:
        return []

    p_scratch = Path(scratch_dir).resolve()
    if not p_scratch.exists() or not p_scratch.is_dir():
        return []

    cleaned: List[Path] = []
    try:
        recorded_artifacts = registry.get_scratch_artifacts()
    except Exception:
        return []

    for item in recorded_artifacts:
        art_path_str = item.get("artifact_path")
        if not art_path_str:
            continue
        art_path = Path(art_path_str).resolve()

        # Strict containment check: must be inside scratch_dir
        try:
            if not art_path.is_relative_to(p_scratch):
                continue
        except (ValueError, AttributeError):
            continue

        try:
            if art_path.is_file() or art_path.is_symlink():
                art_path.unlink(missing_ok=True)
                cleaned.append(art_path)
            elif art_path.is_dir():
                shutil.rmtree(art_path, ignore_errors=True)
                cleaned.append(art_path)
            registry.remove_scratch_artifact(art_path)
        except Exception:
            pass

    return cleaned


class ScratchTracker:
    """Context manager and tracker for temporary scratch files created during file execution."""

    def __init__(
        self,
        scratch_dir: Optional[Union[str, Path]] = None,
        registry: Optional[Any] = None,
        run_id: Optional[str] = None,
        tracking_id: Optional[str] = None,
    ):
        self.scratch_dir = Path(scratch_dir).resolve() if scratch_dir else None
        self.registry = registry
        self.run_id = run_id
        self.tracking_id = tracking_id
        self.tracked_paths: List[Path] = []

    def set_tracking_id(self, tracking_id: str) -> None:
        """Update tracking_id for subsequently or already tracked paths."""
        self.tracking_id = tracking_id
        if self.registry and hasattr(self.registry, "record_scratch_artifact"):
            for p in self.tracked_paths:
                try:
                    self.registry.record_scratch_artifact(
                        artifact_path=p,
                        run_id=self.run_id,
                        tracking_id=self.tracking_id,
                    )
                except Exception:
                    pass

    def register(self, path: Union[str, Path]) -> Path:
        """Register an existing or about-to-be-created scratch path for lifecycle cleanup."""
        p = Path(path).resolve()
        if p not in self.tracked_paths:
            self.tracked_paths.append(p)
            if self.registry and hasattr(self.registry, "record_scratch_artifact"):
                try:
                    self.registry.record_scratch_artifact(
                        artifact_path=p,
                        run_id=self.run_id,
                        tracking_id=self.tracking_id,
                    )
                except Exception:
                    pass
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
            if self.registry and hasattr(self.registry, "remove_scratch_artifact"):
                try:
                    self.registry.remove_scratch_artifact(p)
                except Exception:
                    pass
        self.tracked_paths.clear()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
