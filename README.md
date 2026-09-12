# media-archive-tooling

Tools for processing media files in the archive.

## Project implementation architecture

The project uses a **local, reusable Python 3.12 application/package** with two first-class interfaces: a CLI for automation/testing/batch work and a **localhost browser-based review portal** for human review and corrections.

Project-wide architecture: `docs/project-implementation-architecture.md`

The implementation uses `uv` for Python environment/dependency management. Tool logic remains callable programmatically for the future orchestrator; CLI and review UI both call the same Python application services. The initial review portal uses FastAPI with server-rendered Jinja2 + HTMX so no Xcode/Swift or Node/React toolchain is required for v1. A packaged desktop shell can be evaluated later without moving archive logic out of Python.

## Build plans

Each finalized tool has its own implementation-ready Markdown build plan under `docs/`. A finalized build plan is the authoritative specification for that tool.

**Implementation models must not edit finalized build plans.** If a requirement is unclear, contradictory, impossible as written, or conflicts with another finalized requirement, the implementation model must record the problem in the tool's status file under `status/` and continue unaffected work where possible. Specification changes are made only through planning/review with the user.

Project-wide handoff and review rules: `docs/implementation-protocol.md`

### Tool 1 — Renamer

Status: **build plan finalized; implementation pending**

Build plan: `docs/tool-1-renamer-build-plan.md`

Implementation status: `status/tool-1-renamer.md`

Implementation tracking/discussion: GitHub issue #1

The Renamer is a fast, repeatable filename interpretation and normalization tool. It assigns a stable temporary `_ID-xxxxxxxx` during processing, extracts and progressively enriches WHEN/WHO/WHAT/WHERE metadata from filenames, folders, Baserow reference data and later-tool evidence, handles ambiguous dates and multilingual archive naming patterns, resolves locations against shared Baserow data, and performs safe dry-run/commit renames without blocking the batch on unclear files. It deliberately avoids slow audio/content analysis; later passes reuse the same Renamer engine as stronger evidence becomes available.

Tool 1 will also provide the first useful review-portal view so uncertain rename proposals, evidence, alternatives, conflicts, and corrections can be reviewed from the browser while automatic files continue without blocking.

## Project progress protocol

GitHub is the durable communication channel between planning/review and implementation models.

For each tool:

1. Create a dedicated finalized build-plan Markdown file in `docs/`.
2. Create a per-tool implementation status file under `status/`.
3. Create a GitHub implementation issue when useful for discussion/notifications.
4. The implementation model updates the status file at meaningful milestones with completed work, commits/PRs, tests, observed behavior, blockers, open questions, and the next milestone.
5. The implementation model must not change the finalized build plan. Unclear or contradictory requirements are recorded in the status file for planning/review resolution.
6. The status file maintains the current implementation HEAD and the last planning/review commit checkpoint.
7. The planning/review model inspects implementation commits/diffs since the previous checkpoint and checks for fundamental changes, including changes to archive behavior, tool boundaries, Baserow/shared-state semantics, persistent schemas, interfaces, safety/idempotency, authoritative providers, major framework choices, and acceptance criteria.
8. A potentially fundamental implementation change must not silently become project policy. It is either corrected to match the build plan or raised as a specific decision for the user.
9. When a tool is accepted, its build-plan acceptance criteria, current implementation HEAD, tests, and sample results must have been reviewed, and this README is updated with its final status and concise summary.

This process avoids relying on direct model-to-model memory and keeps the repository itself as the project record.