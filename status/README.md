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

The planning/review model should inspect the status file first when reviewing build progress.