# Tool 2 — Media Database Reviewer Implementation Status

Build plan: `docs/tool-2-media-database-reviewer-build-plan.md`  
Project architecture: `docs/project-implementation-architecture.md`  
Implementation issue: #2  
Protocol: `docs/implementation-protocol.md`  
Planner / Builder coordination: `docs/planner-builder-coordination.md`

## Current state

Status: `NOT_STARTED`

Planned implementation branch: `tool-2-implementation`  
Implementation PR: none yet  
Last implementation update: none  
Last planning/review update: 2026-09-13

## Review checkpoint

Last planning/review commit: none  
Current implementation HEAD: none  
Fundamental-change review pending: no

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

## Finalized scope summary

Tool 2 is the project's reusable **read-only Baserow Media database lookup/reconciliation service**.

It reads:

- `media`
- `category_title`
- `travel_schedule`

It does not currently use `users` and does not mutate Baserow.

Tool 2 consumes structured Tool 1 evidence, finds and compares plausible Media rows, distinguishes confirmed from probable/ambiguous/conflicting associations, and returns confirmed metadata to Renamer Enrich. It remains separate from Tool 3; travel-schedule data is supporting context here while Tool 3 owns full travel-schedule review.

Confirmed long Baserow titles remain preserved in full metadata but are automatically shortened at whole-word boundaries by the Renamer/common filename rendering logic only when needed to satisfy the filename length budget.

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
- [ ] Implementation branch created
- [ ] Implementation PR opened
- [ ] Implementation started
- [ ] Baserow snapshot/schema provider implemented
- [ ] Media candidate retrieval/reconciliation implemented
- [ ] Confirmed-vs-candidate enrichment separation implemented
- [ ] Local registry/audit integration implemented
- [ ] Renamer Enrich/title compaction integration implemented
- [ ] CLI implemented
- [ ] Review portal extended
- [ ] Required tests implemented and passing
- [ ] Representative Baserow/sample evaluation completed
- [ ] Required GitHub CI passing on review head
- [ ] Ready for review
- [ ] Accepted and merged to `main`

## Tests/results

None yet. Tool 2 has not been implemented.

## Sample/evaluation results

None yet.

## Known defects / limitations

None yet; implementation has not started.

## Open questions / contradictions

None currently requiring user input.

If the live Baserow schema or API/MCP behavior contradicts the finalized assumptions, the builder must add a `Q-###` entry here rather than changing the build plan.

## Next milestone

Builder runs:

```sh
./scripts/builder-start.sh 2
```

The helper synchronizes the repository, creates/resumes `tool-2-implementation`, and restarts the briefing on that branch. The Builder must first read the planner/Builder coordination note above and build on the current shared `main` baseline. It then implements the first milestones from the finalized build plan, starting with inspection/reuse of Tool 1 interfaces and the read-only Baserow snapshot/schema provider. Before `READY_FOR_REVIEW`, the builder must push all work, open/update the Tool 2 PR to `main`, and record the PR/head/local-test/CI state here.

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
