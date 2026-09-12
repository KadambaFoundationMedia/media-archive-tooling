# Implementation and Handoff Protocol

This document defines how finalized build plans are implemented and how the implementation model communicates progress, questions, contradictions, and test findings back to the planning/review model and the user.

## 1. Build plans are specifications

A finalized build-plan Markdown file under `docs/` is the authoritative specification for that tool.

The implementation model **must not edit, rewrite, reinterpret, or silently relax a finalized build plan**.

The implementation model may make ordinary technical implementation choices that are not specified by the plan when those choices do not change archive policy, tool behavior, data semantics, safety requirements, interfaces promised to other tools, or acceptance criteria.

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

## 4. Progress updates

At meaningful milestones, the implementation model should update the status file with:

1. what was completed
2. files/components changed
3. tests run and their results
4. observed behavior, especially incorrect automatic behavior and correctly unresolved cases
5. any new questions/contradictions
6. the next milestone

Progress updates should be concise but sufficiently specific that a different model can resume the work without relying on chat memory.

## 5. GitHub issues and pull requests

A GitHub implementation issue may be used for discussion, notifications, review comments, and links to PRs/commits, but the per-tool status file is the canonical progress/handoff record.

If an issue is used, milestone comments should point back to the current status file rather than duplicating an independent source of truth.

Pull requests should reference both:

- the finalized build plan
- the tool status file

## 6. Planning/review workflow

When asked to review implementation progress, the planning/review model should inspect, in this order:

1. the finalized build plan
2. the per-tool status file
3. open questions/contradictions
4. relevant commits / pull request
5. test and sample-evaluation output

If no archive-policy decision is needed, the planning/review model can give implementation feedback without changing the build plan.

If a genuine policy decision is needed, the planning/review model should ask the user only for that decision, then update the plan and/or project documentation as appropriate and record the resolution.

## 7. Status lifecycle

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

A tool is only `ACCEPTED` after its build-plan acceptance criteria have been demonstrated and reviewed.

## 8. Project-wide rule

The GitHub repository is the persistent project memory for implementation. Chat messages can coordinate work, but finalized specifications, implementation status, unresolved questions, and review outcomes must be recoverable from the repository without depending on a previous AI conversation.