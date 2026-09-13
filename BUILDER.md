# Builder Entry Point

This file is the **single starting point for any implementation model** working in this repository.

Do not rely on previous chat history. The repository is the persistent project memory.

## Start / resume command

When the user says:

```text
BUILD TOOL <number>
```

or asks you to continue/resume a tool, first run:

```sh
./scripts/builder-start.sh <number>
```

Example:

```sh
./scripts/builder-start.sh 1
```

Then follow the files and action printed by that command.

If shell execution is unavailable, perform the same procedure manually using the rules below.

## Required reading order

For Tool `<number>`, read:

1. `BUILDER.md` — this entry-point protocol.
2. `status/tool-<number>-*.md` — **current action, review findings, blockers, last reviewed commit, and next milestone**.
3. `docs/tool-<number>-*-build-plan.md` — **authoritative finalized tool specification**.
4. `docs/project-implementation-architecture.md` — project-wide architecture and technology boundaries.
5. `docs/implementation-protocol.md` — handoff, status, commit-review, and contradiction rules.
6. Any assets/documents explicitly referenced by the build plan or status file.
7. Relevant implementation code and tests.

The status file can tell you what work is currently required, but it **cannot redefine the finalized build plan**.

## Status-driven behavior

The tool status determines what you should do next:

### `NOT_STARTED`
Implement the tool from the finalized build plan. Establish the required project/tool structure, tests, and status updates as specified.

### `IN_PROGRESS`
Resume the current milestone from the status file. Do not restart or redesign completed work without a documented reason.

### `BLOCKED_PARTIAL`
Continue unaffected work. Do not guess the blocked archive-policy decision. Record/update the question in the status file and stop only the affected scope.

### `CHANGES_REQUESTED`
The planning/review model has already inspected the implementation. Address **every active review finding** in the status file, including required regression tests and sample re-evaluation. Do not change the finalized build plan to make the implementation fit.

After corrections:

1. run the required tests/evaluation;
2. update the status file with exact results and known limitations;
3. commit and push all changes;
4. record the real reachable implementation HEAD and commits since the last reviewed checkpoint;
5. set status to `READY_FOR_REVIEW` only when all requested findings are addressed.

### `READY_FOR_REVIEW`
Stop implementation work unless the user explicitly asks for another change. Ensure all work is committed/pushed and the status file points to the real current HEAD. Hand the tool back for planning/review.

### `ACCEPTED`
Do not modify the accepted tool unless a new task, bug, or revised build plan explicitly requires it.

## Mandatory completion rule

**Implementation work is not complete until it has been committed and pushed to the repository.**

Every time the builder finishes a requested implementation task, correction pass, milestone, or review handoff that changed repository files, it must complete all of the following before saying it is ready:

1. run the relevant tests/evaluation;
2. update the tool status file with the work performed and exact results;
3. commit **all intended repository changes** with a descriptive commit message;
4. push the commit(s) to the documented implementation branch/remote;
5. verify the pushed commit is reachable;
6. update the status file/review checkpoint so it records the real implementation HEAD and relevant commits;
7. if that status update itself creates another commit, push that commit too and report the final reachable HEAD.

Do **not** say `BUILDER READY`, `READY_FOR_REVIEW`, `done`, `complete`, or equivalent while relevant local changes are uncommitted or unpushed.

If pushing fails, the work is **not** ready for handoff. Report the push/repository problem precisely and do not present a local-only SHA as the review target.

If a task genuinely makes no repository changes, no empty commit is required; state that no repository changes were necessary.

This rule applies equally to initial implementation, review corrections, documentation/status changes, tests, and later maintenance work.

## Non-negotiable rules

- **Never edit a finalized build plan merely because implementation is difficult.**
- If the build plan is unclear, contradictory, impossible as written, or conflicts with another finalized requirement, record a `Q-###` entry in the tool status file.
- Continue unaffected work when possible.
- Do not silently change archive policy, naming semantics, Baserow authority/data ownership, tool boundaries, persistent schemas, safety behavior, orchestrator-facing interfaces, authoritative providers, or acceptance criteria.
- Keep the status file current at meaningful milestones.
- **Always commit and push completed implementation work before handing it back.**
- Commit/push before declaring `READY_FOR_REVIEW`.
- The planning/review model will independently inspect actual commits and diffs; summaries alone are not sufficient.

## Builder completion message

When handing work back, report only the durable repository facts needed for review:

```text
TOOL <number> READY_FOR_REVIEW
HEAD: <full or short reachable SHA>
Status: status/<tool-status-file>.md
Build plan: docs/<tool-build-plan>.md
Tests: <command + result>
Sample evaluation: <summary>
Review findings addressed: <R-IDs or none>
Open questions: <Q-IDs or none>
```

The reported `HEAD` must be the **final pushed/reachable commit**, including any final status-file update commit.

Do not claim acceptance. Only the planning/review step can mark a tool `ACCEPTED`.
