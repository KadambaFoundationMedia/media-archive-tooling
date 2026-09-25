"""Terminal progress reporter, verbose inspection, and run summary presentation."""
from pathlib import Path
import time
from typing import Any, Dict, List, Optional

from .models import FileExecutionStatus, FileRunResult, RunSummary, StageName, StageResult, WorkflowType
from .logger import redact_secrets


class TerminalReporter:
    """Formats and prints concise human-readable output and run summaries to stdout/stderr."""

    def __init__(self, verbose: bool = False, dry_run: bool = False):
        self.verbose = verbose
        self.dry_run = dry_run
        self._tool5_started_at: Optional[float] = None
        self._tool5_last_heartbeat_at: Optional[float] = None

    @staticmethod
    def _brief(value: Any, limit: int = 120) -> str:
        clean = " ".join(str(redact_secrets(value)).split())
        return clean if len(clean) <= limit else clean[: limit - 1] + "…"

    @staticmethod
    def _clock(seconds: Any) -> str:
        try:
            total = max(0, int(float(seconds)))
        except (TypeError, ValueError):
            return "?"
        return f"{total // 60}:{total % 60:02d}"

    def _stage_line(self, result: StageResult) -> str:
        d = result.details
        stage = result.stage_name
        if stage == StageName.TOOL_1_INITIAL and d:
            line = (f"Tool 1 — Renamer: {d.get('when') or 'date unknown'} | "
                    f"{d.get('what') or 'type unknown'} | {d.get('where') or 'location unknown'}")
            if d.get("proposed_filename"):
                line += f"\n    Draft: {d['proposed_filename']}"
            return line
        if stage == StageName.TOOL_2_REVIEW and d.get("decision"):
            row = f", row #{d['selected_media_row_id']}" if d.get("selected_media_row_id") else ""
            count = d.get("candidate_count")
            candidates = f" ({count} candidate{'s' if count != 1 else ''}{row})" if count is not None else row
            line = f"Tool 2 — Media DB: {d['decision']}{candidates}"
            preview = d.get("candidate_preview") or {}
            if preview.get("row_id"):
                def option_text(value: Any) -> str:
                    if isinstance(value, dict):
                        value = value.get("value") or value.get("name") or ""
                    return self._brief(value, 65) if value else ""

                title = option_text(preview.get("title"))
                place = option_text(preview.get("place"))
                country = option_text(preview.get("country"))
                location = ", ".join(item for item in (place, country) if item)
                kind = "Matched" if d.get("selected_media_row_id") == preview["row_id"] else "Candidate"
                facts = " | ".join(item for item in (title, location) if item)
                line += f"\n    {kind} row #{preview['row_id']}: {facts or 'details unavailable'}"
                for field, label in (
                    ("notes", "Notes"),
                    ("filename", "Filename"),
                    ("media_archive_path", "media_archive_path"),
                ):
                    if preview.get(field):
                        limit = 500 if self.verbose else 200
                        line += f"\n    {label}: {self._brief(preview[field], limit)}"
            return line
        if stage == StageName.TOOL_3_REVIEW and d.get("decision"):
            location = f" — {self._brief(d['location'], 70)}" if d.get("location") else ""
            return f"Tool 3 — Travel Schedule: {d['decision']}{location}"
        if stage == StageName.TOOL_1_FINALIZE and d.get("proposed_filename"):
            state = "review" if d.get("needs_review") else "ready"
            return f"Tool 1 — Final Name: {d['proposed_filename']} ({state})"
        if stage == StageName.TOOL_4_SYNC:
            first_line = result.summary.splitlines()[0].split(" [", 1)[0]
            fields = d.get("fields") or {}
            if fields:
                key_fields = [f"{key}={self._brief(fields[key], 48)}" for key in ("Title", "Category", "Date") if key in fields]
                suffix = "; ".join(key_fields)
                if suffix:
                    return f"{first_line} — {suffix} ({len(fields)} fields)"
                return f"{first_line} ({len(fields)} fields)"
            return self._brief(first_line, 180)
        if stage == StageName.TOOL_5_CONTENT_DISCOVERY and d.get("classification"):
            type_confidence = d.get("content_confidence") or d.get("confidence", "?")
            boundary = ""
            if d.get("cut_point_seconds") is not None:
                cut_confidence = d.get("boundary_confidence") or d.get("confidence", "?")
                boundary = f" | proposed cut {self._clock(d['cut_point_seconds'])} ({cut_confidence} boundary)"
            route = " | Tool 6 next" if d.get("process_by_tool_6") else " | review" if d.get("review_required") else ""
            return f"Tool 5 — Content: {d['classification']} ({type_confidence} type){boundary}{route}"
        if stage == StageName.TOOL_6_FILE_CUTTER and d:
            if d.get("success"):
                cut = self._clock(d.get("cut_point_seconds"))
                singing = Path(d.get("singing_output_path") or "").name
                class_file = Path(d.get("class_output_path") or "").name
                return f"Tool 6 — Cutter: split at {cut} → {singing} + {class_file}"
            if d.get("review_required"):
                return "Tool 6 — Cutter: review required"
        return self._brief(result.summary.splitlines()[0], 180)

    def report_startup(self, workflow: WorkflowType, target_count: Optional[int] = None) -> None:
        mode = "DRY-RUN (no media or Baserow writes)" if self.dry_run else "LIVE (changes apply without prompt)"
        print(f"Media Archive — {mode} | workflow: {workflow.value}")
        if target_count is not None:
            print(f"Files: {target_count}")
        if workflow != WorkflowType.RENAMER:
            print("Tools 7–11 pending")
        print()

    def report_file_start(self, file_path: Path, index: int, total: Optional[int] = None) -> None:
        self._tool5_started_at = None
        self._tool5_last_heartbeat_at = None
        number = f"{index}/{total}" if total is not None else str(index)
        if self.verbose:
            label = str(file_path)
        else:
            try:
                label = str(file_path.relative_to(Path.cwd()))
            except ValueError:
                label = str(file_path)
        print(f"[{number}] {label}")

    def report_stage_result(self, result: StageResult) -> None:
        print(f"  {redact_secrets(self._stage_line(result))}")
        if result.stage_name == StageName.TOOL_4_SYNC and "Verified live row #" in result.summary:
            row_id = result.details.get("media_row_id")
            if row_id:
                print(f"    Verified live row #{row_id}")
        if self.verbose and result.details:
            clean_details = redact_secrets(result.details)
            for k, v in clean_details.items():
                if k == "live_row":
                    print("    - live_row: full readback in log file")
                elif k == "fields" and isinstance(v, dict):
                    for field, value in v.items():
                        print(f"    - field {field}: {self._brief(value, 180)}")
                elif v is not None:
                    print(f"    - {k}: {self._brief(v, 180)}")
        if result.stage_name == StageName.TOOL_5_CONTENT_DISCOVERY:
            print()

    def report_tool5_progress(self, stage: str, elapsed_seconds: float, status: str) -> None:
        if not self.verbose:
            if stage == "cache":
                print("  Tool 5 — Reusing cached analysis", flush=True)
                return
            now = time.monotonic()
            if self._tool5_started_at is None:
                self._tool5_started_at = now
                self._tool5_last_heartbeat_at = now
                print("  Tool 5 — Analysing audio…", flush=True)
            elif self._tool5_last_heartbeat_at is not None and now - self._tool5_last_heartbeat_at >= 30:
                self._tool5_last_heartbeat_at = now
                print(f"  Tool 5 — Still analysing ({self._clock(now - self._tool5_started_at)} elapsed)", flush=True)
            return
        if stage == "cache":
            print("  Tool 5 — Reusing saved transcript", flush=True)
            return
        labels = {
            "decode": "Converting audio",
            "decode_slice": "Decoding excerpt",
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
            print(f"  Result: {status_label} ({len(file_result.review_reasons)} reason(s); see review portal)")
            for reason in file_result.review_reasons[:2 if not self.verbose else None]:
                short_reason = str(reason).split(" (", 1)[0] if not self.verbose else reason
                print(f"    - {self._brief(short_reason, 120 if not self.verbose else 240)}")
            if not self.verbose and len(file_result.review_reasons) > 2:
                print(f"    - {len(file_result.review_reasons) - 2} more in log/portal")
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
        print(f"Summary: {summary.total_discovered} file(s) | {summary.completed} completed | "
              f"{summary.dry_run_previews} previews | {summary.review_required} review | {summary.failed} failed")
        if summary.pending_sync or summary.database_unavailable or summary.failed_retryable or summary.failed_blocked:
            print(f"Sync: {summary.pending_sync} pending | {summary.database_unavailable} DB unavailable | "
                  f"{summary.failed_retryable} retryable | {summary.failed_blocked} blocked")
        if summary.unchanged or summary.skipped_unsupported:
            print(f"Other: {summary.unchanged} unchanged | {summary.skipped_unsupported} unsupported")
        print(f"Log: {summary.log_path}")
        if self.verbose:
            print(f"Run ID: {summary.run_id} | Registry: {summary.registry_path}")
