# Main Tooling Script — Implementation Status

Build plan: `docs/main-tooling-script-build-plan.md`
Implementation protocol: `docs/implementation-protocol.md`
Project architecture: `docs/project-implementation-architecture.md`
Walkthrough and verification: `docs/main-tooling-script-walkthrough.md`

## Current state

Status: `ACCEPTED`

Implementation branch: `main-tooling-script-implementation`
Implementation PR: https://github.com/KadambaFoundationMedia/media-archive-tooling/pull/40
Last planning update: 2026-09-19

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
All 41 test scenarios (including R-001 through R-005 scenarios) are implemented and passing:
- 41 passed in 1.34s.

### 2. Full Project Pytest Suite
All 399 automated tests in the repository pass with zero regressions:
```text
======================= 399 passed, 2 warnings in 5.49s ========================
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
