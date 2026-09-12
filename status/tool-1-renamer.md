# Tool 1 — Renamer Implementation Status

Build plan: `docs/tool-1-renamer-build-plan.md`
Project architecture: `docs/project-implementation-architecture.md`
Implementation issue: #1
Protocol: `docs/implementation-protocol.md`

## Current state

Status: `NOT_STARTED`

Implementation branch / PR: not yet assigned
Last implementation update: not yet started

## Review checkpoint

Last planning/review commit: none
Current implementation HEAD: none
Fundamental-change review pending: no

Relevant commits since last review:
- none

When implementation starts, the builder must update this section after meaningful commits. The planning/review model will independently inspect the diff since the last reviewed SHA, especially for changes affecting tool boundaries, archive behavior, Baserow/state semantics, interfaces, safety/idempotency, provider authority, major framework/dependency choices, or acceptance criteria.

## Milestones

- [x] Requirements gathered
- [x] Build plan finalized
- [x] Project implementation architecture defined
- [x] Review portal architecture defined
- [ ] Implementation started
- [ ] Project package/application-service skeleton implemented
- [ ] Core parser implemented
- [ ] Local registry implemented
- [ ] Structured JSONL logging implemented
- [ ] Human-readable CSV summary implemented
- [ ] Rename planner implemented
- [ ] Dry-run mode implemented
- [ ] Minimal localhost review portal implemented
- [ ] Renamer review/evidence/correction workflow implemented
- [ ] Baserow/reference adapters implemented
- [ ] Safe commit/collision handling implemented
- [ ] Golden/sample tests implemented
- [ ] Sample archive evaluation completed through review portal
- [ ] Open questions resolved
- [ ] Acceptance criteria demonstrated
- [ ] Ready for review
- [ ] Accepted

## Current work

Implementation has not started.

The first implementation uses a shared Python application/service layer with two adapters: CLI and a localhost browser-based review portal. The portal is FastAPI + Jinja2 + HTMX and must call the same Renamer services/registry rather than reimplementing naming logic.

## Tests and evaluation

No implementation tests have been run yet.

The eventual sample evaluation must report at least:

- correct automatic interpretations
- correct provisional interpretations
- correctly unresolved files
- incorrect automatic interpretations
- collision/idempotency behavior
- representative batch performance
- review portal correction/approve/defer behavior

Incorrect automatic interpretation is the most important regression category.

## Known defects / limitations

None yet; implementation has not started.

## Open questions / contradictions

None currently.

When the implementation model finds a specification ambiguity or contradiction, add an entry here instead of editing the build plan.

Use:

```text
### Q-001 — Short title

Status: OPEN
Build-plan section(s): <section numbers/names>
Blocking scope: <what cannot safely proceed>

Problem:
<precise ambiguity or contradiction>

Evidence:
<relevant plan text, test case, filename, API behavior, etc.>

Why this matters:
<risk of guessing>

Possible interpretations:
1. ...
2. ...

Implementation action:
<blocked work and unaffected work that continues>
```

Resolved questions must remain in this file for history with `Status: RESOLVED` and a reference to the decision or revised specification.

## Next milestone

Start implementation from the finalized build plan and `docs/project-implementation-architecture.md` without modifying either. Establish the Python 3.12 + `uv` project/package skeleton and reusable application-service boundary first; then implement the deterministic Renamer core, local registry/logging and dry-run path, followed by the minimal FastAPI/Jinja2/HTMX localhost review portal so real sample results can be reviewed early.

## Progress log

### 2026-09-12 — Planning handoff created

- Finalized Tool 1 build plan exists.
- Project-wide implementation protocol established.
- This status file created as the canonical implementation handoff.
- Implementation model must keep the build plan read-only and record unclear or contradictory requirements here.
- Commit-review checkpoints are required; implementation commits will be reviewed for fundamental changes before acceptance.

### 2026-09-12 — Project implementation architecture finalized

- Core application shape fixed as a reusable local Python 3.12 package/application.
- `uv` selected for Python environment/dependency management.
- Tool logic must be programmatically callable for the future orchestrator; UI and CLI are adapters, not the business-logic boundary.
- Builder startup instructions are in `docs/project-implementation-architecture.md`.

### 2026-09-12 — Local review portal architecture finalized

- Human review is promoted to a first-class project interface instead of being deferred until all tools are complete.
- v1 review UI is a localhost browser application served by the Python project using FastAPI + Jinja2 + HTMX.
- No SwiftUI/Xcode or Node/React toolchain is required for v1.
- Initial Tool 1 review covers proposed names, parsed fields, resolution states, evidence, alternatives, conflicts, corrections, approve/defer actions, errors and progress.
- Automatic files must continue without waiting for human review.
- A native/packaged desktop wrapper can be reconsidered after the review workflow is proven.