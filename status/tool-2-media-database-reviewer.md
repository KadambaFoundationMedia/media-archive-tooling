# Tool 2 — Media Database Reviewer Implementation Status

Build plan: `docs/tool-2-media-database-reviewer-build-plan.md`  
Authoritative live-data amendment: `docs/tool-2-media-database-reviewer-live-data-amendment.md`  
Project-wide Baserow policy: `docs/baserow-live-data-policy.md`  
Project architecture: `docs/project-implementation-architecture.md`  
Implementation issue: #2  
Implementation PR: #19  
Protocol: `docs/implementation-protocol.md`  
Planner / Builder coordination: `docs/planner-builder-coordination.md`

## Current state

Status: `CHANGES_REQUESTED`

Implementation branch: `tool-2-implementation`  
Implementation PR: #19  
Builder handoff branch HEAD before review: `1c33ae254313a4a3be5681ef2426c9a56e386e98`  
Primary implementation code commit: `4e888186710fc0593be3de0f55a5134bba3af248`  
Last implementation update: 2026-09-13  
Last planning/review update: 2026-09-14

The Builder implementation was produced from planning checkpoint `f744d4ef92ea7140589851bfb27bba955b8b25f2`. While it was being built, the project-wide live-Baserow policy and Tool 2 live-data amendment were finalized and merged to `main` through PR #18 / merge `31563eea819511c24fc87764b92ad5cd5c1d1061`. The implementation branch therefore diverged from current `main` and must be synchronized before corrections continue.

## Review checkpoint

Last planning/review commit: current `CHANGES_REQUESTED` status update on PR #19 (see PR branch HEAD)  
Current implementation HEAD reviewed: `1c33ae254313a4a3be5681ef2426c9a56e386e98`  
Fundamental-change review pending: yes — live shared-state semantics and automatic-association safety require correction

Relevant commits since last review:
- `4e888186710fc0593be3de0f55a5134bba3af248` — Tool 2 implementation
- `1c33ae254313a4a3be5681ef2426c9a56e386e98` — Builder status/readiness handoff

Planning/review independently inspected the actual branch, implementation files, tests, branch/main divergence, and PR state. The branch was 5 commits behind `main` when review started. The Builder status claimed an open PR, but no Tool 2 PR existed; planning/orchestration opened PR #19 as the durable review surface.

## Planner-authored maintenance / coordination

Before corrections, the Builder must synchronize current `main` into `tool-2-implementation` and preserve all post-Tool-1 shared infrastructure.

Current shared baseline includes Tool 1 maintenance through PR #16 plus the Planner/Builder coordination policy from PR #17.

### Live Baserow coordination — mandatory

The Media database is continuously updated by external collaborators. Read and implement both:

- `docs/baserow-live-data-policy.md`
- `docs/tool-2-media-database-reviewer-live-data-amendment.md`

The amendment is authoritative wherever it conflicts with the older Tool 2 build-plan cache/snapshot wording.

Current Baserow state must be obtained live for each independent current-state decision. Persisted row copies are audit/history only. Human confirmation revalidates relevant live state. Tool 4 later re-reads immediately before update and re-checks existence immediately before create.

## Finalized scope summary

Tool 2 is the reusable **read-only live Baserow Media lookup/reconciliation service**. It reads `media`, `category_title`, and `travel_schedule`; it does not use `users` for current scope and does not mutate Baserow.

It consumes structured Tool 1 evidence, finds plausible current Media rows, compares database and local evidence, separates confirmed enrichment from candidate-only metadata, and produces structured results for Tool 3, Tool 4, Renamer Enrich, CLI, portal, and future orchestration.

Current database state is always live. Stored values/results are retained only for review history, audit provenance, and stale-state comparison.

## Active review findings

### R-001 — Live Baserow policy not implemented

Status: OPEN  
Severity: BLOCKING / fundamental shared-state correctness

The implementation centers on `BaserowSnapshotProvider`, persists `.renamer/baserow_snapshot.json`, reuses `_current_snapshot`, loads stale disk data after live failure, and loads one snapshot for an entire batch. Human candidate confirmation can use the stored candidate row directly; `confirm_new` can finalize an earlier no-match without any fresh Baserow existence check.

This contradicts the authoritative live-data amendment and project-wide Baserow policy.

Required correction:
- synchronize latest `main` first;
- redesign current-state access around live per-decision queries/direct row reads rather than a session-wide authoritative snapshot;
- persisted Baserow values become audit/history only and are never current operational input;
- live failure returns unavailable/incomplete and cannot fall back to stale rows for a current conclusion;
- human `confirm_existing` / `choose_candidate` re-read the selected row and recompute materially changed comparisons before confirmation;
- human `confirm_new` performs a fresh live candidate/existence search before finalization;
- current confirmed enrichment carries live-read provenance;
- add the required race/freshness tests from the live-data amendment.

### R-002 — Automatic association is driven by numeric score thresholds

Status: OPEN  
Severity: BLOCKING / incorrect automatic association risk

The engine assigns numeric weights, treats top candidates within 10 points as multiple, auto-confirms a best candidate at score `>= 70`, and calls `>= 30` probable. This makes a numeric score the primary decision mechanism and permits combinations outside the explicit automatic-association rules to become `EXISTING_MEDIA_MATCH`.

Required correction:
- implement explicit evidence predicates for direct identity and unique high-specificity semantic association;
- a score may be retained only as secondary ranking/diagnostic support, never as the rule that authorizes confirmed association;
- add negative regression tests proving weaker score combinations cannot auto-confirm.

### R-003 — WHERE comparison ignores country contradictions

Status: OPEN  
Severity: HIGH

`_compare_places()` accepts place equality/fuzzy similarity but does not use `local_country` or `db_country` when deciding agreement. Same-named places in different countries can therefore be treated as agreeing and can contribute to automatic association.

Required correction:
- normalize/compare country when both sides provide it;
- incompatible country values must prevent exact WHERE agreement and surface a conflict/not-comparable state as appropriate;
- add regression tests for same place name with different countries.

### R-004 — Scripture WHAT matching uses unsafe substring equivalence

Status: OPEN  
Severity: HIGH

`_compare_what()` strips punctuation and then treats substring containment as agreement. Structured scripture references such as `BG-1-1` and `BG-1-10` can therefore compare as equal by containment. The same risk exists for SB/CC verse numbers and ranges.

Required correction:
- compare recognized scripture references structurally/canonically rather than by token substring;
- preserve range semantics from the Tool 1 scripture grammar;
- add negative tests for neighboring verse numbers/ranges that share string prefixes.

### R-005 — Partial Tool 1 dates can be misclassified as conflicts

Status: OPEN  
Severity: HIGH

Tool 1 intentionally supports partial dates such as `2015-02-DD`. `_compare_dates()` treats two 10-character unequal values as an immediate conflict, so `2015-02-DD` versus a database date such as `2015-02-15` cannot reach a compatibility rule.

Required correction:
- compare Tool 1 date precision/state explicitly;
- compatible partial year/month evidence must not be treated as a full-date contradiction;
- add regression tests for `YYYY-MM-DD` placeholders/partial precision against concrete Baserow dates.

### R-006 — Travel schedule same-month matching is too broad

Status: OPEN  
Severity: HIGH

Travel corroboration currently treats any file date in the same month as a schedule row start date as relevant (`eval_date.startswith(start_date[:7])`) and can add match weight when the place agrees. This is not exact date evidence and can imply presence on dates that the schedule does not establish.

Required correction:
- Tool 2 may use exact date/place or other explicitly justified bounded schedule evidence only;
- do not infer presence merely from being in the same month or from gaps/ranges Tool 3 owns;
- add regression tests showing same-month/different-date schedule rows do not corroborate a candidate unless explicit schedule semantics establish the date.

### R-007 — Ordinary conflicts are promoted to immediate human review

Status: OPEN  
Severity: HIGH / progressive-processing regression

For any leading candidate conflict, the engine copies all conflicts into `review_reasons`; `review_required` is then `bool(review_reasons)`. That makes every candidate conflict an immediate human-review item even though the build plan says probable/multiple/conflicting evidence should continue to Tool 3 when travel evidence may resolve it, with human review reserved for irreducible/direct-identity contradictions or later unresolved cases.

The reported evaluation result of `1107` immediate human-review items is consistent with this over-routing.

Required correction:
- separate conflict existence from `review_required_now`;
- route resolvable/progressive cases to Tool 3 without prematurely requiring human action;
- keep direct-identity/material irreducible contradictions reviewable now;
- add tests for both downstream-only conflicts and immediate-review contradictions.

### R-008 — Acceptance sample evaluation used stale operational state and cached Baserow data

Status: OPEN  
Severity: BLOCKING acceptance evidence

The Builder report evaluated `2,040` rows from `.renamer/registry.db` and a 3.3 MB cached Baserow snapshot. The current representative Tool 1 sample contains 260 files; the previous 2,040 count was already diagnosed as historical/stale registry accumulation. The new live-data policy also disallows a captured cache as the live acceptance authority.

Required correction:
- after R-001–R-007, rerun Tool 2 on a fresh current Tool 1 sample snapshot for the actual `sample-files` target;
- expected input population for that sample is 260 files unless the filesystem itself has deliberately changed;
- perform the Tool 2 acceptance evaluation against live read-only Baserow current state;
- report the required decision counts and manually inspect a meaningful sample of automatic confirmed associations;
- do not claim semantic correctness solely from algorithmic output.

### R-009 — Builder handoff did not satisfy PR / up-to-date / CI protocol

Status: OPEN  
Severity: PROCESS BLOCKER

At handoff, the branch was 5 commits behind current `main`, no Tool 2 PR existed despite the status claiming one was open, the status recorded implementation HEAD `4e888186...` although actual branch HEAD was `1c33ae25...`, and there was no required PR CI result for the handoff head.

Planning/orchestration opened PR #19 so review can proceed.

Required correction:
- synchronize latest `main` into the same Tool 2 implementation branch without discarding implementation work;
- keep PR #19 as the review surface;
- after corrections, run local tests/evaluation, commit and push everything, record the actual final reachable branch HEAD including status updates, and ensure required `Python 3.12 tests` CI passes on that review head before `READY_FOR_REVIEW`.

## Milestones

- [x] Requirements gathered
- [x] Tool 2 / Tool 3 boundary decided
- [x] Read-only Baserow boundary decided
- [x] Required Baserow tables decided
- [x] Candidate/contradiction/enrichment policy finalized
- [x] Long-title filename policy finalized
- [x] Build plan finalized
- [x] Protected-main / branch / PR / CI workflow established
- [x] Planner/Builder coordination baseline recorded
- [x] Project-wide live Baserow policy finalized
- [x] Tool 2 live-data amendment finalized
- [x] Implementation branch created
- [x] Implementation started
- [x] Initial implementation commit produced
- [x] PR #19 opened for review
- [ ] R-001 live-current Baserow provider and revalidation corrected
- [ ] R-002 explicit automatic-association predicates corrected
- [ ] R-003 country-aware WHERE comparison corrected
- [ ] R-004 structured scripture comparison corrected
- [ ] R-005 partial-date comparison corrected
- [ ] R-006 travel evidence boundary corrected
- [ ] R-007 progressive conflict routing corrected
- [ ] R-008 fresh 260-file + live Baserow evaluation completed
- [ ] R-009 branch/head/CI handoff protocol satisfied
- [ ] Required freshness/race and regression tests passing
- [ ] Required GitHub CI passing on corrected review head
- [ ] Ready for re-review
- [ ] Accepted and merged to `main`

## Tests/results

Builder-reported pre-review result from the stale branch:

- 125 total local tests passing under Python 3.12;
- 31 Tool 2 tests plus 94 existing tests;
- helper shell syntax validation passed;
- offline package build passed.

These results demonstrate substantial implementation work but do not satisfy acceptance because the tests predate the live-data amendment and do not cover the review findings above. Required PR CI had not run for the Builder handoff because no PR existed.

## Sample/evaluation results

Builder-reported pre-review evaluation:

```text
total files: 2040
confirmed existing matches: 31
probable existing matches: 175
multiple candidates: 22
new-media candidates: 307
insufficient evidence: 398
conflicts: 1107
database failures: 0
human-review-required-now: 1107
downstream-to-Tool-3 count: 1304
confirmed title/metadata enrichments: 31
```

This evaluation is **not accepted as Tool 2 acceptance evidence** because it used the stale 2,040-row operational registry and a cached full Baserow snapshot. R-008 requires a fresh 260-file sample run against current live read-only Baserow after corrections.

## Known defects / limitations

Active defects are R-001 through R-009 above. No additional user archive-policy decision is currently required.

## Open questions / contradictions

None currently requiring user input.

The previous cache/snapshot wording is resolved by the authoritative live-data amendment; it is not an open question for the Builder.

## Next milestone

Builder runs:

```sh
./scripts/builder-start.sh 2
```

The helper must synchronize current `main` and the existing `tool-2-implementation` branch. The Builder then addresses **all R-001 through R-009 on the same branch and PR #19**, adds the required regression/freshness tests, performs the fresh live read-only evaluation, updates this status with exact evidence, commits/pushes all work, and returns to `READY_FOR_REVIEW` only when the actual final branch HEAD is reachable and required CI is successful or accurately recorded as pending.

## Progress log

### 2026-09-13 — Build plan finalized

- Tool 2 defined as a reusable read-only Baserow lookup/reconciliation service.
- `media`, `category_title`, and `travel_schedule` included; `users` excluded.
- Tool 2 and Tool 3 intentionally kept separate despite overlap.
- Confirmed candidate metadata may enrich Renamer; probable/conflicting candidate metadata may not leak into confirmed enrichment.
- Long-title shortening policy finalized.

### 2026-09-13 — Protected-main workflow established

- Tool implementation uses `tool-2-implementation` and a PR to protected `main`.
- `Python 3.12 tests` is the required GitHub Actions merge check.

### 2026-09-13 — Planner / Builder coordination checkpoint added

- Tool 2 must preserve current shared Tool 1 infrastructure and planner-authored maintenance.

### 2026-09-13 — Initial Tool 2 implementation completed by Builder

- Builder produced the initial provider, reconciliation engine, persistence, Renamer integration, CLI, portal changes, and 31 Tool 2 tests in commit `4e888186710fc0593be3de0f55a5134bba3af248`.
- Builder added status handoff commit `1c33ae254313a4a3be5681ef2426c9a56e386e98` and marked `READY_FOR_REVIEW`.

### 2026-09-14 — Live Baserow authority policy finalized

- User clarified that Baserow is continuously changed by external collaborators.
- PR #18 added `docs/baserow-live-data-policy.md` and `docs/tool-2-media-database-reviewer-live-data-amendment.md` to `main`.

### 2026-09-14 — Planning/review first pass

- Confirmed implementation branch was stale relative to `main` and no Tool 2 PR actually existed.
- Opened PR #19 as the durable implementation review surface.
- Inspected the actual provider/service/engine/test implementation rather than relying on the Builder summary.
- Recorded blocking findings R-001 through R-009 and moved status to `CHANGES_REQUESTED`.
