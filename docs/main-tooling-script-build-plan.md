# Main Tooling Script — Build Plan

Status: **FINALIZED — implementation-ready**

Alpha/beta cleanup amendment: `docs/alpha-beta-test-data-purge-build-plan.md`
is authoritative for review-state invalidation and the standalone `--purge`
workflow.

This document is the authoritative implementation specification for the unnumbered **Main Tooling Script**. It is an orchestration entry point, not Tool 12 and not a replacement for any existing tool.

Implementation progress, questions, review findings, and commit checkpoints belong in `status/main-tooling-script.md`. The Builder must not rewrite this finalized plan to fit an implementation.

Required project context:

- `BUILDER.md`
- `status/main-tooling-script.md`
- `docs/project-implementation-architecture.md`
- `docs/implementation-protocol.md`
- `docs/baserow-access-boundary-amendment.md`
- `docs/baserow-live-data-policy.md`
- the finalized plans and current status files for Tools 1–4

---

## 1. Identity and purpose

The Main Tooling Script is the local command-line orchestrator for the Media Archive Tooling Project.

It must:

- execute the existing tools in the required order;
- operate on media files stored on the local machine;
- accept a single file, several files, one or more folders, or a mixture of files and folders;
- recursively discover supported media files in folders;
- show concise progress and each tool's useful result in the terminal;
- write detailed execution evidence to one persistent log file;
- route only items that genuinely require evaluation to the review portal;
- support a non-mutating dry-run;
- be extensible to Tools 5–11 without inventing or mocking tools that have not been built.

The Main Tooling Script owns orchestration only. It must call the existing application-service boundaries and must not duplicate parsing, matching, travel reasoning, canonical naming, Baserow mutation, or later media-processing algorithms.

It is deliberately unnumbered. The user starts the external Builder with:

```text
BUILD MAIN SCRIPT
```

The implementation branch is:

```text
main-tooling-script-implementation
```

---

## 2. Implementation phases

### Phase A — required initial implementation

Phase A must be complete and practically usable with the tools currently available:

```text
Tool 1 → Tool 2 → Tool 3 → Tool 1 finalization → Tool 4
```

This is the current Renamer workflow without Tool 11, because Tool 11 has not been built.

Phase A is the implementation requested now. It must not wait for Tools 5–11.

### Phase B — later integration

When Tools 5–11 have been accepted, the same orchestrator will be extended to support:

Tool 5's finalized independent discovery contract is in
`docs/tool-5-content-discoverer-build-plan.md`. Its later integration must use
that typed service and its durable `process_by_tool_6` handoff state; it must
not reimplement transcription or content classification in the orchestrator.

```text
Renamer workflow:
Tools 1, 2, 3, 4, 11

Processing workflow:
Tools 4, 5, 6, 7, 8, 9, 10, 11

All workflow:
all applicable stages in the accepted project order
```

Phase A must provide clean workflow/stage registration points for these additions. It must not provide fake success results, placeholder media transformations, or speculative implementations for pending tools.

---

## 3. Main command-line interface

Extend the existing unified Python CLI instead of creating a second independent orchestration implementation.

The required command shape is:

```sh
media-archive run <target> [<target> ...] [options]
```

It must also work from the locked local project environment:

```sh
uv run media-archive run <target> [<target> ...] [options]
```

Required options:

```text
--dry-run
--verbose
--workflow all|renamer|processing
--registry-path PATH
--log-file PATH
--review-portal
--host 127.0.0.1|localhost
--port PORT
```

Rules:

- one or more positional targets are required;
- the default workflow selection is `all`;
- live execution is the default when `--dry-run` is absent;
- there is no interactive confirmation prompt;
- there is no pause between Tool 1, Tool 2, Tool 3, Tool 1 finalization, and Tool 4;
- `--verbose` changes terminal detail, not the completeness of the log file;
- `--review-portal` starts the local review portal after processing, using the same registry and evaluation queue;
- without `--review-portal`, the runner finishes normally and prints the command needed to open the portal when evaluation items exist.

The Builder may add narrowly necessary options, but must not change these semantics or require confirmations that the user rejected.

---

## 4. Live mode and dry-run mode

### Live mode

Without `--dry-run`, the script immediately operates on the original files.

It must:

- apply approved final Tool 1 filesystem renames directly to the original files;
- call Tool 4 after the final rename;
- allow Tool 4 to create or update Baserow under its existing safety rules;
- continue automatically without asking the user to confirm the rename or Baserow mutation;
- never make an intermediate Tool 1 rename trigger Tool 4;
- preserve the existing durable pending-sync behavior when a file rename succeeds but Tool 4 cannot complete.

The command must print a conspicuous startup line showing that it is in live mode, but this is informational and must not pause execution.

### Dry-run mode

With `--dry-run`, the runner may perform local analysis, live read-only Tool 2 queries, Tool 3 reference evaluation, Tool 4 live schema/row reads, registry writes needed for review, and logging.

It must not:

- rename, move, delete, or rewrite a media file;
- create, patch, or delete a Baserow row;
- create or alter a Baserow select option or schema;
- perform a later organizer move;
- claim that a proposed Baserow row has a real row ID.

Dry-run Tool 4 output must be based on the projected final filename and path, not a stale intermediate filename. It must clearly say `WOULD CREATE`, `WOULD UPDATE`, `WOULD NO-OP`, or `WOULD BLOCK/REVIEW`.

For a proposed update, show the existing target row number. For a proposed create, show `new row — ID assigned only on commit`.

---

## 5. Target discovery

The runner must accept:

- one media file;
- several explicitly listed media files;
- one folder;
- several folders;
- a mixture of files and folders.

Folder traversal is recursive.

Discovery requirements:

- resolve targets to absolute local paths;
- reject missing targets before mutating anything;
- remove duplicate file targets deterministically;
- use a stable deterministic processing order;
- process only supported media types already recognized by the project;
- log unsupported files as skipped rather than treating them as failed media;
- do not follow directory symlinks by default;
- never process files outside the supplied target scope merely because they are siblings.

For a single selected file, Tool 1 may inspect sibling names to construct its existing collection grammar, but only the selected file may be renamed or synchronized.

---

## 6. Initial workflow: Tools 1–4

The initial workflow is one continuous per-file collaboration. Tool names are shown separately for clarity, but they are not interactive checkpoints.

### Stage 1 — Tool 1 initial interpretation

Tool 1 must:

- establish/reuse the stable tracking identity;
- parse filename and folder evidence;
- extract current WHEN, WHO, WHAT, WHERE, technical flags, sequence context, and collection evidence;
- create the initial structured proposal;
- emit only a summary to normal terminal output while detailed evidence goes to the log.

Example terminal summary:

```text
Processing: /archive/path/file.mp3
Tool 1 — Renamer
  Date: 2022-09-19
  Speaker: KKS
  WHAT: SB 1.2.19
  Location: Oslo, Norway
```

### Stage 2 — Tool 2 live Media review

Tool 2 must run through its existing read-only service.

Show:

- that the Media database is being reviewed;
- the decision;
- selected row number when an existing association is established;
- candidate/conflict count when relevant;
- any confirmed enrichment passed back to Tool 1.

Tool 2 remains technically read-only. The Main Tooling Script must not receive or expose a Baserow write operation through Tool 2.

### Stage 3 — Tool 3 travel-schedule review

Tool 3 must run through its verified local reference/service boundary.

Show:

- that the travel schedule is being checked;
- the decision;
- supported date/location evidence or bounded contextual evidence;
- whether provisional enrichment was applied;
- concise reasons when the schedule cannot narrow the result.

Tool 3 must not access Baserow or rename files.

### Stage 4 — Tool 1 finalization

Tool 1 combines accepted Tool 2 and Tool 3 evidence and renders the final proposal.

Show:

- original filename;
- final proposed filename;
- final proposed full path;
- whether the item can be committed automatically;
- concise review reasons when it cannot.

In live mode, Tool 1 commits the final rename immediately when permitted. In dry-run mode, it reports the projected rename without changing the file.

### Stage 5 — Tool 4 synchronization

Tool 4 is invoked only for the final state.

In live mode it runs after the successful final rename. In dry-run mode it previews synchronization using the projected final state.

Show:

- fresh Tool 2 create-vs-update decision;
- `CREATE`, `UPDATE`, `NOOP`, `CONFLICT`, or `BLOCKED`;
- existing row number or the created row number when available;
- every field that would be/is set, with its value;
- fields preserved when relevant to understanding a conflict;
- conflicts/review reasons;
- live readback summary after a successful mutation.

Tool 4 remains the only Baserow writer. The Main Tooling Script must never call Baserow row/schema mutation endpoints itself.

---

## 7. Workflow selection and pending tools

Required workflow meanings:

```text
renamer
    Phase A: Tools 1, 2, 3, 4
    Later: Tools 1, 2, 3, 4, 11

processing
    Later: Tools 4, 5, 6, 7, 8, 9, 10, 11

all
    All currently installed/accepted workflow stages in project order
```

During Phase A:

- `--workflow renamer` runs Tools 1–4;
- default `--workflow all` runs Tools 1–4 and explicitly reports that the Processing workflow is pending Tools 5–11;
- `--workflow processing` exits before mutation with a clear `workflow not available yet` message and non-zero configuration exit status;
- the runner must not report pending Tools 5–11 as passed, skipped media, or successful processing.

The workflow registry must make later integration possible without rewriting the Tools 1–4 orchestration core.

Tool 11 belongs to both eventual high-level workflows but must execute only once per applicable final file state when `all` is used.

---

## 8. Automatic continuation and review routing

Normal execution has no confirmation prompts and no tool-by-tool pauses.

Incomplete metadata alone does not automatically mean review. Existing Tool 1–4 policy decides whether a partial but useful filename can proceed.

An item enters the review/evaluation queue when any of the following applies:

- Tool 1 marks `needs_review` or blocks the final rename;
- there is a target collision that cannot be resolved safely;
- Tool 2 reports multiple candidates, a material conflict, insufficient identity evidence, or database unavailability that blocks the operation;
- Tool 3 reports an actual processing/reference failure or a conflict requiring evaluation;
- Tool 4 reports a conflict, blocked write, stale precondition, uncertain outcome, schema problem, or retryable failure;
- a filesystem operation fails;
- another accepted tool explicitly marks the item for evaluation.

An ordinary `NO_SCHEDULE_SUPPORT`, an allowed incomplete date, or a still-unknown location must not be sent to review merely because it is incomplete when the accepted tool policies allow continued processing.

A failed/review item must not stop unrelated files in the same run unless the failure is global, such as invalid configuration or an unavailable required shared service.

---

## 9. Review portal integration

The existing review portal must be extended as needed; do not create a separate portal.

Only items currently subject to evaluation should appear in its active evaluation queue.

Each evaluation item must show, where available:

- source/current path;
- original and proposed/final filename;
- tracking ID in technical detail views;
- Tool 1 structured result;
- Tool 2 decision, candidate rows, and selected row;
- Tool 3 decision and schedule/context evidence;
- Tool 4 operation, field diff, row number, conflicts, and pending/retry status;
- the exact reason the automatic workflow stopped;
- relevant detailed-log run ID/timestamp.

Successfully completed items must not clutter the active evaluation queue, though their durable registry/audit history may remain available through status/detail views.

The portal must use application services. Browser routes/templates must not directly mutate Baserow or the filesystem.

---

## 10. Terminal progress output

Normal terminal output is concise and human-readable.

For every file, show at least:

```text
Processing file: /absolute/path/file.mp3
Tool 1 — extracted metadata and proposed/final filename
Tool 2 — Media database review decision and row/candidates
Tool 3 — travel schedule decision/evidence
Tool 4 — create/update/no-op/review and exact written/proposed fields
Result — completed, dry-run, review required, pending sync, or failed
```

The tool may update progress on multiple lines. Output must remain understandable without reading the detailed log.

`--verbose` additionally shows provenance, candidate summaries, preserved fields, timing, and diagnostic details. It must never print credentials or secret headers.

At the end, print a run summary containing:

- total discovered media files;
- completed files;
- dry-run previews;
- unchanged/no-op files;
- items requiring evaluation;
- pending/retryable Tool 4 synchronizations;
- failed files;
- skipped unsupported files;
- log-file path;
- registry path;
- review portal command/URL when evaluation items exist.

---

## 11. One detailed log file

Use one persistent append-only log file, not a separate log directory/file for every run.

Default path:

```text
.renamer/media-archive-tooling.log
```

`--log-file` may override the path.

Requirements:

- every run receives a unique run ID;
- every entry includes timestamp, run ID, severity, workflow, file/tracking ID when applicable, tool/stage, event, and details;
- record input targets, resolved files, mode, options, tool results, decisions, diffs, errors, timing, and final counts;
- append safely without truncating prior runs;
- flush important state transitions so a crash still leaves useful evidence;
- avoid interleaved/corrupt lines if overlapping local runs occur;
- redact API tokens, authorization headers, passwords, keys, and sensitive environment values;
- never log raw `.env` contents;
- normal mode and verbose mode write equally complete logs.

The format may be structured JSON Lines or a stable structured text format, but it must be readable and machine-parseable. If JSON Lines is selected, retain the `.log` filename required above.

Existing tool-specific loggers may be adapted or bridged, but the Main Tooling Script must expose one canonical combined log file to the user.

---

## 12. Registry, identity, and audit behavior

Use the existing SQLite registry and service contracts rather than introducing a parallel state store.

Requirements:

- one configured operational registry may contain many runs/files;
- stable tracking identity survives repeated runs and renames;
- rerunning a successfully synchronized file must be idempotent;
- dry-run proposals are distinguishable from committed state;
- Tool 2/3 review evidence and Tool 4 request/result audit remain queryable;
- the review portal reads the same registry selected by the Main Tooling Script;
- pending Tool 4 sync survives process termination and can be retried through existing service behavior;
- original filename/path provenance remains preserved after later renames.

Do not create a fresh isolated registry for every invocation by default. `--registry-path` exists for deliberate test isolation.

---

## 13. Failure and consistency rules

Per-file execution must be safe and resumable.

- Validate configuration and all supplied targets before the first mutation.
- A Tool 1 final rename that succeeds must never be rolled back merely because Tool 4 fails.
- Such a Tool 4 failure becomes durable pending/retry/review state.
- A Baserow failure must never be interpreted as proof that no row exists.
- A Tool 4 timeout with uncertain create/update outcome must use existing reconciliation rules before retrying.
- Do not overwrite collaborator changes.
- Do not silently choose the first of several Baserow candidates.
- Continue to the next independent file after a per-file failure.
- Global configuration failure stops the run before mutation.
- Keyboard interruption must leave already committed work and durable registry/audit state truthful.

Recommended exit semantics:

```text
0  run completed; review-required items may exist as a normal business outcome
1  global configuration/startup failure
2  one or more unexpected execution failures occurred
```

The exact codes may be refined if existing CLI conventions require it, but review-required must remain distinguishable from an unexpected crash in terminal summary and logs.

---

## 14. Architecture constraints

The implementation should add a thin orchestration service with CLI and portal adapters.

It must:

- compose existing Tool 1–4 services through dependency injection;
- keep orchestration logic testable without real network or production files;
- provide typed per-stage and per-file results suitable for terminal, logs, and portal;
- avoid shelling out to the project's own CLI for each internal stage;
- avoid reparsing console output to communicate between tools;
- avoid duplicating business rules already owned by a tool;
- preserve Baserow access boundaries;
- keep future workflow registration explicit and ordered.

The implementation may refactor the existing `run_renamer` CLI orchestration into a reusable application service when necessary. Existing supported commands must remain backward compatible unless the finalized plan explicitly supersedes them.

---

## 15. Performance and concurrency

Correctness and auditability are more important than aggressive parallelism.

Initial implementation may process files sequentially.

It must:

- avoid loading full media file contents when metadata/path evidence is sufficient;
- avoid full-table Baserow downloads where Tool 2 targeted/paginated behavior applies;
- avoid repeated environment/dependency initialization per file;
- show progress during long folder runs;
- not run concurrent writes that could bypass Tool 4 race guards.

Parallel execution may be considered later only with correct registry/log locking and unchanged Tool 4 safety.

---

## 16. Security and privacy

- Load configuration through the existing configuration boundary.
- Never expose secrets in terminal output, portal pages, logs, exceptions, or serialized audit results.
- Bind the portal only to loopback.
- Do not send local media to external services unless an accepted later tool explicitly requires and authorizes it.
- Keep Tool 2 read-only and Tools 1/3 without Baserow clients.
- Keep all Baserow mutations inside Tool 4.

---

## 17. Required automated tests

Automated tests must use temporary files, temporary registries, and fake service/network adapters. CI must not require `.env` or production credentials.

At minimum cover:

1. one explicit media file;
2. multiple explicit files;
3. recursive folder discovery;
4. mixed files/folders and duplicate target removal;
5. unsupported-file skipping;
6. single-file scope does not mutate siblings while collection grammar may inspect names;
7. default workflow is `all`;
8. Phase A `all` runs Tools 1–4 and reports pending processing tools honestly;
9. `renamer` runs Tools 1–4 in the required order;
10. unavailable `processing` fails before mutation;
11. no interactive prompt occurs in live mode;
12. live mode renames the original file;
13. Tool 4 is called only after successful finalization/rename;
14. dry-run makes no filesystem mutation;
15. dry-run makes no Baserow/schema/select-option mutation;
16. dry-run Tool 4 request uses projected final filename/path;
17. dry-run create reports no fabricated row ID;
18. Tool 2 remains read-only;
19. Tool 3 has no Baserow access;
20. Tool 4 remains the only writer;
21. concise terminal output contains separate Tool 1–4 summaries;
22. verbose terminal output adds detail without secrets;
23. exact planned/written Baserow fields are displayed;
24. created/selected row ID is displayed when available;
25. one persistent log file is appended across two runs;
26. log entries contain run IDs and tool/file context;
27. secrets are redacted from logs and errors;
28. completed items do not enter the active portal evaluation queue;
29. blocked/conflict/failure items do enter the evaluation queue with exact reasons;
30. an allowed partial date/location can continue without unnecessary review;
31. one per-file failure does not stop later independent files;
32. global configuration failure occurs before mutation;
33. rename success plus Tool 4 failure preserves the rename and durable pending sync;
34. rerun/idempotency does not create a duplicate Baserow row;
35. existing CLI commands remain regression-safe;
36. full project test suite and package build pass.

Add regression fixtures for the practical Oslo and Czech/Duben filename patterns without performing production writes.

---

## 18. Required practical evaluation

Before `READY_FOR_REVIEW`, the Builder must provide:

1. a dry-run against one explicit sample file;
2. a dry-run against multiple explicit files;
3. a dry-run against a recursively scanned sample folder;
4. captured terminal summaries showing Tools 1–4 separately;
5. the combined log-file excerpt for the same run ID;
6. proof that dry-run changed neither files nor Baserow fake state;
7. a hermetic live-mode test proving original-file rename and fake Tool 4 write/readback;
8. portal evidence showing only review-required items in the active queue.

The Builder must not perform a live production Baserow write as part of automated acceptance. A real-file/real-Baserow test is performed later only under user/planner direction.

---

## 19. Deliverables

The Builder must deliver:

- reusable Main Tooling orchestration service;
- `media-archive run` CLI integration;
- workflow/stage registry ready for later accepted tools;
- single/multiple/recursive target discovery;
- live and dry-run behavior;
- concise terminal reporter and verbose mode;
- one persistent combined logger;
- review portal evaluation-queue integration;
- typed results/audit integration;
- automated unit/integration/architecture tests;
- updated README usage documentation;
- `docs/main-tooling-script-walkthrough.md`;
- updated `status/main-tooling-script.md` with exact test/evaluation results;
- an implementation PR from `main-tooling-script-implementation` to `main`.

---

## 20. Non-goals for Phase A

Phase A does not:

- implement Tools 5–11;
- simulate successful processing by missing tools;
- add audio analysis, cutting, trimming, gain boosting, content discovery, or organization logic;
- redesign accepted Tool 1–4 business rules;
- merge Tools 1–3 into one code module merely because the terminal presents one continuous workflow;
- add confirmation prompts;
- create a second Baserow client outside Tool 4;
- delete or clean up production Baserow rows;
- automatically resolve genuine review conflicts;
- process a sibling file that was not in the supplied target scope.

---

## 21. Builder handoff

The Builder must follow `BUILDER.md`, use `main-tooling-script-implementation`, keep one PR open, and update `status/main-tooling-script.md` throughout implementation.

The completion message is:

```text
MAIN SCRIPT READY_FOR_REVIEW
BRANCH: main-tooling-script-implementation
PR: <number or URL>
HEAD: <reachable SHA>
Status: status/main-tooling-script.md
Build plan: docs/main-tooling-script-build-plan.md
Local tests: <commands and results>
GitHub CI: <passed/pending>
Practical evaluation: <summary>
Open questions: <Q-IDs or none>
```

Only planning/review may accept and merge the implementation.

---

## 22. User-confirmed full-pipeline and archive-scale amendment (2026-09-23)

The authoritative cross-tool sequence and Tool 6 split/Tool 4 row-identity
rules are in `docs/full-pipeline-workflow-amendment.md`. This section adds
actionable Main Script requirements without pretending that pending Tools
6–11 are implemented.

For current Tools 1–5, the Builder must:

1. Remove the unbounded `shutil.copytree(sample-files, .../eval_workspace/media)`
   behavior from the Tool 4 evaluation helper. An evaluation requiring media
   copies must select an explicit bounded fixture/subset, enforce a preflight
   byte/file budget, and never mutate the selected archive originals. A normal
   run must never copy a selected directory wholesale.
2. Keep per-file processing in place and bounded: only the current file (or an
   explicitly configured small worker limit) may consume scratch space;
   preflight free space for conversion/extraction; clean up owned scratch data
   after completion/failure; and make abandoned owned scratch recoverable on
   restart without touching arbitrary media.
3. Persist enough per-file/per-stage state to resume after interruption and
   skip already completed stages when their inputs/configuration remain valid.
   A stuck or failed file must become a visible retry/review item and must not
   prevent independent files from progressing indefinitely.
4. Avoid retaining the full archive's detailed file results in memory or
   emitting one giant log event containing every discovered path. Keep
   terminal progress and the review portal useful for long folder runs.
5. Retain existing alpha/beta purge behavior **only for test mode**. Before
   processing the real archive, obtain an explicit production-mode policy
   separating durable work from test-data purging; do not silently disable the
   user's current test cleanup or silently purge production work.

For future integration, the Main Script must route a confirmed combination
through Tool 6, Tool 4 singing-row creation, class-only Tools 7–10 as
applicable, final Tool 1 rename, Tool 11 move, and final Tool 4 updates in the
order fixed by the amendment. The class keeps its existing row identity and
the singing part has a distinct row. The original full-length working file
does not remain after a successful Tool 6 split. Do not implement pending tool
logic in the Main Script merely to satisfy this future sequence; route only
through accepted tool services when their finalized plans exist.

Acceptance for this amendment requires hermetic interruption/retry and
scratch-cleanup tests, a bounded evaluation-helper test proving no full
`sample-files` copy, a long-folder test proving bounded memory/log behavior,
and the full existing regression suite. No live Baserow mutation or archive
folder run is required for automated acceptance.
