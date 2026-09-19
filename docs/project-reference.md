# Project Reference and Development Process

This document contains the detailed project, tool, and implementation-process
reference that was moved out of the root README. The README is intentionally
focused on the project summary and instructions for the Main Tooling Script.

## Builder quick start

The external implementation model has one stable entry point: `BUILDER.md`.

To start or resume a numbered tool locally, run:

```sh
./scripts/builder-start.sh <tool-number>
```

The user may therefore tell the Builder:

```text
BUILD TOOL 1
```

The Builder must run the helper command, or follow `BUILDER.md` manually if
shell execution is unavailable. It must use repository status and must not
depend on previous chat history.

The Main Tooling Script is deliberately unnumbered. Its accepted build and
review records are:

- `docs/main-tooling-script-build-plan.md`;
- `docs/main-tooling-script-walkthrough.md`;
- `status/main-tooling-script.md`.

## Protected `main`, branches, and pull requests

`main` is protected by the GitHub ruleset **Protect main**. Normal
implementation work is performed on a dedicated branch rather than directly on
`main`.

Numbered tools use the standard branch name:

```text
tool-<number>-implementation
```

Implementation, corrections, tests, walkthroughs, and status changes remain on
the implementation branch through review. A pull request to `main` is the
review surface. Planning/review findings are committed to that same branch so
the Builder can resume from the durable status record.

See `docs/implementation-protocol.md` for the complete lifecycle, CI gates, and
acceptance rules. See `docs/planner-builder-coordination.md` for role boundaries.

## Project implementation architecture

The project is a local, reusable Python 3.12 application/package with two
first-class interfaces:

- a CLI for automation, testing, and batch work;
- a localhost browser-based review portal for human decisions.

The environment and dependencies are managed with `uv`. Tool logic remains
callable programmatically. The CLI and review portal call the same application
services. The portal uses FastAPI with server-rendered Jinja2 and HTMX; v1 does
not require an Xcode/Swift or Node/React toolchain.

The full architecture is documented in
`docs/project-implementation-architecture.md`.

## Baserow data authority

The Baserow Media database is mutable and may be updated by external
collaborators:

- Tool 2 has read-only access for current Media lookup and reconciliation;
- Tool 4 has read/write access and is the only tool allowed to mutate rows,
  select options, or schema;
- Tools 1 and 3 do not access Baserow directly;
- persisted mutable row copies are audit/history only and are not authoritative
  operational caches.

Operational consequences:

- database failure is not treated as a valid Media no-match;
- mutable cached rows cannot confirm a current association or enrichment;
- Tool 2 performs live targeted, pagination-complete reads;
- Tool 3 may reuse the verified immutable `travel_schedule` reference;
- Tool 4 performs fresh existence and exact write-precondition checks;
- populated relevant metadata in a confirmed current row is leading;
- contradictions are preserved for review;
- `media_archive_link` remains empty on create and is preserved on existing
  rows because Tools 1–4 do not possess that URL.

Authoritative policies:

- `docs/baserow-live-data-policy.md`;
- `docs/baserow-access-boundary-amendment.md`.

## Continuous integration

GitHub Actions is configured in `.github/workflows/ci.yml`. On pushes to
`main`, pull requests, and manual dispatch it:

1. checks out the repository;
2. installs Python 3.12 and `uv`;
3. reproduces the locked environment with `uv sync --extra dev --frozen`;
4. validates shell helper syntax;
5. runs the complete pytest suite;
6. builds the Python package with `uv build`.

CI does not use the local `.env` or live service credentials. Tests use fake or
mocked external services. The protected-branch required check is named
`Python 3.12 tests`.

## Build plans and status records

Each finalized tool has an authoritative build plan under `docs/` and a durable
status record under `status/`.

Implementation models must not edit finalized build plans. If a requirement is
unclear, contradictory, or impossible as written, the Builder records the
problem in the applicable status file and continues unaffected work. Only
planning/review with the user changes the specification.

Project-wide rules:

- `docs/implementation-protocol.md`;
- `docs/planner-builder-coordination.md`;
- `BUILDER.md`.

## Accepted tools

### Tool 1 — Renamer

Status: **ACCEPTED**

- Build plan: `docs/tool-1-renamer-build-plan.md`
- Walkthrough: `docs/tool-1-renamer-walkthrough.md`
- Acceptance record: `status/tool-1-renamer.md`

Tool 1 interprets and normalizes filenames, assigns stable temporary tracking
identity, extracts and enriches WHEN/WHO/WHAT/WHERE metadata, and performs safe
dry-run or committed renames. It consumes stronger Tool 2 and Tool 3 evidence
but does not access Baserow itself. After a final rename it hands synchronization
to Tool 4.

The standalone Tool 1 review helper is:

```sh
./scripts/review-tool-1.sh [optional-media-directory]
```

It performs a dry run, creates an isolated review registry, starts the localhost
portal, and does not rename files unless a commit action is explicitly chosen in
the portal.

### Tool 2 — Media Database Reviewer

Status: **ACCEPTED**

- Build plan: `docs/tool-2-media-database-reviewer-build-plan.md`
- Live-data amendment: `docs/tool-2-media-database-reviewer-live-data-amendment.md`
- Walkthrough: `docs/tool-2-media-database-reviewer-walkthrough.md`
- Acceptance record: `status/tool-2-media-database-reviewer.md`

Tool 2 performs live, read-only Baserow Media lookup and reconciliation. It
returns structured candidate and association decisions to Tool 1, Tool 3, Tool
4, the CLI, and the review portal. It cannot mutate Baserow.

### Tool 3 — Travel Schedule Reviewer

Status: **ACCEPTED**

- Build plan: `docs/tool-3-travel-schedule-reviewer-build-plan.md`
- Walkthrough: `docs/tool-3-travel-schedule-reviewer-walkthrough.md`
- Acceptance record: `status/tool-3-travel-schedule-reviewer.md`

Tool 3 checks an immutable verified local travel-schedule reference. It
corroborates or proposes WHEN/WHERE evidence and detects schedule conflicts. It
has no live Baserow access.

### Tool 4 — Media Database Updater

Status: **ACCEPTED**

- Build plan: `docs/tool-4-media-database-updater-build-plan.md`
- Walkthrough: `docs/tool-4-media-database-updater-walkthrough.md`
- Acceptance record: `status/tool-4-media-database-updater.md`

Tool 4 is the sole authorized Baserow writer. It creates or minimally updates
Media rows after final Tool 1 state, preserves leading existing metadata,
performs fresh race/precondition checks, and retains durable pending or review
states when synchronization cannot complete safely.

### Main Tooling Script

Status: **ACCEPTED**

- Build plan: `docs/main-tooling-script-build-plan.md`
- Walkthrough: `docs/main-tooling-script-walkthrough.md`
- Acceptance record: `status/main-tooling-script.md`

The Main Tooling Script coordinates the currently accepted Tools 1–4. User
instructions are kept in the root `README.md`.

## Pending tools

- Tool 5: Content discoverer
- Tool 6: Combination-file cutter
- Tool 7: Class type discoverer
- Tool 8: Class trimmer
- Tool 9: Class gain booster
- Tool 10: Questions gain booster
- Tool 11: Processed media organiser

These tools are not implemented yet. The Main Tooling Script must report their
workflow as unavailable rather than simulate successful processing.
