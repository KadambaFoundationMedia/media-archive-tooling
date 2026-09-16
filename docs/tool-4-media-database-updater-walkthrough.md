# Tool 4 — Media Database Updater Walkthrough

## 1. Executive Summary

Tool 4 (**Media Database Updater**) provides the authoritative, safe, and auditable write-boundary between the local archive files (governed by Tool 1, Tool 2, and Tool 3) and the remote Baserow Media database table. Tool 4 is the **sole application component authorized to mutate Baserow Media-database state**.

Tool 4 adheres strictly to `docs/tool-4-media-database-updater-build-plan.md` and implements all 48 test scenarios specified in Section 22, along with the safe representative 260-file evaluation specified in Section 23.

---

## 2. Architecture & Implementation Components

### 2.1 Data Models (`src/media_archive_tooling/media_db_updater/models.py`)
- **`SyncStatus`**: `SYNCING`, `SYNCED`, `REVIEW_REQUIRED`, `FAILED_RETRYABLE`, `FAILED_BLOCKED`.
- **`SyncOperation`**: `CREATE`, `UPDATE`, `NOOP`, `BLOCKED`, `CONFLICT`.
- **`FieldAction`**: `SET`, `PRESERVED`, `CONFLICT`, `SKIPPED`.
- **`FieldDiff`**: Granular tracking of `field_name`, `old_value`, `new_value`, `action`, and human-readable `details`.
- **`MediaDbSyncRequest`**: Self-contained synchronization request capturing tracking ID, current/original paths, normalized WHEN/WHAT/WHERE attributes, parent folder context, and Tool 2/Tool 3 review decisions.
- **`MediaDbSyncResult`**: Structured synchronization result containing status, operation, Baserow row ID, list of `FieldDiff`s, modified field names, detected conflicts, diagnostic notes, attempt counts, and timestamp.

### 2.2 Write Adapter Layer (`src/media_archive_tooling/media_db_updater/write_adapter.py`)
- **`BaserowWriteAdapter`**:
  - Encapsulates all Baserow REST API communications (`GET /api/database/fields/table/...`, `POST /api/database/rows/table/...`, `PATCH /api/database/rows/table/{id}/...`, `PATCH /api/database/fields/...`).
  - Strict select-option management: new select options are allowed **only** for `Country` and `Place, location`. Category, Language, Status, and Tag select options are strictly immutable; missing options fail or route to review.
  - Automatic credential redaction: API tokens and credentials are encrypted/masked and never leak into logs or serialized audit records.
- **`FakeBaserowWriteAdapter`**:
  - In-memory mock adapter supporting full schema inspection, row creation, row patching, simulated network timeouts, and transport errors for 100% hermetic offline testing.

### 2.3 Update Engine (`src/media_archive_tooling/media_db_updater/engine.py`)
- **Tool 2 Gating (Section 10)**:
  - `EXISTING_MEDIA_MATCH` -> UPDATE planning.
  - `NEW_MEDIA_CANDIDATE` -> CREATE planning.
  - `MULTIPLE_CANDIDATES` / `CONFLICT_WITH_EXISTING` / `INSUFFICIENT_EVIDENCE` -> blocked with `REVIEW_REQUIRED` (zero first-match-wins or blind duplicate creation).
- **Pre-Write Race Guards (Section 7)**:
  - Pre-Create: Queries live Tool 2 immediately before creating a row to ensure a collaborator has not created a matching row concurrently.
  - Pre-Update: Queries fresh live row immediately before updating to ensure relevant fields have not been modified concurrently by a collaborator.
- **Minimal PATCH Generator (Section 8)**:
  - Updates only safe archive fields (`Filename`, `media_archive_path`, and enriched blank semantic fields).
  - Preserves unrelated online fields (`Youtube`, `Audio link`, `Youtube descr`, `Thumb image`, `Transcriber`, `Status Media`, `Status Transcript`).
- **Semantic Field Merge Rules (Sections 11-13)**:
  - Blank target field + trustworthy incoming value -> enriched.
  - Equivalent incoming and existing value -> no-op.
  - Populated conflict -> preserved in database, routes to review.
  - Complete dates (`YYYY-MM-DD`) written to `Date`.
  - Incomplete dates (e.g. `2014-08-DD`) preserved in `Notes` with idempotent marker `Incomplete recording date: ...`; `Date` field left empty.
  - `merge_notes`: Prepends `Added from archive` exactly once upon archive linkage; safely replaces incomplete-date marker when full date is resolved; preserves all human notes.

### 2.4 Service Orchestration (`src/media_archive_tooling/media_db_updater/service.py`)
- `build_sync_request`: Reconstructs complete sync context from local registry and latest Tool 2/Tool 3 evaluations.
- `preview`: Read-only calculation of field diffs and actions without performing any remote Baserow mutations (`commit=False`).
- `synchronize`: Full transactional workflow managing pre-write planning, pre-write race guards, write execution, and durable audit recording in SQLite.
- `retry_pending`: Re-evaluates pending or failed-retryable synchronizations using fresh live Baserow state.
- `reconcile_uncertain_create` / `reconcile_uncertain_update`: Reconciles transport timeouts by re-querying the database instead of blind duplicate writes.

### 2.5 Local Registry Persistence (`src/media_archive_tooling/renamer/registry/registry.py`)
- Created `media_db_syncs` table:
  - `tracking_id` (PRIMARY KEY)
  - `sync_status`, `operation_type`, `media_row_id`, `attempt_count`, `last_attempt_at`, `error_message`, `request_json`, `result_json`, `created_at`, `updated_at`.
- Added indexing: `idx_media_db_syncs_status` for fast lookup of pending syncs.
- Implemented CRUD methods: `save_media_db_sync`, `get_media_db_sync`, `list_media_db_syncs`, `list_pending_media_db_syncs`.

### 2.6 Tool 1 Post-Commit Integration (`src/media_archive_tooling/renamer/commit_service.py`)
- `RenameCommitService.commit_file()` now invokes Tool 4 synchronization immediately after a successful disk rename.
- **Durable Failure Guarantee**: If Baserow is unreachable or fails during post-rename sync, the disk rename is **not reverted**; instead, a durable record with status `PENDING_SYNC` is saved in the local registry for subsequent retries.

### 2.7 CLI Interface (`src/media_archive_tooling/cli.py`)
- Added `media-archive media-db-update` command:
  - `--commit`: Execute live mutation (default is safe dry-run / preview).
  - `--dry-run`: Explicit preview without mutation.
  - `--retry-pending`: Retry all unresolved/pending synchronizations with fresh state.
  - `--json`: Output full structured JSON results.
  - `--registry-path`: Specify custom registry database path.
  - Optional `tracking_id` positional argument to target a single file.

### 2.8 Review Portal Integration (`src/media_archive_tooling/review_portal/`)
- Added Tool 4 synchronization card in `detail.html`:
  - Displays current Baserow sync status badge, operation type, matched row ID, and last attempt time.
  - Displays granular field diff table showing before, after, and action (SET, PRESERVED, CONFLICT).
  - Action buttons for "Preview Sync" and "Commit Sync to Baserow".
- Added endpoint `POST /file/{tracking_id}/media-db-sync` in `app.py`:
  - Invokes `MediaDatabaseUpdaterService` with live revalidation.
  - Redirects back to file detail view with updated sync state.

---

## 3. Verification & Test Suite

### 3.1 All 48 Build Plan Tests Passing
The comprehensive test suite in `tests/test_media_db_updater.py` implements all 48 test scenarios specified in Section 22 of the build plan:

| # | Test Scenario | Status |
|---|---|:---:|
| 1 | Confirmed existing Tool 2 match updates only safe relevant fields | PASSED |
| 2 | Existing online links/transcript/audio fields survive unchanged | PASSED |
| 3 | Blank trusted semantic field is enriched | PASSED |
| 4 | Equivalent semantic field is a no-op | PASSED |
| 5 | Conflicting populated semantic field is preserved and routed to review | PASSED |
| 6 | Explicit human overwrite is live-revalidated before write | PASSED |
| 7 | Collaborator relevant-field change between review and write blocks stale write | PASSED |
| 8 | Collaborator unrelated-field change is preserved and not erased by minimal PATCH | PASSED |
| 9 | Current `NEW_MEDIA_CANDIDATE` creates one row with required defaults | PASSED |
| 10 | Collaborator-created matching row between initial review and create prevents duplicate creation | PASSED |
| 11 | Database failure never becomes a no-match/create decision | PASSED |
| 12 | Multiple candidates never create first-match-wins or new duplicate row | PASSED |
| 13 | Repeated create/update synchronization is idempotent | PASSED |
| 14 | Timeout/uncertain create outcome is reconciled before retry | PASSED |
| 15 | Timeout/uncertain update outcome is reconciled before retry | PASSED |
| 16 | Full trusted recording date writes `Date` as `YYYY-MM-DD` | PASSED |
| 17 | Partial date leaves `Date` empty and writes deterministic incomplete-date Notes marker | PASSED |
| 18 | Later full date fills `Date` and safely removes only incomplete-date marker | PASSED |
| 19 | Provisional schedule-derived date/location not written as authoritative Media metadata | PASSED |
| 20 | Title priority: filename title -> parent-folder title -> filename fallback | PASSED |
| 21 | Category abbreviation maps only to valid live Category option | PASSED |
| 22 | Missing Category option does not get invented automatically | PASSED |
| 23 | Scripture verse/reference produces expected Tag behavior | PASSED |
| 24 | Existing multi-value Tags preserved when adding approved tag | PASSED |
| 25 | Language defaults to existing English option on new row | PASSED |
| 26 | `Status Media`, `Status thumb`, `Status Transcript` default to `Not-started` | PASSED |
| 27 | Missing required status/language option blocks rather than inventing taxonomy | PASSED |
| 28 | New legitimate country option can be added safely and then assigned | PASSED |
| 29 | New legitimate location option can be added safely and then assigned | PASSED |
| 30 | Equivalent country/location option is reused rather than duplicated | PASSED |
| 31 | Ambiguous similar location options route to review | PASSED |
| 32 | Select-option schema update preserves all existing options | PASSED |
| 33 | Notes begins with `Added from archive` exactly once and preserves human notes | PASSED |
| 34 | New-row timestamps/default dates are populated as specified | PASSED |
| 35 | Existing `Created_on`/`imported_on` not reset on ordinary rename updates | PASSED |
| 36 | `Last modified` and `Last modified by` follow specified current-date write rule | PASSED |
| 37 | `Media Archive link` remains untouched/empty and is never auto-derived | PASSED |
| 38 | `media_archive_path` stores full current path | PASSED |
| 39 | Same tracked file rename safely updates Filename/path from old to new | PASSED |
| 40 | Different unproven archive representation does not overwrite existing path | PASSED |
| 41 | Tool 1 successful commit records/initiates Tool 4 synchronization | PASSED |
| 42 | Tool 1 mere approval/dry-run does not write Baserow | PASSED |
| 43 | Tool 4 failure after rename leaves durable pending sync without reverting file | PASSED |
| 44 | Retry of pending sync uses fresh current Tool 2/Baserow state | PASSED |
| 45 | Portal mutating action calls service layer and revalidates live state | PASSED |
| 46 | CLI dry-run performs zero mutations | PASSED |
| 47 | CLI explicit commit invokes same service used by Tool 1/portal | PASSED |
| 48 | Audit record contains before/after/provenance without credentials | PASSED |

### 3.2 Full Project Regression Suite
- Total tests executed across entire repository: **269 tests**.
- Total passing: **269 passed** (100% pass rate).
- `sh -n scripts/builder-start.sh scripts/review-tool-1.sh`: passed.
- `uv build --offline`: successfully built package wheels and source tarball.

---

## 4. Representative 260-File Sample Evaluation

Executed `scripts/run_tool_4_evaluation.py` in safe write-preview mode (`commit=False`) against the 260 files in `sample-files/` using live Baserow snapshot context. Evidence saved to `docs/eval_summary_tool4.json`.

```text
==========================================
TOOL 4 REPRESENTATIVE EVALUATION REPORT
==========================================
total files: 260
would-update existing rows: 1
would-create new rows: 39
no-op/already synchronized: 0
review-required conflicts: 246
duplicate/multiple-candidate blocked: 99
insufficient-evidence blocked: 32
database-unavailable: 0
partial-date Notes cases: 1
country/location option additions proposed: 13
archive-path representation conflicts: 1
```

### Analysis of Evaluation Findings
1. **Existing Row Match (1 file)**:
   - `HH Kadamba Kanana Swami - SB 3.6.6 - Sweden - 27_8_15.mp3` matched live Baserow row `2335`.
   - Tool 4 planned an `UPDATE` operation with minimal PATCH (`Filename`, `Place, location`, `Notes`), correctly detected that row `2335` had an archive path from a different archive directory (`/renamed-files/...`), and flagged an archive path representation conflict to prevent unproven path overwriting.
2. **New Media Candidates (39 files)**:
   - Tool 2 identified 39 legitimate new recordings.
   - Tool 4 planned `CREATE` operations with required defaults (`Language="English"`, `Status Media="Not-started"`, `Created_on` timestamps, `Notes="Added from archive"`).
   - Category option check flagged that some categories require human confirmation before select-option assignment.
3. **Safety Blocks (131 files)**:
   - 99 files had multiple candidate matches in Baserow (`MULTIPLE_CANDIDATES`); Tool 4 strictly blocked automatic creation or first-match-wins.
   - 32 files had insufficient metadata (`INSUFFICIENT_EVIDENCE`); Tool 4 strictly blocked automatic creation.
4. **Select Option Discovery (13 files)**:
   - 13 candidate new files presented legitimate novel locations/countries (e.g. Serbian Summer Camp, Praha), validating Tool 4's capability to propose schema additions strictly within the Country & Location scope.
5. **Partial Date Handling (1 file)**:
   - 1 file had an incomplete date, correctly routing to `Notes` marker without corrupting the authoritative `Date` column.
