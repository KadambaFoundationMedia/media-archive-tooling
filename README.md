# media-archive-tooling

Tools for processing media files in the archive.

## Builder quick start

The implementation model has one stable entry point: `BUILDER.md`.

To start or resume a tool locally, run:

```sh
./scripts/builder-start.sh <tool-number>
```

For Tool 1:

```sh
./scripts/builder-start.sh 1
```

The command detects the tool's current status and prints the exact files to read plus the action required for that state. The user can therefore simply tell the implementation model:

```text
BUILD TOOL 1
```

The builder must then run the helper command (or follow `BUILDER.md` manually if shell execution is unavailable). It must not depend on previous chat history.

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

Status: **CHANGES_REQUESTED after first implementation review**

Build plan: `docs/tool-1-renamer-build-plan.md`

Implementation status and active review findings: `status/tool-1-renamer.md`

Implementation tracking/discussion: GitHub issue #1

The first implementation was reviewed through repository HEAD `822f011`. The project foundation, deterministic parser, SQLite registry, logging, CLI, review portal, adapters and tests are in place, but the planning/review pass found specification and safety gaps that must be corrected before Tool 1 can be accepted. The active `R-###` findings and required regression tests are recorded in the status file. The builder should run `./scripts/builder-start.sh 1`, address every active review finding, push the corrected implementation, and return the tool to `READY_FOR_REVIEW`.

The Renamer is a fast, repeatable filename interpretation and normalization tool. It assigns a stable temporary `_ID-xxxxxxxx` during processing, extracts and progressively enriches WHEN/WHO/WHAT/WHERE metadata from filenames, folders, Baserow reference data and later-tool evidence, handles ambiguous dates and multilingual archive naming patterns, resolves locations against shared Baserow data, and performs safe dry-run/commit renames without blocking the batch on unclear files. It deliberately avoids slow audio/content analysis; later passes reuse the same Renamer engine as stronger evidence becomes available.

Tool 1 also provides the first useful review-portal view so uncertain rename proposals, evidence, alternatives, conflicts, and corrections can be reviewed from the browser while automatic files continue without blocking.

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
