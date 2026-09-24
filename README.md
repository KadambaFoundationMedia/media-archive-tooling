# Media Archive Tooling

Media Archive Tooling is a local Python application for inspecting, renaming,
reviewing, and registering archive media. It provides a command-line runner for
individual files and recursive batches, plus a localhost review portal for
items that need human attention.

The current runnable workflow is:

```text
Tool 1: interpret filename
→ Tool 2: review the live Baserow Media database (read-only)
→ Tool 3: check the verified travel schedule
→ Tool 1: create and commit the final filename
→ Tool 4: create, update, or preserve the Baserow Media row
→ Tool 5: transcribe and discover the recording's content in place
```

Tools 1–5 and the Main Tooling Script are available. Tools 6 and 7 have
finalized build plans but are not yet implemented; Tools 8–11 still need
individual build plans. The current Tool 5 code fully transcribes recordings.
Its planned revision will use bounded classification analysis instead; Tool 7
will then fully transcribe every non-kirtan recording after any Tool 6 cut.

## Quick start

Use the included launcher from the repository folder. It performs the required
first-time setup automatically. You do not need to install or operate Python
development tools yourself.

Preview one file without renaming it or writing to Baserow:

```sh
./run-media-archive.sh "/path/to/recording.mp3" --dry-run
```

Process that file live:

```sh
./run-media-archive.sh "/path/to/recording.mp3"
```

On its first run—or after the project dependencies change—the launcher prepares
the private application environment. This may require an internet connection
and can take a little longer. Later runs start directly. Live Baserow use also
requires the project `.env` configuration.

> **Important:** live mode is the default. Without `--dry-run`, eligible files
> are renamed immediately, Tool 4 may write to Baserow, and Tool 5 may
> transcribe the recording. There is no confirmation prompt.

## Main Tooling Script

The main command accepts one or more files and directories:

```sh
./run-media-archive.sh <target> [<target> ...] [options]
```

Directories are scanned recursively. Files, directories, and mixed targets may
be supplied in the same command.

### Common examples

Preview a directory recursively:

```sh
./run-media-archive.sh "/path/to/archive-folder" --dry-run
```

Preview several explicit files:

```sh
./run-media-archive.sh \
  "/path/to/first.mp3" \
  "/path/to/second.wma" \
  --dry-run
```

Run with detailed per-stage output:

```sh
./run-media-archive.sh "/path/to/archive-folder" --dry-run --verbose
```

Use explicit registry and log locations:

```sh
./run-media-archive.sh "/path/to/archive-folder" \
  --dry-run \
  --registry-path "/path/to/registry.db" \
  --log-file "/path/to/media-archive-tooling.log"
```

Start the localhost review portal after processing:

```sh
./run-media-archive.sh "/path/to/archive-folder" \
  --dry-run \
  --review-portal
```

The portal is available at `http://127.0.0.1:8000` by default. Its active
queue shows files needing evaluation, including Tool 5 content decisions and
actionable Tool 4 synchronization states.

### Options

| Option | Meaning |
| --- | --- |
| `--dry-run` | Preview final filenames and Tool 4 changes without renaming files or mutating Baserow. |
| `--verbose` | Show additional structured details for every tool stage. |
| `--workflow all` | Default: runs the available Tools 1–5 in sequence. Tools 6–11 are not yet executed. |
| `--workflow renamer` | Runs the renaming/database workflow: Tools 1–4. |
| `--workflow processing` | Runs Tool 5 for a file that already passed Phase 1 registration; it does not run pending Tools 6–11. |
| `--purge` | Standalone alpha/beta cleanup: remove Tool 4-tracked test rows from Baserow, then reset local review state when remote cleanup succeeds. Use `--purge --dry-run` to preview. Do not supply file targets. |
| `--registry-path PATH` | Use a specific SQLite registry instead of the configured default. |
| `--log-file PATH` | Write the combined append-only JSONL log to a specific file. |
| `--review-portal` | Start the localhost review portal after the run. |
| `--host HOST` | Select `127.0.0.1` or `localhost` as the loopback portal host. |
| `--port PORT` | Select the portal port; the default is `8000`. |

Run `./run-media-archive.sh --help` for the complete command reference.

### What the runner reports

For each file, terminal output separates the stages and summarizes:

- Tool 1 filename evidence and proposed final filename;
- Tool 2 live Media database decision and candidate row number when available;
- Tool 3 travel-schedule result;
- whether Tool 1 can safely commit the rename;
- Tool 4 `CREATE`, `UPDATE`, `NOOP`, conflict, or blocked result;
- fields that would be or were written;
- the created or selected Baserow row number;
- verified live values after a successful write;
- Tool 5 content type, detected mantra, and transcription progress when run.

Detailed structured events from all tools are appended to one log file. With
the default configuration it is `.renamer/logs/media-archive-tooling.log`.
Registry state is stored in SQLite so interrupted or retryable Tool 4 work
remains recoverable.

### Safety and data ownership

- Tool 2 may read current Baserow Media data but cannot mutate it.
- Tool 3 uses a verified local travel-schedule reference and has no Baserow
  access.
- Tool 4 is the only component allowed to create or update Baserow rows or
  select options.
- Tool 5 analyzes audio locally and does not access Baserow.
- Existing confirmed Baserow metadata is leading. Contradictions are routed to
  review instead of being overwritten automatically.
- An existing `media_archive_link` is preserved; Tools 1–4 do not invent or
  replace it.
- A filesystem rename is not rolled back when a later Baserow operation fails.
  The synchronization state is retained for review or retry.

## Review portal only

To open the review portal later against an existing registry:

```sh
./run-media-archive.sh --review-only --registry-path "/path/to/registry.db"
```

The older Tool 1 review helper remains available for a standalone dry-run and
portal session:

```sh
./scripts/review-tool-1.sh
```

## Builder plans and prompts

The Builder is a **separate Antigravity model** started manually by the owner.
Send one of the exact prompts below to that model; these are not commands for
the media-processing terminal. The Builder reads [BUILDER.md](BUILDER.md), the
linked plan, and the matching status file, then works on its own branch. The
planner reviews/tests the result and merges it only after approval and CI.

| Work | Build plan | Builder prompt |
| --- | --- | --- |
| Main Tooling Script | [Main Script plan](docs/main-tooling-script-build-plan.md) | `BUILD MAIN SCRIPT` |
| Tool 1 — Renamer | [Tool 1 plan](docs/tool-1-renamer-build-plan.md) | `BUILD TOOL 1` |
| Tool 2 — Media database reviewer | [Tool 2 plan](docs/tool-2-media-database-reviewer-build-plan.md) | `BUILD TOOL 2` |
| Tool 3 — Travel schedule reviewer | [Tool 3 plan](docs/tool-3-travel-schedule-reviewer-build-plan.md) | `BUILD TOOL 3` |
| Tool 4 — Media database updater | [Tool 4 plan](docs/tool-4-media-database-updater-build-plan.md) | `BUILD TOOL 4` |
| Tool 5 — Content discoverer | [Tool 5 plan](docs/tool-5-content-discoverer-build-plan.md) | `BUILD TOOL 5` |
| Tool 6 — File cutter | [Tool 6 plan](docs/tool-6-file-cutter-build-plan.md) | `BUILD TOOL 6` |
| Tool 7 — Class type discoverer and full transcription | [Tool 7 plan](docs/tool-7-class-type-discoverer-build-plan.md) | `BUILD TOOL 7` |

Tools 1–5 already have implementations; their prompts are for directed
corrections or resumption, not an instruction to rebuild them. After this
planning revision is merged, send `BUILD TOOL 6` first: its plan includes
the revised Tool 5 exact-cut handoff without full transcription and the Tool
4 video-audio path extension. Then send `BUILD TOOL 7` for full non-kirtan
transcription, category/verse discovery, and the Tool 4 `description` link
handoff. Tools 8–11 have no finalized plans or build prompts yet.

## Project documentation

- [Project reference and development process](docs/project-reference.md)
- [Main Tooling Script walkthrough](docs/main-tooling-script-walkthrough.md)
- [Main Tooling Script acceptance status](status/main-tooling-script.md)
- [Tool 6 implementation status](status/tool-6-file-cutter.md)
- [Tool 7 implementation status](status/tool-7-class-type-discoverer.md)
- [Full pipeline workflow](docs/full-pipeline-workflow-amendment.md)
- [Alpha/beta test-data purge plan](docs/alpha-beta-test-data-purge-build-plan.md)
- [Project implementation architecture](docs/project-implementation-architecture.md)
- [Baserow live-data policy](docs/baserow-live-data-policy.md)
