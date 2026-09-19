# Media Archive Tooling

Media Archive Tooling is a local Python application for inspecting, renaming,
reviewing, and registering archive media. It provides a command-line runner for
individual files and recursive batches, plus a localhost review portal for
items that need human attention.

The accepted Phase A workflow is:

```text
Tool 1: interpret filename
→ Tool 2: review the live Baserow Media database (read-only)
→ Tool 3: check the verified travel schedule
→ Tool 1: create and commit the final filename
→ Tool 4: create, update, or preserve the Baserow Media row
```

Tools 1–4 and the Main Tooling Script are accepted. Tools 5–11 are planned but
not yet implemented.

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
> are renamed immediately and Tool 4 may write to Baserow. There is no
> confirmation prompt.

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

The portal is available at `http://127.0.0.1:8000` by default. Only files that
need evaluation, have a conflict, or have an actionable Tool 4 synchronization
state appear in its active queue.

### Options

| Option | Meaning |
| --- | --- |
| `--dry-run` | Preview final filenames and Tool 4 changes without renaming files or mutating Baserow. |
| `--verbose` | Show additional structured details for every tool stage. |
| `--workflow all` | Default Phase A workflow. Runs Tools 1–4 and reports Tools 5–11 as pending. |
| `--workflow renamer` | Runs the currently available renaming workflow: Tools 1–4. |
| `--workflow processing` | Reserved for Tools 4–11; currently exits before mutation because Tools 5–11 are pending. |
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
- verified live values after a successful write.

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

## Project documentation

- [Project reference and development process](docs/project-reference.md)
- [Main Tooling Script build plan](docs/main-tooling-script-build-plan.md)
- [Main Tooling Script walkthrough](docs/main-tooling-script-walkthrough.md)
- [Main Tooling Script acceptance status](status/main-tooling-script.md)
- [Project implementation architecture](docs/project-implementation-architecture.md)
- [Baserow live-data policy](docs/baserow-live-data-policy.md)
- [Builder instructions](BUILDER.md)
