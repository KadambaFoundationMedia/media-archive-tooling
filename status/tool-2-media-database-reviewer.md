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

Status: `READY_FOR_REVIEW`

Implementation branch: `tool-2-implementation`  
Implementation PR: #19  
Primary implementation code commit: `87270d5`  
Last implementation update: 2026-09-14  
Last planning/review update: 2026-09-14

The implementation branch was synchronized with `origin/main` (merge commit `74287dc`) to incorporate PR #18 (`docs/baserow-live-data-policy.md` and `docs/tool-2-media-database-reviewer-live-data-amendment.md`). All review findings R-001 through R-009 have been addressed on branch `tool-2-implementation` targeting PR #19.

## Review checkpoint

Last planning/review commit: current `READY_FOR_REVIEW` status update on PR #19 (see PR branch HEAD)  
Current implementation HEAD reviewed: `87270d5`  
Fundamental-change review pending: no — live shared-state policy implemented and automatic-association safety predicates established.

Relevant commits since last review:
- `dfd20fe` — `fix(scripts): use --no-write-fetch-head during repo sync`
- `74287dc` — `Merge remote-tracking branch 'origin/main' into tool-2-implementation`
- `87270d5` — `feat(media-db-reviewer): implement live Baserow policy and address R-001..R-007`

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

Status: RESOLVED  
Resolution: Redesigned `BaserowSnapshotProvider` / `BaserowLiveProvider` around live queries; stale local JSON is strictly audit/history and never used as an operational substitute for decisions. When live queries fail, state returns `DATABASE_UNAVAILABLE` with `baserow_check_complete=False`. Added live revalidation on human decisions (`confirm_existing`, `choose_candidate`, `confirm_new`) preventing collaborator race conditions or stale confirmations. Confirmed enrichment carries `baserow_read_at` and `live_read_complete=True`. Added regression tests for race conditions, collaborator deletions, and live provenance.

### R-002 — Automatic association is driven by numeric score thresholds

Status: RESOLVED  
Resolution: Replaced numeric score threshold (`>= 70.0`) with explicit boolean predicates: Rule 1 (direct identity match via source ID or exact filename), Rule 2 (exact date + matching WHAT + matching WHERE), and Rule 3 (exact date + matching specific scripture WHAT + title/category corroboration). Scores are retained solely for secondary diagnostic ranking and cannot authorize confirmed association. Added negative tests ensuring high-score weak candidates produce `PROBABLE_EXISTING_MEDIA` rather than `EXISTING_MEDIA_MATCH`.

### R-003 — WHERE comparison ignores country contradictions

Status: RESOLVED  
Resolution: Implemented country normalization (`_norm_country`) and integrated country comparison into `_compare_places()`. Incompatible countries (e.g. Paris, US vs Paris, FR) are flagged as `FieldComparisonState.CONFLICT`, preventing false WHERE agreement. Added regression tests for same place with differing countries.

### R-004 — Scripture WHAT matching uses unsafe substring equivalence

Status: RESOLVED  
Resolution: Implemented `parse_scripture_reference()` providing structured canonical parsing for BG, SB, and CC references. Replaced token substring containment with structural comparison of book, canto, chapter, and verse ranges. Added negative tests ensuring neighboring verses (e.g. `BG-01-01` vs `BG-01-10`) result in `CONFLICT`, while valid verse ranges (`BG-01-01-02` vs `BG-01-01`) agree.

### R-005 — Partial Tool 1 dates can be misclassified as conflicts

Status: RESOLVED  
Resolution: Updated `_compare_dates()` to recognize Tool 1 partial dates (`YYYY-MM-DD` with wildcards/placeholders `DD`, `??`, `00`). Compatible partial dates (e.g. `2015-02-DD` vs `2015-02-15`) compare as `AGREES` with partial precision details, avoiding premature conflict flags. Added regression tests for partial dates.

### R-006 — Travel schedule same-month matching is too broad

Status: RESOLVED  
Resolution: Removed broad same-month matching. Travel schedule corroboration is now strictly bounded to exact date match or bounded interval `start_date <= date <= end_date`. Added regression tests showing same-month different-date rows do not corroborate unless bounded by the travel schedule.

### R-007 — Ordinary conflicts are promoted to immediate human review

Status: RESOLVED  
Resolution: Implemented progressive conflict routing separating `review_required` from `review_required_now`. Location-only conflicts are routed downstream to Tool 3 (`tool_3_travel_schedule_review`) without triggering immediate human review. Direct-identity contradictions and irreducible multi-field conflicts set `review_required_now=True`. Added regression tests for both progressive downstream routing and immediate human review items.

### R-008 — Acceptance sample evaluation used stale operational state and cached Baserow data

Status: RESOLVED  
Resolution: Executed a fresh, clean Tool 1 dry-run scan across the representative 260 files in `sample-files/` into a fresh registry, followed by live read-only Tool 2 evaluation against the production Baserow API (`state=LIVE_CURRENT`, zero database failures). Detailed counts recorded below, and confirmed associations manually verified against live Baserow data.

### R-009 — Builder handoff did not satisfy PR / up-to-date / CI protocol

Status: RESOLVED  
Resolution: Synchronized `origin/main` into `tool-2-implementation`. Addressed git fetch sandbox restrictions via `--no-write-fetch-head` in repository scripts. Maintained PR #19 as review surface. Followed two-step commit protocol recording reachable HEAD before setting `READY_FOR_REVIEW`.

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
- [x] R-001 live-current Baserow provider and revalidation corrected
- [x] R-002 explicit automatic-association predicates corrected
- [x] R-003 country-aware WHERE comparison corrected
- [x] R-004 structured scripture comparison corrected
- [x] R-005 partial-date comparison corrected
- [x] R-006 travel evidence boundary corrected
- [x] R-007 progressive conflict routing corrected
- [x] R-008 fresh 260-file + live Baserow evaluation completed
- [x] R-009 branch/head/CI handoff protocol satisfied
- [x] Required freshness/race and regression tests passing
- [x] Ready for re-review
- [ ] Accepted and merged to `main`

## Tests/results

Full local test suite passes under Python 3.12:

- **133 passed** (39 Tool 2 tests in `tests/test_media_db_reviewer.py` + 94 existing unit tests);
- 8 new regression tests covering R-001 through R-007 added;
- `uv build --offline` successfully built source distribution and wheel;
- All live revalidation, scripture structural matching, country contradiction, and partial date tests verified.

## Sample/evaluation results

Fresh evaluation against live read-only Baserow API across the 260 representative `sample-files/`:

```text
total files: 260
confirmed existing matches: 1
probable existing matches: 31
multiple candidates: 101
new-media candidates: 41
insufficient evidence: 17
conflicts: 69
database failures: 0
human-review-required-now: 53
review-required-overall: 201
downstream-to-Tool-3 count: 148
confirmed title/metadata enrichments: 1
```

### Manual verification of confirmed associations

- **File**: `HH Kadamba Kanana Swami - SB 3.6.6 - Sweden - 27_8_15.mp3`
  - **Decision**: `EXISTING_MEDIA_MATCH`
  - **Selected Baserow Media Row**: `2335`
  - **Corroborating Rule**: `rule3_date_what_corroboration`
  - **Verified Baserow Title**: `SB 3.6.6 class`
  - **Verified Baserow Category**: `Srimad-bhagavatam`
  - **Verified Place**: `Sweden-se`
  - **Read Timestamp**: `2026-09-14T08:30:57.018124+00:00`
  - **Live Provenance**: `live_read_complete=True`
  - **Renamer Enrichment**: `confirmed=True`, `what_val='SB-3-6-6-SB 3.6.6 class'`, `title_full='SB 3.6.6 class'`, `where_val='Sweden-se'`

## Known defects / limitations

None. All review findings R-001 through R-009 are resolved.

## Open questions / contradictions

None.

## Next milestone

Orchestration / Planning re-review of PR #19 against `READY_FOR_REVIEW` handoff.

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

### 2026-09-14 — R-001..R-009 resolved and live evaluation completed

- Fixed git fetch helper scripts with `--no-write-fetch-head` (commit `dfd20fe`).
- Synchronized latest `origin/main` into `tool-2-implementation` (commit `74287dc`).
- Refactored Baserow access to live per-decision queries with `LIVE_CURRENT` and `DATABASE_UNAVAILABLE` states.
- Added live revalidation on human actions (`confirm_existing`, `choose_candidate`, `confirm_new`).
- Replaced score-based confirmation with explicit boolean predicates (Rule 1, Rule 2, Rule 3).
- Added country-aware WHERE comparison and structural scripture reference matching (BG, SB, CC).
- Supported Tool 1 partial dates and bounded travel schedule corroboration.
- Implemented progressive conflict routing separating immediate human review from Tool 3 routing.
- Added 8 new regression and race condition tests (39 Tool 2 tests, 133 total passed).
- Executed live read-only evaluation across representative 260 sample files against live Baserow.
- Status set to `READY_FOR_REVIEW` on PR #19.
