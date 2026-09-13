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
./scripts/builder-start.sh 2
```

The helper synchronizes the local checkout with GitHub before it reads the tool status or build plan. It fetches the remote, safely fast-forwards when possible, and refuses to continue when local work is dirty, unpushed, or divergent. This prevents the builder from missing new review findings or planning/status commits.

For implementation states, the helper also enforces the protected-main workflow. When started on `main`, it automatically creates or resumes the standard tool branch:

```text
tool-<number>-implementation
```

and pushes/tracks that branch before implementation begins. The user therefore keeps using the same `BUILD TOOL <number>` command even though implementation no longer happens directly on `main`.

If the helper updates or switches the repository, it restarts itself and reads the newly current files.

If shell execution is unavailable, perform the same procedure manually using the sync and branch rules below before reading any project files.

## Mandatory start-of-work repository sync

**Never begin implementation from whatever happens to be in the local checkout. GitHub is the durable project state.**

Before reading the status file or deciding that no work is required, the builder must:

1. verify it is in the intended Git repository and branch;
2. run `git fetch --prune origin`;
3. verify the working tree is clean;
4. compare local HEAD with the branch upstream;
5. fast-forward to the upstream when the local branch is merely behind;
6. refuse to continue when the local branch contains unpushed commits or has diverged until that state is deliberately reconciled;
7. when working on a feature branch, verify it also contains the latest default-branch planning/specification commits;
8. only then read `BUILDER.md`, the tool status, build plan, protocol, code, and tests.

Do not silently stash, reset, discard, force-push, or auto-merge divergent implementation work just to make synchronization succeed.

A stale local `READY_FOR_REVIEW` or `ACCEPTED` state must never override a newer remote state on the active tool branch.

## Protected-main implementation rule

`main` is protected by the repository ruleset `Protect main`.

Normal implementation work must not be committed directly to `main`.

For a tool in `NOT_STARTED`, `IN_PROGRESS`, `BLOCKED_PARTIAL`, or `CHANGES_REQUESTED`, work on the tool implementation branch, normally:

```text
tool-<number>-implementation
```

The helper automatically creates/resumes that branch when invoked from `main`.

All implementation, correction, tests, walkthrough, and tool-status changes stay on that branch until acceptance. Keep one pull request from the tool branch to `main` open across review/correction cycles.

The PR must reference:

- the finalized build plan
- the tool status file

Do not merge a partial implementation merely to communicate review findings. Planning/review writes `CHANGES_REQUESTED` and R-### findings to the same PR branch; the builder then synchronizes that branch and continues there.

## Required reading order

For Tool `<number>`, after synchronization and branch selection read:

1. `BUILDER.md` — this entry-point protocol.
2. `status/tool-<number>-*.md` — **current action, review findings, blockers, last reviewed commit, and next milestone**.
3. `docs/tool-<number>-*-build-plan.md` — **authoritative finalized tool specification**.
4. `docs/project-implementation-architecture.md` — project-wide architecture and technology boundaries.
5. `docs/implementation-protocol.md` — handoff, branch/PR/CI, status, commit-review, and contradiction rules.
6. Any assets/documents explicitly referenced by the build plan or status file.
7. Relevant implementation code and tests.

The status file can tell you what work is currently required, but it **cannot redefine the finalized build plan**.

## Status-driven behavior

The tool status determines what you should do next:

### `NOT_STARTED`
Implement the tool from the finalized build plan on the tool implementation branch. Establish the required project/tool structure, tests, and status updates as specified.

### `IN_PROGRESS`
Resume the current milestone from the status file. Do not restart or redesign completed work without a documented reason.

### `BLOCKED_PARTIAL`
Continue unaffected work. Do not guess the blocked archive-policy decision. Record/update the question in the status file and stop only the affected scope.

### `CHANGES_REQUESTED`
The planning/review model has already inspected the implementation PR. Address **every active review finding** in the status file, including required regression tests and sample re-evaluation. Do not change the finalized build plan to make the implementation fit.

After corrections:

1. run the required local tests/evaluation;
2. update the status file with exact results and known limitations;
3. commit and push all changes to the same tool branch;
4. record the real reachable implementation HEAD and commits since the last reviewed checkpoint;
5. ensure the existing implementation PR is updated;
6. set status to `READY_FOR_REVIEW` only when all requested findings are addressed and no known required CI failure remains.

### `READY_FOR_REVIEW`
Stop implementation work unless the user explicitly asks for another change. Ensure all work is committed/pushed, the status file points to the real current branch HEAD, and the implementation PR exists. Hand the tool back for planning/review.

### `ACCEPTED`
Do not modify the accepted tool unless a new task, bug, or revised build plan explicitly requires it. Accepted PRs are merged to protected `main`.

## GitHub CI gate

The required GitHub Actions check is:

```text
Python 3.12 tests
```

It is defined in `.github/workflows/ci.yml` and is required by the `Protect main` ruleset before the PR can merge.

The builder still runs local tests before pushing. GitHub CI is an independent merge gate and runs:

- locked Python 3.12 dependency sync
- the complete pytest suite
- package build verification

The CI workflow must not require private `.env` credentials; external services are mocked/faked in automated tests.

If the required CI check has completed and failed, the implementation is not ready. Fix it before handoff. If CI is still running when all local work is complete, record it as pending; do not claim it passed.

Only planning/review can accept the tool, and acceptance/merge requires successful required CI.

## Mandatory completion rule

**Implementation work is not complete until it has been committed and pushed to the tool implementation branch and a review PR exists.**

Every time the builder finishes a requested implementation task, correction pass, milestone, or review handoff that changed repository files, it must complete all of the following before saying it is ready:

1. run the relevant local tests/evaluation;
2. update the tool status file with the work performed and exact results;
3. commit **all intended repository changes** with a descriptive commit message;
4. push the commit(s) to the documented tool branch/remote;
5. verify the pushed commit is reachable;
6. update the status file/review checkpoint so it records the real implementation HEAD and relevant commits;
7. if that status update itself creates another commit, push that commit too and report the final reachable HEAD;
8. create or update the pull request from the tool branch to `main`;
9. record the PR in the status file.

Do **not** say `BUILDER READY`, `READY_FOR_REVIEW`, `done`, `complete`, or equivalent while relevant local changes are uncommitted/unpushed or the review PR does not exist.

If pushing fails, the work is **not** ready for handoff. Report the push/repository problem precisely and do not present a local-only SHA as the review target.

If a task genuinely makes no repository changes, no empty commit is required; state that no repository changes were necessary.

This rule applies equally to initial implementation, review corrections, documentation/status changes, tests, and later maintenance work.

## Non-negotiable rules

- **Synchronize with the remote repository before every implementation/resume attempt.**
- **Do not implement directly on protected `main`.**
- **Use the tool implementation branch and PR for implementation/review cycles.**
- **Never edit a finalized build plan merely because implementation is difficult.**
- If the build plan is unclear, contradictory, impossible as written, or conflicts with another finalized requirement, record a `Q-###` entry in the tool status file.
- Continue unaffected work when possible.
- Do not silently change archive policy, naming semantics, Baserow authority/data ownership, tool boundaries, persistent schemas, safety behavior, orchestrator-facing interfaces, authoritative providers, or acceptance criteria.
- Keep the status file current at meaningful milestones.
- **Always commit and push completed implementation work before handing it back.**
- **Keep an implementation PR open to `main`.**
- A known failed required CI check blocks handoff/merge.
- The planning/review model will independently inspect the actual PR, commits, diffs, and CI; summaries alone are not sufficient.

## Builder completion message

When handing work back, report only the durable repository facts needed for review:

```text
TOOL <number> READY_FOR_REVIEW
BRANCH: tool-<number>-implementation
PR: <number or URL>
HEAD: <full or short reachable SHA>
Status: status/<tool-status-file>.md
Build plan: docs/<tool-build-plan>.md
Local tests: <command + result>
GitHub CI: <passed / pending / not yet observed>
Sample evaluation: <summary>
Review findings addressed: <R-IDs or none>
Open questions: <Q-IDs or none>
```

The reported `HEAD` must be the **final pushed/reachable branch commit**, including any final status-file update commit.

Do not claim acceptance. Only the planning/review step can mark a tool `ACCEPTED` and merge the accepted PR to `main`.
