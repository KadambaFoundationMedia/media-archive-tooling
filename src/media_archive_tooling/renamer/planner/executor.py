"""Batch executor coordinating Analysis phase, Commit phase, and safety validations."""
import os
import shutil
import time
from pathlib import Path
from typing import List, Optional, Dict, Any

from ..models import RenameProposal, RenameMode, ParserResult
from ..parser.engine import RenamerParser
from ..parser.collection import CollectionGrammar
from .planner import RenamePlanner
from ..registry.registry import LocalRegistry
from ..logging.logger import RenamerLogger
from ...adapters.baserow import BaserowReferenceProvider

MEDIA_EXTENSIONS = {
    ".mp3", ".wav", ".wma", ".m4a", ".aac", ".flac", ".ogg",
    ".mp4", ".avi", ".mkv", ".mov", ".mpg", ".wmv"
}


class BatchExecutor:
    def __init__(
        self,
        registry: LocalRegistry,
        logger: RenamerLogger,
        provider: Optional[BaserowReferenceProvider] = None,
        mode: RenameMode = RenameMode.INITIAL
    ):
        self.registry = registry
        self.logger = logger
        self.provider = provider or BaserowReferenceProvider()
        self.mode = mode

        # Load references and initialize parser
        self.provider.load_all_references()
        self.parser = RenamerParser(
            categories_ref=self.provider.get_category_titles(),
            locations_ref=self.provider.get_known_locations(),
            countries_ref=self.provider.get_country_values()
        )
        self.planner = RenamePlanner(mode=self.mode)

    def scan_directory(self, target_dir: Path) -> List[RenameProposal]:
        """Phase 1: Analysis Phase.
        
        Scans directory tree, analyzes sibling grammar per folder, parses each file,
        generates proposals, detects collisions, saves state to local registry, and logs.
        """
        start_time = time.time()
        proposals: List[RenameProposal] = []

        # Walk directory
        for root, dirs, files in os.walk(target_dir):
            root_path = Path(root)
            # Filter media files
            media_files = [f for f in files if Path(f).suffix.lower() in MEDIA_EXTENSIONS and not f.startswith(".")]
            if not media_files:
                continue

            # Inferred sibling grammar for this folder
            grammar = CollectionGrammar(root_path, media_files)

            folder_proposals: List[RenameProposal] = []
            for filename in media_files:
                file_path = root_path / filename
                # Parse
                parser_res = self.parser.parse_file(
                    file_path=file_path,
                    mode=self.mode,
                    collection_grammar=grammar
                )
                # Plan proposal
                prop = self.planner.plan_rename(parser_res)
                folder_proposals.append(prop)

            # Detect & resolve collisions in folder
            resolved_folder_proposals = self.planner.resolve_batch_collisions(folder_proposals)
            for prop in resolved_folder_proposals:
                self.registry.save_proposal(prop)
                proposals.append(prop)

        duration = time.time() - start_time
        self.logger.log_proposals(proposals, duration_secs=duration)
        return proposals

    def commit_proposals(self, proposals: List[RenameProposal]) -> List[RenameProposal]:
        """Phase 2: Commit Phase.
        
        Applies safe atomic renames on filesystem and updates local registry.
        Never silently overwrites an existing destination file.
        Isolates per-file failures so independent files succeed.
        """
        for prop in proposals:
            if not prop.changes_detected:
                prop.status = "skipped_unchanged"
                continue

            orig_path = Path(prop.original_path)
            target_path = Path(prop.proposed_path)

            if not orig_path.exists():
                prop.status = "failed"
                prop.error = f"Source file does not exist: {orig_path}"
                continue

            if target_path.exists() and orig_path != target_path:
                prop.status = "failed"
                prop.error = f"Safety error: target file already exists and will not be overwritten: {target_path}"
                continue

            try:
                # Perform filesystem rename
                orig_path.rename(target_path)
                prop.status = "committed"
                prop.current_filename = target_path.name
                # Record in local registry audit trail
                self.registry.record_commit(prop, target_path)
            except Exception as e:
                prop.status = "failed"
                prop.error = f"Filesystem error during rename: {str(e)}"
                self.registry.update_status(prop.tracking_id, "failed")

        return proposals
