# Project Implementation Architecture

Status: **authoritative project-wide implementation decision**

This document defines the implementation shell in which the individual media-archive tools should be built. Tool-specific behavior remains defined by each finalized build plan.

## 0. Baserow access boundary

The authoritative access amendment is `docs/baserow-access-boundary-amendment.md`.

Tool 2 owns read-only Baserow lookup/reconciliation. Tool 4 has read-and-write access and is the only writer/schema mutator. Tools 1 and 3 do not access Baserow. Tool 3 consumes a verified static schedule artifact produced through Tool 2's read-only provider boundary.

The integrated flow is:

```text
Tool 1 finds/interprets the file and may make an initial rename
→ Tool 1 asks Tool 2 for a live Baserow Media check
→ Tool 1 asks Tool 3 for date/schedule evidence
→ Tool 1 renders and commits the final filename
→ Tool 1 calls Tool 4 once for that final state
→ Tool 4 uses Tool 2 for a fresh existing-item check
→ Tool 4 directly revalidates the write and synchronizes Baserow
```

For later audio processing, the user-confirmed sequence is in
`docs/full-pipeline-workflow-amendment.md`. In particular, a successful Tool 6
combination cut replaces one working input with singing and class outputs;
the latest Tool 1 naming precedes Tool 11 destination selection, and Tool 4
follows Baserow-relevant metadata/path changes while preserving the existing
class row identity. The sequence is illustrative: Tool 1 and Tool 4 may be
re-invoked whenever later trustworthy metadata warrants it, even if the
filename does not change for a Tool 4-only update.

## 1. Application shape

Build the project as a **local Python application/package with two first-class interfaces**:

1. a command-line interface (CLI) for automation, testing, diagnostics, and unattended batch work;
2. a **local browser-based review portal** for human review, corrections, approvals, progress, and later media-oriented review workflows.

The processing logic must live in reusable Python modules. Neither the CLI nor the review portal may contain a second implementation of archive rules.

Do **not** build Tool 1 as:

- a one-off standalone script;
- a native SwiftUI/Xcode application;
- an Electron application;
- a cloud-hosted service;
- a GUI-only application.

This keeps the functional tools, human review surface, future orchestrator, and any later desktop wrapper cleanly separated.

## 2. Why Python core + local review portal

The project is primarily filesystem, parsing, media-processing, reference-data, database, orchestration, and later local-AI work. Python is appropriate because it has mature libraries for those workloads and works well on Apple Silicon macOS while remaining portable.

Human review is also a core workflow requirement, so a review UI should not be postponed until all tools are complete. A local web UI gives us that without introducing a second programming language, Xcode project, native-app signing/build process, or a Python-to-Swift bridge.

This architecture makes it possible to:

- test every tool without a GUI;
- run large batches unattended from the CLI;
- review uncertain files comfortably in a browser;
- reuse exactly the same service layer from CLI, review portal, and future orchestrator;
- add audio playback/waveforms and richer review views later using standard browser capabilities;
- package the portal in a desktop shell later if that becomes useful, without moving archive logic out of Python;
- keep the core reasonably portable beyond macOS.

The initial target remains Apple Silicon macOS.

## 3. Python runtime and environment

Use **Python 3.12** as the initial development/runtime baseline unless a required dependency proves incompatible.

Use **`uv`** for Python version/dependency/environment management and commit the project metadata/lock file needed for reproducible setup.

Expected repository-level setup:

```text
pyproject.toml
uv.lock
.python-version        # when useful for pinning the local project runtime
src/
tests/
assets/
docs/
status/
```

The real `.env` remains local and ignored by Git. `.env.example` documents the required environment variables.

If a later tool has a dependency that requires a runtime change, the builder must treat that as a project-wide compatibility decision and record it in the relevant status file before changing the baseline.

## 4. Package structure

Use a real installable package, not loose scripts. A suitable structure is:

```text
src/
  media_archive_tooling/
    cli.py
    config.py
    common/
    adapters/
    registry/
    review_portal/
    renamer/
    media_database_reviewer/
    travel_schedule_reviewer/
    ...future tools...
```

Exact internal module names may differ when there is a good technical reason, but the important boundary is fixed:

- reusable tool logic belongs in Python modules/classes/functions;
- CLI code handles arguments, presentation, and exit codes;
- review-portal code handles HTTP/UI concerns and calls the same tool/application services;
- external systems are behind adapters;
- local operational state is behind a registry/storage layer;
- the future orchestrator calls reusable tool interfaces directly rather than scraping CLI or UI output.

## 5. CLI shape

Create one project-level executable entry point, recommended name:

```text
media-archive
```

Individual tools should be exposed as subcommands or equivalent command groups, for example conceptually:

```text
media-archive renamer ...
media-archive media-db-review ...
media-archive travel-review ...
media-archive review
```

`media-archive review` should start the local review portal and may open the default browser automatically.

The exact CLI framework and final option names are implementation details. The builder may choose `argparse`, Typer, Click, or an equivalent maintained library as long as it does not alter tool behavior or constrain future orchestration.

Tool 1 must support non-interactive/dry-run operation as required by its finalized build plan.

## 6. Programmatic interfaces are mandatory

Do not make either the CLI or HTTP routes the only way to use a tool.

Each tool must expose a programmatic interface suitable for the future orchestrator. Conceptually:

```text
input/config + shared context
        ↓
Tool/application service
        ↓
structured result/events
       ↙       ↘
     CLI     review portal
```

The exact class/function signatures are implementation details until cross-tool contracts are finalized, but tool logic must be callable directly in Python.

## 7. Review portal architecture

The first review portal should be a **local web application served by the same Python project**.

Use **FastAPI** for the local application/API layer. Prefer server-rendered HTML using **Jinja2 + HTMX** for the initial UI rather than introducing a Node/React build pipeline before it is needed. Small amounts of focused browser JavaScript are acceptable where they materially improve interaction, audio playback, or later waveform views.

The portal must:

- bind to loopback (`127.0.0.1` / localhost) by default;
- not require Internet access for its own UI;
- use the same typed application services and local registry as the CLI;
- never duplicate archive/naming/business rules in templates or JavaScript;
- expose structured review actions rather than modifying SQLite or files directly from UI code;
- support incremental expansion as later tools add review needs.

If the portal is ever exposed beyond localhost, authentication/network-security requirements must be designed explicitly first; do not casually change the bind address.

### Initial Tool 1 review scope

The minimal useful portal built alongside Tool 1 should support:

- batch/file list and processing state;
- original/current/proposed filename;
- parsed WHEN/WHO/WHAT/WHERE;
- field resolution state (`exact`, `strong`, `provisional`, `ambiguous`, `unresolved`);
- evidence and alternative candidates;
- conflicts/review reasons/unclassified text;
- approve, edit/correct, and defer actions where human review is required;
- dry-run/commit visibility and per-file errors;
- basic batch progress and filtering for items requiring review.

The portal is a review surface, not a replacement for deterministic automatic processing. Files that do not require human review should continue through the batch without waiting for UI interaction.

## 8. Future desktop packaging

No native desktop framework is required for v1.

A native SwiftUI application would require the Apple/Xcode toolchain and introduce a second implementation environment plus a bridge to the Python processing core. That cost is not justified while the workflows and review screens are still evolving.

If, after the review portal is proven, a packaged desktop experience is desirable, evaluate a thin desktop wrapper such as Tauri or Electron, or reconsider SwiftUI. Any wrapper must remain above the Python application/orchestrator layer:

```text
optional desktop shell
        ↓
local review portal / application API
        ↓
Python orchestrator + tool services
```

Do not move archive rules, parsing logic, Baserow logic, or media-processing logic into the desktop shell.

## 9. Configuration and secrets

Use repository `.env.example` as the documented configuration contract.

The local `.env` may contain credentials, IDs, and machine-specific paths and must never be committed.

Configuration should be loaded into a typed/validated application configuration object before tool execution. The exact validation library is the builder's choice.

Do not scatter direct reads from `os.environ` throughout parser/tool logic.

## 10. Local state and generated data

Local operational state belongs outside authoritative archive/reference assets.

For Tool 1, the finalized build plan already specifies a local processing registry, with SQLite recommended. That registry should be implemented as an internal storage component shared by the CLI, review portal, and future orchestrator on the same machine.

Caches, runtime logs, generated summaries, temporary files, model caches, and similar machine-local artifacts must remain distinct from committed `assets/`.

`assets/` is for committed project/reference material.

## 11. External integrations

External systems should be behind explicit adapters/providers rather than called directly throughout tool or UI logic.

Examples include:

- Baserow;
- Vedabase;
- online geocoding/location lookup;
- YouTube APIs;
- future transcription/model runtimes.

The builder may choose ordinary implementation libraries such as an HTTP client. A provider/framework choice becomes review-worthy when it changes authority, data ownership, privacy, portability, costs, persistent schemas, or later tool contracts.

## 12. Testing baseline

Use `pytest` as the project test runner unless a concrete compatibility problem requires otherwise.

Test tool/application services independently from the UI, and add focused integration tests for critical review-portal routes/actions.

Keep unit tests and representative golden/sample tests in the repository while keeping private archive media itself out of Git according to `.gitignore`.

Tool-specific acceptance criteria remain defined by each build plan.

## 13. What the builder may decide independently

The builder may choose ordinary implementation details when they do not change project behavior, including:

- exact Python module/class names;
- CLI framework;
- HTTP client;
- fuzzy-string library;
- data-validation library;
- test helper libraries;
- CSS/layout approach within the local portal;
- internal algorithms that satisfy the specification;
- formatting/linting tooling.

The builder should prefer maintained, lightweight dependencies and avoid introducing a framework merely because it is convenient for one tool.

## 14. What the builder must not decide silently

The builder must raise a status-file question before changing any of the following:

- Python package/service layer as the core application shape;
- the local web review portal architecture or replacing it with a native/cloud UI;
- the Python runtime baseline in a way that affects other tools;
- local-first processing;
- separation between functional tools, orchestrator, and UI;
- Baserow/shared-state semantics;
- persistent schemas or processing identity relied on across tools;
- authority of reference sources;
- archive/naming policy;
- safety/idempotency behavior;
- exposing the local portal to the network;
- a framework/runtime choice that materially constrains future tools or UI.

Unclear or contradictory requirements follow `docs/implementation-protocol.md`.

## 15. Builder startup sequence

For the first implementation, the builder should therefore:

1. Read `docs/project-implementation-architecture.md`.
2. Read the finalized Tool 1 build plan.
3. Read the Tool 1 status file.
4. Create/verify the Python 3.12 + `uv` project skeleton and `pyproject.toml`.
5. Create the reusable package/application-service boundary before CLI or portal presentation code.
6. Add tests from the beginning.
7. Implement the Tool 1 deterministic core, local registry, logging, and dry-run path.
8. Add the minimal FastAPI/Jinja2/HTMX review portal against the same services/registry so real sample results can be reviewed early.
9. Continue Tool 1 integrations and behavior according to its finalized plan.
10. Update the status file and commit checkpoints according to `docs/implementation-protocol.md`.

The builder does not need to invent the application/UI approach: **the core is a reusable local Python application/package, operated through both a CLI and a localhost review portal.**
