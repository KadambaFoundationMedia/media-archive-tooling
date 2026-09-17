"""Reusable application service for committing reviewed Tool 1 rename proposals."""
from pathlib import Path
from typing import Any, Dict, Optional

from .models import ParserResult, RenameMode, RenameProposal
from .registry.registry import LocalRegistry
from .validator import validate_canonical_filename


class RenameCommitService:
    """Apply already-analyzed rename proposals to the filesystem safely.

    The review portal and other front ends must use this service rather than
    mutating files or SQLite state directly. A commit is allowed only after all
    human-review blockers have been resolved.
    """

    def __init__(
        self,
        registry: LocalRegistry,
        mode: RenameMode = RenameMode.INITIAL,
        media_db_updater_service: Optional[Any] = None,
    ):
        self.registry = registry
        self.mode = mode
        self.media_db_updater_service = media_db_updater_service

    def commit_file(self, tracking_id: str, reviewer: str = "human") -> Dict[str, Any]:
        record = self.registry.get_file(tracking_id)
        if not record:
            raise ValueError(f"File with tracking_id '{tracking_id}' not found in registry")

        if record.get("needs_review"):
            raise ValueError(
                f"File '{record.get('original_filename') or tracking_id}' still requires human review; approve it before committing"
            )
        if record.get("status") == "deferred":
            raise ValueError(
                f"File '{record.get('original_filename') or tracking_id}' is deferred; approve or edit it before committing"
            )
        if record.get("status") == "committed":
            return record

        proposed_filename = (record.get("proposed_filename") or "").strip()
        if not proposed_filename:
            raise ValueError(
                f"File '{record.get('original_filename') or tracking_id}' has no proposed filename to commit"
            )

        ok, errors = validate_canonical_filename(
            proposed_filename,
            mode=self.mode,
            tracking_id=tracking_id,
        )
        if not ok:
            raise ValueError("; ".join(errors))

        source_path = Path(record.get("current_path") or record["original_path"])
        target_path = source_path.with_name(proposed_filename)
        previous_values = {
            "current_path": str(source_path),
            "current_filename": record.get("current_filename"),
            "proposed_filename": proposed_filename,
            "status": record.get("status"),
        }

        if not source_path.exists():
            raise ValueError(f"Source file does not exist: {source_path}")
        if target_path.exists() and target_path != source_path:
            raise ValueError(
                f"Safety error: target file already exists and will not be overwritten: {target_path}"
            )

        parser_res = ParserResult.model_validate(record["parser_result"])

        # A proposal that already equals the physical filename is still complete;
        # record the completion without inventing a filesystem rename event.
        if target_path == source_path:
            self.registry.update_status(tracking_id, "committed")
            self.registry.record_review_action(
                tracking_id=tracking_id,
                action="commit",
                reviewer=reviewer,
                changes={"filesystem_rename": False, "path": str(source_path)},
                previous_values=previous_values,
            )
            self._trigger_media_db_sync(tracking_id)
            return self.registry.get_file(tracking_id)

        proposal = RenameProposal(
            tracking_id=tracking_id,
            original_path=str(source_path),
            current_filename=source_path.name,
            proposed_filename=proposed_filename,
            proposed_path=str(target_path),
            mode=self.mode,
            is_collision=False,
            needs_review=False,
            review_reasons=[],
            diagnostic_notes=list(parser_res.diagnostic_notes),
            downstream_routing=list(parser_res.downstream_routing),
            changes_detected=True,
            parser_result=parser_res,
            status="approved",
        )

        try:
            source_path.rename(target_path)
        except Exception as exc:
            raise ValueError(f"Filesystem error during rename: {exc}") from exc

        parser_res.identity.current_filename = target_path.name
        proposal.status = "committed"
        proposal.parser_result = parser_res
        # Keep proposal.current_filename as the pre-rename source name so the
        # rename_history row accurately records from_filename -> to_filename.
        self.registry.record_commit(proposal, target_path)
        self.registry.update_file_review(
            tracking_id=tracking_id,
            when_val=record.get("when_val") or parser_res.when.selected_value,
            what_val=record.get("what_val") or parser_res.what.selected_value or "",
            where_val=record.get("where_val") or "",
            proposed_filename=proposed_filename,
            status="committed",
            needs_review=False,
            review_reasons=[],
            parser_result_json=parser_res.model_dump_json(),
        )
        self.registry.record_review_action(
            tracking_id=tracking_id,
            action="commit",
            reviewer=reviewer,
            changes={
                "filesystem_rename": True,
                "from_path": str(source_path),
                "to_path": str(target_path),
            },
            previous_values=previous_values,
        )
        self._trigger_media_db_sync(tracking_id)
        return self.registry.get_file(tracking_id)

    def _trigger_media_db_sync(self, tracking_id: str):
        """Record durable sync outbox state and trigger automatic synchronization if configured.

        Tool 4 is called only after the final filename is committed for the current
        processing stage, not on RenameMode.INITIAL (amendment section 2).
        """
        if self.mode == RenameMode.INITIAL:
            return

        try:
            self.registry.save_media_db_sync(
                tracking_id=tracking_id,
                sync_status="PENDING_SYNC",
                attempt_count=0,
            )
            if self.media_db_updater_service is not None:
                self.media_db_updater_service.synchronize(tracking_id, commit=True)
        except Exception:
            # Filesystem commit must never be rolled back if Baserow sync fails
            pass
