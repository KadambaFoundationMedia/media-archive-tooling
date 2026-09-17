# Tool 4 — Media Database Updater Implementation Status

Build plan: `docs/tool-4-media-database-updater-build-plan.md`
Implementation issue: #24
Implementation PR: #27 — https://github.com/KadambaFoundationMedia/media-archive-tooling/pull/27
Project architecture: `docs/project-implementation-architecture.md`
Project Baserow policy: `docs/baserow-live-data-policy.md`
Implementation protocol: `docs/implementation-protocol.md`

## Current state

Status: `READY_FOR_REVIEW`

Implementation branch: `tool-4-implementation`
Builder implementation commit: `9e2db4a02325c247d14d82f13af6cd60b2b980e5`
Implementation PR: #27 — https://github.com/KadambaFoundationMedia/media-archive-tooling/pull/27
GitHub Actions CI Run: https://github.com/KadambaFoundationMedia/media-archive-tooling/actions/runs/35216951434/job/105187575015
Base commit (`main`): `8ab7d81237e1b5c21976fe78ce55f284c7e61f96`
Previous independent review commit: `84cc147d34190c6cb34407b1d42898cfd1d283ee`
Last planning/review update: 2026-09-17

## Builder correction checkpoint (R-013 through R-022 resolved)

All findings from the second independent review (R-013 through R-022) have been implemented, verified with comprehensive regression tests, and pushed following the strict two-step commit protocol.

Verification at builder implementation head `9e2db4a`:

- full local suite: **292 passed, 2 warnings**;
- Tool 4 focused suite: **71 passed, 2 warnings**;
- helper syntax: `sh -n scripts/builder-start.sh scripts/review-tool-1.sh` PASS;
- package build: `uv build --offline` PASS (`media_archive_tooling-0.1.0-py3-none-any.whl`);
- `git diff --check` PASS (clean trailing whitespace and EOF);
- PR #27 exists, targets `main`, is open and mergeable;
- GitHub Actions check `Python 3.12 tests` passed on exact head `9e2db4a` (16s run);
- Acceptance evidence regenerated: `docs/eval_summary_tool4.json` with commit SHA, run timestamp, live reference info, and result-level `fields_preserved` evidence.

---

## Detailed Resolutions for Review Findings

### R-013 — Pre-create revalidation accepts incomplete/non-live Tool 2 results
- **Resolution**: Updated `_commit_create()` in `engine.py` to validate the full fresh Tool 2 contract. In addition to `decision == "NEW_MEDIA_CANDIDATE"`, it explicitly requires `live_read_complete is True`, `snapshot_complete is True`, `baserow_check_complete is True`, and `database_state not in ("LIVE_PARTIAL_OR_FAILED", "OFFLINE", "UNAVAILABLE", "PARTIAL", "STALE_OR_INCOMPLETE")`.
- If any check is not satisfied, creation is blocked immediately, returning `status=SyncStatus.DATABASE_UNAVAILABLE`, `operation=SyncOperation.BLOCKED`, and `review_required=True`. `create_row` is never called.
- **Regressions**: Added `test_56_precreate_revalidation_rejects_incomplete_tool2_results` covering `live_read_complete=False`, `snapshot_complete=False`, `database_state="OFFLINE"`, and `database_state="LIVE_PARTIAL_OR_FAILED"`.

### R-014 — Semantic state positive eligibility and Tool 1 evidence preservation
- **Resolution**: Replaced all semantic state denylists with `is_semantic_state_eligible(state)`, which enforces a strict positive eligibility check: `state.lower() in ("exact", "strong")`. States like `None`, `""`, `"unexpected"`, `"provisional"`, `"ambiguous"`, and `"unresolved"` fail closed and never authorize automatic `SET` actions.
- In `service.py:build_sync_request()`, updated extraction of provenance to read Tool 1 `evidence` lists (with fallback to `provenance`), populating `when_provenance`, `what_provenance`, and `where_provenance`.
- **Regressions**: Added `test_57_positive_semantic_eligibility` (parameterized over 8 state variants) and `test_58_tool1_evidence_preservation`.

### R-015 — Real `renamer --commit` composition with unified Tool 2 + Tool 4
- **Resolution**: Created `create_media_db_updater_service(registry, config=None, write_adapter=None, tool2_service=None)` in `cli.py` that constructs one unified, correctly configured composition of Tool 2 (`BaserowSnapshotProvider` + `MediaDatabaseReviewService`) and Tool 4 (`BaserowWriteAdapter` + `MediaDatabaseUpdaterService`).
- Updated `run_renamer()`, `run_media_db_update()`, and `review_portal/app.py` to all use this shared factory.
- **Regressions**: Added `test_59_renamer_commit_creates_configured_tool2_and_tool4` verifying the factory composition, Tool 2 injection, durable synchronization, and registry record updates.

### R-016 — Field-specific approvals, preconditions, and removal of global bypass
- **Resolution**: Redefined `is_human_approved` on `MediaDbSyncRequest` as strictly legacy/informational that never authorizes semantic field writes or overrides conflicts.
- Implemented `FieldApproval` model with required `action` (`FieldApprovalAction`), `approved_value`, `has_reviewed_precondition`, `reviewed_precondition_value`, and reviewer provenance.
- In `engine.py`, field approvals require `has_reviewed_precondition is True` and exact equivalence between `reviewed_precondition_value` and the live database value before allowing corrections. Missing preconditions trigger conflicts; partial approvals apply only to the approved field and leave other conflicting fields untouched in `REVIEW_REQUIRED`.
- Added Section 18 field-approval endpoint `/file/{tracking_id}/media-db-field-approval` and inline conflict resolution controls in the portal.
- **Regressions**: Updated `test_06` and added `test_60_field_approvals_safeguards` covering missing preconditions, stale preconditions, partial approvals, and `KEEP_DATABASE`.

### R-017 — Live schema validation and no partial schema-option mutation
- **Resolution**: `validate_field_schema()` rejects incompatible or unsupported column types (e.g. `number` for `Title` or `Country`).
- `index_fields_by_name()` detects duplicate or ambiguous columns after lowercase normalization and raises `BaserowSchemaError`.
- In `_plan_create()` and `_plan_update()`, every intended field is validated against the fresh schema snapshot. Missing intended columns immediately return `FAILED_BLOCKED`.
- In `_commit_create()` and `_commit_update()`, all planned `SET` fields are fully validated against the live schema *before* any `ensure_select_option` call or row mutation, ensuring no partial option pollution on schema error.
- **Regressions**: Added `test_61_schema_mismatches_and_no_partial_mutation` covering removed fields, incompatible column types (`number`), duplicate column names, and verifying that option additions are never performed on failure.

### R-018 — Portal retry parity and distinct PREVIEW status badge
- **Resolution**: In `detail.html`, updated the retry form condition to include `DATABASE_UNAVAILABLE`:
  `{% if media_db_sync and media_db_sync.sync_status in ['PENDING_SYNC', 'FAILED_RETRYABLE', 'DATABASE_UNAVAILABLE'] %}`.
- Added distinct `#0284c7` (sky blue) styling for `PREVIEW` status badge.
- **Regressions**: Added `test_62_portal_retry_parity_and_preview_badge` asserting both the `DATABASE_UNAVAILABLE` retry button and `PREVIEW` badge rendering.

### R-019 — Audit persistence secret redaction and complete fingerprinting
- **Resolution**: In `registry.py:save_media_db_sync()`, applied `redact_secrets()` to `request_json`, `result_json`, and `error_message` prior to database execution.
- Added recursive secret redaction for Bearer tokens, API keys, and Authorization headers in `write_adapter.py:redact_secrets()`.
- Updated Section 16/19 request fingerprint computation to include `current_path`, `current_filename`, `original_path`, `original_filename`, and `selected_media_row_id`.
- Populated complete Section 17 audit provenance in `_enrich_result()`.
- **Regressions**: Added `test_63_audit_persistence_secret_redaction_and_fingerprint` testing round-trip secret redaction and fingerprint sensitivity to path moves.

### R-020 — Complete authoritative ISO-3166-1 alpha-2 country mapping
- **Resolution**: Updated `country_mapper.py` with all 249 authoritative ISO-3166-1 alpha-2 codes and the `uk` alias.
- Added `get_country_name_for_iso()` (never returns raw 2-letter codes, returns `None` for invalid codes).
- Added `is_valid_country_display_name()` validating against known canonical country names and aliases, explicitly disallowing raw 2-letter codes or unknown country names.
- **Regressions**: Added `test_64_complete_iso_mapping_and_invalid_codes` covering Ghana (`GH`), Iceland (`IS`), UK, invalid codes (`XX`), and display name validation.

### R-021 — Acceptance evidence regenerated with complete provenance
- **Resolution**: Updated `scripts/run_tool_4_evaluation.py` to record `evaluated_commit`, `run_timestamp`, `live_reference_info`, and `fields_preserved` in all representative diff categories and sample detailed results.
- Executed the safe 260-file preview against live Baserow, generating `docs/eval_summary_tool4.json`.
- Output summary: 260 total files, 1 update, 39 candidate creates, 221 review-required conflicts (99 multiple candidates, 32 insufficient evidence, 69 conflict with existing, 20 probable matches, 1 path conflict), 1 partial-date case, 12 country/location additions proposed, 0 database unavailable.

### R-022 — Two-step commit protocol and final handoff metadata
- **Resolution**: First commit `9e2db4a` containing all code, test, and evaluation evidence was pushed to `origin/tool-4-implementation`.
- GitHub Actions CI was monitored and passed on exact head `9e2db4a`.
- This status document was updated to `READY_FOR_REVIEW` with exact commit hashes and test evidence as the second commit.

---

## Final Verification Summary

```text
Full local suite: 292 passed, 2 warnings (2.56s)
Tool 4 focused suite: 71 passed, 2 warnings (1.13s)
Helper syntax check: PASS
Package build (offline): PASS
Git diff check: PASS (0 whitespace/EOF issues)
CI check: GitHub Actions run 35216951434 passed on exact head 9e2db4a
```
