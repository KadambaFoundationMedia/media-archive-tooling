"""Main Tooling Script application service coordinating discovery, Tools 1–4 pipeline, and audit trails."""
from datetime import datetime, timezone
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
        workflow: WorkflowType = WorkflowType.ALL,
        dry_run: bool = False,
        verbose: bool = False,
    ):
        self.registry = registry
        self.logger = logger
        self.reporter = reporter
        self.parser = parser
        self.tool2_service = tool2_service
        self.travel_service = travel_service
        self.tool4_service = tool4_service
        self.workflow = workflow
        self.dry_run = dry_run
        self.verbose = verbose

        self.planner_initial = RenamePlanner(mode=RenameMode.INITIAL)
        self.planner_finalize = RenamePlanner(mode=RenameMode.FINALIZE)

    def run(self, targets: List[Union[str, Path]]) -> RunSummary:
        """Execute the configured workflow over the target paths."""
        start_time = time.time()
        self.logger.info(
            "RUN_START",
            details={
                "workflow": self.workflow.value,
                "dry_run": self.dry_run,
                "verbose": self.verbose,
                "targets": [str(t) for t in targets],
            },
        )

        # 1. Validate workflow availability
        if self.workflow == WorkflowType.PROCESSING:
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
        discovery = discover_media_targets(targets)
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
                "media_files": [str(p) for p in discovery.media_files],
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
            skipped_files=[str(p) for p in discovery.skipped_unsupported_files],
            exit_code=0,
        )

        has_unexpected_failure = False

        for idx, media_path in enumerate(discovery.media_files, start=1):
            self.reporter.report_file_start(media_path, idx, len(discovery.media_files))
            file_result = self._process_single_file(media_path)
            summary.file_results.append(file_result)
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

    def _process_single_file(self, file_path: Path) -> FileRunResult:
        """Execute the continuous Phase A pipeline on an individual media file."""
        file_path = file_path.resolve()
        orig_filename = file_path.name
        stage_results: List[StageResult] = []
        review_reasons: List[str] = []

        try:
            # Sibling collection grammar (inspection only)
            parent_dir = file_path.parent
            sibling_names = [
                f.name for f in parent_dir.iterdir()
                if f.is_file() and not f.name.startswith(".")
            ] if parent_dir.exists() else [orig_filename]
            grammar = CollectionGrammar(parent_dir, sibling_names)

            # -------------------------------------------------------------
            # Stage 1: Tool 1 Initial Interpretation
            # -------------------------------------------------------------
            self.logger.info("STAGE_START", tool="tool_1", file_path=file_path, details={"stage": "initial"})
            existing_tid = self.registry.find_tracking_id_by_path(file_path)

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
            stage_results.append(t1_stage)
            self.reporter.report_stage_result(t1_stage)
            self.logger.info("STAGE_COMPLETE", tool="tool_1", file_path=file_path, tracking_id=tracking_id, details=t1_stage.details)

            # -------------------------------------------------------------
            # Stage 2: Tool 2 Live Read-Only Media Review
            # -------------------------------------------------------------
            self.logger.info("STAGE_START", tool="tool_2", file_path=file_path, tracking_id=tracking_id)
            t2_res: Optional[MediaDatabaseReviewResult] = None
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
                        # R-002: Surface diagnostic notes or concise reason when review_reasons is empty
                        reason = None
                        if t2_res.diagnostic_notes:
                            reason = f"Tool 2 media review required: {'; '.join(t2_res.diagnostic_notes)}"
                        else:
                            reason = f"Tool 2 media review required: {dec_str} (candidates: {cand_count})"
                        if reason not in review_reasons:
                            review_reasons.append(reason)

            stage_results.append(t2_stage)
            self.reporter.report_stage_result(t2_stage)
            self.logger.info("STAGE_COMPLETE", tool="tool_2", file_path=file_path, tracking_id=tracking_id, details=t2_stage.details)

            # -------------------------------------------------------------
            # Stage 3: Tool 3 Travel Schedule Review
            # -------------------------------------------------------------
            self.logger.info("STAGE_START", tool="tool_3", file_path=file_path, tracking_id=tracking_id)
            t3_res: Optional[TravelReviewResult] = None
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

                return FileRunResult(
                    target_path=str(file_path),
                    tracking_id=tracking_id,
                    original_filename=orig_filename,
                    final_filename=prop_final.proposed_filename,
                    final_path=str(file_path),
                    status=status,
                    review_reasons=prop_final.review_reasons,
                    stage_results=stage_results,
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

            stage_results.append(t4_stage)
            self.reporter.report_stage_result(t4_stage)
            self.logger.info("STAGE_COMPLETE", tool="tool_4", file_path=current_path, tracking_id=tracking_id, details=t4_stage.details)

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
    registry: Optional[LocalRegistry] = None,
    logger: Optional[UnifiedArchiveLogger] = None,
    reporter: Optional[TerminalReporter] = None,
    tool2_service: Optional[MediaDatabaseReviewService] = None,
    travel_service: Optional[TravelScheduleReviewService] = None,
    tool4_service: Optional[MediaDatabaseUpdaterService] = None,
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

    return MainToolingScriptService(
        registry=registry,
        logger=logger,
        reporter=reporter,
        parser=parser,
        tool2_service=tool2_service,
        travel_service=travel_service,
        tool4_service=tool4_service,
        workflow=wf,
        dry_run=dry_run,
        verbose=verbose,
    )
