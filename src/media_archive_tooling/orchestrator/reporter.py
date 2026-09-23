"""Terminal progress reporter, verbose inspection, and run summary presentation."""
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

from .models import FileExecutionStatus, FileRunResult, RunSummary, StageResult, WorkflowType
from .logger import redact_secrets


class TerminalReporter:
    """Formats and prints concise human-readable output and run summaries to stdout/stderr."""

    def __init__(self, verbose: bool = False, dry_run: bool = False):
        self.verbose = verbose
        self.dry_run = dry_run

    def report_startup(self, workflow: WorkflowType, target_count: int) -> None:
        if self.dry_run:
            print("=== [DRY-RUN MODE] Media Archive Tooling Runner ===")
            print("Dry-run preview active: no filesystem or Baserow mutations will be made.")
        else:
            print("=== [LIVE MODE] Media Archive Tooling Runner ===")
            print("Operating directly on files; authorized renames and Baserow mutations will occur without prompt.")

        if workflow == WorkflowType.ALL:
            print("Workflow: all (Tools 1–5 active; Tools 6–11 pending)")
        elif workflow == WorkflowType.RENAMER:
            print("Workflow: renamer (Phase A: Tools 1–4 active)")
        elif workflow == WorkflowType.PROCESSING:
            print("Workflow: processing (Tool 5 active for Phase 1-tracked files; Tools 6–11 pending)")

        print(f"Discovered {target_count} media file(s) for processing.\n")

    def report_file_start(self, file_path: Path, index: int, total: int) -> None:
        print(f"[{index}/{total}] Processing file: {file_path}")

    def report_stage_result(self, result: StageResult) -> None:
        print(f"  {result.summary}")
        if self.verbose and result.details:
            clean_details = redact_secrets(result.details)
            for k, v in clean_details.items():
                if result.summary.startswith("Tool 4 —") and k in {"fields", "live_row"}:
                    # Tool 4 already prints these values in its summary. Avoid
                    # repeating large Notes fields and nested Baserow rows.
                    continue
                if v:
                    print(f"    - {k}: {v}")

    def report_tool5_progress(self, stage: str, elapsed_seconds: float, status: str) -> None:
        if stage == "cache":
            print("  Tool 5 — Reusing saved transcript", flush=True)
            return
        labels = {
            "decode": "Converting audio",
            "transcribe_metal": "Transcribing on Metal",
            "transcribe_cpu": "Transcribing on CPU",
        }
        label = labels.get(stage, stage)
        if status == "start":
            print(f"  Tool 5 — {label}…", flush=True)
        elif status == "heartbeat":
            print(f"  Tool 5 — {label}: {elapsed_seconds:.0f}s elapsed", flush=True)
        elif status in {"done", "failed"}:
            outcome = "finished" if status == "done" else "failed"
            print(f"  Tool 5 — {label} {outcome} after {elapsed_seconds:.1f}s", flush=True)

    def report_file_result(self, file_result: FileRunResult) -> None:
        status_label = file_result.status.value.upper()
        if file_result.status == FileExecutionStatus.COMPLETED:
            print(f"  Result: {status_label} (renamed and synchronized)")
        elif file_result.status == FileExecutionStatus.DRY_RUN:
            print(f"  Result: {status_label} (preview complete; no disk or database changes)")
        elif file_result.status == FileExecutionStatus.UNCHANGED:
            print(f"  Result: {status_label} (already has canonical name and sync state)")
        elif file_result.status == FileExecutionStatus.REVIEW_REQUIRED:
            reasons_str = "; ".join(file_result.review_reasons) if file_result.review_reasons else "Needs review"
            print(f"  Result: {status_label} — {reasons_str}")
        elif file_result.status == FileExecutionStatus.PENDING_SYNC:
            print(f"  Result: {status_label} (file renamed; Baserow sync pending/retryable)")
        elif file_result.status == FileExecutionStatus.DATABASE_UNAVAILABLE:
            reasons_str = "; ".join(file_result.review_reasons) if file_result.review_reasons else "Baserow database unavailable"
            print(f"  Result: {status_label} (file processed; Baserow unavailable) — {reasons_str}")
        elif file_result.status == FileExecutionStatus.FAILED_RETRYABLE:
            reasons_str = "; ".join(file_result.review_reasons) if file_result.review_reasons else "Sync failed (retryable)"
            print(f"  Result: {status_label} (file processed; sync retryable) — {reasons_str}")
        elif file_result.status == FileExecutionStatus.FAILED_BLOCKED:
            reasons_str = "; ".join(file_result.review_reasons) if file_result.review_reasons else "Sync failed (blocked)"
            print(f"  Result: {status_label} (file processed; sync blocked) — {reasons_str}")
        elif file_result.status == FileExecutionStatus.FAILED:
            err_msg = file_result.error or "Unknown error"
            print(f"  Result: {status_label} — {err_msg}")
        print()

    def report_skipped_unsupported(self, count: int, skipped_files: List[Path]) -> None:
        if count > 0:
            print(f"Notice: Skipped {count} unsupported file(s) (non-media or ignored artifacts).")
            if self.verbose:
                for f in skipped_files[:10]:
                    print(f"  - Skipped: {f.name}")
                if len(skipped_files) > 10:
                    print(f"  ... and {len(skipped_files) - 10} more.")
            print()

    def report_summary(self, summary: RunSummary) -> None:
        print("===================== Run Summary =====================")
        print(f"Run ID:                      {summary.run_id}")
        print(f"Workflow:                    {summary.workflow.value}")
        print(f"Mode:                        {'DRY-RUN' if summary.is_dry_run else 'LIVE'}")
        print(f"Total Discovered Media:      {summary.total_discovered}")
        print(f"Completed (Committed):       {summary.completed}")
        print(f"Dry-Run Previews:            {summary.dry_run_previews}")
        print(f"Unchanged / No-Op:           {summary.unchanged}")
        print(f"Items Requiring Evaluation:  {summary.review_required}")
        print(f"Pending Baserow Sync:        {summary.pending_sync}")
        if summary.database_unavailable > 0:
            print(f"Database Unavailable:        {summary.database_unavailable}")
        if summary.failed_retryable > 0:
            print(f"Failed Retryable Sync:       {summary.failed_retryable}")
        if summary.failed_blocked > 0:
            print(f"Failed Blocked Sync:         {summary.failed_blocked}")
        print(f"Failed Files:                {summary.failed}")
        print(f"Skipped Unsupported Files:   {summary.skipped_unsupported}")
        print(f"Log File:                    {summary.log_path}")
        print(f"Registry Database:           {summary.registry_path}")

        if summary.workflow in (WorkflowType.ALL, WorkflowType.PROCESSING):
            print("Note:                        Processing workflow (Tools 6–11) is pending and not yet installed.")

        needs_eval = (
            summary.review_required > 0
            or summary.pending_sync > 0
            or summary.database_unavailable > 0
            or summary.failed_retryable > 0
            or summary.failed_blocked > 0
        )
        if needs_eval:
            print("\nEvaluation required:")
            print("  Items have been routed to the local review portal queue.")
            print(f"  To review, run: media-archive review --registry-path \"{summary.registry_path}\"")
        print("=======================================================")
