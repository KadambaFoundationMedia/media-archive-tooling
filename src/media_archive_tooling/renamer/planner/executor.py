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
from ...adapters.vedabase import VedabaseValidator
from ...adapters.location import LocationLookupProvider

IGNORED_FILENAMES = {"thumbs.db", "desktop.ini"}
IGNORED_EXTENSIONS = {".tmp", ".temp", ".bak", ".swp", ".part", ".crdownload"}


def is_ignored_file(filename: Any) -> bool:
    """Check if a file should be ignored (hidden, system, temp, backup artifacts)."""
    name = filename.name if isinstance(filename, Path) else str(filename).strip()
    if not name:
        return True
    if name.startswith("."):
        return True
    if name.startswith("~$"):
        return True
    if name.lower() in IGNORED_FILENAMES:
        return True
    suffix = Path(name).suffix.lower()
    if not suffix:
        return True
    if suffix in IGNORED_EXTENSIONS or name.endswith("~"):
        return True
    return False


class BatchExecutor:
    def __init__(
        self,
        registry: LocalRegistry,
        logger: RenamerLogger,
        provider: Optional[BaserowReferenceProvider] = None,
        vedabase_validator: Optional[VedabaseValidator] = None,
        location_provider: Optional[LocationLookupProvider] = None,
        mode: RenameMode = RenameMode.INITIAL,
        media_db_updater_service: Optional[Any] = None,
    ):
        self.registry = registry
        self.logger = logger
        self.provider = provider or BaserowReferenceProvider()
        self.vedabase_validator = vedabase_validator or VedabaseValidator()
        self.location_provider = location_provider or LocationLookupProvider()
        self.mode = mode
        self.media_db_updater_service = media_db_updater_service

        # Load references and initialize parser
        self.provider.load_all_references()
        self.parser = RenamerParser(
            categories_ref=self.provider.get_category_titles(),
            locations_ref=self.provider.get_known_locations(),
            countries_ref=self.provider.get_country_values(),
            vedabase_validator=self.vedabase_validator,
            location_lookup_provider=self.location_provider,
            registry=self.registry,
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
            # Filter regular archive files (extension-agnostic, ignoring system/temp artifacts)
            archive_files = [f for f in files if not is_ignored_file(f)]
            if not archive_files:
                continue

            # Inferred sibling grammar for this folder
            grammar = CollectionGrammar(root_path, archive_files)

            folder_proposals: List[RenameProposal] = []
            for filename in archive_files:
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
            if prop.needs_review or prop.status in ("blocked", "deferred"):
                continue

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
                # Tool 4 is called only after the final filename is committed for the current
                # processing stage, and only for a proposal proven to be the finalized output of the
                # required Tool 2/Tool 3 collaboration (mode == RenameMode.FINALIZE, amendment section 2 & R-037).
                if self.mode == RenameMode.FINALIZE:
                    try:
                        self.registry.save_media_db_sync(
                            tracking_id=prop.tracking_id,
                            sync_status="PENDING_SYNC",
                            attempt_count=0,
                        )
                        if self.media_db_updater_service is not None:
                            self.media_db_updater_service.synchronize(prop.tracking_id, commit=True)
                    except Exception:
                        # Filesystem commit must never be rolled back if Baserow sync fails
                        pass
            except Exception as e:
                prop.status = "failed"
                prop.error = f"Filesystem error during rename: {str(e)}"
                self.registry.update_status(prop.tracking_id, "failed")

        return proposals
