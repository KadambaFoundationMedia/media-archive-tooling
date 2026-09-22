"""Deterministic code fingerprinting for runtime review logic (Section 6 of purge plan)."""
import hashlib
import os
from pathlib import Path
from typing import Optional, List, Set, Tuple

# Directories and files under src/media_archive_tooling that impact review logic
RELEVANT_SUBDIRS: Set[str] = {
    "renamer",
    "media_db_reviewer",
    "travel_reviewer",
    "media_db_updater",
    "orchestrator",
    "review_portal",
    "adapters",
    "common",
}

RELEVANT_FILES: Set[str] = {
    "config.py",
}

INCLUDED_EXTENSIONS: Set[str] = {
    ".py",
    ".html",
    ".jinja",
    ".jinja2",
    ".js",
    ".css",
    ".json",
}

EXCLUDED_PARTS: Set[str] = {
    "__pycache__",
    ".pytest_cache",
    ".git",
    ".venv",
    ".DS_Store",
}


def compute_review_data_fingerprint(root_dir: Optional[Path] = None) -> str:
    """Compute deterministic SHA-256 hash of runtime code that can affect review results.

    Hashes sorted relative paths and file contents across Tools 1–4, Orchestrator,
    and Review Portal components. Excludes caches, bytecodes, tests, logs, and docs.
    """
    if root_dir is None:
        root_dir = Path(__file__).resolve().parent.parent

    root_dir = root_dir.resolve()
    hasher = hashlib.sha256()

    files_to_hash: List[Tuple[str, Path]] = []

    for entry in root_dir.iterdir():
        if entry.is_file():
            if entry.name in RELEVANT_FILES or (entry.suffix in INCLUDED_EXTENSIONS and entry.name != ".DS_Store"):
                rel_path = entry.relative_to(root_dir).as_posix()
                files_to_hash.append((rel_path, entry))
        elif entry.is_dir() and entry.name in RELEVANT_SUBDIRS:
            for item in entry.rglob("*"):
                if not item.is_file():
                    continue
                if any(part in EXCLUDED_PARTS for part in item.parts):
                    continue
                if item.suffix not in INCLUDED_EXTENSIONS:
                    continue
                rel_path = item.relative_to(root_dir).as_posix()
                files_to_hash.append((rel_path, item))

    files_to_hash.sort(key=lambda t: t[0])

    for rel_path, file_path in files_to_hash:
        hasher.update(rel_path.encode("utf-8"))
        try:
            with open(file_path, "rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
        except OSError:
            pass

    return hasher.hexdigest()
