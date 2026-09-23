"""Target discovery, recursive folder traversal, deduplication, and media type filtering."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, List, Optional, Set, Union

from ..renamer.planner.executor import is_ignored_file

SUPPORTED_AUDIO_EXTENSIONS: Set[str] = {
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
    ".ogg",
    ".flac",
    ".wma",
    ".aiff",
    ".alac",
    ".opus",
}

SUPPORTED_VIDEO_EXTENSIONS: Set[str] = {
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".wmv",
    ".webm",
    ".m4v",
}

SUPPORTED_COMPANION_EXTENSIONS: Set[str] = {
    ".txt",
    ".srt",
    ".pdf",
}

SUPPORTED_MEDIA_EXTENSIONS: Set[str] = (
    SUPPORTED_AUDIO_EXTENSIONS
    | SUPPORTED_VIDEO_EXTENSIONS
    | SUPPORTED_COMPANION_EXTENSIONS
)


def is_supported_media_file(path: Union[str, Path]) -> bool:
    """Return True if path has an accepted media or companion extension."""
    suffix = Path(path).suffix.lower()
    return suffix in SUPPORTED_MEDIA_EXTENSIONS


@dataclass
class DiscoveryResult:
    media_files: List[Path] = field(default_factory=list)
    skipped_unsupported_files: List[Path] = field(default_factory=list)
    missing_targets: List[str] = field(default_factory=list)


def validate_targets(targets: List[Union[str, Path]]) -> List[str]:
    """Pre-flight existence check: verify all explicit targets exist (R-055).

    Returns list of missing target path strings, or empty list if all exist.
    """
    missing_targets: List[str] = []
    for target in targets:
        p = Path(target).expanduser().resolve()
        if not p.exists():
            missing_targets.append(str(target))
    return missing_targets


def iter_discover_media_targets(
    targets: List[Union[str, Path]],
    follow_symlinks: bool = False,
    registry: Optional[Any] = None,
    unsupported_callback: Optional[Callable[[Path], None]] = None,
) -> Iterator[Path]:
    """Incremental streaming media target discovery generator (R-055).

    Yields media target paths incrementally as directories are traversed,
    preserving deduplication and avoiding materializing the complete archive in memory.
    """
    seen: Set[Path] = set()

    for target in targets:
        p = Path(target).expanduser().resolve()
        if p.is_file():
            if is_ignored_file(p):
                continue
            if is_supported_media_file(p):
                if registry is not None and hasattr(registry, "is_video_audio_derivative") and registry.is_video_audio_derivative(str(p)):
                    continue
                if p not in seen:
                    seen.add(p)
                    yield p
            else:
                if unsupported_callback:
                    unsupported_callback(p)
        elif p.is_dir():
            for root, dirs, files in os.walk(p, followlinks=follow_symlinks):
                root_path = Path(root)
                # Filter out hidden or ignored directories
                dirs[:] = sorted([d for d in dirs if not d.startswith(".")])
                for fname in sorted(files):
                    file_path = (root_path / fname).resolve()
                    if is_ignored_file(file_path):
                        continue
                    if is_supported_media_file(file_path):
                        if registry is not None and hasattr(registry, "is_video_audio_derivative") and registry.is_video_audio_derivative(str(file_path)):
                            continue
                        if file_path not in seen:
                            seen.add(file_path)
                            yield file_path
                    else:
                        if unsupported_callback:
                            unsupported_callback(file_path)


def discover_media_targets(
    targets: List[Union[str, Path]],
    follow_symlinks: bool = False,
    registry: Optional[Any] = None,
) -> DiscoveryResult:
    """Discover, filter, and deterministically sort media targets.

    Accepts:
    - a single media file
    - multiple explicit files
    - one folder (recursively traversed)
    - multiple folders
    - mixed files and folders

    Validates target existence, eliminates duplicates, filters ignored files,
    suppresses registered video audio derivatives, and separates supported media from unsupported files.
    """
    missing = validate_targets(targets)
    if missing:
        return DiscoveryResult(
            media_files=[],
            skipped_unsupported_files=[],
            missing_targets=missing,
        )

    skipped: List[Path] = []
    media_files: List[Path] = []

    def on_unsupported(p: Path) -> None:
        if p not in skipped:
            skipped.append(p)

    for p in iter_discover_media_targets(
        targets=targets,
        follow_symlinks=follow_symlinks,
        registry=registry,
        unsupported_callback=on_unsupported,
    ):
        media_files.append(p)

    # Sort stably for discover_media_targets callers
    media_files.sort(key=lambda p: str(p))
    skipped.sort(key=lambda p: str(p))

    return DiscoveryResult(
        media_files=media_files,
        skipped_unsupported_files=skipped,
        missing_targets=[],
    )
