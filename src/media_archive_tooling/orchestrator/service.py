"""Main Tooling Script application service coordinating discovery, Tools 1–4 pipeline, and audit trails."""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from ..common.ascii_latin import to_ascii_latin, sanitize_filename_token
from ..config import AppConfig, load_config
from ..media_db_reviewer.models import MediaDatabaseReviewResult, ReviewDecision
from ..media_db_reviewer.service import MediaDatabaseReviewService
from ..media_db_updater.models import MediaDbSyncResult, SyncOperation, SyncStatus
from ..media_db_updater.service import MediaDatabaseUpdaterService
from ..renamer.models import (
    EnrichmentEvidence,
    FileMetadata,
    ParserResult,
    RenameMode,
    RenameProposal,
    ResolutionState,
)
from ..renamer.commit_service import RenameCommitService
from ..renamer.parser.collection import CollectionGrammar
from ..renamer.parser.engine import RenamerParser
from ..renamer.planner.planner import RenamePlanner
from ..renamer.registry.registry import LocalRegistry
from ..travel_reviewer.models import TravelReviewDecision, TravelReviewResult
from ..travel_reviewer.service import TravelScheduleReviewService
from .discovery import DiscoveryResult, discover_media_targets
from .fingerprint import compute_review_data_fingerprint
from .logger import UnifiedArchiveLogger
from .models import (
    FileExecutionStatus,
    FileRunResult,
    RunSummary,
    StageName,
    StageResult,
    WorkflowType,
)
from .reporter import TerminalReporter
from .scratch import (
    ScratchTracker,
    check_available_scratch_space,
    clean_abandoned_scratch,
    compute_file_sha256,
    ensure_sufficient_scratch_space,
)


def check_and_enforce_fingerprint(
    registry: LocalRegistry,
    tool4_service: Optional[MediaDatabaseUpdaterService],
    logger: Optional[UnifiedArchiveLogger] = None,
    reporter: Optional[TerminalReporter] = None,
) -> Tuple[bool, Optional[str]]:
    """Verify runtime code fingerprint; trigger automatic fresh slate if code changed (Section 6)."""
    curr_fp = compute_review_data_fingerprint()
    stored_fp = registry.get_metadata("review_data_fingerprint")
    blocked_state = registry.get_metadata("purge_blocked")

    if blocked_state:
        return False, blocked_state

    if stored_fp is None:
        # First run: initialize fingerprint
        registry.set_metadata("review_data_fingerprint", curr_fp)
        return True, None

    if stored_fp == curr_fp:
        return True, None

    # Code has changed!
    if tool4_service is None:
        return False, "Code change detected but Tool 4 updater is unavailable to purge test data"

    purge_summary = tool4_service.purge_test_rows(dry_run=False)
    if purge_summary.blocked == 0:
        registry.clear_review_state()
        registry.set_metadata("review_data_fingerprint", curr_fp)
        registry.delete_metadata("purge_blocked")
        if logger:
            logger.info(
                "AUTOMATIC_FRESH_SLATE",
                details={
                    "prior_fingerprint": stored_fp,
                    "new_fingerprint": curr_fp,
                    "deleted": purge_summary.deleted,
                    "already_absent": purge_summary.already_absent,
                },
            )
        if reporter:
            print("Automatic fresh slate: runtime code change detected, test data purged.")
        return True, None
    else:
        reasons = "; ".join(f"Row {it.row_id}: {it.reason}" for it in purge_summary.items if it.status.value == "PURGE_BLOCKED")
        blocked_msg = f"Cleanup blocked: {purge_summary.blocked} Baserow row(s) retained. Reason: {reasons}"
        registry.set_metadata("purge_blocked", blocked_msg)
        if logger:
            logger.error(
                "AUTOMATIC_PURGE_BLOCKED",
                details={
                    "prior_fingerprint": stored_fp,
                    "new_fingerprint": curr_fp,
                    "blocked": purge_summary.blocked,
                    "reasons": reasons,
                },
            )
        return False, blocked_msg


class MainToolingScriptService:
    """Orchestration service implementing Phase A of the Main Tooling Script."""

    def __init__(
        self,
        registry: LocalRegistry,
        logger: UnifiedArchiveLogger,
        reporter: TerminalReporter,
        parser: RenamerParser,
        tool2_service: Optional[MediaDatabaseReviewService] = None,
        travel_service: Optional[TravelScheduleReviewService] = None,
        tool4_service: Optional[MediaDatabaseUpdaterService] = None,
        tool5_service: Optional[Any] = None,
        workflow: WorkflowType = WorkflowType.ALL,
        dry_run: bool = False,
        verbose: bool = False,
        production: bool = False,
        max_retained_file_results: Optional[int] = 500,
        scratch_dir: Optional[Union[str, Path]] = None,
        min_scratch_bytes: int = 100 * 1024 * 1024,
    ):
        self.registry = registry
        self.logger = logger
        self.reporter = reporter
        self.parser = parser
        self.tool2_service = tool2_service
        self.travel_service = travel_service
        self.tool4_service = tool4_service
        self.tool5_service = tool5_service
        self.workflow = workflow
        self.dry_run = dry_run
        self.verbose = verbose
        self.production = production
        self.max_retained_file_results = max_retained_file_results
        self.scratch_dir = Path(scratch_dir).resolve() if scratch_dir else None
        self.min_scratch_bytes = min_scratch_bytes

        self.planner_initial = RenamePlanner(mode=RenameMode.INITIAL)
        self.planner_finalize = RenamePlanner(mode=RenameMode.FINALIZE)

    def purge(self, dry_run: bool = False) -> Tuple[int, Any]:
        """Perform standalone alpha/beta test data cleanup (Section 5 of purge plan)."""
        with self.registry.acquire_lock():
            if self.tool4_service is None:
                err_msg = "Tool 4 media database updater service is not configured; purge unavailable."
                self.logger.error("PURGE_SERVICE_UNAVAILABLE", details={"error": err_msg})
                print(f"Error: {err_msg}", file=sys.stderr)
                return 1, None

            purge_summary = self.tool4_service.purge_test_rows(dry_run=dry_run)

            if dry_run:
                print("Alpha/beta test data purge preview (dry-run):")
                print(f"Baserow test rows: {purge_summary.deleted} would delete, {purge_summary.already_absent} already absent, {purge_summary.blocked} blocked")
                if purge_summary.items:
                    for it in purge_summary.items:
                        print(f"  - Row #{it.row_id} (table {it.table_id}): {it.action} ({it.reason or it.status.value})")
                print("Local review registry: would clear all files, proposals, reviews, and test ledger")
                print("No filesystem, registry, Baserow, schema, or select-option mutation performed.")
                return 0, purge_summary

            if purge_summary.blocked == 0:
                self.registry.clear_review_state()
                current_fp = compute_review_data_fingerprint()
                self.registry.set_metadata("review_data_fingerprint", current_fp)
                self.registry.delete_metadata("purge_blocked")

                self.logger.info(
                    "ALPHA_BETA_PURGE_COMPLETE",
                    details={
                        "deleted": purge_summary.deleted,
                        "already_absent": purge_summary.already_absent,
                        "blocked": purge_summary.blocked,
                        "cleared_registry": True,
                    },
                )

                print("Alpha/beta purge complete")
                print(f"Baserow test rows: {purge_summary.deleted} deleted, {purge_summary.already_absent} already absent, {purge_summary.blocked} blocked")
                print("Local review registry: cleared")
                return 0, purge_summary
            else:
                reasons = "; ".join(f"Row {it.row_id}: {it.reason}" for it in purge_summary.items if it.status.value == "PURGE_BLOCKED")
                blocked_msg = f"Cleanup blocked: {purge_summary.blocked} row(s) retained. Reason: {reasons}"
                self.registry.set_metadata("purge_blocked", blocked_msg)

                self.logger.error(
                    "ALPHA_BETA_PURGE_BLOCKED",
                    details={
                        "blocked": purge_summary.blocked,
                        "deleted": purge_summary.deleted,
                        "already_absent": purge_summary.already_absent,
                        "reasons": reasons,
                    },
                )

                print(f"Alpha/beta purge blocked: {purge_summary.blocked} row(s) retained.", file=sys.stderr)
                for it in purge_summary.items:
                    if it.status.value == "PURGE_BLOCKED":
                        print(f"  - Row #{it.row_id}: {it.reason}", file=sys.stderr)
                print("Local review registry: preserved", file=sys.stderr)
                print("Run './run-media-archive.sh --purge' to retry.", file=sys.stderr)
                return 1, purge_summary

    def run(self, targets: List[Union[str, Path]]) -> RunSummary:
        """Execute the configured workflow over the target paths."""
        start_time = time.time()
        self.logger.info(
            "RUN_START",
            details={
                "workflow": self.workflow.value,
                "dry_run": self.dry_run,
                "verbose": self.verbose,
                "production": self.production,
                "targets": [str(t) for t in targets],
            },
        )

        # Gate production mode if enabled
        if self.production:
            err_msg = (
                "Production mode policy is currently open (Q-001 in status/main-tooling-script.md). "
                "Archive runs with --production are gated pending confirmed policy separating durable "
                "production Baserow data from alpha/beta test-data purging. Aborting for safety."
            )
            self.logger.error("PRODUCTION_MODE_GATED", details={"error": err_msg})
            print(f"Error: {err_msg}", file=sys.stderr)
            return RunSummary(
                run_id=self.logger.run_id,
                workflow=self.workflow,
                is_dry_run=self.dry_run,
                log_path=str(self.logger.log_path),
                registry_path=str(self.registry.db_path),
                exit_code=1,
            )

        # Startup abandoned scratch recovery
        target_roots = [
            Path(t).resolve() if Path(t).is_dir() else Path(t).resolve().parent
            for t in targets if Path(t).exists()
        ]
        cleaned_scratch = clean_abandoned_scratch(scratch_dir=self.scratch_dir, target_roots=target_roots)
        if cleaned_scratch:
            self.logger.info(
                "ABANDONED_SCRATCH_CLEANED",
                details={
                    "count": len(cleaned_scratch),
                    "sample": [str(p) for p in cleaned_scratch[:10]],
                },
            )

        # 0. Check code fingerprint & enforce fresh slate
        with self.registry.acquire_lock():
            ok, blocked_msg = check_and_enforce_fingerprint(
                registry=self.registry,
                tool4_service=self.tool4_service,
                logger=self.logger,
                reporter=self.reporter,
            )
            if not ok:
                err_msg = f"Error: {blocked_msg}. Please run './run-media-archive.sh --purge' to retry."
                print(err_msg, file=sys.stderr)
                self.logger.error("PURGE_BLOCKED_EXECUTION_HALTED", details={"error": blocked_msg})
                return RunSummary(
                    run_id=self.logger.run_id,
                    workflow=self.workflow,
                    is_dry_run=self.dry_run,
                    log_path=str(self.logger.log_path),
                    registry_path=str(self.registry.db_path),
                    exit_code=1,
                )

        # 1. Validate workflow availability
        if self.workflow == WorkflowType.PROCESSING and self.tool5_service is None:
            msg = "Workflow 'processing' is not available yet (Tools 5–11 pending)."
            self.logger.error("WORKFLOW_UNAVAILABLE", details={"workflow": "processing", "error": msg})
            print(f"Error: {msg}", file=sys.stderr if "sys" in globals() else None)
            return RunSummary(
                run_id=self.logger.run_id,
                workflow=self.workflow,
                is_dry_run=self.dry_run,
                log_path=str(self.logger.log_path),
                registry_path=str(self.registry.db_path),
                exit_code=1,
            )

        # 2. Target discovery & pre-flight existence checks
        discovery = discover_media_targets(targets, registry=self.registry)
        if discovery.missing_targets:
            err_msg = f"One or more target paths do not exist: {', '.join(discovery.missing_targets)}"
            self.logger.error("MISSING_TARGETS", details={"missing": discovery.missing_targets})
            print(f"Error: {err_msg}", file=sys.stderr if "sys" in globals() else None)
            return RunSummary(
                run_id=self.logger.run_id,
                workflow=self.workflow,
                is_dry_run=self.dry_run,
                log_path=str(self.logger.log_path),
                registry_path=str(self.registry.db_path),
                exit_code=1,
            )

        self.logger.info(
            "TARGETS_DISCOVERED",
            details={
                "media_count": len(discovery.media_files),
                "unsupported_count": len(discovery.skipped_unsupported_files),
                "sample_media_files": [str(p) for p in discovery.media_files[:10]],
            },
        )

        self.reporter.report_startup(self.workflow, len(discovery.media_files))
        self.reporter.report_skipped_unsupported(
            len(discovery.skipped_unsupported_files),
            discovery.skipped_unsupported_files,
        )

        summary = RunSummary(
            run_id=self.logger.run_id,
            workflow=self.workflow,
            is_dry_run=self.dry_run,
            total_discovered=len(discovery.media_files),
            skipped_unsupported=len(discovery.skipped_unsupported_files),
            log_path=str(self.logger.log_path),
            registry_path=str(self.registry.db_path),
            max_retained_file_results=self.max_retained_file_results,
            skipped_files=[str(p) for p in discovery.skipped_unsupported_files[:50]],
            exit_code=0,
        )

        has_unexpected_failure = False

        for idx, media_path in enumerate(discovery.media_files, start=1):
            self.reporter.report_file_start(media_path, idx, len(discovery.media_files))
            file_result = self._process_single_file(media_path)
            summary.add_file_result(file_result)
            self.reporter.report_file_result(file_result)

            # Update summary counts
            if file_result.status == FileExecutionStatus.COMPLETED:
                summary.completed += 1
            elif file_result.status == FileExecutionStatus.DRY_RUN:
                summary.dry_run_previews += 1
            elif file_result.status == FileExecutionStatus.UNCHANGED:
                summary.unchanged += 1
            elif file_result.status == FileExecutionStatus.REVIEW_REQUIRED:
                summary.review_required += 1
            elif file_result.status == FileExecutionStatus.PENDING_SYNC:
                summary.pending_sync += 1
            elif file_result.status == FileExecutionStatus.DATABASE_UNAVAILABLE:
                summary.database_unavailable += 1
            elif file_result.status == FileExecutionStatus.FAILED_RETRYABLE:
                summary.failed_retryable += 1
            elif file_result.status == FileExecutionStatus.FAILED_BLOCKED:
                summary.failed_blocked += 1
            elif file_result.status == FileExecutionStatus.FAILED:
                summary.failed += 1
                has_unexpected_failure = True

        if has_unexpected_failure:
            summary.exit_code = 2

        self.reporter.report_summary(summary)
        self.logger.info(
            "RUN_SUMMARY",
            details={
                "total_discovered": summary.total_discovered,
                "completed": summary.completed,
                "dry_run_previews": summary.dry_run_previews,
                "unchanged": summary.unchanged,
                "review_required": summary.review_required,
                "pending_sync": summary.pending_sync,
                "database_unavailable": summary.database_unavailable,
                "failed_retryable": summary.failed_retryable,
                "failed_blocked": summary.failed_blocked,
                "failed": summary.failed,
                "skipped_unsupported": summary.skipped_unsupported,
                "duration_secs": round(time.time() - start_time, 2),
                "exit_code": summary.exit_code,
            },
        )

        return summary

    def _run_tool_5(self, path: Path, tracking_id: str, phase1_context: Optional[Any] = None) -> Tuple[StageResult, Optional[Any]]:
        """Execute Tool 5 Content Discovery on target media path."""
        self.logger.info("STAGE_START", tool="tool_5", file_path=path, tracking_id=tracking_id, details={"dry_run": self.dry_run})
        if self.tool5_service is None:
            t5_stage = StageResult(
                stage_name=StageName.TOOL_5_CONTENT_DISCOVERY,
                success=False,
                summary="Tool 5 — Content Discovery: Service not configured",
                error="Tool 5 service unavailable",
            )
            return t5_stage, None

        input_sha256 = compute_file_sha256(path)
        cp = self.registry.get_stage_checkpoint(tracking_id, StageName.TOOL_5_CONTENT_DISCOVERY.value)
        if cp and cp.get("status") == "COMPLETED" and cp.get("input_sha256") == input_sha256:
            cr_rec = self.registry.get_content_review(tracking_id)
            if cr_rec and cr_rec.get("result"):
                from ..content_discoverer.models import ContentDiscoveryResult
                content_res = ContentDiscoveryResult.model_validate(cr_rec["result"])
                boundary_str = f" | Boundary: {content_res.cutter_proposal.suggested_cut_points}" if content_res.cutter_proposal else ""
                route_str = " -> process_by_tool_6" if content_res.process_by_tool_6 else ""
                summary_str = (
                    f"Tool 5 — Content Discovery: {content_res.classification.value} ({content_res.confidence.value}) | "
                    f"Mantra: {content_res.mantra_type.value}{boundary_str}{route_str} (resumed from checkpoint)"
                )
                t5_stage = StageResult(
                    stage_name=StageName.TOOL_5_CONTENT_DISCOVERY,
                    success=not content_res.review_required,
                    summary=summary_str,
                    details=cp.get("details") or {},
                )
                self.logger.info("STAGE_SKIPPED_CHECKPOINT", tool="tool_5", file_path=path, tracking_id=tracking_id, details=t5_stage.details)
                return t5_stage, content_res

        try:
            def report_progress(stage: str, elapsed_seconds: float, status: str) -> None:
                self.reporter.report_tool5_progress(stage, elapsed_seconds, status)
                if status in {"heartbeat", "done", "failed"}:
                    self.logger.info(
                        "TOOL_5_PROGRESS",
                        tool="tool_5",
                        file_path=path,
                        tracking_id=tracking_id,
                        details={"stage": stage, "status": status, "elapsed_seconds": round(elapsed_seconds, 1)},
                    )

            content_res = self.tool5_service.discover_content(
                target=path,
                tracking_id=tracking_id,
                dry_run=self.dry_run,
                phase1_context=phase1_context,
                progress_callback=report_progress,
            )
            boundary_str = f" | Boundary: {content_res.cutter_proposal.suggested_cut_points}" if content_res.cutter_proposal else ""
            route_str = " -> process_by_tool_6" if content_res.process_by_tool_6 else ""
            summary_str = (
                f"Tool 5 — Content Discovery: {content_res.classification.value} ({content_res.confidence.value}) | "
                f"Mantra: {content_res.mantra_type.value}{boundary_str}{route_str}"
            )
            t5_stage = StageResult(
                stage_name=StageName.TOOL_5_CONTENT_DISCOVERY,
                success=not content_res.review_required,
                summary=summary_str,
                details={
                    "classification": content_res.classification.value,
                    "confidence": content_res.confidence.value,
                    "mantra_type": content_res.mantra_type.value,
                    "process_by_tool_6": content_res.process_by_tool_6,
                    "transcript_path": content_res.transcript_path,
                    "review_required": content_res.review_required,
                    "review_reason": content_res.review_reason,
                },
            )
            self.registry.save_stage_checkpoint(
                tracking_id=tracking_id,
                stage_name=StageName.TOOL_5_CONTENT_DISCOVERY.value,
                input_path=path,
                input_sha256=input_sha256,
                status="COMPLETED",
                summary=t5_stage.summary,
                details=t5_stage.details,
            )
            self.logger.info("STAGE_COMPLETE", tool="tool_5", file_path=path, tracking_id=tracking_id, details=t5_stage.details)
            return t5_stage, content_res
        except Exception as e:
            err_msg = f"Tool 5 content discovery error: {e}"
            self.logger.error("TOOL_5_ERROR", tool="tool_5", tracking_id=tracking_id, details={"error": err_msg})
            self.registry.save_stage_checkpoint(
                tracking_id=tracking_id,
                stage_name=StageName.TOOL_5_CONTENT_DISCOVERY.value,
                input_path=path,
                input_sha256=input_sha256,
                status="FAILED",
                summary=f"Tool 5 — Error: {err_msg}",
                details={"error": err_msg},
            )
            t5_stage = StageResult(
                stage_name=StageName.TOOL_5_CONTENT_DISCOVERY,
                success=False,
                summary=f"Tool 5 — Error: {err_msg}",
                error=err_msg,
            )
            return t5_stage, None

    def _process_single_file(self, file_path: Path) -> FileRunResult:
        """Execute the continuous Phase A pipeline on an individual media file."""
        file_path = file_path.resolve()
        orig_filename = file_path.name
        stage_results: List[StageResult] = []
        review_reasons: List[str] = []
        existing_tid = self.registry.find_tracking_id_by_path(file_path)
        input_sha256 = ""

        with ScratchTracker(self.scratch_dir) as scratch:
            # Preflight disk space verification before processing/conversion
            file_sz = file_path.stat().st_size if file_path.exists() else 0
            required_scratch = max(self.min_scratch_bytes, file_sz * 2)
            scratch_check_dir = self.scratch_dir or file_path.parent
            try:
                ensure_sufficient_scratch_space(
                    path=scratch_check_dir,
                    required_bytes=required_scratch,
                    operation_name=f"processing {orig_filename}",
                )
            except RuntimeError as e:
                err_msg = str(e)
                self.logger.error("SCRATCH_SPACE_INSUFFICIENT", file_path=file_path, details={"error": err_msg})
                fail_stage = StageResult(
                    stage_name=StageName.TOOL_1_INITIAL,
                    success=False,
                    summary=f"Error: {err_msg}",
                    error=err_msg,
                )
                stage_results.append(fail_stage)
                self.reporter.report_stage_result(fail_stage)
                return FileRunResult(
                    target_path=str(file_path),
                    tracking_id=existing_tid or "unregistered",
                    original_filename=orig_filename,
                    final_filename=orig_filename,
                    final_path=str(file_path),
                    status=FileExecutionStatus.FAILED_RETRYABLE,
                    error=err_msg,
                    stage_results=stage_results,
                )

            try:
                input_sha256 = compute_file_sha256(file_path)
                # Sibling collection grammar (inspection only)
                parent_dir = file_path.parent
                sibling_names = [
                    f.name for f in parent_dir.iterdir()
                    if f.is_file() and not f.name.startswith(".")
                ] if parent_dir.exists() else [orig_filename]
                grammar = CollectionGrammar(parent_dir, sibling_names)

                # Processing workflow: Tool 5 Content Discovery only
                if self.workflow == WorkflowType.PROCESSING:
                    if not existing_tid:
                        reason = "Target has not undergone Phase 1 renamer processing"
                        self.logger.warning("FILE_PHASE1_INELIGIBLE", file_path=file_path, details={"reason": reason})
                        t5_stage = StageResult(
                            stage_name=StageName.TOOL_5_CONTENT_DISCOVERY,
                            success=False,
                            summary=f"Tool 5 — Skipped: {reason}",
                            error=reason,
                        )
                        stage_results.append(t5_stage)
                        self.reporter.report_stage_result(t5_stage)
                        review_reasons.append(reason)
                        return FileRunResult(
                            target_path=str(file_path),
                            tracking_id="unregistered",
                            original_filename=orig_filename,
                            final_filename=orig_filename,
                            final_path=str(file_path),
                            status=FileExecutionStatus.REVIEW_REQUIRED,
                            review_reasons=review_reasons,
                            stage_results=stage_results,
                            content_discovery_result=None,
                        )

                    tracking_id = existing_tid
                    t5_stage, content_res = self._run_tool_5(file_path, tracking_id)
                    stage_results.append(t5_stage)
                    self.reporter.report_stage_result(t5_stage)

                    status = FileExecutionStatus.DRY_RUN if self.dry_run else FileExecutionStatus.COMPLETED
                    if content_res and content_res.review_required:
                        status = FileExecutionStatus.REVIEW_REQUIRED
                        if content_res.review_reason:
                            review_reasons.append(content_res.review_reason)

                    return FileRunResult(
                        target_path=str(file_path),
                        tracking_id=tracking_id,
                        original_filename=orig_filename,
                        final_filename=orig_filename,
                        final_path=str(file_path),
                        status=status,
                        review_reasons=review_reasons,
                        stage_results=stage_results,
                        content_discovery_result=content_res,
                    )

                # -------------------------------------------------------------
                # Stage 1: Tool 1 Initial Interpretation
                # -------------------------------------------------------------
                self.logger.info("STAGE_START", tool="tool_1", file_path=file_path, details={"stage": "initial"})

                cp1 = self.registry.get_stage_checkpoint(existing_tid, StageName.TOOL_1_INITIAL.value) if existing_tid else None
                file_rec = self.registry.get_file(existing_tid) if existing_tid else None

                if cp1 and cp1.get("status") == "COMPLETED" and cp1.get("input_sha256") == input_sha256 and file_rec and file_rec.get("parser_result"):
                    parser_res = ParserResult.model_validate(file_rec["parser_result"])
                    parser_res.identity.original_path = str(file_path)
                    parser_res.identity.current_filename = file_path.name
                    tracking_id = existing_tid
                    prop_filename = cp1.get("details", {}).get("proposed_filename") or file_rec.get("proposed_filename") or ""
                    t1_summary = cp1.get("summary") or (
                        f"Tool 1 — Renamer: Date: {parser_res.when.selected_value or '—'} | "
                        f"WHAT: {parser_res.what.selected_value or '—'} | "
                        f"Location: {parser_res.where.place_location or '—'} | "
                        f"Initial Proposal: {prop_filename}"
                    )
                    t1_stage = StageResult(
                        stage_name=StageName.TOOL_1_INITIAL,
                        success=True,
                        summary=t1_summary,
                        details=cp1.get("details") or {},
                    )
                    stage_results.append(t1_stage)
                    self.reporter.report_stage_result(t1_stage)
                    self.logger.info("STAGE_SKIPPED_CHECKPOINT", tool="tool_1", file_path=file_path, tracking_id=tracking_id, details=t1_stage.details)
                else:
                    parser_res = self.parser.parse_file(
                        file_path=file_path,
                        mode=RenameMode.INITIAL,
                        collection_grammar=grammar,
                    )
                    # If path was already registered, reuse the existing tracking ID
                    if existing_tid and parser_res.identity.tracking_id != existing_tid:
                        parser_res.identity.tracking_id = existing_tid

                    tracking_id = parser_res.identity.tracking_id
                    prop_init = self.planner_initial.plan_rename(parser_res)
                    self.registry.save_proposal(prop_init)

                    t1_summary = (
                        f"Tool 1 — Renamer: Date: {parser_res.when.selected_value or '—'} | "
                        f"WHAT: {parser_res.what.selected_value or '—'} | "
                        f"Location: {parser_res.where.place_location or '—'} | "
                        f"Initial Proposal: {prop_init.proposed_filename}"
                    )
                    t1_stage = StageResult(
                        stage_name=StageName.TOOL_1_INITIAL,
                        success=True,
                        summary=t1_summary,
                        details={
                            "when": parser_res.when.selected_value,
                            "what": parser_res.what.selected_value,
                            "where": parser_res.where.place_location,
                            "country": parser_res.where.country_iso2,
                            "proposed_filename": prop_init.proposed_filename,
                        },
                    )
                    self.registry.save_stage_checkpoint(
                        tracking_id=tracking_id,
                        stage_name=StageName.TOOL_1_INITIAL.value,
                        input_path=file_path,
                        input_sha256=input_sha256,
                        status="COMPLETED",
                        summary=t1_summary,
                        details=t1_stage.details,
                    )
                    stage_results.append(t1_stage)
                    self.reporter.report_stage_result(t1_stage)
                    self.logger.info("STAGE_COMPLETE", tool="tool_1", file_path=file_path, tracking_id=tracking_id, details=t1_stage.details)

                # -------------------------------------------------------------
                # Stage 2: Tool 2 Live Read-Only Media Review
                # -------------------------------------------------------------
                self.logger.info("STAGE_START", tool="tool_2", file_path=file_path, tracking_id=tracking_id)
                t2_res: Optional[MediaDatabaseReviewResult] = None
                cp2 = self.registry.get_stage_checkpoint(tracking_id, StageName.TOOL_2_REVIEW.value)
                t2_dict = self.registry.get_media_db_review(tracking_id)

                if cp2 and cp2.get("status") == "COMPLETED" and cp2.get("input_sha256") == input_sha256 and t2_dict and t2_dict.get("result"):
                    t2_res = MediaDatabaseReviewResult.model_validate(t2_dict["result"])
                    dec_str = cp2.get("details", {}).get("decision") or (t2_res.decision.value if hasattr(t2_res.decision, "value") else str(t2_res.decision))
                    t2_stage = StageResult(
                        stage_name=StageName.TOOL_2_REVIEW,
                        success=True,
                        summary=cp2.get("summary") or f"Tool 2 — Media DB: {dec_str}",
                        decision=dec_str,
                        details=cp2.get("details") or {},
                    )
                    if dec_str == "DATABASE_UNAVAILABLE":
                        review_reasons.append("Tool 2 reported DATABASE_UNAVAILABLE")
                    elif t2_res.review_required:
                        if t2_res.review_reasons:
                            for r in t2_res.review_reasons:
                                if r not in review_reasons:
                                    review_reasons.append(r)
                        else:
                            reason = f"Tool 2 media review required: {dec_str}"
                            if reason not in review_reasons:
                                review_reasons.append(reason)
                    stage_results.append(t2_stage)
                    self.reporter.report_stage_result(t2_stage)
                    self.logger.info("STAGE_SKIPPED_CHECKPOINT", tool="tool_2", file_path=file_path, tracking_id=tracking_id, details=t2_stage.details)
                else:
                    if self.tool2_service is not None:
                        try:
                            t2_res = self.tool2_service.review_file(tracking_id, auto_enrich=True)
                        except Exception as e:
                            self.logger.warning("TOOL_2_ERROR", tool="tool_2", tracking_id=tracking_id, details={"error": str(e)})

                    if t2_res is None:
                        t2_stage = StageResult(
                            stage_name=StageName.TOOL_2_REVIEW,
                            success=False,
                            summary="Tool 2 — Media DB: Review unavailable or unconfigured",
                            decision="UNAVAILABLE",
                        )
                        review_reasons.append("Tool 2 media database review unavailable")
                        self.registry.save_stage_checkpoint(
                            tracking_id=tracking_id,
                            stage_name=StageName.TOOL_2_REVIEW.value,
                            input_path=file_path,
                            input_sha256=input_sha256,
                            status="FAILED",
                            summary=t2_stage.summary,
                            details={"error": "Tool 2 unavailable or unconfigured"},
                        )
                    else:
                        dec_str = t2_res.decision.value if hasattr(t2_res.decision, "value") else str(t2_res.decision)
                        cand_count = len(t2_res.candidates)
                        selected_row = t2_res.selected_media_row_id
                        enriched_title = t2_res.renamer_enrichment.title_full if (t2_res.renamer_enrichment and t2_res.renamer_enrichment.confirmed) else None
                        related_series = t2_res.selected_field_evidence.get("related_series", [])

                        t2_summary = f"Tool 2 — Media DB: {dec_str} (candidates: {cand_count})"
                        if selected_row:
                            t2_summary += f", selected row #{selected_row}"
                        if enriched_title:
                            t2_summary += f", confirmed title: '{enriched_title}'"
                        if related_series:
                            related = related_series[0]
                            t2_summary += (
                                f", related series row #{related.get('media_row_id')} "
                                f"({related.get('date')}, {related.get('title') or related.get('what')})"
                            )

                        t2_stage = StageResult(
                            stage_name=StageName.TOOL_2_REVIEW,
                            success=True,
                            summary=t2_summary,
                            decision=dec_str,
                            details={
                                "decision": dec_str,
                                "candidate_count": cand_count,
                                "selected_media_row_id": selected_row,
                                "confirmed_title": enriched_title,
                                "related_series": related_series,
                            },
                        )
                        if dec_str == "DATABASE_UNAVAILABLE":
                            review_reasons.append("Tool 2 reported DATABASE_UNAVAILABLE")
                        elif t2_res.review_required:
                            if t2_res.review_reasons:
                                for r in t2_res.review_reasons:
                                    if r not in review_reasons:
                                        review_reasons.append(r)
                            else:
                                reason = None
                                if t2_res.diagnostic_notes:
                                    reason = f"Tool 2 media review required: {'; '.join(t2_res.diagnostic_notes)}"
                                else:
                                    reason = f"Tool 2 media review required: {dec_str} (candidates: {cand_count})"
                                if reason not in review_reasons:
                                    review_reasons.append(reason)

                        self.registry.save_stage_checkpoint(
                            tracking_id=tracking_id,
                            stage_name=StageName.TOOL_2_REVIEW.value,
                            input_path=file_path,
                            input_sha256=input_sha256,
                            status="COMPLETED",
                            summary=t2_stage.summary,
                            details=t2_stage.details,
                        )

                    stage_results.append(t2_stage)
                    self.reporter.report_stage_result(t2_stage)
                    self.logger.info("STAGE_COMPLETE", tool="tool_2", file_path=file_path, tracking_id=tracking_id, details=t2_stage.details)

                # -------------------------------------------------------------
                # Stage 3: Tool 3 Travel Schedule Review
                # -------------------------------------------------------------
                self.logger.info("STAGE_START", tool="tool_3", file_path=file_path, tracking_id=tracking_id)
                t3_res: Optional[TravelReviewResult] = None
                cp3 = self.registry.get_stage_checkpoint(tracking_id, StageName.TOOL_3_REVIEW.value)
                t3_dict = self.registry.get_travel_review(tracking_id)

                if cp3 and cp3.get("status") == "COMPLETED" and cp3.get("input_sha256") == input_sha256 and (t3_dict and t3_dict.get("result") or self.travel_service is None):
                    t3_res = TravelReviewResult.model_validate(t3_dict["result"]) if (t3_dict and t3_dict.get("result")) else None
                    t3_dec = cp3.get("details", {}).get("decision") or (t3_res.decision.value if t3_res and hasattr(t3_res.decision, "value") else "UNAVAILABLE")
                    t3_stage = StageResult(
                        stage_name=StageName.TOOL_3_REVIEW,
                        success=True,
                        summary=cp3.get("summary") or f"Tool 3 — Travel Schedule: {t3_dec}",
                        decision=t3_dec,
                        details=cp3.get("details") or {},
                    )
                    if t3_dec in (
                        TravelReviewDecision.REFERENCE_UNAVAILABLE.value,
                        TravelReviewDecision.PROCESSING_ERROR.value,
                        TravelReviewDecision.SCHEDULE_CONFLICT.value,
                    ):
                        review_reasons.append(f"Tool 3 travel schedule review: {t3_dec}")
                    stage_results.append(t3_stage)
                    self.reporter.report_stage_result(t3_stage)
                    self.logger.info("STAGE_SKIPPED_CHECKPOINT", tool="tool_3", file_path=file_path, tracking_id=tracking_id, details=t3_stage.details)
                else:
                    if self.travel_service is not None:
                        try:
                            t3_res = self.travel_service.review_file(
                                tracking_id,
                                tool2_context=t2_res,
                                auto_enrich=True,
                            )
                        except Exception as e:
                            self.logger.warning("TOOL_3_ERROR", tool="tool_3", tracking_id=tracking_id, details={"error": str(e)})

                    if t3_res is None:
                        t3_stage = StageResult(
                            stage_name=StageName.TOOL_3_REVIEW,
                            success=True,
                            summary="Tool 3 — Travel Schedule: Not configured or unavailable",
                            decision="UNAVAILABLE",
                        )
                    else:
                        t3_dec = t3_res.decision.value if hasattr(t3_res.decision, "value") else str(t3_res.decision)
                        loc_summary = ""
                        if t3_res.provisional_enrichment and t3_res.provisional_enrichment.where_val:
                            loc_summary = t3_res.provisional_enrichment.where_val
                        elif t3_res.candidates:
                            c = t3_res.candidates[0]
                            loc_summary = f"{c.place}, {c.country}" if c.place else c.country

                        t3_summary = f"Tool 3 — Travel Schedule: {t3_dec}"
                        retained_location_note = next(
                            (
                                note for note in (t3_res.diagnostic_notes or [])
                                if "retained exact filename location" in note
                            ),
                            None,
                        )
                        if retained_location_note:
                            retained_where = t3_res.input_where_val or "exact filename location"
                            t3_summary += (
                                f" (date supported; retained {retained_where}; "
                                f"schedule context: {loc_summary or 'different place'})"
                            )
                        elif loc_summary:
                            t3_summary += f" ({loc_summary})"

                        is_enriched = t3_res.provisional_enrichment is not None
                        t3_reasons = getattr(t3_res, "review_reasons", []) or []

                        t3_stage = StageResult(
                            stage_name=StageName.TOOL_3_REVIEW,
                            success=True,
                            summary=t3_summary,
                            decision=t3_dec,
                            details={
                                "decision": t3_dec,
                                "location": loc_summary,
                                "retained_filename_location": t3_res.input_where_val if retained_location_note else None,
                                "enriched": is_enriched,
                                "reasons": t3_reasons,
                            },
                        )
                        if t3_dec in (
                            TravelReviewDecision.REFERENCE_UNAVAILABLE.value,
                            TravelReviewDecision.PROCESSING_ERROR.value,
                            TravelReviewDecision.SCHEDULE_CONFLICT.value,
                        ):
                            review_reasons.append(f"Tool 3 travel schedule review: {t3_dec}")

                    self.registry.save_stage_checkpoint(
                        tracking_id=tracking_id,
                        stage_name=StageName.TOOL_3_REVIEW.value,
                        input_path=file_path,
                        input_sha256=input_sha256,
                        status="COMPLETED",
                        summary=t3_stage.summary,
                        details=t3_stage.details,
                    )
                    stage_results.append(t3_stage)
                    self.reporter.report_stage_result(t3_stage)
                    self.logger.info("STAGE_COMPLETE", tool="tool_3", file_path=file_path, tracking_id=tracking_id, details=t3_stage.details)

                # -------------------------------------------------------------
                # Stage 4: Tool 1 Finalization
                # -------------------------------------------------------------
                self.logger.info("STAGE_START", tool="tool_1", file_path=file_path, tracking_id=tracking_id, details={"stage": "finalize"})
                # Re-read parser result from registry (which now includes any Tool 2 / Tool 3 enrichments)
                file_rec = self.registry.get_file(tracking_id)
                if file_rec and file_rec.get("parser_result"):
                    updated_parser_res = ParserResult.model_validate(file_rec["parser_result"])
                else:
                    updated_parser_res = parser_res
                updated_parser_res.identity.original_path = str(file_path)
                updated_parser_res.identity.current_filename = file_path.name

                prop_final = self.planner_finalize.plan_rename(updated_parser_res)
                # Carry existing review reasons
                for r in review_reasons:
                    if r not in prop_final.review_reasons:
                        prop_final.review_reasons.append(r)
                t2_requires_review = bool(t2_res and t2_res.review_required)
                if review_reasons or t2_requires_review:
                    prop_final.needs_review = True
                    prop_final.status = "blocked"

                self.registry.save_proposal(prop_final)

                can_commit = (
                    (not prop_final.needs_review)
                    and (prop_final.status not in ("blocked", "deferred"))
                    and not t2_requires_review
                )
                t1_fin_summary = (
                    f"Tool 1 — Final Proposal: {prop_final.proposed_filename} "
                    f"({'can commit' if can_commit else 'review required'})"
                )
                t1_fin_stage = StageResult(
                    stage_name=StageName.TOOL_1_FINALIZE,
                    success=True,
                    summary=t1_fin_summary,
                    details={
                        "proposed_filename": prop_final.proposed_filename,
                        "proposed_path": prop_final.proposed_path,
                        "needs_review": prop_final.needs_review,
                        "can_commit": can_commit,
                        "review_reasons": prop_final.review_reasons,
                    },
                )
                self.registry.save_stage_checkpoint(
                    tracking_id=tracking_id,
                    stage_name=StageName.TOOL_1_FINALIZE.value,
                    input_path=file_path,
                    input_sha256=input_sha256,
                    status="COMPLETED",
                    summary=t1_fin_stage.summary,
                    details=t1_fin_stage.details,
                )
                stage_results.append(t1_fin_stage)
                self.reporter.report_stage_result(t1_fin_stage)
                self.logger.info("STAGE_COMPLETE", tool="tool_1", file_path=file_path, tracking_id=tracking_id, details=t1_fin_stage.details)

                # -------------------------------------------------------------
                # Stage 5: Tool 4 Synchronization (Dry-Run Preview vs Live Commit)
                # -------------------------------------------------------------
                self.logger.info("STAGE_START", tool="tool_4", file_path=file_path, tracking_id=tracking_id, details={"dry_run": self.dry_run})
                proposed_path = Path(prop_final.proposed_path)
                target_path = proposed_path
                status = FileExecutionStatus.COMPLETED
                tool4_row_id: Optional[int] = None
                tool4_operation: Optional[str] = None
                tool4_fields: Dict[str, Any] = {}

                if self.dry_run:
                    # Dry-run: preview using projected final filename and projected path
                    t4_res = None
                    if self.tool4_service is not None:
                        try:
                            t4_res = self.tool4_service.preview(
                                tracking_id,
                                projected_filename=prop_final.proposed_filename,
                                projected_path=prop_final.proposed_path,
                            )
                        except Exception as e:
                            self.logger.warning("TOOL_4_PREVIEW_ERROR", tool="tool_4", tracking_id=tracking_id, details={"error": str(e)})

                    if t4_res is None:
                        t4_summary = "Tool 4 — Media DB: Preview not available (Tool 4 service not configured)"
                        status = FileExecutionStatus.DRY_RUN
                    else:
                        tool4_row_id = t4_res.media_row_id
                        tool4_operation = t4_res.operation.value if hasattr(t4_res.operation, "value") else str(t4_res.operation)
                        tool4_fields = {d.field_name: d.new_value for d in (t4_res.field_diffs or [])}

                        if t4_res.operation == SyncOperation.CREATE:
                            op_str = "WOULD CREATE (new row — ID assigned only on commit)"
                        elif t4_res.operation == SyncOperation.UPDATE:
                            op_str = f"WOULD UPDATE row #{tool4_row_id or '—'}"
                        elif t4_res.operation == SyncOperation.NOOP:
                            op_str = f"WOULD NOOP (row #{tool4_row_id or '—'} already in sync)"
                        else:
                            op_str = f"WOULD {t4_res.operation.value.upper()}"

                        t4_summary = f"Tool 4 — Media DB: {op_str}"
                        if t4_res.field_diffs:
                            diff_summary = ", ".join(f"{d.field_name}='{d.new_value}'" for d in t4_res.field_diffs if d.action.value == "SET")
                            if diff_summary:
                                t4_summary += f" [{diff_summary}]"

                        if t4_res.review_required:
                            status = FileExecutionStatus.REVIEW_REQUIRED
                            for r in (t4_res.conflicts or []):
                                if r not in review_reasons:
                                    review_reasons.append(r)
                            if t4_res.error_message and t4_res.error_message not in review_reasons:
                                review_reasons.append(t4_res.error_message)
                        else:
                            status = FileExecutionStatus.DRY_RUN

                    t4_stage = StageResult(
                        stage_name=StageName.TOOL_4_SYNC,
                        success=True,
                        summary=t4_summary,
                        details={
                            "dry_run": True,
                            "operation": tool4_operation,
                            "media_row_id": tool4_row_id,
                            "fields": tool4_fields,
                        },
                    )
                    stage_results.append(t4_stage)
                    self.reporter.report_stage_result(t4_stage)
                    self.logger.info("STAGE_COMPLETE", tool="tool_4", file_path=file_path, tracking_id=tracking_id, details=t4_stage.details)

                    content_res = None
                    if self.workflow == WorkflowType.ALL and self.tool5_service is not None:
                        t5_stage, content_res = self._run_tool_5(file_path, tracking_id, phase1_context=prop_final)
                        stage_results.append(t5_stage)
                        self.reporter.report_stage_result(t5_stage)
                        if content_res and content_res.review_required:
                            status = FileExecutionStatus.REVIEW_REQUIRED
                            if content_res.review_reason and content_res.review_reason not in review_reasons:
                                review_reasons.append(content_res.review_reason)

                    return FileRunResult(
                        target_path=str(file_path),
                        tracking_id=tracking_id,
                        original_filename=orig_filename,
                        final_filename=prop_final.proposed_filename,
                        final_path=prop_final.proposed_path,
                        status=status,
                        review_reasons=review_reasons,
                        stage_results=stage_results,
                        tool4_row_id=tool4_row_id,
                        tool4_operation=tool4_operation,
                        tool4_fields=tool4_fields,
                        content_discovery_result=content_res,
                    )

                # Live Mode execution
                if not can_commit:
                    # File cannot be committed safely (R-002: gated before rename)
                    status = FileExecutionStatus.REVIEW_REQUIRED
                    t4_stage = StageResult(
                        stage_name=StageName.TOOL_4_SYNC,
                        success=False,
                        summary="Tool 4 — Media DB: Skipped write (file requires human review)",
                        details={"reasons": prop_final.review_reasons},
                    )
                    stage_results.append(t4_stage)
                    self.reporter.report_stage_result(t4_stage)
                    self.logger.info("STAGE_COMPLETE", tool="tool_4", file_path=file_path, tracking_id=tracking_id, details=t4_stage.details)

                    content_res = None
                    if self.workflow == WorkflowType.ALL and self.tool5_service is not None:
                        t5_stage, content_res = self._run_tool_5(file_path, tracking_id)
                        stage_results.append(t5_stage)
                        self.reporter.report_stage_result(t5_stage)
                        if content_res and content_res.review_required:
                            if content_res.review_reason and content_res.review_reason not in prop_final.review_reasons:
                                prop_final.review_reasons.append(content_res.review_reason)

                    return FileRunResult(
                        target_path=str(file_path),
                        tracking_id=tracking_id,
                        original_filename=orig_filename,
                        final_filename=prop_final.proposed_filename,
                        final_path=str(file_path),
                        status=status,
                        review_reasons=prop_final.review_reasons,
                        stage_results=stage_results,
                        content_discovery_result=content_res,
                    )

                # Execute commit through accepted RenameCommitService boundary (R-003)
                current_path = file_path
                was_filesystem_renamed = False
                try:
                    commit_service = RenameCommitService(
                        registry=self.registry,
                        mode=RenameMode.FINALIZE,
                        media_db_updater_service=None,
                    )
                    committed_rec = commit_service.commit_file(tracking_id, reviewer="main-script")
                    current_path = Path(committed_rec.get("current_path") or prop_final.proposed_path)
                    was_filesystem_renamed = (current_path != file_path)
                    self.logger.info(
                        "COMMIT_APPLIED",
                        tool="tool_1",
                        file_path=current_path,
                        details={"from": str(file_path), "to": str(current_path)},
                    )
                except Exception as e:
                    err_msg = f"Commit error: {e}"
                    self.logger.error("COMMIT_FAILED", tool="tool_1", file_path=file_path, details={"error": err_msg})
                    t1_fail_stage = StageResult(
                        stage_name=StageName.TOOL_1_FINALIZE,
                        success=False,
                        summary=f"Tool 1 — Error: {err_msg}",
                        error=err_msg,
                    )
                    stage_results.append(t1_fail_stage)
                    return FileRunResult(
                        target_path=str(file_path),
                        tracking_id=tracking_id,
                        original_filename=orig_filename,
                        final_filename=orig_filename,
                        final_path=str(file_path),
                        status=FileExecutionStatus.FAILED,
                        error=err_msg,
                        stage_results=stage_results,
                    )

                # Tool 4 synchronization in live mode
                cp5 = self.registry.get_stage_checkpoint(tracking_id, StageName.TOOL_4_SYNC.value)
                sync_rec = self.registry.get_media_db_sync(tracking_id)
                if cp5 and cp5.get("status") == "COMPLETED" and sync_rec and sync_rec.get("sync_status") == SyncStatus.SYNCED.value:
                    tool4_sync_status = sync_rec["sync_status"]
                    tool4_row_id = sync_rec.get("media_row_id")
                    tool4_operation = sync_rec.get("operation")
                    import json
                    tool4_fields = json.loads(sync_rec["fields_json"]) if sync_rec.get("fields_json") else {}
                    tool4_live_row = json.loads(sync_rec["live_row_json"]) if sync_rec.get("live_row_json") else None
                    t4_stage = StageResult(
                        stage_name=StageName.TOOL_4_SYNC,
                        success=True,
                        summary=cp5.get("summary") or f"Tool 4 — Media DB: Already synchronized (row #{tool4_row_id})",
                        details=cp5.get("details") or {},
                    )
                    if not was_filesystem_renamed and tool4_operation == SyncOperation.NOOP.value:
                        status = FileExecutionStatus.UNCHANGED
                    else:
                        status = FileExecutionStatus.COMPLETED
                    stage_results.append(t4_stage)
                    self.reporter.report_stage_result(t4_stage)
                    self.logger.info("STAGE_SKIPPED_CHECKPOINT", tool="tool_4", file_path=current_path, tracking_id=tracking_id, details=t4_stage.details)
                else:
                    t4_res = None
                    if self.tool4_service is not None:
                        try:
                            t4_res = self.tool4_service.synchronize(tracking_id, commit=True)
                        except Exception as e:
                            self.logger.error("TOOL_4_SYNC_EXCEPTION", tool="tool_4", tracking_id=tracking_id, details={"error": str(e)})

                    tool4_live_row: Optional[Dict[str, Any]] = None
                    tool4_sync_status: Optional[str] = None

                    if t4_res is None:
                        # Rename succeeded, but Tool 4 service unavailable: state preserved in outbox
                        sync_err = "Tool 4 service unavailable"
                        t4_summary = f"Tool 4 — Media DB: PENDING_SYNC ({sync_err}) — state preserved in outbox"
                        status = FileExecutionStatus.PENDING_SYNC
                        tool4_sync_status = "PENDING_SYNC"
                        review_reasons.append(f"Baserow sync pending: {sync_err}")

                        t4_stage = StageResult(
                            stage_name=StageName.TOOL_4_SYNC,
                            success=False,
                            summary=t4_summary,
                            error=sync_err,
                        )
                        self.registry.save_stage_checkpoint(
                            tracking_id=tracking_id,
                            stage_name=StageName.TOOL_4_SYNC.value,
                            input_path=current_path,
                            input_sha256=input_sha256,
                            status="FAILED",
                            summary=t4_stage.summary,
                            details={"error": sync_err},
                        )
                    elif t4_res.status == SyncStatus.SYNCED:
                        tool4_sync_status = t4_res.status.value
                        tool4_row_id = t4_res.media_row_id
                        tool4_operation = t4_res.operation.value if hasattr(t4_res.operation, "value") else str(t4_res.operation)
                        tool4_fields = {d.field_name: d.new_value for d in (t4_res.field_diffs or [])}
                        tool4_live_row = t4_res.live_row

                        if t4_res.operation == SyncOperation.CREATE:
                            op_str = f"CREATED row #{tool4_row_id}"
                        elif t4_res.operation == SyncOperation.UPDATE:
                            op_str = f"UPDATED row #{tool4_row_id}"
                        elif t4_res.operation == SyncOperation.NOOP:
                            op_str = f"NOOP (row #{tool4_row_id} already in sync)"
                        else:
                            op_str = f"{t4_res.operation.value.upper()}"

                        t4_summary = f"Tool 4 — Media DB: {op_str}"
                        if t4_res.field_diffs:
                            diff_summary = ", ".join(f"{d.field_name}='{d.new_value}'" for d in t4_res.field_diffs if d.action.value == "SET")
                            if diff_summary:
                                t4_summary += f" [{diff_summary}]"

                        # R-005: Show verified live readback summary
                        if tool4_live_row:
                            verified_items = []
                            for k in ("Title", "Filename", "Path", "Date", "Country", "Place, location"):
                                if k in tool4_live_row and tool4_live_row[k]:
                                    v = tool4_live_row[k]
                                    if isinstance(v, dict) and "value" in v:
                                        v = v["value"]
                                    verified_items.append(f"{k}='{v}'")
                            if verified_items:
                                readback_str = ", ".join(verified_items)
                                t4_summary += f"\n    Verified live row #{tool4_row_id}: {readback_str}"

                        t4_stage = StageResult(
                            stage_name=StageName.TOOL_4_SYNC,
                            success=True,
                            summary=t4_summary,
                            details={
                                "operation": tool4_operation,
                                "media_row_id": tool4_row_id,
                                "fields": tool4_fields,
                                "live_row": tool4_live_row,
                            },
                        )

                        # R-004: If filename was already canonical, but Tool 4 performed CREATE or UPDATE,
                        # the item is COMPLETED (synchronized), NOT UNCHANGED!
                        # Only if Tool 4 was NOOP and the filename was unchanged is it UNCHANGED.
                        if not was_filesystem_renamed and t4_res.operation == SyncOperation.NOOP:
                            status = FileExecutionStatus.UNCHANGED
                        else:
                            status = FileExecutionStatus.COMPLETED

                        self.registry.save_stage_checkpoint(
                            tracking_id=tracking_id,
                            stage_name=StageName.TOOL_4_SYNC.value,
                            input_path=current_path,
                            input_sha256=input_sha256,
                            status="COMPLETED",
                            summary=t4_stage.summary,
                            details=t4_stage.details,
                        )
                    else:
                        # Specific non-SYNCED Tool 4 outcome (R-004)
                        tool4_sync_status = t4_res.status.value
                        tool4_row_id = t4_res.media_row_id
                        tool4_operation = t4_res.operation.value if hasattr(t4_res.operation, "value") else str(t4_res.operation)
                        sync_err = t4_res.error_message or f"Tool 4 status: {t4_res.status.value}"

                        if t4_res.status == SyncStatus.REVIEW_REQUIRED:
                            status = FileExecutionStatus.REVIEW_REQUIRED
                            for c in (t4_res.conflicts or []):
                                if c not in review_reasons:
                                    review_reasons.append(c)
                            if sync_err and sync_err not in review_reasons:
                                review_reasons.append(sync_err)
                        elif t4_res.status == SyncStatus.DATABASE_UNAVAILABLE:
                            status = FileExecutionStatus.DATABASE_UNAVAILABLE
                            review_reasons.append(f"Baserow database unavailable: {sync_err}")
                        elif t4_res.status == SyncStatus.FAILED_RETRYABLE:
                            status = FileExecutionStatus.FAILED_RETRYABLE
                            review_reasons.append(f"Baserow sync retryable failure: {sync_err}")
                        elif t4_res.status == SyncStatus.FAILED_BLOCKED:
                            status = FileExecutionStatus.FAILED_BLOCKED
                            review_reasons.append(f"Baserow sync blocked: {sync_err}")
                        else:
                            status = FileExecutionStatus.PENDING_SYNC
                            review_reasons.append(f"Baserow sync pending: {sync_err}")

                        t4_summary = f"Tool 4 — Media DB: {t4_res.status.value} ({sync_err}) — state preserved in outbox"
                        t4_stage = StageResult(
                            stage_name=StageName.TOOL_4_SYNC,
                            success=False,
                            summary=t4_summary,
                            error=sync_err,
                            details={
                                "status": t4_res.status.value,
                                "operation": tool4_operation,
                                "media_row_id": tool4_row_id,
                                "conflicts": t4_res.conflicts,
                                "diagnostic_notes": t4_res.diagnostic_notes,
                            },
                        )
                        self.registry.save_stage_checkpoint(
                            tracking_id=tracking_id,
                            stage_name=StageName.TOOL_4_SYNC.value,
                            input_path=current_path,
                            input_sha256=input_sha256,
                            status="FAILED",
                            summary=t4_stage.summary,
                            details=t4_stage.details,
                        )

                    stage_results.append(t4_stage)
                    self.reporter.report_stage_result(t4_stage)
                    self.logger.info("STAGE_COMPLETE", tool="tool_4", file_path=current_path, tracking_id=tracking_id, details=t4_stage.details)

                content_res = None
                if self.workflow == WorkflowType.ALL and self.tool5_service is not None:
                    t5_stage, content_res = self._run_tool_5(current_path, tracking_id, phase1_context=prop_final)
                    stage_results.append(t5_stage)
                    self.reporter.report_stage_result(t5_stage)
                    if content_res and content_res.review_required:
                        status = FileExecutionStatus.REVIEW_REQUIRED
                        if content_res.review_reason and content_res.review_reason not in review_reasons:
                            review_reasons.append(content_res.review_reason)

                return FileRunResult(
                    target_path=str(file_path),
                    tracking_id=tracking_id,
                    original_filename=orig_filename,
                    final_filename=current_path.name,
                    final_path=str(current_path),
                    status=status,
                    review_reasons=review_reasons,
                    stage_results=stage_results,
                    tool4_row_id=tool4_row_id,
                    tool4_operation=tool4_operation,
                    tool4_fields=tool4_fields,
                    tool4_sync_status=tool4_sync_status,
                    tool4_live_row=tool4_live_row,
                    content_discovery_result=content_res,
                )

            except Exception as e:
                # Per-file exception isolation: log and report failure, continue without crashing run
                err_msg = f"Unexpected processing failure: {e}"
                self.logger.error("FILE_PROCESSING_EXCEPTION", file_path=file_path, details={"error": str(e)})
                fail_stage = StageResult(
                    stage_name=StageName.TOOL_1_INITIAL,
                    success=False,
                    summary=f"Error: {err_msg}",
                    error=err_msg,
                )
                stage_results.append(fail_stage)
                if existing_tid:
                    try:
                        self.registry.save_stage_checkpoint(
                            tracking_id=existing_tid,
                            stage_name=StageName.TOOL_1_INITIAL.value,
                            input_path=file_path,
                            input_sha256=input_sha256,
                            status="FAILED",
                            summary=err_msg,
                            details={"error": err_msg},
                        )
                    except Exception:
                        pass
                return FileRunResult(
                    target_path=str(file_path),
                    tracking_id=existing_tid or "unknown",
                    original_filename=orig_filename,
                    final_filename=orig_filename,
                    final_path=str(file_path),
                    status=FileExecutionStatus.FAILED,
                    error=err_msg,
                    stage_results=stage_results,
                )


def create_main_tooling_service(
    config: Optional[AppConfig] = None,
    registry_path: Optional[Union[str, Path]] = None,
    log_file: Optional[Union[str, Path]] = None,
    workflow: Union[WorkflowType, str] = WorkflowType.ALL,
    dry_run: bool = False,
    verbose: bool = False,
    production: bool = False,
    max_retained_file_results: Optional[int] = 500,
    scratch_dir: Optional[Union[str, Path]] = None,
    min_scratch_bytes: int = 100 * 1024 * 1024,
    registry: Optional[LocalRegistry] = None,
    logger: Optional[UnifiedArchiveLogger] = None,
    reporter: Optional[TerminalReporter] = None,
    tool2_service: Optional[MediaDatabaseReviewService] = None,
    travel_service: Optional[TravelScheduleReviewService] = None,
    tool4_service: Optional[MediaDatabaseUpdaterService] = None,
    tool5_service: Optional[Any] = None,
    travel_schedule_path: Optional[Union[str, Path]] = None,
) -> MainToolingScriptService:
    """Factory creating a fully wired MainToolingScriptService with all dependencies."""
    from ..adapters.baserow import BaserowReferenceProvider
    from ..adapters.vedabase import VedabaseValidator
    from ..adapters.location import LocationLookupProvider
    from ..cli import create_media_db_updater_service
    from ..media_db_reviewer.baserow_provider import BaserowSnapshotProvider
    from ..renamer.service import RenamerApplicationService
    from ..travel_reviewer.reference_store import TravelReferenceStore

    if config is None:
        config = load_config()

    wf = WorkflowType(workflow) if isinstance(workflow, str) else workflow

    if registry is None:
        selected_reg = Path(registry_path) if registry_path else config.registry_path
        registry = LocalRegistry(selected_reg)

    if logger is None:
        selected_log = Path(log_file) if log_file else (getattr(config, "log_dir", Path(".renamer")) / "media-archive-tooling.log")
        logger = UnifiedArchiveLogger(log_path=selected_log, workflow=wf.value)

    if reporter is None:
        reporter = TerminalReporter(verbose=verbose, dry_run=dry_run)

    # Tool 1 receives no direct Baserow credentials/access (amendment section 1 & 5)
    provider = BaserowReferenceProvider()
    provider.load_all_references()
    vedabase_validator = VedabaseValidator()
    location_provider = LocationLookupProvider()
    parser = RenamerParser(
        categories_ref=provider.get_category_titles(),
        locations_ref=provider.get_known_locations(),
        countries_ref=provider.get_country_values(),
        vedabase_validator=vedabase_validator,
        location_lookup_provider=location_provider,
        registry=registry,
    )

    # Tool 2 Media Database Reviewer
    if tool2_service is None:
        t2_provider = BaserowSnapshotProvider(
            api_url=config.baserow_api_url,
            api_token=config.baserow_api_token,
            media_table_id=config.baserow_media_table_id,
            category_table_id=config.baserow_category_table_id,
            travel_schedule_table_id=config.baserow_travel_schedule_table_id,
            snapshot_path=config.baserow_snapshot_path,
        )
        tool2_service = MediaDatabaseReviewService(registry=registry, provider=t2_provider)

    # Tool 3 Travel Schedule Reviewer
    if travel_service is None:
        ref_path = Path(travel_schedule_path) if travel_schedule_path else Path(".renamer/reference/travel_schedule.json")
        ref_store = TravelReferenceStore(reference_path=ref_path, provider=None)
        renamer_app_service = RenamerApplicationService(registry=registry)
        travel_service = TravelScheduleReviewService(
            registry=registry,
            reference_store=ref_store,
            renamer_service=renamer_app_service,
        )

    # Tool 4 Media Database Updater
    if tool4_service is None:
        tool4_service = create_media_db_updater_service(
            registry=registry,
            config=config,
            tool2_service=tool2_service,
        )

    # Tool 5 Content Discoverer
    if tool5_service is None:
        from ..content_discoverer.service import ContentDiscovererService
        tool5_service = ContentDiscovererService(registry=registry)

    return MainToolingScriptService(
        registry=registry,
        logger=logger,
        reporter=reporter,
        parser=parser,
        tool2_service=tool2_service,
        travel_service=travel_service,
        tool4_service=tool4_service,
        tool5_service=tool5_service,
        workflow=wf,
        dry_run=dry_run,
        verbose=verbose,
        production=production,
        max_retained_file_results=max_retained_file_results,
        scratch_dir=scratch_dir,
        min_scratch_bytes=min_scratch_bytes,
    )
