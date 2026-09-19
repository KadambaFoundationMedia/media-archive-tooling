# Main Tooling Script — Implementation Status

Build plan: `docs/main-tooling-script-build-plan.md`
Implementation protocol: `docs/implementation-protocol.md`
Project architecture: `docs/project-implementation-architecture.md`
Walkthrough and verification: `docs/main-tooling-script-walkthrough.md`

## Current state

Status: `READY_FOR_REVIEW`

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

## Open questions / contradictions

None.

## Review findings

None.

## Next milestone

Planning and user review of Phase A on PR #40.
