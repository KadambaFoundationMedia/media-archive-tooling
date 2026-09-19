# Main Tooling Script — Implementation Status

Build plan: `docs/main-tooling-script-build-plan.md`
Implementation protocol: `docs/implementation-protocol.md`
Project architecture: `docs/project-implementation-architecture.md`
Walkthrough and verification: `docs/main-tooling-script-walkthrough.md`

## Current state

Status: `CHANGES_REQUESTED`

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
All 36 test scenarios from Section 17 of `docs/main-tooling-script-build-plan.md` are implemented and passing:
- 36 passed in 1.14s.

### 2. Full Project Pytest Suite
All 394 automated tests in the repository pass with zero regressions:
```text
======================= 394 passed, 2 warnings in 5.17s ========================
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
- `scripts/builder-commit-and-push.sh`: syntax valid
- `scripts/review-tool-1.sh`: syntax valid

### 5. Practical Evaluations
- **Single file dry-run**: Verified `media-archive run "sample-files/2011-08-20_KKS_Jaya-radha-madhava_oslo_fi.mp3" --dry-run` runs cleanly without mutations.
- **Multiple files dry-run**: Verified multiple targets processed independently.
- **Recursive folder & unsupported files**: Verified recursive scanning of `sample-files/2008` and non-fatal skipping of `Anti-test.zip`.
- **Live hermetic test double**: Verified live rename of `2022-09-19_KKS_Oslo.mp3` to `2022-09-19_KKS_SB-1-2-19_Oslo-no.mp3`, Baserow row #1234 update, and outbox state `SYNCED`.
- **Portal Active Queue**: Verified `/` renders only items requiring active review, excluding clean completed items.

## Independent review — 2026-09-19

Reviewed branch head: `8988ec9`

Verification performed by the planner:

- full local suite: **394 passed, 2 warnings**;
- PR #40 required GitHub check: **passed**;
- practical read-only dry run against
  `sample-files/KKS DUBEN 2008 MP3/04 KKS. SB. 3,1,21.mp3`;
- inspection of the live commit path, Tool 4 result handling, registry state, and
  active review-portal filtering.

The implementation is not approved yet. The following findings are concrete
functional blockers; keep the correction small and use the existing service
boundaries.

### R-001 — Preserve comma-separated scripture verses in the accepted Duben collection

The practical dry run parsed `04 KKS. SB. 3,1,21.mp3` as the generic value
`Srimad-Bhagavatam`, discarded `3,1,21` into unclassified text, and proposed:

```text
2008-04-DD_KKS_Srimad-Bhagavatam_cz.mp3
```

The collection clearly contains a progression (`3.1.20`, `3,1,21`, `3.1.25`,
`3.1.26`). The comma form must normalize exactly like the dotted form. The
expected WHAT is `SB-3-1-21`, producing:

```text
2008-04-DD_KKS_SB-3-1-21_cz.mp3
```

Correct this in Tool 1's shared scripture parser, not in the orchestrator. Add
exact end-to-end Main Script regressions for the real `02`, `04`, `06`, and
`08` filename patterns. The current test 36 uses a different synthetic Czech
filename and only checks that `Duben` remains in the result, so it does not
prove the accepted practical behavior.

### R-002 — Tool 2 review-required decisions must block Tool 1 before rename

In the same real dry run, Tool 2 returned `MULTIPLE_CANDIDATES`,
`review_required=true`, and 140 candidates. Because its `review_reasons` list
was empty, the orchestrator nevertheless printed Tool 1 `can commit` and only
became review-required when Tool 4 refused the write. In live mode this ordering
would rename the file before the known association ambiguity is handled.

Gate finalization on Tool 2's decision/review state itself, not on whether a
human-readable reasons list happens to be non-empty. Surface Tool 2 diagnostic
notes as the concise reason when needed. Add a regression where
`review_required=true` and `review_reasons=[]`; Tool 1 must report review
required, the file must remain unchanged, and Tool 4 write synchronization must
be skipped.

### R-003 — Use the accepted Tool 1 commit boundary

The live runner currently calls `Path.rename()` and
`LocalRegistry.record_commit()` directly. It also replaces
`proposal.current_filename` with the destination name before recording history,
so `rename_history.from_filename` is wrong. This path bypasses the accepted
`RenameCommitService` safeguards and audit updates, including canonical-name
validation, parser identity update, review-action recording, and its Tool 4
outbox handoff.

Route live final commits through `RenameCommitService` (or a small shared
service method extracted from it) and avoid duplicating the commit transaction
inside the orchestrator. Add assertions for accurate old-to-new rename history,
updated parser identity, commit audit, and one Tool 4 handoff.

### R-004 — Preserve and route the actual Tool 4 outcome

After calling Tool 4, the runner describes every non-`SYNCED` result as
`PENDING_SYNC`, even though Tool 4 durably records specific states such as
`REVIEW_REQUIRED`, `DATABASE_UNAVAILABLE`, `FAILED_RETRYABLE`, or
`FAILED_BLOCKED`. The active portal filter checks obsolete/nonexistent values
(`FAILED_FATAL`, `BLOCKED`, `CONFLICT`) and omits several real `SyncStatus`
values. Consequently a blocked or unavailable Tool 4 item can be mislabeled in
the terminal and omitted from the evaluation queue.

Map the actual `SyncStatus` to the run result and terminal text. The portal must
use the real enum values and include every actionable Tool 4 state. Add focused
tests for `REVIEW_REQUIRED`, `DATABASE_UNAVAILABLE`, `FAILED_RETRYABLE`, and
`FAILED_BLOCKED`, including an already-canonical filename whose Tool 4 action is
CREATE or UPDATE; that item is synchronized/completed, not “unchanged”.

### R-005 — Show the required live verification result

After a successful CREATE or UPDATE, the Main Script currently prints the
operation and planned field diffs but no explicit live readback summary. Use the
verified final-row data already provided by the Tool 4 boundary (extend that
typed result minimally if it is not exposed) and show the row number plus the
verified values. Do not add a second independent Baserow writer or bypass Tool
4. Cover this with the existing fake-adapter live test.

## Commit-review checkpoint

Current implementation HEAD: `8988ec9`
Required GitHub status check: `Python 3.12 tests` passed (job: https://github.com/KadambaFoundationMedia/media-archive-tooling/actions/runs/35454629913/job/105927632423)

## Next milestone

Builder resolves R-001 through R-005 on the same PR and returns the status to
`READY_FOR_REVIEW` only after focused regressions, the full suite, and one
practical dry run using the exact Duben collection patterns pass.
