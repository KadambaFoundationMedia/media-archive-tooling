# Project Implementation Architecture

Status: **authoritative project-wide implementation decision**

This document defines the implementation shell in which the individual media-archive tools should be built. Tool-specific behavior remains defined by each finalized build plan.

## 1. Application shape

Build the project first as a **local, headless Python application/package with a command-line interface (CLI)**.

Do **not** build Tool 1 as:

- a one-off standalone script
- an Electron application
- a web application
- a GUI-only application
- an online/cloud service

The processing logic must live in reusable Python modules. The CLI is only an adapter/entry point around those modules.

This keeps the functional tools independent from the future user interface and orchestrator.

## 2. Why CLI/package first

The current tools are primarily filesystem, parsing, media-processing, reference-data, database, and orchestration workloads. A Python core is appropriate because it provides mature libraries for these jobs and works well on Apple Silicon macOS.

A headless core also makes it possible to:

- test every tool without a GUI
- run large batches unattended
- use the tools from a future orchestrator
- expose the same functionality to a future desktop UI without duplicating business logic
- keep later Electron or other UI decisions separate from processing-tool implementation

The initial target is Apple Silicon macOS, while avoiding unnecessary platform-specific coupling in the core.

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
    renamer/
    media_database_reviewer/
    travel_schedule_reviewer/
    ...future tools...
```

Exact internal module names may differ when there is a good technical reason, but the important boundary is fixed:

- reusable tool logic belongs in Python modules/classes/functions
- CLI code handles arguments, presentation, and exit codes
- external systems are behind adapters
- local operational state is behind a registry/storage layer
- later UI/orchestrator code must call the reusable tool interfaces rather than contain duplicate processing logic

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
```

The exact CLI framework and final option names are implementation details. The builder may choose `argparse`, Typer, Click, or an equivalent maintained library as long as it does not alter tool behavior or constrain future orchestration.

Tool 1 must support non-interactive/dry-run operation as required by its finalized build plan.

## 6. Programmatic interfaces are mandatory

Do not make the CLI the only way to use a tool.

Each tool must expose a programmatic interface suitable for the future orchestrator. Conceptually:

```text
input/config + shared context
        ↓
Tool service
        ↓
structured result/events
```

The exact class/function signatures are implementation details until cross-tool contracts are finalized, but tool logic must be callable without shelling out to the CLI.

This is important because the future orchestrator should coordinate tools directly and the future UI should sit above the orchestrator rather than scrape terminal output.

## 7. Future UI

A desktop UI may later use Electron or another framework, but **no UI framework is selected by this decision**.

If Electron is chosen later, the intended architecture is:

```text
Desktop UI
   ↓
Orchestrator / application layer
   ↓
Python tool interfaces
```

Do not move archive rules, parsing logic, Baserow logic, or media-processing logic into Electron/JavaScript merely because a GUI is added.

The integration mechanism between a future desktop UI and Python (local API, IPC, subprocess protocol, etc.) will be decided when the UI/orchestrator is designed.

## 8. Configuration and secrets

Use repository `.env.example` as the documented configuration contract.

The local `.env` may contain credentials, IDs, and machine-specific paths and must never be committed.

Configuration should be loaded into a typed/validated application configuration object before tool execution. The exact library is the builder's choice.

Do not scatter direct reads from `os.environ` throughout parser/tool logic.

## 9. Local state and generated data

Local operational state belongs outside authoritative archive/reference assets.

For Tool 1, the finalized build plan already specifies a local processing registry, with SQLite recommended. That registry should be implemented as an internal storage component that can later be shared by the orchestrator on the same machine.

Caches, runtime logs, generated summaries, temporary files, model caches, and similar machine-local artifacts must remain distinct from committed `assets/`.

`assets/` is for committed project/reference material.

## 10. External integrations

External systems should be behind explicit adapters/providers rather than called directly throughout tool logic.

Examples include:

- Baserow
- Vedabase
- online geocoding/location lookup
- YouTube APIs
- future transcription/model runtimes

The builder may choose ordinary implementation libraries such as an HTTP client. A provider/framework choice becomes review-worthy when it changes authority, data ownership, privacy, portability, costs, persistent schemas, or later tool contracts.

## 11. Testing baseline

Use `pytest` as the project test runner unless a concrete compatibility problem requires otherwise.

Keep unit tests and representative golden/sample tests in the repository while keeping private archive media itself out of Git according to `.gitignore`.

Tool-specific acceptance criteria remain defined by each build plan.

## 12. What the builder may decide independently

The builder may choose ordinary implementation details when they do not change project behavior, including:

- exact Python module/class names
- CLI framework
- HTTP client
- fuzzy-string library
- data-validation library
- test helper libraries
- internal algorithms that satisfy the specification
- formatting/linting tooling

The builder should prefer maintained, lightweight dependencies and avoid introducing a framework merely because it is convenient for one tool.

## 13. What the builder must not decide silently

The builder must raise a status-file question before changing any of the following:

- Python CLI/package as the core application shape
- the Python runtime baseline in a way that affects other tools
- local-first processing
- separation between functional tools, orchestrator, and UI
- Baserow/shared-state semantics
- persistent schemas or processing identity relied on across tools
- authority of reference sources
- archive/naming policy
- safety/idempotency behavior
- a framework/runtime choice that materially constrains future tools or UI

Unclear or contradictory requirements follow `docs/implementation-protocol.md`.

## 14. Builder startup sequence

For the first implementation, the builder should therefore:

1. Read `docs/project-implementation-architecture.md`.
2. Read the finalized tool build plan.
3. Read the tool status file.
4. Create/verify the Python 3.12 + `uv` project skeleton and `pyproject.toml`.
5. Create the reusable package/module boundary before implementing CLI presentation.
6. Add tests from the beginning.
7. Implement Tool 1 according to its finalized plan.
8. Update the status file and commit checkpoints according to `docs/implementation-protocol.md`.

The builder does not need to invent whether this project is a script, app, or GUI: **the core is a reusable local Python application/package, initially operated through a CLI.**