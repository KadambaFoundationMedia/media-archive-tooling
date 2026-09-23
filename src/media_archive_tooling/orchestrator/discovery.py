"""Target discovery, recursive folder traversal, deduplication, and media type filtering."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Set, Union

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
    missing_targets: List[str] = []
    discovered_media: Set[Path] = set()
    skipped_unsupported: Set[Path] = set()

    # Pre-flight existence check: all supplied targets must exist
    for target in targets:
        p = Path(target).expanduser().resolve()
        if not p.exists():
            missing_targets.append(str(target))

    if missing_targets:
        return DiscoveryResult(
            media_files=[],
            skipped_unsupported_files=[],
            missing_targets=missing_targets,
        )

    for target in targets:
        p = Path(target).expanduser().resolve()
        if p.is_file():
            if is_ignored_file(p):
                continue
            if is_supported_media_file(p):
                if registry is not None and hasattr(registry, "is_video_audio_derivative") and registry.is_video_audio_derivative(str(p)):
                    continue
                discovered_media.add(p)
            else:
                skipped_unsupported.add(p)
        elif p.is_dir():
            for root, dirs, files in os.walk(p, followlinks=follow_symlinks):
                root_path = Path(root)
                # Filter out hidden or ignored directories
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for fname in files:
                    file_path = (root_path / fname).resolve()
                    if is_ignored_file(file_path):
                        continue
                    if is_supported_media_file(file_path):
                        if registry is not None and hasattr(registry, "is_video_audio_derivative") and registry.is_video_audio_derivative(str(file_path)):
                            continue
                        discovered_media.add(file_path)
                    else:
                        skipped_unsupported.add(file_path)

    # Stable deterministic sorting
    sorted_media = sorted(discovered_media, key=lambda p: str(p))
    sorted_unsupported = sorted(skipped_unsupported, key=lambda p: str(p))

    return DiscoveryResult(
        media_files=sorted_media,
        skipped_unsupported_files=sorted_unsupported,
        missing_targets=[],
    )
