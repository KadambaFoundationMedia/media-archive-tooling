# Media Archive Tooling

Media Archive Tooling processes local archive recordings in place. Its main
script accepts one file, several files, or folders; reports each tool's
progress; and sends uncertain decisions to a separate localhost review portal.
It writes detailed events to one log file and tracks work in a local SQLite
registry.

The current runnable workflow is:

```text
Tool 1: interpret filename
→ Tool 2: review the live Baserow Media database (read-only)
→ Tool 3: check the verified travel schedule
→ Tool 1: create and commit the final filename
→ Tool 4: create, update, or preserve the Baserow Media row
→ Tool 5: discover content from audio and short targeted excerpts
→ Tool 6: split a confirmed singing-and-class combination when safe
→ Tool 4: synchronize the resulting class and singing items
```

The main script currently integrates Tools 1–6. Tool 7 has a build plan but is
not yet implemented; Tools 8–11 still need individual build plans. This is
alpha/beta software: test with `--dry-run` first, and remember that a live
Tool 6 split replaces the full-length working audio with two verified outputs.

## Quick start

Use the included launcher from the repository folder. It performs the required
first-time setup automatically. You do not need to install or operate Python
development tools yourself.

Preview one file, with a concise result for each tool:

```sh
./run-media-archive.sh "/path/to/recording.mp3" --dry-run
```

Process that file live, without a confirmation prompt:

```sh
./run-media-archive.sh "/path/to/recording.mp3"
```

On its first run—or after the project dependencies change—the launcher prepares
the private application environment. This may require an internet connection
and can take a little longer. Later runs start directly. Live Baserow use also
requires the project `.env` configuration.

> **Important:** live mode is the default. Without `--dry-run`, eligible files
> may be renamed, Tool 4 may write to Baserow, and Tool 6 may replace a
> confirmed combination recording with two files. There is no confirmation
> prompt. Work on backed-up media and preview unfamiliar batches first.

## Main Tooling Script

The main command accepts one or more files and directories:

```sh
./run-media-archive.sh <target> [<target> ...] [options]
```

Directories are scanned recursively. Files, directories, and mixed targets may
be supplied in the same command.

### Common examples

Preview a directory recursively (including supported media in subfolders):

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

Show detailed per-stage output while previewing:

```sh
./run-media-archive.sh "/path/to/archive-folder" --dry-run --verbose
```

Run only the renaming/database stages on one file:

```sh
./run-media-archive.sh "/path/to/recording.mp3" --workflow renamer --dry-run
```

Run the audio-discovery/cutting stages on a file already registered by the
renaming workflow:

```sh
./run-media-archive.sh "/path/to/registered-recording.mp3" --workflow processing --dry-run
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
queue shows files needing evaluation, including Tool 5/6 decisions and
actionable Tool 4 synchronization states. The portal can also be started
independently; see [Review portal only](#review-portal-only).

Preview an alpha/beta cleanup, then run it only if the listed test rows are
the ones you intend to remove:

```sh
./run-media-archive.sh --purge --dry-run
./run-media-archive.sh --purge
```

`--purge` may delete Tool 4-tracked test rows from the **live** Baserow table
before clearing local review state. It is not a general archive cleanup
command, and it does not accept file targets.

### Options

| Option | Meaning |
| --- | --- |
| `--dry-run` | Preview final filenames and Tool 4 changes without renaming files or mutating Baserow. |
| `--verbose` | Show extra diagnostics on the console only; the log file is equally detailed in either mode. |
| `--workflow all` | Default: runs the currently integrated Tools 1–6 when applicable. |
| `--workflow renamer` | Runs the renaming/database workflow: Tools 1–4. |
| `--workflow processing` | Runs Tools 5–6 when applicable for a file already registered by Phase 1. |
| `--purge` | Standalone alpha/beta cleanup: remove Tool 4-tracked test rows from Baserow, then reset local review state when remote cleanup succeeds. Use `--purge --dry-run` to preview. Do not supply file targets. |
| `--production` | Currently blocked pending a production retention policy; do not use for archive-wide processing yet. |
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
- key planned/written fields (Title, Category, Date) and the field count;
- the created or selected Baserow row number;
- confirmation of the live row after a successful write;
- Tool 5 content type, confidence, and cut point when run;
- Tool 6 cut result and both output names when a safe split applies.

Default Tool 5 progress is one analysis notice plus a heartbeat about every
30 seconds during long work, instead of one line per short audio excerpt.
Use `--verbose` to see per-excerpt timings and additional field details in the
console. Detailed structured events from all tools are appended to one log file
in either mode. By default, that file is
`.renamer/logs/media-archive-tooling.log`.
Registry state is stored in SQLite so interrupted or retryable Tool 4 work
remains recoverable.

### Safety and data ownership

- Tool 2 may read current Baserow Media data but cannot mutate it.
- Tool 3 uses a verified local travel-schedule reference and has no Baserow
  access.
- Tool 4 is the only component allowed to create or update Baserow rows or
  select options.
- Tool 5 analyzes audio locally and does not access Baserow; Tool 6 cuts local
  audio, while Tool 4 alone handles its Baserow updates.
- Existing confirmed Baserow metadata is leading. Contradictions are routed to
  review instead of being overwritten automatically.
- An existing `media_archive_link` is preserved; Tools 1–4 do not invent or
  replace it.
- A filesystem rename is not rolled back when a later Baserow operation fails.
  The synchronization state is retained for review or retry.

## Review portal only

To open the review portal later against an existing registry:

```sh
./run-media-archive.sh --review-only
```

`--registry-path` is only needed when you deliberately used a different
registry for a separate test run. Normal runs and the portal use the same
default registry automatically.

The older Tool 1 review helper remains available for a standalone dry-run and
portal session:

```sh
./scripts/review-tool-1.sh
```

## Tooling overview

The [Main Tooling Script plan](docs/main-tooling-script-build-plan.md) defines
the local orchestrator. It discovers selected media files, invokes the
available tools in the right order, summarizes progress in the terminal,
records detailed events in one log, and routes unresolved items to the review
portal. It does not replace any tool's own decisions or processing logic.

**Tool 1 — Renamer.** The [Tool 1 build plan](docs/tool-1-renamer-build-plan.md)
defines how filename, folder, database, travel, and later audio evidence become
a canonical filename. Tool 1 can run again as stronger metadata arrives; it
renames in place only when the evidence is sufficient, otherwise it requests
review.

**Tool 2 — Media database reviewer.** The [Tool 2 build plan](docs/tool-2-media-database-reviewer-build-plan.md)
defines a read-only lookup of Baserow's Media table. It searches for matching
recordings, identifies duplicate or conflicting candidates, and passes
confirmed metadata or a structured review decision to Tool 1 and Tool 4. It
never writes Baserow data.

**Tool 3 — Travel schedule reviewer.** The [Tool 3 build plan](docs/tool-3-travel-schedule-reviewer-build-plan.md)
uses the verified local travel schedule as supporting evidence for a
recording's date and place. It can corroborate a filename or flag a genuine
conflict, but a scheduled trip alone does not establish where a recording was
made.

**Tool 4 — Media database updater.** The [Tool 4 build plan](docs/tool-4-media-database-updater-build-plan.md)
defines the sole Baserow writer. After Tool 2's matching decision, it creates
a genuinely new Media row or adds trustworthy metadata to an existing row,
preserving confirmed fields and unrelated online links. Ambiguous matches,
contradictions, and unsafe writes are held for review or retry.

**Tool 5 — Content discoverer.** The [Tool 5 build plan](docs/tool-5-content-discoverer-build-plan.md)
classifies the audio as a class, kirtan, combination, ceremony, or another
supported type. It identifies opening singing and, where possible, a
recording-specific cut point using acoustic analysis and short local
transcription excerpts. Full transcription belongs to Tool 7, not this
pre-cut stage.

**Tool 6 — File cutter.** The [Tool 6 build plan](docs/tool-6-file-cutter-build-plan.md)
handles a confirmed singing-and-class combination. With a trustworthy cut
point, it produces and verifies separate singing and class files, trims only
actual leading silence, and removes the full-length working audio after a
successful split. Tool 1 owns their filenames and Tool 4 handles the distinct
class and singing Media items; uncertain cuts stay intact for portal review.

**Tool 7 — Class type discoverer and full transcription (planned).** The
[Tool 7 build plan](docs/tool-7-class-type-discoverer-build-plan.md) specifies
full local transcription after any Tool 6 cut for every non-kirtan recording.
It uses the reusable transcript to discover or corroborate the class category
and scripture verse, then asks Tool 1/Tool 4 to apply trustworthy metadata.
Kirtan-only files skip this full-transcription stage.

**Tool 8 — Class Trimmer (planned).** This is the future class-audio trimming
stage. Its exact rules and individual build plan have not been agreed yet.

**Tool 9 — Class Gain Booster (planned).** This is the future gain-adjustment
stage for class audio. Its processing rules and individual build plan are
still pending.

**Tool 10 — Questions Gain Booster (planned).** This is the future stage for
questions that need gain adjustment. Its detection and processing rules still
need a build plan.

**Tool 11 — Processed Media Organiser (planned).** This will move finalized
media and applicable transcripts into their destination under
`processed-files`, based on the latest WHAT/category. Its individual build
plan is pending. The [full pipeline workflow](docs/full-pipeline-workflow-amendment.md)
records the confirmed ordering and dependencies for Tools 8–11.

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

Tools 1–6 have implementations; those prompts are for directed corrections or
resumption, not an instruction to rebuild them. Tool 7 is the next planned
build. Tools 8–11 have no finalized individual plans or build prompts yet.

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
