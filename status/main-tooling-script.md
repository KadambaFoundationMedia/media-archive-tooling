# Main Tooling Script — Implementation Status

Build plan: `docs/main-tooling-script-build-plan.md`
Implementation protocol: `docs/implementation-protocol.md`
Project architecture: `docs/project-implementation-architecture.md`
Walkthrough and verification: `docs/main-tooling-script-walkthrough.md`

## Current state

Status: `CHANGES_REQUESTED`

Implementation branch: `main-tooling-script-implementation`
Implementation PR: https://github.com/KadambaFoundationMedia/media-archive-tooling/pull/60

## Independent planner review of PR #60 — 2026-09-23

The focused Main Script suite passes (56 tests) and required GitHub CI is
green. A full local suite reached 471 passes with two permission failures
because this review sandbox cannot write to the checkout's `.renamer` test
paths; these are not CI failures. PR #60 is **not approved for merge** until
the following narrow safety/correctness findings are resolved. The separate
Builder should correct them on this same branch, add regressions, commit and
push, and hand back a new clean PR head. Do not run live archive files.

### R-054 — Startup scratch recovery may delete user files [BLOCKING]

`orchestrator/service.py` calls `clean_abandoned_scratch()` on each supplied
target directory **before** processing, even for `--dry-run`. The new
`orchestrator/scratch.py` treats a name prefix such as `.tmp_extract_` or
`tmp_main_` as proof of ownership and unlinks matching files or recursively
deletes matching directories in those target roots. An unrelated archive/user
file with such a name is therefore removable merely by starting the runner.
Remove directory-wide prefix deletion. Clean only scratch artifacts proved
owned by a durable per-run manifest/registry identity and confined to the
tool's scratch area; never sweep arbitrary target directories. Dry-run must
delete nothing. Test a colliding user file/directory and a dry-run explicitly.

### R-055 — Bounds and evaluation workspace safety are incomplete [BLOCKING]

`select_and_copy_bounded_evaluation_media()` allows the first selected file
to exceed `max_bytes`, so the stated hard budget is not enforced. Its caller
also recursively deletes any existing `--workspace` path, which is
user-supplied; no ownership marker or safe-path check protects unrelated
data. Reject invalid/nonpositive limits, enforce the byte cap for **every**
file, and only clean an evaluation workspace proven owned by this helper.

The normal runner still calls `discover_media_targets()`, whose unchanged
implementation accumulates and sorts all media and skipped paths in sets and
lists before yielding any file. Capping `RunSummary.file_results` and logging
only ten paths does not meet Section 22's archive-scale bounded-discovery
requirement. Use incremental traversal/bounded batching or a durable queue;
test with an iterator/instrumentation that proves processing begins without
materializing the complete discovered path set. Preserve missing-target and
deduplication semantics.

### R-056 — Checkpoints can replay stale remote decisions [BLOCKING]

Tool 2's saved Baserow review is reused when the media byte hash matches,
without fresh remote-row or relevant configuration/evidence validation. A new
collaborator row between runs can therefore be hidden from Tool 1. Tool 4's
`SYNCED` checkpoint is reused without comparing the current file, path, or
new metadata at all, so a later metadata-only enrichment may never reach
Baserow. Keep checkpoints for immutable/expensive local stages when their
complete input/config fingerprints match; refresh live Tool 2 review before
decisions, and let Tool 4 re-evaluate new metadata/path or pending work under
its existing safety gates. Add tests for a new remote candidate and a
metadata-only update after an earlier `SYNCED` result.

## Open questions / contradictions

### Q-001 — Production-mode data retention and test-row purge separation policy

Status: OPEN
Build-plan section(s): Section 22 (`docs/main-tooling-script-build-plan.md`), Section 5 (`status/main-tooling-script.md`)
Blocking scope: Live production processing of the full 15+ TB archive under `--production`. Normal test-mode runs and alpha/beta testing remain unaffected.

Problem:
The existing test harness automatically purges test rows and resets review state when the code fingerprint changes or `--purge` is invoked. In real archive production on 15+ TB of data, operator decisions, review approvals, and production Baserow entries must be permanently retained and isolated from automated test-slate resets.

Why this matters:
Running production data through the test-mode purge logic could inadvertently clear durable review actions or delete live rows if the code fingerprint changes.

Implementation action:
The runner retains existing alpha/beta purge and fresh-slate logic exclusively for test mode. Any invocation with `--production` fails closed with an explanatory error until an explicit production-mode data retention and purge separation policy is confirmed by the owner/planner.

## Builder action — archive-scale correction and future workflow contract (2026-09-23)

Read `docs/full-pipeline-workflow-amendment.md` and Section 22 of
`docs/main-tooling-script-build-plan.md`. The user has clarified the complete
stage order and that a successful Tool 6 combination cut replaces the one
working input with class and singing outputs; no permanent original remains.
The class retains its existing Baserow row and Tool 4 creates a separate
singing row after the cut. Final Tool 1 renaming precedes Tool 11's move;
Tool 4 updates final paths/metadata afterward. These are future integration
contracts, not a fixed invocation schedule or permission to invent Tools 6–11.
Any tool's newly accepted metadata must prompt Tool 1 to re-evaluate whether
the filename needs changing and Tool 4 to synchronize any database-relevant
change, even when no rename occurs. Repeat calls must be idempotent and retain
the matched row identities.

Active correction findings for `BUILD MAIN SCRIPT`:

- **R-052 — Unbounded evaluation copy.**
  `scripts/run_tool_4_evaluation.py` currently copies all of `sample-files`
  into `.renamer/eval_workspace/media` (272 files/about 32 GB in the last
  local test). Replace this with an explicit bounded fixture/subset and
  preflight limit; never create a full archive copy. Preserve source files.
- **R-053 — Archive-scale resumability and scratch bounds.**
  Implement the current-stage requirements in Main Script plan Section 22:
  bounded per-file scratch use, owned-temp cleanup/recovery, durable stage
  checkpoints and retry/review after interruption, continuation past failed
  independent files, and bounded discovery/result logging. Keep the current
  alpha/beta purge policy in test mode; flag production-mode policy as open
  before any real 15+ TB archive run.

Builder implementation belongs on `main-tooling-script-implementation`,
synced from current `main`, with tests, status updates, a pushed PR, and CI.
Do not run a live full-folder test or create a permanent media copy.

## Planner-authored maintenance / coordination — 2026-09-23

PR [#57](https://github.com/KadambaFoundationMedia/media-archive-tooling/pull/57), code commit `d558163`, wires Tool 5's optional progress callback through the Main Script and standalone CLI, adds concise conversion/transcription heartbeat lines, and omits duplicated large Tool 4 `fields`/`live_row` dumps from verbose terminal output while retaining the normal Tool 4 write summary and structured log details. The workflow banner now correctly lists Tools 1–5 as active. The builder must preserve the callback/reporting integration and keep JSON output free of terminal progress text. Full local suite: 469 passed.

## Alpha/beta cleanup amendment — 2026-09-22 [RESOLVED]

Implemented the Main Tooling Script portion of
`docs/alpha-beta-test-data-purge-build-plan.md` using Tool 4's typed cleanup service:
- **Standalone `--purge` & `--purge --dry-run`**:
  - Added `--purge` flag to `./run-media-archive.sh` and CLI `media-archive run`.
  - Enforced CLI mutual exclusivity: file targets are rejected if `--purge` is provided (exit code 2); targets are required if `--purge` is omitted (exit code 2).
  - Dry-run inspects rows without mutating remote tables or local registry.
  - Live purge confirms markers, deletes test rows via Tool 4, and clears local review state only upon complete remote success.
- **Automatic review-data fingerprint detection**:
  - Implemented `compute_review_data_fingerprint` generating deterministic SHA-256 over runtime code in `src/media_archive_tooling`.
  - Automatic fresh slate triggered at runner and independent portal startup if fingerprint differs.
  - If cleanup blocks, transitions to `PURGE_BLOCKED`, stores reason in registry metadata, and hides stale review queue/files in runner and review portal.
- **Portal integration**:
  - Renders concise "Fresh test slate" badge when 0 tracked files are present.
  - Renders visible warning banner and hides review table/batch bar when `purge_blocked` is active; returns HTTP 503 on file detail view.
- **Cross-process locking**:
  - Implemented `acquire_lock` context manager using `fcntl.flock` on file SQLite databases and thread locks for in-memory databases.
- **Strict architectural boundaries**:
  - Preserved Tool 4 as the sole deleter/writer of Baserow rows. Main Script and Portal invoke Tool 4's `purge_test_rows`.

Implementation branch: `main-tooling-script-implementation`
Implementation PR: https://github.com/KadambaFoundationMedia/media-archive-tooling/pull/52
Last planning update: 2026-09-22

## Implemented Phase A Pipeline

```text
Tool 1 initial interpretation
→ Tool 2 live read-only Media review
→ Tool 3 verified travel-schedule review
→ Tool 1 final proposal and immediate live commit when allowed
→ Tool 4 synchronization (preview or live write)
```

The Main Tooling Script is unnumbered. It is not Tool 12. CLI command: `media-archive run <targets>`.

## Confirmed requirements verified

- Runs locally from the command line: `media-archive run <targets>`.
- Accepts single file, multiple files, folders recursively, and mixed targets.
- Works on original files directly in live mode without confirmation prompts.
- `--dry-run` performs zero filesystem or Baserow mutations; computes rich projection previews.
- Default workflow selection is `all` (executes Tools 1–4, reports Tools 5–11 pending).
- `--workflow renamer` runs Tools 1–4; `--workflow processing` exits with code 1 before mutation.
- Execution between tools is continuous and does not pause.
- Terminal reporter displays concise multi-line per-tool progress and run summary banner.
- Unified structured logger writes append-only JSONL entries to `.renamer/media-archive-tooling.log` with unique run IDs and automatic secret redaction.
- Review portal active queue (`/`) filters out clean completed files and isolates only items genuinely requiring evaluation.
- Preserved planner-authored baseline:
  - Strict punctuation-safe final naming;
  - Dotted scripture recognition and readable Baserow scripture titles;
  - Czech `Duben` month/country context;
  - Tool 2 country-only candidate filtering and read-only boundaries;
  - Tool 4 sole Baserow writer, read-only Tool 2 revalidation (`auto_enrich=False`), and durable pending synchronization.

## Verification results

### 1. Main Script Dedicated Test Suite (`tests/test_main_script.py`)
All 51 test scenarios (including alpha/beta purge and fingerprint scenarios 42–51) are implemented and passing:
- 51 passed in 1.77s.

### 2. Full Project Pytest Suite
All 430 automated tests in the repository pass with zero regressions:
```text
======================= 430 passed, 2 warnings in 6.00s ========================
```

### 3. Package Build
Package builds cleanly offline:
```text
Building source distribution...
Building wheel from source distribution...
Successfully built dist/media_archive_tooling-0.1.0.tar.gz
Successfully built dist/media_archive_tooling-0.1.0-py3-none-any.whl
```

### 4. Shell Helper Checks
All shell helper scripts validated with `bash -n`:
- `scripts/builder-start.sh`: syntax valid
- `scripts/review-tool-1.sh`: syntax valid

### 5. Practical Evaluations
- **Single file dry-run**: Verified `media-archive run "sample-files/2011-08-20_KKS_Jaya-radha-madhava_oslo_fi.mp3" --dry-run` runs cleanly without mutations.
- **Multiple files dry-run**: Verified multiple targets processed independently.
- **Duben Collection dry-run**: Verified `media-archive run "sample-files/KKS DUBEN 2008 MP3/04 KKS. SB. 3,1,21.mp3" --dry-run` parses WHAT as `SB-3-1-21` and handles database routing cleanly.
- **Recursive folder & unsupported files**: Verified recursive scanning of `sample-files/2008` and non-fatal skipping of `Anti-test.zip`.
- **Live hermetic test double**: Verified live rename of `2022-09-19_KKS_Oslo.mp3` to `2022-09-19_KKS_SB-1-2-19_Oslo-no.mp3`, Baserow row #1234 update, and outbox state `SYNCED`.
- **Portal Active Queue**: Verified `/` renders only items requiring active review, excluding clean completed items.

## Resolutions for Review Findings (2026-09-19)

### R-001 — Comma-separated scripture verses in accepted Duben collection [RESOLVED]
- Fixed regexes (`SB_REGEX`, `BG_REGEX`, `CC_REGEX`) and raw evidence stripping in `src/media_archive_tooling/renamer/parser/what.py` to support comma chapter/verse separators (`3,1,21` -> `SB-3-1-21`, `14,6` -> `BG-14-6`, `1,2,3` -> `CC-1-2-3`).
- Added regressions in `tests/test_what.py` and `tests/test_main_script.py::test_36`.
- Verified live dry-run on `sample-files/KKS DUBEN 2008 MP3/04 KKS. SB. 3,1,21.mp3` outputs WHAT: `SB-3-1-21`.

### R-002 — Tool 2 review-required decisions block Tool 1 before rename [RESOLVED]
- In `src/media_archive_tooling/orchestrator/service.py`, synthesize fallback review reason from `t2_res.diagnostic_notes` or decision if `t2_res.review_required` is `True` and `review_reasons` is empty.
- Explicitly gate Tool 1 rename finalization on `not t2_res.review_required`.
- Verified with `tests/test_main_script.py::test_37`.

### R-003 — Route through accepted Tool 1 commit boundary (`RenameCommitService`) [RESOLVED]
- Replaced direct filesystem rename and ad-hoc `record_commit` with `RenameCommitService(registry=self.registry, mode=RenameMode.FINALIZE, media_db_updater_service=None).commit_file(tracking_id, reviewer="main-script")`.
- Guarantees accurate `rename_history` old-to-new recording, updates `file_records.parser_result.identity`, and records audit review action.
- Verified with `tests/test_main_script.py::test_38`.

### R-004 — Preserve and route actual Tool 4 outcome to evaluation queue [RESOLVED]
- Extended `FileExecutionStatus` with `DATABASE_UNAVAILABLE`, `FAILED_RETRYABLE`, and `FAILED_BLOCKED`.
- Mapped all `SyncStatus` outcomes to the file run result and reporter counters.
- Updated `requires_evaluation()` in `src/media_archive_tooling/renamer/service.py` using canonical `SyncStatus` enum members.
- Canonical files with Tool 4 CREATE/UPDATE marked COMPLETED, only UNCHANGED if NOOP.
- Verified with `tests/test_main_script.py::test_39` and `test_40`.

### R-005 — Show verified live readback summary [RESOLVED]
- Added `live_row: Optional[Dict[str, Any]]` to `MediaDbSyncResult` in `src/media_archive_tooling/media_db_updater/models.py`.
- Populated `live_row` in `MediaDbUpdaterEngine` on CREATE, UPDATE, and NOOP.
- `TerminalReporter` and `MainToolingScriptService` format and display verified live row values (Row ID, Title, Date, Place, Filename) in terminal output upon write.
- Verified with `tests/test_main_script.py::test_41`.

## Planning/review acceptance — 2026-09-19

Accepted implementation commit: `0aba5de`

The planner independently inspected the implementation diff and confirmed that
R-001 through R-005 are resolved. Verification performed:

- focused Tool 1/Main Script regressions: **47 passed**;
- portal suite after test isolation correction: **13 passed**;
- full local suite: **399 passed, 2 warnings**;
- exact Duben practical dry run:
  - WHAT: `SB-3-1-21`;
  - final projection: `2008-04-DD_KKS_SB-3-1-21_cz.mp3`;
  - Tool 2: `NEW_MEDIA_CANDIDATE`;
  - Tool 4: `WOULD CREATE`, with Title `SB 3.1.21`, Category
    `Srimad-bhagavatam`, Tag `3.1.21`, Language `English`, Czech country
    evidence, and original filename/path provenance in Notes;
- GitHub Actions required check `Python 3.12 tests`: **passed** on `0aba5de`
  (job 105933860813).

The planner also made one test-only correction after review: the older portal
detail/update test now creates and configures its own temporary registry instead
of reading whichever persistent developer registry is active. This does not
change production behavior and prevents local state from making the full suite
order-dependent.

No open review findings remain. PR #40 is approved for a normal merge commit
after the required check passes on the final review/status head.

## Post-acceptance documentation maintenance — 2026-09-19

The root README was refocused on the project summary and practical Main Tooling
Script instructions. Detailed Builder workflow, branch/PR protocol,
architecture, Baserow authority, CI, and per-tool reference material was moved
to `docs/project-reference.md`.

This is a documentation-only reorganization on branch
`docs/readme-main-script-focus`. It does not change accepted tool behavior or
the Main Tooling Script interface. The Builder should use `BUILDER.md` and the
new project-reference document for the moved implementation-process context.

## Post-acceptance launcher maintenance — 2026-09-19

The user requested one executable entry point that hides Python environment
setup. `run-media-archive.sh` now prepares the locked private environment when
needed and then invokes the accepted Main Tooling Script. It passes normal
targets/options through unchanged and supports `--review-only` for opening the
portal against an existing registry.

The README now uses this launcher exclusively for operator instructions. The
Builder should preserve this simple user-facing entry point when later tools
are added; development and CI may continue using `uv` directly.

Planner verification:

- launcher shell syntax: passed;
- automatic isolated runtime setup: passed;
- Main Script and review-portal help passthrough: passed;
- practical Duben dry run through the launcher: passed with zero mutations;
- full regression suite: **399 passed, 2 warnings**.

## Post-acceptance Oslo date/verse correction — 2026-09-19

The planner corrected the exact practical case
`KKS_S.B. 1.19.30_28.8.11_Oslo_ .WMA` after the user clarified that
consecutive verses may be consecutive daily classes at the same location.
Builder must preserve these rules in later work:

- the explicit filename date `28.8.11` resolves to `2011-08-28`; earlier
  scripture numbers must not stop date scanning;
- folder `From JVD (8.9.11)` is supporting August/September 2011 collection
  context, not an authoritative exact recording date;
- a database row is related sequence evidence—not a duplicate/conflict—only
  when the date and single scripture verse both move by exactly one day/verse,
  the scripture book/canto/chapter match, the location matches exactly, and
  the countries do not contradict;
- same-date verse contradictions and direct-identity contradictions remain
  conflicts.

Live dry-run result:

- Tool 1 final proposal:
  `2011-08-28_KKS_SB-1-19-30_Oslo-no.wma`;
- Tool 2: `NEW_MEDIA_CANDIDATE`, with Baserow row **3231** (`2011-08-29`,
  `SB 1.19.31`, Oslo) shown separately as the next class in the series;
- Tool 4: `WOULD CREATE` a new row titled `SB 1.19.30`; no write was made;
- full regression suite: **402 passed, 2 warnings**.
