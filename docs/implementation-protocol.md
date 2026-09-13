# Implementation and Handoff Protocol

This document defines how finalized build plans are implemented and how the implementation model communicates progress, questions, contradictions, test findings, and potentially fundamental implementation changes back to the planning/review model and the user.

## 0. Builder entry point

The builder always starts from repository root `BUILDER.md`.

The standard start/resume command is:

```sh
./scripts/builder-start.sh <tool-number>
```

The user may simply tell the implementation model:

```text
BUILD TOOL <number>
```

The implementation model must then run the helper command, or manually follow the same procedure in `BUILDER.md` if shell execution is unavailable. The helper reads the per-tool status and tells the builder whether to start, resume, address review changes, stop for review, or leave an accepted tool untouched.

This entry-point convention is deliberately status-driven so a new implementation-model session can resume correctly without access to previous chat history.

## 1. Build plans are specifications

A finalized build-plan Markdown file under `docs/` is the authoritative specification for that tool.

The implementation model **must not edit, rewrite, reinterpret, or silently relax a finalized build plan**.

The implementation model may make ordinary technical implementation choices that are not specified by the plan when those choices do not change archive policy, tool behavior, data semantics, safety requirements, interfaces promised to other tools, acceptance criteria, or project-wide architectural boundaries.

If a requirement is unclear, contradictory, impossible to implement as written, or conflicts with another finalized requirement, the implementation model must not resolve the archive-policy question by changing the plan. It must record the problem in the tool's status file and continue with unaffected work where possible.

Only the planning/review model, working with the user, should revise a finalized build plan. When a plan is revised, the reason should be explicit and the corresponding status-file question should be marked resolved.

## 2. Per-tool status files are the implementation source of truth

Each finalized tool gets a status file under:

```text
status/<tool-name>.md
```

For example:

```text
status/tool-1-renamer.md
```

The implementation model should update this file throughout the build. The status file is the durable handoff between models and sessions.

The status file should always contain:

- current implementation state
- branch / pull request / relevant commits when available
- commit-review checkpoint
- completed milestones
- current work
- tests run and results
- sample/evaluation results
- known defects or limitations
- open questions or contradictions
- next planned milestone
- a chronological progress log

The status file is **not** a replacement for the build plan and must not redefine requirements.

## 3. Required format for unclear or contradictory requirements

When the implementation model finds something that does not add up, add an entry under `Open questions / contradictions` using this structure:

```text
### Q-001 — Short title

Status: OPEN
Build-plan section(s): <section numbers/names>
Blocking scope: <what cannot safely proceed>

Problem:
<precise description of the ambiguity or contradiction>

Evidence:
<tests, filenames, code behavior, API behavior, or conflicting plan text>

Why this matters:
<what could be implemented incorrectly if guessed>

Possible interpretations:
1. ...
2. ...

Implementation action:
<what work has been paused and what unaffected work continues>
```

The builder must not choose an archive-policy interpretation merely to make the question disappear.

When resolved, keep the entry for history and change it to:

```text
Status: RESOLVED
Resolution: <link/reference to revised plan or explicit decision>
```

## 4. Commit review checkpoints

Implementation commits are also part of the handoff. The planning/review model should inspect implementation changes, not only status summaries.

Each tool status file must maintain:

```text
## Review checkpoint

Last planning/review commit: <SHA or none>
Current implementation HEAD: <SHA or none>
Fundamental-change review pending: <yes/no>
Relevant commits since last review:
- <SHA> — <summary>
```

After making commits, the implementation model should update the current HEAD and list the relevant commits. It should mark `Fundamental-change review pending: yes` whenever a commit may affect a fundamental project decision.

The planning/review model must also independently inspect the commits/diff since the last reviewed SHA at meaningful review points; it must not rely solely on the implementation model's classification.

A change is potentially **fundamental** when it affects or materially reshapes any of the following:

- archive-policy behavior or naming semantics
- tool responsibilities or boundaries between tools
- data ownership or shared-vs-local state
- Baserow schema assumptions or write behavior
- persistent storage/schema or processing identity
- public/internal interfaces that other tools or the orchestrator will rely on
- safety behavior, overwrite/collision behavior, or idempotency
- external service/provider dependencies or authoritative data sources
- platform/runtime architecture in a way that reduces agreed portability
- processing architecture or concurrency in a way that changes observable behavior
- acceptance criteria or test meaning
- a major dependency/framework choice that would constrain later tools or UI/orchestration

Routine refactoring, test additions, small implementation details, and equivalent library substitutions generally do not require user involvement unless they cause one of the effects above.

If the planning/review model finds that a commit has introduced a fundamental change not authorized by the finalized build plan, it should request correction or raise the specific policy decision with the user. The build plan itself remains unchanged until that decision is made.

## 5. Protected `main`, implementation branches, pull requests, and CI

The repository default branch `main` is protected by the active GitHub ruleset `Protect main`.

Normal implementation work must **not** be performed directly on `main`. Each tool uses a durable implementation branch, with the standard name:

```text
tool-<number>-implementation
```

Examples:

```text
tool-2-implementation
tool-3-implementation
```

`./scripts/builder-start.sh <number>` automatically creates or resumes this branch when implementation starts from `main`, so the user's stable `BUILD TOOL <number>` command does not change.

All implementation, correction, test, walkthrough, and status-file commits for a tool remain on that tool branch until the tool is accepted. An open pull request from the tool branch to `main` is the review surface.

The PR must reference the finalized build plan and status file. Planning/review findings should be recorded in the status file on the same PR branch so the builder sees `CHANGES_REQUESTED` after synchronizing that branch; do not merge partial implementation merely to communicate review findings.

The GitHub Actions workflow `.github/workflows/ci.yml` is a required merge gate. The required status check is:

```text
Python 3.12 tests
```

The active ruleset requires the PR branch to be up to date with `main` and this check to pass before merge. The workflow runs the locked Python 3.12 environment, the complete pytest suite, and package build verification without depending on private `.env` credentials.

A known failing required CI check means the implementation is not ready. A PR may be handed to planning/review while CI is still running, but it cannot be accepted/merged until the required check succeeds.

The planning/review model must inspect the actual PR diff and required CI result in addition to builder-reported local tests. Local tests remain useful because they catch failures before push; GitHub CI is the independent merge gate.

For auditability, prefer a normal merge commit when accepting a tool so reviewed implementation SHAs remain reachable in `main` history. Squash/rebase merges should be used only deliberately, with the final merged SHA recorded in the status file.

After acceptance, merge the accepted PR into `main`. The tool branch may then be deleted. Any future regression or new requirement should use a new branch/PR rather than reopening direct writes to `main`.

## 6. Mandatory commit-and-push completion rule

**Implementation work is not complete until all intended repository changes are committed and pushed to the tool's implementation branch.**

Whenever the implementation model finishes a requested implementation task, correction pass, milestone, review handoff, test addition, documentation/status update, or maintenance change that modifies repository files, it must do all of the following before reporting completion:

1. run the relevant local tests/evaluation;
2. update the tool status file with the completed work and exact results;
3. commit all intended repository changes with a descriptive commit message;
4. push the commit(s) to the documented implementation branch/remote;
5. verify the pushed commit is reachable;
6. update the status-file review checkpoint with the real implementation HEAD and relevant commits;
7. if that checkpoint/status update creates an additional commit, push that commit too and report the final reachable HEAD;
8. ensure an implementation PR to `main` exists or is updated for review.

The implementation model must **not** say `BUILDER READY`, `READY_FOR_REVIEW`, `done`, `complete`, or equivalent while relevant work exists only in the local working tree or in unpushed commits.

A local-only SHA is not a valid review target.

If the push fails, the handoff is not complete. The builder should report the repository/push failure precisely and keep the tool out of `READY_FOR_REVIEW` until the durable repository state is available.

If the required GitHub CI check has completed and failed, the builder must correct the failure before final handoff. If CI is still running, record that fact accurately rather than claiming it passed.

No empty commit is required when a task genuinely produces no repository changes; the builder should explicitly say that no repository change was necessary.

This requirement is project-wide and applies even when the code/tests themselves are finished. The repository and PR are the durable implementation record.

## 7. Progress updates

At meaningful milestones, the implementation model should update the status file with:

1. what was completed
2. files/components changed
3. commits / PR involved
4. local tests run and their results
5. GitHub CI status when available
6. observed behavior, especially incorrect automatic behavior and correctly unresolved cases
7. any new questions/contradictions
8. whether any commit may contain a fundamental change
9. the next milestone

Progress updates should be concise but sufficiently specific that a different model can resume the work without relying on chat memory.

## 8. GitHub issues and pull requests

A GitHub implementation issue may be used for discussion, notifications, review comments, and links to PRs/commits, but the per-tool status file is the canonical progress/handoff record.

If an issue is used, milestone comments should point back to the current status file rather than duplicating an independent source of truth.

Every implementation pull request should reference both:

- the finalized build plan
- the tool status file

The PR remains open across correction/re-review cycles. `CHANGES_REQUESTED` is handled by new commits on the same tool branch unless there is a specific reason to replace the branch/PR.

## 9. Planning/review workflow

When asked to review implementation progress, the planning/review model should inspect, in this order:

1. the finalized build plan
2. the per-tool status file on the implementation branch
3. open questions/contradictions
4. commits/diff since the last planning-review checkpoint
5. the implementation pull request and review discussion
6. local test/sample-evaluation evidence
7. the required GitHub CI result for the PR head

The planning/review model should compare implementation behavior against the build plan and specifically look for accidental fundamental changes.

If no archive-policy decision is needed, the planning/review model can give implementation feedback and update the review checkpoint/status on the same PR branch without changing the build plan.

If a genuine policy decision is needed, the planning/review model should ask the user only for that decision, then update the plan and/or project documentation as appropriate and record the resolution.

The tool must not be merged merely because local tests pass. Acceptance requires review of the actual implementation commits through the accepted PR head and a successful required CI check.

## 10. Status lifecycle

Use these high-level states:

```text
NOT_STARTED
IN_PROGRESS
BLOCKED_PARTIAL
READY_FOR_REVIEW
CHANGES_REQUESTED
ACCEPTED
```

`BLOCKED_PARTIAL` means one part of the tool is blocked by a specification question but unaffected work should continue.

A tool is only `ACCEPTED` after its build-plan acceptance criteria have been demonstrated, implementation commits have been reviewed through the current accepted PR head, relevant test/sample results have been checked, and the required GitHub CI status is successful.

`READY_FOR_REVIEW` is valid only when the implementation and status evidence have been committed and pushed, the recorded review target is reachable from the tool branch, and the implementation PR exists. A still-running CI job should be recorded as pending; a failed required check is not review-ready.

After final acceptance, merge the accepted PR to protected `main` and ensure the merged main-branch status still records `ACCEPTED`.

## 11. Project-wide rule

The GitHub repository is the persistent project memory for implementation. Chat messages can coordinate work, but finalized specifications, implementation status, commit-review state, unresolved questions, CI evidence, PR review outcomes, and acceptance must be recoverable from the repository without depending on a previous AI conversation.
