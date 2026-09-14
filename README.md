# media-archive-tooling

Tools for processing media files in the archive.

## Builder quick start

The implementation model has one stable entry point: `BUILDER.md`.

To start or resume a tool locally, run:

```sh
./scripts/builder-start.sh <tool-number>
```

Examples:

```sh
./scripts/builder-start.sh 1
./scripts/builder-start.sh 2
```

The helper synchronizes the repository, detects the tool's current status, and prints the exact files to read plus the action required for that state. The user can therefore simply tell the implementation model:

```text
BUILD TOOL 1
```

or:

```text
BUILD TOOL 2
```

The builder must run the helper command (or follow `BUILDER.md` manually if shell execution is unavailable). It must not depend on previous chat history.

## Protected `main`, implementation branches, and pull requests

`main` is protected by the active GitHub ruleset **Protect main**. Normal implementation work is no longer performed directly on `main`.

Each tool uses a durable implementation branch with the standard name:

```text
tool-<number>-implementation
```

When `./scripts/builder-start.sh <number>` is started from `main` for a mutable tool state, the helper automatically creates or resumes that branch and restarts there. The user-facing `BUILD TOOL <number>` command therefore does not change.

Implementation, correction, tests, walkthrough, and status changes remain on the tool branch through review cycles. A pull request from the tool branch to `main` is the review surface, and planning/review findings are committed back to the same branch rather than merging partial work into `main`.

## Project implementation architecture

The project uses a **local, reusable Python 3.12 application/package** with two first-class interfaces: a CLI for automation/testing/batch work and a **localhost browser-based review portal** for human review and corrections.

Project-wide architecture: `docs/project-implementation-architecture.md`

Planner / Builder coordination: `docs/planner-builder-coordination.md`

The implementation uses `uv` for Python environment/dependency management. Tool logic remains callable programmatically for the future orchestrator; CLI and review UI both call the same Python application services. The initial review portal uses FastAPI with server-rendered Jinja2 + HTMX so no Xcode/Swift or Node/React toolchain is required for v1. A packaged desktop shell can be evaluated later without moving archive logic out of Python.

## Baserow data authority

The mutable Baserow Media database is continuously updated by external collaborators. Any tool that needs to know the **current mutable database state** must perform a live Baserow read for that decision; persisted mutable row copies are audit/history only and must not be used as an authoritative operational cache.

Project-wide policy: `docs/baserow-live-data-policy.md`

Important consequences include:

- database/network failure is not treated as a valid Media no-match;
- cached mutable rows cannot produce a current confirmed Media association or enrichment;
- Tool 2 revalidates mutable live state when a human confirms a candidate/new-item decision;
- `travel_schedule` is a deliberate exception: it is a static historical reference table that will never be updated, so Tool 3 may use a complete verified local snapshot/cache across files, batches, sessions, and offline runs;
- Tool 4 re-reads immediately before update and re-checks existence immediately before create so collaborator changes are not silently overwritten or duplicated;
- stored mutable Baserow values remain useful for audit provenance, but not as a substitute for a fresh current read.

The static nature of `travel_schedule` changes only its freshness/caching semantics. Its evidentiary strength remains limited: planned travel may corroborate or suggest WHEN/WHERE, but it is not absolute proof that a recording occurred at that place/time.

## Continuous integration

GitHub Actions CI is configured in `.github/workflows/ci.yml`.

On every push to `main`, every pull request, and manual workflow dispatch, CI:

1. checks out the repository;
2. uses Python 3.12;
3. installs `uv`;
4. reproduces the locked environment with `uv sync --extra dev --frozen`;
5. validates repository shell helper syntax;
6. runs the full `pytest` suite;
7. verifies that the Python package builds successfully with `uv build`.

CI deliberately does not use the local `.env` or live Baserow/Vedabase/location credentials. Automated tests must mock external services so repository verification remains deterministic and safe.

The `Protect main` ruleset requires the status check **Python 3.12 tests** to pass and requires the PR branch to be up to date before merge. It also blocks branch deletion/force-push behavior covered by the ruleset and requires pull-request review flow with conversations resolved.

## Build plans

Each finalized tool has its own implementation-ready Markdown build plan under `docs/`. A finalized build plan is the authoritative specification for that tool.

**Implementation models must not edit finalized build plans.** If a requirement is unclear, contradictory, impossible as written, or conflicts with another finalized requirement, the implementation model must record the problem in the tool's status file under `status/` and continue unaffected work where possible. Specification changes are made only through planning/review with the user.

Project-wide handoff and review rules: `docs/implementation-protocol.md`

Builder entry point: `BUILDER.md`

### Tool 1 — Renamer

Status: **ACCEPTED**

Build plan: `docs/tool-1-renamer-build-plan.md`

Implementation status and acceptance record: `status/tool-1-renamer.md`

Implementation tracking/discussion: GitHub issue #1

Accepted implementation code commit: `9e96c4550977c59e9a1840cde6b4e53a5b80b638`.

Tool 1 is the fast, repeatable filename interpretation and normalization engine. It assigns a stable temporary `_ID-xxxxxxxx`, extracts and progressively enriches WHEN/WHO/WHAT/WHERE metadata, consumes stronger later-tool evidence, handles ambiguous dates and multilingual archive naming patterns, resolves locations against shared Baserow data, and performs safe dry-run/commit renames without blocking the batch on ordinary incompleteness.

The accepted v1 passed the project's review/correction cycle through findings R-001 to R-024. The final builder report records 68 passing Python 3.12 tests and a 260-file representative dry-run in which 255 files continued automatically/downstream, 5 required immediate human review, 2 were routed as combination candidates, and 0 were blocked. The five human-review cases were genuine filename/folder date contradictions rather than routine missing metadata. GitHub Actions CI is now operational for subsequent commits; Tool 1's original acceptance remains based on the reviewed implementation/test evidence recorded in its status file.

#### One-command local review

From the repository root, run:

```sh
./scripts/review-tool-1.sh
```

The helper safely synchronizes the current branch when possible, prepares the locked Python environment, performs a Tool 1 **dry-run** against `sample-files/`, starts the localhost review portal, and opens `http://127.0.0.1:8000` in the default browser on macOS/Linux when supported. The dry-run does not rename files.

The review helper uses a **separate per-target review registry** under `.renamer/review/` rather than the general operational registry. A normal review run starts from a fresh current-scan snapshot so stale rows from older parser versions cannot inflate the dashboard counts. Each reviewed directory stays isolated from other review targets, while Tool 1 still reuses existing tracking IDs in persistent operational registries.

The dashboard shows original filename, proposed filename, source path, WHEN/WHAT/WHERE and review status. Technical tracking IDs remain part of Tool 1's underlying in-process identity and filename semantics, but the dashboard intentionally hides the ID column and `_ID-xxxxxxxx` token from the **displayed** proposed filename because they are not useful for human review. The dashboard also includes dark mode, sticky table headers and batch row selection.

Selected rows can be processed with batch **Approve**, **Defer**, **Commit**, and **Approve + commit** actions. Approval accepts the current proposal without changing files; commit performs the filesystem rename through the shared safe commit service and requires explicit confirmation plus resolved review blockers.

To review another directory instead of `sample-files/`:

```sh
./scripts/review-tool-1.sh /path/to/media/files
```

Press `Ctrl-C` in the terminal to stop the local review portal when finished.

### Tool 2 — Media Database Reviewer

Status: **ACCEPTED**

Build plan: `docs/tool-2-media-database-reviewer-build-plan.md`

Authoritative live-data amendment: `docs/tool-2-media-database-reviewer-live-data-amendment.md`

Project-wide Baserow policy: `docs/baserow-live-data-policy.md`

Implementation status and acceptance record: `status/tool-2-media-database-reviewer.md`

Implementation walkthrough: `docs/tool-2-media-database-reviewer-walkthrough.md`

Implementation tracking/discussion: GitHub issue #2

Accepted implementation code/docs commit: `fb43685b529e69d10a1642498abe3c7d3775290e`.

Tool 2 is the read-only live Baserow Media reconciliation service. It consumes structured Tool 1 filename evidence, performs targeted and pagination-complete live candidate retrieval, compares WHEN/WHAT/WHERE and supporting category/travel evidence, separates confirmed enrichment from candidate-only metadata, and returns structured decisions for the Renamer, Tool 3, Tool 4, CLI, and review portal.

Every current-state **mutable Media/database** decision is live: persisted mutable Baserow rows/results are audit/history only. Database unavailability cannot be treated as a Media no-match, human confirmations are live-revalidated, and Tool 4 remains the only Baserow writer. The immutable `travel_schedule` reference is exempt from per-decision freshness requirements; Tool 2's existing live schedule reads remain valid but are not a requirement for Tool 3.

The accepted implementation resolved findings R-001 through R-015. Required GitHub CI passed with **157 tests**, helper-script validation, and package build success. A fresh live read-only evaluation across the 260 representative sample files produced 1 confirmed existing match, 20 probable matches, 99 multiple-candidate cases, 39 new-media candidates, 32 insufficient-evidence cases, 69 conflicts, and 0 database failures.

The final Tool 1 ↔ Tool 2 acceptance smoke test verified the supported automatic enrichment path. The confirmed live match for Baserow row `2335` changed the Tool 1 proposal from `2015-08-27_KKS_SB-3-6-6_Sweden-se_ID-f7903be1.mp3` to `2015-08-27_KKS_SB-3-6-6-class_Sweden-se_ID-f7903be1.mp3`. Candidate-only metadata from unconfirmed results was not copied into filenames, and valid completed no-match decisions propagated `baserow_check_complete=True` without inventing title/location metadata.
