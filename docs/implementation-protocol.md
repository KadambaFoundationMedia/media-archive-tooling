# Implementation and Handoff Protocol

This document defines how finalized build plans are implemented and how the implementation model communicates progress, questions, contradictions, test findings, and potentially fundamental implementation changes back to the planning/review model and the user.

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

## 5. Progress updates

At meaningful milestones, the implementation model should update the status file with:

1. what was completed
2. files/components changed
3. commits / PR involved
4. tests run and their results
5. observed behavior, especially incorrect automatic behavior and correctly unresolved cases
6. any new questions/contradictions
7. whether any commit may contain a fundamental change
8. the next milestone

Progress updates should be concise but sufficiently specific that a different model can resume the work without relying on chat memory.

## 6. GitHub issues and pull requests

A GitHub implementation issue may be used for discussion, notifications, review comments, and links to PRs/commits, but the per-tool status file is the canonical progress/handoff record.

If an issue is used, milestone comments should point back to the current status file rather than duplicating an independent source of truth.

Pull requests should reference both:

- the finalized build plan
- the tool status file

## 7. Planning/review workflow

When asked to review implementation progress, the planning/review model should inspect, in this order:

1. the finalized build plan
2. the per-tool status file
3. open questions/contradictions
4. commits/diff since the last planning-review checkpoint
5. relevant pull request and review discussion
6. test and sample-evaluation output

The planning/review model should compare implementation behavior against the build plan and specifically look for accidental fundamental changes.

If no archive-policy decision is needed, the planning/review model can give implementation feedback and update the review checkpoint without changing the build plan.

If a genuine policy decision is needed, the planning/review model should ask the user only for that decision, then update the plan and/or project documentation as appropriate and record the resolution.

## 8. Status lifecycle

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

A tool is only `ACCEPTED` after its build-plan acceptance criteria have been demonstrated, implementation commits have been reviewed through the current accepted HEAD, and relevant test/sample results have been checked.

## 9. Project-wide rule

The GitHub repository is the persistent project memory for implementation. Chat messages can coordinate work, but finalized specifications, implementation status, commit-review state, unresolved questions, and review outcomes must be recoverable from the repository without depending on a previous AI conversation.