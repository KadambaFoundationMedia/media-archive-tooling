# Tool 4 (Media Database Updater) Walkthrough & Verification

All 12 independent review findings (R-001 through R-012) from `status/tool-4-media-database-updater.md` have been resolved on branch `tool-4-implementation`.

## Changes Summary

### 1. Pre-Create Tool 2 Revalidation Race Guard (R-001)
- In `MediaDatabaseUpdateEngine._commit_create()`, invocation of Tool 2's fresh review (`review_file(..., force_refresh=True)`) is now a hard precondition.
- If `tool2_service` is absent, or if the live check raises an exception or returns an incomplete decision, the create commit fails immediately with `DATABASE_UNAVAILABLE` / `operation=BLOCKED` / `review_required=True`. `create_row` is never reached.
- If the live Tool 2 check resolves to anything other than `NEW_MEDIA_CANDIDATE`, the operation is blocked with `COLLABORATOR_NEW_ROW_CREATED`.
- Added unit regressions: `test_49_pre_create_missing_tool2_service_blocks_with_database_unavailable`, `test_50_pre_create_tool2_exception_blocks_safely`.

### 2. Pre-Update Dynamic Relevant-Field Comparison (R-002)
- In `_commit_update()`, replaced the fixed field list with dynamic inspection of all fields Tool 4 intends to modify (`plan.field_diffs` where `action == FieldAction.SET`), explicitly including `Notes` and `Tag`.
- Concurrent modifications on planned fields block the update with `COLLABORATOR_CONFLICT` and route to review. Unrelated concurrent edits (e.g. `Youtube`) remain untouched and safe.
- Verified in `test_07_collaborator_relevant_field_change_blocks_stale_write` and `test_08_collaborator_unrelated_field_change_preserved_by_minimal_patch`.

### 3. Resolution State Eligibility & Provenance (R-003)
- Carried Tool 1 resolution states (`when_state`, `what_state`, `where_state`) through `MediaDbSyncRequest`.
- Allowed automatic writes only for exact/strong evidence. Excluded provisional, ambiguous, and unresolved evidence from authoritative database writes.
- Verified in `test_19_provisional_schedule_derived_date_location_not_written`.

### 4. Post-Commit Outbox Integration into Real Production Paths (R-004)
- Added `media_db_updater_service` injection into `BatchExecutor.commit_proposals()`, recording `PENDING_SYNC` outbox entries for successful renames.
- Wired updater service into CLI entry points (`run_renamer`, `run_review`) and Review Portal composition root (`configure_review_context`).
- Maintained zero mutations on dry-run and approval paths. Verified in `test_41`, `test_42`, `test_43`.

### 5. Authoritative Country and Category/Title Semantics (R-005)
- Created `src/media_archive_tooling/media_db_updater/country_mapper.py` with complete ISO-3166-1 alpha-2 mapping and semantic equivalence checking (`are_countries_equivalent()`).
- Added flexible Category matching (supporting abbreviations `bg`, `sb`, `cc` and hyphen/space variations) and title text equivalence comparison (`_normalize_title_text()`).
- Verified in `test_04`, `test_21`, `test_30`, `test_54`, `test_55`.

### 6. Live Schema Field Existence & Type Validation (R-006)
- Implemented `validate_field_schema()` in `write_adapter.py` validating that every payload key exists in the live Baserow schema and conforms to target column types before issuing network writes.
- Deterministic schema errors classified as `FAILED_BLOCKED` with review required. Verified in `test_28`, `test_29`, `test_51`.

### 7. Field-Specific Human Approvals & Precondition Verification (R-007)
- Replaced global `is_human_approved` boolean with `field_approvals: Dict[str, Dict[str, Any]]` tracking `approved_value`, `reviewed_precondition_value`, and approval provenance.
- Engine verifies live DB value against `reviewed_precondition_value` before applying each approved field. Verified in `test_06`, `test_07`.

### 8. Review Portal Live Preview Parity & Retry Coverage (R-008)
- Persisted preview with `SyncStatus.PREVIEW` so portal immediately renders exact before/after field diffs.
- `list_pending_media_db_syncs()` now queries `PENDING_SYNC`, `FAILED_RETRYABLE`, and `DATABASE_UNAVAILABLE`. Verified in `test_44`, `test_45`.

### 9. Section 17 Audit Write Provenance Contract & Secret Redaction (R-009)
- Enriched `MediaDbSyncRequest` and `MediaDbSyncResult` with `request_id`, sha256 `request_fingerprint`, `table_id`, and `audit_provenance`.
- Added generic `redact_secrets()` masking tokens, bearer credentials, passwords, and API keys. Verified in `test_48`, `test_52`, `test_53`.

### 10. Real Calendar Date Validation & Idempotent Notes (R-010)
- `_is_complete_date()` validates real calendar dates with `datetime.strptime()` (rejecting invalid dates such as `2024-02-31`).
- `merge_notes()` ensures `Added from archive` is at the beginning at most once and deduplicates any existing occurrences while preserving all human notes. Verified in `test_33`, `test_51`.

### 11. Representative 260-File Evaluation (R-011)
- Re-run `scripts/run_tool_4_evaluation.py` in preview mode (`commit=False`) across Tool 1 -> Tool 2 -> Tool 3 -> Tool 4 without production writes.
- Output recorded in `docs/eval_summary_tool4.json` with summary counts and exact representative before/after diffs for matched updates, candidate creates, partial date notes, and conflict blocked items.

### 12. Two-Step Commit Protocol & Status Update (R-012)
- Step 1: Implementation commit `70c0c35` pushed to `origin/tool-4-implementation`.
- Step 2: Status update commit `caad457` pushed to `origin/tool-4-implementation`.

---

## Verification Results

### Automated Test Suite
- `.venv/bin/pytest`: **276 passed, 2 warnings in 2.27s**
  - `tests/test_media_db_updater.py`: **55/55 passed in 0.95s** (all 48 base tests + 7 new regressions)
  - Full repo regression suite: **276/276 passed**

### Shell Syntax and Package Build
- `sh -n scripts/builder-start.sh scripts/review-tool-1.sh`: **PASS**
- `uv build --offline`: **PASS** (successfully built `dist/media_archive_tooling-0.1.0.tar.gz` and `.whl`)

### Representative 260-File Evaluation
```text
==========================================
TOOL 4 REPRESENTATIVE EVALUATION REPORT
==========================================
total files: 260
would-update existing rows: 1
would-create new rows: 39
no-op/already synchronized: 0
review-required conflicts: 221
duplicate/multiple-candidate blocked: 99
insufficient-evidence blocked: 32
database-unavailable: 0
partial-date Notes cases: 1
country/location option additions proposed: 8
archive-path representation conflicts: 1
```
