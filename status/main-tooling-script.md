# Main Tooling Script — Implementation Status

Build plan: `docs/main-tooling-script-build-plan.md`
Implementation protocol: `docs/implementation-protocol.md`
Project architecture: `docs/project-implementation-architecture.md`

## Current state

Status: `NOT_STARTED`

Implementation branch: `main-tooling-script-implementation`
Implementation PR: not created
Last planning update: 2026-09-19

## Current action

Implement Phase A of the finalized Main Tooling Script plan:

```text
Tool 1 initial interpretation
→ Tool 2 live read-only Media review
→ Tool 3 verified travel-schedule review
→ Tool 1 final proposal and immediate live commit when allowed
→ Tool 4 preview/write for the final state
```

The Main Tooling Script is unnumbered. It is not Tool 12.

Use branch `main-tooling-script-implementation` and keep one implementation PR open to `main`.

## Confirmed requirements

- Runs locally from the command line.
- Accepts a single file, multiple files, folders recursively, and mixed targets.
- Works on original files in live mode.
- Live mode is default and has no confirmation prompts.
- `--dry-run` performs no filesystem or Baserow mutation.
- Default workflow selection is `all`.
- Phase A makes `all` run all currently available stages (Tools 1–4) while honestly reporting that Processing/Tools 5–11 are pending.
- Tool names are separate for clarity only; execution does not pause between them.
- Terminal shows concise progress and per-tool results.
- Detailed evidence appends to one canonical log file.
- Review portal active queue contains only items genuinely requiring evaluation.
- Initial implementation must be usable before Tools 5–11 are built.
- Future Renamer workflow adds Tool 11; future Processing workflow adds Tools 4–11 as specified in the plan.

## Planner-authored baseline that must be preserved

The current `main` branch contains accepted Tools 1–4 plus planner-authored practical corrections and regressions. In particular preserve:

- strict punctuation-safe final naming;
- dotted scripture recognition and readable Baserow scripture titles;
- Czech `Duben` month/country context;
- Tool 2 country-only candidate filtering;
- Tool 4 read-only Tool 2 revalidation (`auto_enrich=False`);
- existing equivalent Country option reuse;
- original filename/path provenance in Notes;
- final-only Tool 4 invocation and durable pending synchronization;
- Baserow row `3232` practical evidence is production history, not a test fixture to rewrite.

Read `docs/planner-builder-coordination.md` before modifying overlapping components.

## Open questions / contradictions

None.

If implementation reveals a genuine ambiguity or conflict, add a `Q-###` entry here and continue unaffected work. Do not edit the finalized build plan.

## Review findings

None. Implementation has not started.

## Required verification before handoff

- Main-script focused automated tests.
- Entire project pytest suite.
- Locked package build.
- Shell syntax checks for maintained shell helpers.
- Required Phase A practical dry-run evaluations.
- Required GitHub `Python 3.12 tests` check.
- All implementation and status changes committed and pushed.
- PR created from `main-tooling-script-implementation` to `main`.

## Commit-review checkpoint

Last planning-reviewed implementation commit: none
Current implementation HEAD: none

## Next milestone

Builder implements Phase A and hands back `READY_FOR_REVIEW` with pushed commits, PR, exact test results, and practical evaluation evidence.
