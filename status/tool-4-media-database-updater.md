# Tool 4 — Media Database Updater Implementation Status

Build plan: `docs/tool-4-media-database-updater-build-plan.md`  
Implementation issue: #24  
Project architecture: `docs/project-implementation-architecture.md`  
Project Baserow policy: `docs/baserow-live-data-policy.md`  
Implementation protocol: `docs/implementation-protocol.md`

## Current state

Status: `NOT_STARTED`

Planned implementation branch: `tool-4-implementation`  
Planned implementation PR: not opened yet  
Last planning update: 2026-09-14

## Planning decisions

- Tool 4 is the only application tool allowed to mutate Baserow Media-database state.
- Tool 2 remains the live Media lookup/reconciliation gate for create-vs-update decisions.
- Before every existing-row update, Tool 4 re-reads relevant current row state and refuses to silently overwrite collaborator changes.
- Before every create, Tool 4 performs a fresh Tool 2 existence/candidate review so a collaborator-created row is not duplicated.
- One `media` row represents one logical recording and may contain several online/archive formats; routine archive synchronization updates only relevant fields and preserves unrelated links/statuses/source metadata.
- Semantic metadata merge rule: blank current field + trustworthy incoming value may be filled; equivalent value is a no-op; populated contradiction is preserved and routed to review.
- `Filename` and `media_archive_path` may update when Tool 1 tracking/audit proves the same physical archive file changed path/name.
- A different unproven archive representation must not overwrite an existing `media_archive_path` automatically.
- Baserow `Date` stores the recording date only, and only when a trustworthy complete `YYYY-MM-DD` date exists.
- Partial recording dates are not coerced into Baserow `Date`; Tool 4 preserves them in Notes with a deterministic idempotent marker.
- Tool 4 may remove/replace only its own incomplete-date Notes marker after a later trusted full date is known; unrelated Notes are preserved.
- New-row Notes begin with `Added from archive` exactly once.
- New-row field defaults and timestamps follow the finalized build plan.
- `Media Archive link` is deliberately out of scope/manual until a later dedicated tool/workflow; Tool 4 does not derive or populate it.
- `media_archive_path` stores the full actual archive filesystem path.
- All select writes must use valid current Baserow options.
- Only legitimate missing Country and Place/location select options may be added automatically; Category/Language/status/Tag taxonomy is not invented.
- Schedule-only/provisional Tool 3 evidence is not automatically promoted into authoritative Media fields.
- Tool 1 should invoke Tool 4 after a successful committed rename/current metadata change; Tool 1 dry-run or mere approval does not write Baserow.
- Filesystem rename success is not rolled back because Baserow synchronization later fails; instead a durable pending/retry state is recorded.
- Tool 4 mutations are minimal-field, idempotent, auditable, and safe under uncertain network outcomes.
- Builder/CI tests must never bulk-write production Baserow; representative sample evaluation is a write preview unless a deliberate non-production test table is configured.

## Active questions / contradictions

None requiring user input at planning handoff.

If implementation discovers an actual contradiction, record it here as `Q-###` according to `docs/implementation-protocol.md`; do not edit the finalized build plan.

## Builder implementation requirements

When Tool 4 reaches its implementation turn, start with:

```sh
./scripts/builder-start.sh 4
```

The Builder must:

1. implement on `tool-4-implementation`, not protected `main`;
2. preserve accepted Tool 1/Tool 2 behavior and the Tool 3 contract current at implementation start;
3. build a reusable Tool 4 application-service/write-adapter boundary rather than placing Baserow mutations in CLI/UI code;
4. use Tool 2 for live create/update reconciliation and implement mandatory pre-write revalidation;
5. implement minimal field-specific create/update merge behavior and duplicate/conflict routing;
6. implement live schema/select-option handling, including tightly scoped country/location option additions;
7. implement new-row defaults, full-date-only `Date`, and partial-date Notes behavior;
8. integrate Tool 1 committed rename/current-state synchronization through durable pending/outbox state;
9. add CLI and localhost portal integration against the same service layer;
10. add required regression/race/idempotency tests from the build plan;
11. run full project tests/build/helper validations;
12. run and document the safe 260-file Tool 4 write-preview evaluation without bulk production mutations;
13. commit/push all work, open/update the Tool 4 PR, wait for required GitHub CI success, and set this status to `READY_FOR_REVIEW`.

## Review baseline

Orchestrator review will independently inspect:

- actual PR diff and reachable commits;
- Tool 2 live-current integration and no duplicate-create race;
- pre-update collaborator-race protection;
- minimal PATCH/data-preservation behavior;
- exact new-row defaults and partial-date handling;
- select taxonomy discipline and country/location option creation;
- multiple-format preservation and archive-path continuity safety;
- Tool 1 post-commit integration and durable pending sync;
- idempotency under retries/timeouts;
- CLI/portal service boundaries;
- production-write safety;
- full tests, representative evaluation, package build, and GitHub CI.

## Next milestone

Planning is complete. Tool 4 waits for its turn in the project sequence; when implementation starts, Builder follows the finalized plan and returns `READY_FOR_REVIEW` with a pushed PR and green required CI.