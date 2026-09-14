# Tool 3 — Travel Schedule Reviewer Implementation Status

Build plan: `docs/tool-3-travel-schedule-reviewer-build-plan.md`  
Implementation issue: #22  
Project architecture: `docs/project-implementation-architecture.md`  
Project Baserow policy: `docs/baserow-live-data-policy.md`  
Implementation protocol: `docs/implementation-protocol.md`

## Current state

Status: `NOT_STARTED`

Planned implementation branch: `tool-3-implementation`  
Planned implementation PR: not opened yet  
Last planning update: 2026-09-14

## Planning decisions

- Tool 3 is a read-only Travel Schedule Reviewer used primarily by Tool 1 and potentially later tools.
- It consumes Tool 1 structured WHEN/WHERE/path evidence and Tool 2 Media context; it does not reparse filenames or reimplement Tool 2 Media reconciliation.
- The Baserow `travel_schedule` table is user-declared static/immutable and may be bootstrapped once into a verified local reference dataset for reuse across batches, sessions, and offline runs.
- Mutable Media rows remain under Tool 2 live-current rules; the static exception applies only to `travel_schedule`.
- Travel schedule records planned travel, not guaranteed actual presence. Schedule-only evidence is supporting/provisional and never absolute proof.
- Filename/path evidence and confirmed Media evidence are never silently overwritten by travel-schedule evidence.
- A confirmed Tool 2 Media association is authoritative for that logical recording. If explicit local filename/path evidence materially conflicts with confirmed Media evidence, Tool 3 does not adjudicate the high-authority conflict automatically.
- Schedule absence is not proof of absence.
- Known location + missing date can yield possible dates; known date + missing location can yield possible locations.
- When both date and location are missing, Tool 3 must not make an unconstrained guess; later tools may add evidence and Tool 3 can be rerun.
- A safe unique schedule-derived WHEN/WHERE may automatically enrich the Tool 1 proposal, but the stored Tool 1 field state must remain `PROVISIONAL`.
- Multiple schedule candidates, schedule conflicts, no-support, insufficient-evidence, or unavailable-reference states do not auto-change Tool 1 selected fields.
- Tool 1 remains responsible for canonical filename rendering and filesystem renames.

## Active questions / contradictions

None requiring user input at planning handoff.

If implementation discovers an actual contradiction, record it here as `Q-###` according to `docs/implementation-protocol.md`; do not edit the finalized build plan.

## Builder implementation requirements

Start with:

```sh
./scripts/builder-start.sh 3
```

The Builder must:

1. implement on `tool-3-implementation`, not protected `main`;
2. reuse the accepted Tool 2/shared Baserow provider for complete schedule bootstrap;
3. preserve accepted Tool 1 and Tool 2 behavior and regression tests;
4. implement verified static-reference bootstrap/load/checksum/offline semantics;
5. implement the decision/candidate/enrichment contracts in the finalized plan;
6. extend the Renamer enrichment boundary minimally so Tool 3 values remain provisional;
7. add CLI, portal, registry/audit integration and required tests;
8. run the full project test/build/helper checks;
9. run and document the safe 260-file Tool 1 → Tool 2 → Tool 3 representative evaluation;
10. commit and push all work, open/update the Tool 3 PR, wait for required GitHub CI success, then set this status to `READY_FOR_REVIEW`.

## Review baseline

Orchestrator review will independently inspect:

- actual PR diff and reachable commits;
- static-reference integrity and zero-network normal reuse;
- schedule matching/range/location semantics;
- no overstatement of schedule authority;
- Tool 2 current Media boundary;
- Tool 1 provisional enrichment state preservation;
- candidate isolation / no first-match-wins;
- registry/audit and portal/CLI service boundaries;
- full tests, 260-file evaluation, and GitHub CI.

## Next milestone

Builder implements Tool 3 from the finalized plan and returns `READY_FOR_REVIEW` with a pushed PR and green required CI.
