# Tool 2 — Media Database Reviewer Implementation Status

Build plan: `docs/tool-2-media-database-reviewer-build-plan.md`  
Authoritative live-data amendment: `docs/tool-2-media-database-reviewer-live-data-amendment.md`  
Project-wide Baserow policy: `docs/baserow-live-data-policy.md`  
Project architecture: `docs/project-implementation-architecture.md`  
Implementation issue: #2  
Protocol: `docs/implementation-protocol.md`  
Planner / Builder coordination: `docs/planner-builder-coordination.md`

## Current state

Status: `NOT_STARTED`

Planned implementation branch: `tool-2-implementation`  
Implementation PR: none yet  
Last implementation update: none  
Last planning/review update: 2026-09-14

## Review checkpoint

Last planning/review commit: none  
Current implementation HEAD: none  
Fundamental-change review pending: no — the live-data policy was finalized before implementation started

Relevant commits since last review:
- none — planning/specification commits are not Tool 2 implementation HEADs

## Planner-authored maintenance / coordination

Before Tool 2 implementation begins, the Builder must synchronize to current `main` and treat the post-acceptance Tool 1 maintenance baseline as existing shared infrastructure rather than reconstructing Tool 1 from its original acceptance snapshot.

Current shared baseline checkpoint: Tool 1 maintenance through PR #16 / merge commit `8fb6b247d2fce2b64d8771210771970dcc8d53dc`.

Important changes already present on `main` that Tool 2 must preserve and integrate with:

- fresh per-target Tool 1 review snapshots and stable review counts;
- live Baserow structured-field and redirect handling corrections;
- scripture grammar/range validation corrections for BG, SB, and CC;
- review-portal usability improvements;
- batch approve/defer/commit workflows;
- reusable safe `RenameCommitService` for reviewed rename proposals.

These were planner-authored post-acceptance maintenance changes, not Tool 2 implementation work. Because Tool 2 extends the existing portal and integrates with Renamer/Baserow behavior, the Builder should inspect the current relevant code and the referenced maintenance diff when touching overlapping components. Do not revert or duplicate these changes based on an older Tool 1 snapshot.

### Live Baserow coordination — mandatory before implementation

The Media database is continuously updated by external collaborators. Tool 2 must **not** implement the original plan's stale-cache/session-snapshot fallback semantics.

Read both of these before touching the Baserow provider design:

- `docs/baserow-live-data-policy.md`
- `docs/tool-2-media-database-reviewer-live-data-amendment.md`

The amendment is authoritative wherever it conflicts with the older build-plan wording.

Key consequences:

- every current Tool 2 database decision uses a live Baserow read;
- persisted Baserow values are audit/history only, never the current operational source;
- no stale cache may produce a confirmed match, confirmed enrichment, no-match/new-item result, or `baserow_check_complete=true`;
- human candidate/new-item confirmation revalidates live state before finalizing;
- Tool 4 must later re-read immediately before update and re-check existence immediately before create;
- the existing Tool 1 fallback reference provider must not be reused as Tool 2's authoritative current-database provider;
- efficient targeted/filter queries are preferred over a long-lived full-table snapshot.

## Finalized scope summary

Tool 2 is the project's reusable **read-only Baserow Media database lookup/reconciliation service**.

It reads:

- `media`
- `category_title`
- `travel_schedule`

It does not currently use `users` and does not mutate Baserow.

Tool 2 consumes structured Tool 1 evidence, finds and compares plausible Media rows, distinguishes confirmed from probable/ambiguous/conflicting associations, and returns confirmed metadata to Renamer Enrich. It remains separate from Tool 3; travel-schedule data is supporting context here while Tool 3 owns full travel-schedule review.

Confirmed long Baserow titles remain preserved in full metadata but are automatically shortened at whole-word boundaries by the Renamer/common filename rendering logic only when needed to satisfy the filename length budget.

Current database state is always obtained live. Stored row values/results are retained only for review history, audit provenance, and later stale-state comparison.

## Protected-main / PR / CI workflow

`main` is protected by the active repository ruleset `Protect main`.

Tool 2 implementation must occur on:

```text
tool-2-implementation
```

`./scripts/builder-start.sh 2` will create or resume that branch automatically when started from `main`.

The builder must open one pull request from `tool-2-implementation` to `main` and keep it open across implementation/review/correction cycles. Planning/review findings are recorded on the same branch/status file rather than merging partial implementation.

Required GitHub CI check before acceptance/merge:

```text
Python 3.12 tests
```

The PR branch must be up to date with `main` and the required check must pass before merge.

## Milestones

- [x] Requirements gathered
- [x] Tool 2 / Tool 3 boundary decided: separate tools with limited overlap
- [x] Read-only Baserow boundary decided
- [x] Required Baserow tables decided (`media`, `category_title`, `travel_schedule`)
- [x] Candidate/contradiction/enrichment policy finalized
- [x] Long-title filename policy finalized: automatic deterministic shortening
- [x] Build plan finalized
- [x] Protected-main / branch / PR / required-CI workflow established
- [x] Planner/Builder coordination baseline recorded before implementation
- [x] Project-wide live Baserow / no-stale-cache policy finalized
- [x] Tool 2 live-data amendment finalized
- [ ] Implementation branch created
- [ ] Implementation PR opened
- [ ] Implementation started
- [ ] Live Baserow query/schema provider implemented
- [ ] Media candidate retrieval/reconciliation implemented
- [ ] Human-decision live revalidation implemented
- [ ] Confirmed-vs-candidate enrichment separation implemented
- [ ] Local registry/audit integration implemented without operational row-cache fallback
- [ ] Renamer Enrich/title compaction integration implemented
- [ ] CLI implemented
- [ ] Review portal extended
- [ ] Required freshness/race tests implemented and passing
- [ ] Representative live read-only Baserow/sample evaluation completed
- [ ] Required GitHub CI passing on review head
- [ ] Ready for review
- [ ] Accepted and merged to `main`

## Tests/results

None yet. Tool 2 has not been implemented.

## Sample/evaluation results

None yet.

## Known defects / limitations

None yet; implementation has not started.

The existing Tool 1 `BaserowReferenceProvider` supports local seed/fallback behavior for filename parsing. That behavior is explicitly **not suitable as Tool 2's authoritative current-state data provider**. Tool 2 may reuse low-level transport/normalization helpers where appropriate, but current Media decisions must obey the live-data policy.

## Open questions / contradictions

None currently requiring user input.

The apparent contradiction between the original finalized build plan's stale-cache/session-snapshot wording and the new collaborator-concurrency requirement is resolved by the authoritative amendment `docs/tool-2-media-database-reviewer-live-data-amendment.md`.

If the live Baserow schema or API/MCP behavior contradicts the remaining finalized assumptions, the builder must add a `Q-###` entry here rather than changing the build plan or live-data amendment.

## Next milestone

Builder runs:

```sh
./scripts/builder-start.sh 2
```

The helper synchronizes the repository, creates/resumes `tool-2-implementation`, and restarts the briefing on that branch. The Builder must first read the planner/Builder coordination note and the live Baserow policy/amendment above, then build on the current shared `main` baseline. The first implementation milestone is inspection/reuse of Tool 1 interfaces plus a **live-current, read-only Baserow provider/query layer**; do not begin by implementing a reusable session snapshot/cache. Before `READY_FOR_REVIEW`, the builder must push all work, open/update the Tool 2 PR to `main`, and record the PR/head/local-test/CI state here.

## Progress log

### 2026-09-13 — Build plan finalized

- Tool 2 defined as a reusable read-only Baserow lookup/reconciliation service.
- `media`, `category_title`, and `travel_schedule` included; `users` excluded for current scope.
- Tool 2 and Tool 3 intentionally kept separate despite overlap.
- Confirmed candidate metadata may enrich Renamer; probable/conflicting candidate metadata may not leak into confirmed enrichment.
- User selected automatic long-title shortening for filename rendering while preserving full Baserow title evidence.
- Status initialized to `NOT_STARTED`.

### 2026-09-13 — Protected-main workflow established

- Repository ruleset `Protect main` activated for the default branch.
- Tool implementation now uses per-tool branches and pull requests instead of direct `main` commits.
- `Python 3.12 tests` is the required GitHub Actions merge check.
- Tool 2 standard branch fixed as `tool-2-implementation`.

### 2026-09-13 — Planner / Builder coordination checkpoint added

- Recorded that Builder remains the default implementation agent while planning/review may make occasional small maintenance corrections.
- Tool 2 is explicitly required to build on current Tool 1 shared infrastructure through PR #16 / merge `8fb6b247d2fce2b64d8771210771970dcc8d53dc`.
- Added `docs/planner-builder-coordination.md` as the durable coordination rule for planner-authored source changes.

### 2026-09-14 — Live Baserow authority policy finalized before implementation

- User clarified that the Baserow Media database is continuously updated by external collaborators and must never be assumed unchanged.
- Added project-wide `docs/baserow-live-data-policy.md`.
- Added the authoritative Tool 2 amendment `docs/tool-2-media-database-reviewer-live-data-amendment.md` to supersede stale-cache/session-snapshot semantics in the original plan.
- Current Tool 2 decisions now require live reads; stored Baserow values are audit/history only.
- Human confirmation must revalidate live state.
- Tool 4 is pre-specified to re-read before update and re-check existence before create so collaborator edits are not overwritten or duplicated.
