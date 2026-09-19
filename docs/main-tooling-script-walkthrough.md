# Main Tooling Script — Phase A Walkthrough & Verification Report

Build plan: `docs/main-tooling-script-build-plan.md`  
Status: `READY_FOR_REVIEW`  
Implementation branch: `main-tooling-script-implementation`  
PR: [#40](https://github.com/KadambaFoundationMedia/media-archive-tooling/pull/40)  

---

## 1. Executive Summary

Phase A of the unnumbered **Main Tooling Script** orchestrator (`media-archive run <targets>`) has been built, tested, and hermetically verified. The Main Tooling Script coordinates the complete Tools 1–4 pipeline:

```text
Tool 1 initial interpretation
→ Tool 2 live read-only Media review
→ Tool 3 verified travel-schedule review
→ Tool 1 final proposal and immediate live commit when allowed
→ Tool 4 synchronization (preview or live write)
```

The orchestrator operates directly on archive files in live mode without interactive confirmation prompts. It preserves all established safety, access boundary, and provenance contracts:
- **Tool 1**: No Baserow access; interprets names, coordinates enrichments, forms proposals, and executes filesystem renames.
- **Tool 2**: Read-only live Baserow lookup, candidate retrieval, and reconciliation.
- **Tool 3**: Offline static `travel_schedule.json` verification with SHA-256 integrity validation; zero network access.
- **Tool 4**: Sole Baserow writer; invoked only after Tool 1 final proposal/rename; performs fresh precondition checks and durable `PENDING_SYNC` tracking.
- **Unified Logging**: Single canonical append-only JSONL log (`.renamer/media-archive-tooling.log`) recording structured entries with run IDs and secret redaction.
- **Review Portal**: Active evaluation queue (`/`) filters out clean completed files and isolates only items requiring human review.

---

## 2. Implemented Components

### 2.1 Target Discovery (`src/media_archive_tooling/orchestrator/discovery.py`)
- Resolves single files, multiple files, directories recursively, and mixed inputs.
- Deduplicates targets by real filesystem path (`os.path.realpath`) and sorts deterministically.
- Validates media extensions (`.mp3`, `.wav`, `.m4a`, `.mp4`, `.mov`, `.wma`, `.avi`, `.mkv`, etc.).
- Skips unsupported files (e.g. `.zip`, `.txt`, `.ds_store`) with non-fatal user notices without aborting the batch.

### 2.2 Domain Models (`src/media_archive_tooling/orchestrator/models.py`)
- `WorkflowType`: `ALL` (default), `RENAMER`, `PROCESSING`.
- `StageName`: `TOOL_1_INITIAL`, `TOOL_2_REVIEW`, `TOOL_3_REVIEW`, `TOOL_1_FINALIZE`, `TOOL_4_SYNC`.
- `FileExecutionStatus`: `COMPLETED`, `DRY_RUN`, `UNCHANGED`, `REVIEW_REQUIRED`, `PENDING_SYNC`, `FAILED`.
- `StageResult`, `FileRunResult`, and `RunSummary` dataclasses.

### 2.3 Unified Archive Logger (`src/media_archive_tooling/orchestrator/logger.py`)
- Unique run ID generation (`run_YYYYMMDD_HHMMSS_<hex>`).
- Canonical JSONL log destination at `.renamer/media-archive-tooling.log`.
- Immediate line flushing and automatic redaction of API keys, bearer tokens, and secrets.

### 2.4 Terminal Reporter (`src/media_archive_tooling/orchestrator/reporter.py`)
- Concise terminal output displaying per-file progress across Tools 1–4.
- `--verbose` mode showing detailed field diffs, candidate match reasons, and travel matches.
- End-of-run summary banner with comprehensive metrics and actionable review portal command when evaluation is required.

### 2.5 Main Tooling Service (`src/media_archive_tooling/orchestrator/service.py`)
- `MainToolingScriptService`: Orchestrates the 5 stages per file sequentially.
- In `--dry-run` mode: uses projected filenames/paths to preview Tool 4 without filesystem or Baserow changes.
- In live mode: executes Tool 1 final rename, then invokes Tool 4 sync with durable `PENDING_SYNC` state.
- Handles per-file error isolation: a failure on one file does not halt processing of subsequent files.
- `create_main_tooling_service()` factory cleanly wires shared configuration, registry, and service adapters.

### 2.6 CLI Integration (`src/media_archive_tooling/cli.py`)
- Added `media-archive run <targets>` command with flags:
  - `--dry-run`: Zero filesystem or database mutations.
  - `--verbose`: Detailed terminal output.
  - `--workflow {all,renamer,processing}`: Workflow selection (`all` default; `processing` exits code 1).
  - `--registry-path`: Custom registry path.
  - `--log-file`: Custom persistent log path.
  - `--review-portal`: Optional flag to launch review portal server after run.

### 2.7 Portal Active Queue Isolation (`src/media_archive_tooling/renamer/service.py` & `review_portal/`)
- `RenamerApplicationService.list_files(filter_mode="evaluation")` returns only items requiring active evaluation:
  - Files with unresolved review reasons, blocked status, schedule conflicts, database unavailability, or failed sync.
  - Clean completed items (`status="committed"` and `sync_status="SYNCED"`) are excluded.
- Review portal default view set to Active Evaluation Queue (`/`).

---

## 3. Automated Test Verification

### 3.1 Main Tooling Script Test Suite (`tests/test_main_script.py`)
All 36 test scenarios specified in Section 17 of `docs/main-tooling-script-build-plan.md` are implemented and passing:

```text
tests/test_main_script.py::test_01_one_explicit_media_file PASSED
tests/test_main_script.py::test_02_multiple_explicit_files PASSED
tests/test_main_script.py::test_03_recursive_folder_discovery PASSED
tests/test_main_script.py::test_04_mixed_files_folders_and_duplicate_removal PASSED
tests/test_main_script.py::test_05_unsupported_file_skipping PASSED
tests/test_main_script.py::test_06_single_file_scope_does_not_mutate_siblings PASSED
tests/test_main_script.py::test_07_default_workflow_is_all PASSED
tests/test_main_script.py::test_08_phase_a_all_runs_tools_1_to_4_and_reports_pending_tools_honestly PASSED
tests/test_main_script.py::test_09_renamer_workflow_runs_tools_1_to_4 PASSED
tests/test_main_script.py::test_10_unavailable_processing_fails_before_mutation PASSED
tests/test_main_script.py::test_11_no_interactive_prompt_in_live_mode PASSED
tests/test_main_script.py::test_12_live_mode_renames_original_file PASSED
tests/test_main_script.py::test_13_tool4_called_only_after_successful_finalization_rename PASSED
tests/test_main_script.py::test_14_dry_run_makes_no_filesystem_mutation PASSED
tests/test_main_script.py::test_15_dry_run_makes_no_baserow_mutation PASSED
tests/test_main_script.py::test_16_dry_run_tool4_request_uses_projected_final_filename_path PASSED
tests/test_main_script.py::test_17_dry_run_create_reports_no_fabricated_row_id PASSED
tests/test_main_script.py::test_18_tool2_remains_read_only PASSED
tests/test_main_script.py::test_19_tool3_has_no_baserow_access PASSED
tests/test_main_script.py::test_20_tool4_remains_only_writer PASSED
tests/test_main_script.py::test_21_concise_terminal_output_contains_separate_tool_summaries PASSED
tests/test_main_script.py::test_22_verbose_terminal_output_adds_detail_without_secrets PASSED
tests/test_main_script.py::test_23_exact_planned_written_baserow_fields_displayed PASSED
tests/test_main_script.py::test_24_created_selected_row_id_displayed_when_available PASSED
tests/test_main_script.py::test_25_one_persistent_log_file_appended_across_two_runs PASSED
tests/test_main_script.py::test_26_log_entries_contain_run_ids_and_tool_file_context PASSED
tests/test_main_script.py::test_27_secrets_are_redacted_from_logs_and_errors PASSED
tests/test_main_script.py::test_28_completed_items_do_not_enter_active_portal_evaluation_queue PASSED
tests/test_main_script.py::test_29_blocked_conflict_failure_items_enter_evaluation_queue_with_reasons PASSED
tests/test_main_script.py::test_30_allowed_partial_date_location_can_continue_without_unnecessary_review PASSED
tests/test_main_script.py::test_31_one_per_file_failure_does_not_stop_later_independent_files PASSED
tests/test_main_script.py::test_32_global_configuration_failure_occurs_before_mutation PASSED
tests/test_main_script.py::test_33_rename_success_plus_tool4_failure_preserves_rename_and_durable_pending_sync PASSED
tests/test_main_script.py::test_34_rerun_idempotency_does_not_create_duplicate_baserow_row PASSED
tests/test_main_script.py::test_35_existing_cli_commands_remain_regression_safe PASSED
tests/test_main_script.py::test_36_full_pipeline_practical_oslo_and_czech_duben_patterns PASSED
============================== 36 passed in 1.14s ==============================
```

### 3.2 Full Project Regression Suite
The entire repository test suite passes with zero regressions across all tools:

```text
======================= 394 passed, 2 warnings in 5.17s ========================
```

### 3.3 Package Build
Package builds cleanly offline:

```text
Successfully built dist/media_archive_tooling-0.1.0.tar.gz
Successfully built dist/media_archive_tooling-0.1.0-py3-none-any.whl
```

---

## 4. Practical Dry-Run & Hermetic Evaluations

### 4.1 Single Explicit File Dry-Run
```bash
uv run media-archive run "sample-files/2011-08-20_KKS_Jaya-radha-madhava_oslo_fi.mp3" --dry-run
```
**Outcome**:
- Discovered 1 media file.
- Executed Tools 1–4 preview sequentially.
- Reported run summary with review portal routing instructions.
- Zero filesystem or database modifications.

### 4.2 Multiple Explicit Files Dry-Run
```bash
uv run media-archive run "sample-files/2011-08-20_KKS_Jaya-radha-madhava_oslo_fi.mp3" "sample-files/2011-08-20_KKS_SB-1-19-31_fi.mp3" --dry-run
```
**Outcome**:
- Processed both files independently with separate per-tool output.
- Reported aggregate summary metrics cleanly.

### 4.3 Recursive Directory & Unsupported File Skipping
```bash
uv run media-archive run "sample-files/Anti-test.zip" "sample-files/2008" --dry-run
```
**Outcome**:
- Recursively discovered 9 media files inside `sample-files/2008`.
- Safely skipped `Anti-test.zip` with notice: `Notice: Skipped 1 unsupported file(s) (non-media or ignored artifacts).`
- Successfully corroborated Prague travel schedule entries (`Prague-cz`) for 2008-04 lecture dates.

### 4.4 Live Hermetic Test
Tested full pipeline execution with in-memory Baserow double:
- Input: `2022-09-19_KKS_Oslo.mp3`.
- Tool 1 initial parse: `2022-09-19`, `Oslo`.
- Tool 2 match: row `1234` (`SB 1.2.19`).
- Tool 3 review: corroboration of Oslo Norway.
- Tool 1 final proposal: `2022-09-19_KKS_SB-1-2-19_Oslo-no.mp3` (`can commit`).
- Live rename committed to disk: original file removed; canonical file exists with intact content.
- Tool 4 update: row `1234` updated with `Filename: 2022-09-19_KKS_SB-1-2-19_Oslo-no.mp3` and `media_archive_path: ...`.
- Outbox status: `SYNCED`.
- JSONL log entries: 14 entries recorded with run ID and secrets redacted.

### 4.5 Active Evaluation Queue Verification
Tested review portal dashboard (`/`):
- Completed items with `status='committed'` and `sync_status='SYNCED'` do not appear in the active evaluation queue.
- Items with review reasons, conflicts, or failed syncs appear in the active evaluation queue with their exact review reasons rendered.
