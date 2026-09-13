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

## Project implementation architecture

The project uses a **local, reusable Python 3.12 application/package** with two first-class interfaces: a CLI for automation/testing/batch work and a **localhost browser-based review portal** for human review and corrections.

Project-wide architecture: `docs/project-implementation-architecture.md`

The implementation uses `uv` for Python environment/dependency management. Tool logic remains callable programmatically for the future orchestrator; CLI and review UI both call the same Python application services. The initial review portal uses FastAPI with server-rendered Jinja2 + HTMX so no Xcode/Swift or Node/React toolchain is required for v1. A packaged desktop shell can be evaluated later without moving archive logic out of Python.

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

The accepted v1 passed the project's review/correction cycle through findings R-001 to R-024. The final builder report records 68 passing Python 3.12 tests and a 260-file representative dry-run in which 255 files continued automatically/downstream, 5 required immediate human review, 2 were routed as combination candidates, and 0 were blocked. The five human-review cases were genuine filename/folder date contradictions rather than routine missing metadata. GitHub CI is not currently configured; the status file records the reviewed implementation commits and verification provenance.

### Tool 2 — Media Database Reviewer

Status: **FINALIZED BUILD PLAN / NOT_STARTED**

Build plan: `docs/tool-2-media-database-reviewer-build-plan.md`

Implementation status: `status/tool-2-media-database-reviewer.md`

Implementation tracking/discussion: GitHub issue #2

Tool 2 is the reusable **read-only Baserow Media database lookup and reconciliation service**. It consumes structured Tool 1 evidence, searches plausible Media rows, compares database metadata field-by-field, preserves contradictions, distinguishes confirmed matches from probable/multiple/conflicting candidates, and returns only confirmed Media metadata to the Renamer for enrichment.

Tool 2 reads `media`, `category_title`, and `travel_schedule`. It does not currently use `users` and never mutates Baserow; Tool 4 owns writes. Tool 2 may use travel-schedule rows as supporting candidate context, but Tool 3 remains a separate dedicated Travel Schedule Reviewer.

Confirmed Baserow titles can enrich the WHAT field. Full titles remain preserved as metadata/evidence, while overlong filename title components are shortened automatically and deterministically at whole-word boundaries only when required by the filename-length budget.

To begin implementation:

```sh
./scripts/builder-start.sh 2
```

## Project progress protocol

GitHub is the durable communication channel between planning/review and implementation models.

For each tool:

1. Create a dedicated finalized build-plan Markdown file in `docs/`.
2. Create a per-tool implementation status file under `status/`.
3. Create a GitHub implementation issue when useful for discussion/notifications.
4. The implementation model starts from `BUILDER.md` / `./scripts/builder-start.sh <tool-number>` and uses the status file to determine the current action.
5. The implementation model updates the status file at meaningful milestones with completed work, commits/PRs, tests, observed behavior, blockers, open questions, and the next milestone.
6. The implementation model must not change the finalized build plan. Unclear or contradictory requirements are recorded in the status file for planning/review resolution.
7. The status file maintains the current implementation HEAD and the last planning/review commit checkpoint.
8. The planning/review model inspects implementation commits/diffs since the previous checkpoint and checks for fundamental changes, including changes to archive behavior, tool boundaries, Baserow/shared-state semantics, persistent schemas, interfaces, safety/idempotency, authoritative providers, major framework choices, and acceptance criteria.
9. A potentially fundamental implementation change must not silently become project policy. It is either corrected to match the build plan or raised as a specific decision for the user.
10. When a tool is accepted, its build-plan acceptance criteria, current implementation HEAD, tests, and sample results must have been reviewed, and this README is updated with its final status and concise summary.

This process avoids relying on direct model-to-model memory and keeps the repository itself as the project record.
