# Implementation Status Files

This directory contains the durable implementation handoff for each finalized tool.

The rules are defined in `docs/implementation-protocol.md`.

## Convention

Use one file per tool:

```text
status/tool-1-renamer.md
status/tool-2-media-database-reviewer.md
status/tool-3-travel-schedule-reviewer.md
...
```

The implementation model updates the relevant status file throughout the build. Finalized build plans remain read-only to the implementation model.

If a build-plan requirement is unclear, contradictory, impossible as written, or conflicts with another finalized requirement, record it in the tool status file under `Open questions / contradictions` and do not silently change the specification.

Each status file also carries a commit-review checkpoint with the current implementation HEAD and the last commit reviewed by the planning/review model. The planning/review model should inspect the actual commits/diff between those points, particularly for fundamental changes that could affect archive behavior, tool boundaries, shared/local data semantics, interfaces, safety/idempotency, providers, major architecture choices, or acceptance criteria.

**A tool is not ready for handoff while relevant work is uncommitted or unpushed.** The builder must commit and push completed work, verify the review target is reachable, and record the real final implementation HEAD in the status file before declaring `READY_FOR_REVIEW` or equivalent. If the final status update itself creates another commit, that commit must also be pushed and the final reachable HEAD reported.

The planning/review model should inspect the status file first when reviewing build progress, then verify the relevant implementation commits rather than relying only on the builder's summary.