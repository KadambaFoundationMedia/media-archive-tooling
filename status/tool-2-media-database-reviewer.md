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
Primary implementation code commit: `87270d5`  
Last implementation update: 2026-09-14  
Last planning/review update: 2026-09-14

The implementation branch was synchronized with `origin/main` (merge commit `74287dc`) to incorporate PR #18 (`docs/baserow-live-data-policy.md` and `docs/tool-2-media-database-reviewer-live-data-amendment.md`). Findings R-001 through R-008 remain resolved. R-009 is reopened because the required GitHub CI gate failed on the Builder handoff head. R-010 records the concrete CI/test-isolation defect.

## Review checkpoint

Last planning/review checkpoint: required CI review of PR #19 head `bdf5074bc374632a6bdff6d7b91620879d37b1f1`  
Current implementation HEAD reviewed: `bdf5074bc374632a6bdff6d7b91620879d37b1f1`  
Fundamental-change review pending: no — remaining blocker is CI/test isolation around the live provider, not archive policy.

Relevant commits since the previous planning review:
- `dfd20fe` — `fix(scripts): use --no-write-fetch-head during repo sync`
- `74287dc` — `Merge remote-tracking branch 'origin/main' into tool-2-implementation`
- `87270d5` — `feat(media-db-reviewer): implement live Baserow policy and address R-001..R-007`
- `bdf5074` — `docs(status): mark Tool 2 READY_FOR_REVIEW with R-001..R-009 resolved`

Required GitHub CI on `bdf5074` failed: workflow run #41 (`Python 3.12 tests`) reported exactly one failure, `tests/test_media_db_reviewer.py::test_31_portal_media_db_endpoints`, with `1 failed, 132 passed`. The portal POST returned HTTP 400 instead of 200.

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

### R-009 — Builder handoff / CI protocol

Status: REOPENED  
Severity: BLOCKING acceptance

The branch/PR synchronization and reachable-head parts are corrected, but the required `Python 3.12 tests` GitHub Actions gate failed on handoff head `bdf5074bc374632a6bdff6d7b91620879d37b1f1`. A failed required check means the tool cannot be `READY_FOR_REVIEW` or merged.

Required correction:
- address R-010 below on the same `tool-2-implementation` branch / PR #19;
- run the full suite without relying on private `.env` credentials;
- push the correction and wait for the required GitHub CI check to pass;
- record the real final reachable branch HEAD and CI result before returning `READY_FOR_REVIEW`.

### R-010 — Portal integration test depends on local Baserow configuration

Status: OPEN  
Severity: BLOCKING CI / test isolation

GitHub CI run #41 fails exactly one test: `tests/test_media_db_reviewer.py::test_31_portal_media_db_endpoints`. The POST to `/file/portal01/media-db-action` returns HTTP 400 instead of the expected 200.

Root cause: `review_portal.app.media_db_action()` constructs `BaserowSnapshotProvider` from `load_config()` / local `.env`. The test mocks `httpx.Client.get`, but GitHub CI intentionally has no private Baserow credentials or table IDs. Without token/table configuration, `fetch_media_row_live()` returns no row before any mocked HTTP request is made, so the human-confirmation path fails. The Builder's local `.env` masked this and allowed the local suite to pass.

Required correction:
- preserve R-001 live-authority and human-revalidation semantics; do **not** weaken live revalidation and do **not** add Baserow secrets to GitHub Actions;
- make the portal/service test path dependency-injectable, or otherwise provide an explicit fake live provider/config in the test, so the endpoint can be tested hermetically without `.env`;
- add/adjust regression coverage proving the portal action test passes with Baserow environment variables absent while production still requires a live authoritative provider;
- run the complete test suite in a credentials-absent environment (or explicit equivalent) and confirm success;
- package build must also pass after the test suite.

No fresh 260-file semantic evaluation is required solely for this test-isolation correction unless the Builder changes Tool 2 matching/reconciliation behavior while fixing it.

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
- [ ] R-009 required-CI handoff protocol satisfied
- [ ] R-010 credential-independent portal integration test corrected
- [x] Required freshness/race and semantic regression tests passing locally
- [ ] Required GitHub CI passing on corrected review head
- [ ] Ready for re-review
- [ ] Accepted and merged to `main`

## Tests/results

Builder local result before CI:

- **133 passed** under Python 3.12;
- 39 Tool 2 tests + 94 existing tests;
- `uv build --offline` successful.

Independent GitHub CI result on handoff head `bdf5074`:

- required workflow: `CI`, run #41;
- required job: `Python 3.12 tests`;
- result: **FAILURE**;
- test result: **1 failed, 132 passed, 2 warnings**;
- failing test: `tests/test_media_db_reviewer.py::test_31_portal_media_db_endpoints`;
- package-build step skipped because pytest failed.

The discrepancy is explained by local `.env` Baserow configuration being present during Builder local tests while GitHub CI correctly runs without private service credentials.

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

R-010 is currently blocking acceptance: portal human-action integration testing is accidentally coupled to the developer's local `.env`. This is a test/dependency-injection defect; it must be corrected without weakening live Baserow authority.

## Open questions / contradictions

None requiring user input.

## Next milestone

Builder runs `./scripts/builder-start.sh 2`, addresses R-009/R-010 on PR #19, validates the full suite without Baserow credentials, pushes the correction, and hands back only after required GitHub CI passes.

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

### 2026-09-14 — R-001..R-008 corrected and live evaluation completed

- Fixed git fetch helper scripts with `--no-write-fetch-head` (commit `dfd20fe`).
- Synchronized latest `origin/main` into `tool-2-implementation` (commit `74287dc`).
- Refactored Baserow access to live per-decision queries with `LIVE_CURRENT` and `DATABASE_UNAVAILABLE` states.
- Added live revalidation on human actions (`confirm_existing`, `choose_candidate`, `confirm_new`).
- Replaced score-based confirmation with explicit boolean predicates (Rule 1, Rule 2, Rule 3).
- Added country-aware WHERE comparison and structural scripture reference matching (BG, SB, CC).
- Supported Tool 1 partial dates and bounded travel schedule corroboration.
- Implemented progressive conflict routing separating immediate human review from Tool 3 routing.
- Added 8 new regression and race condition tests (39 Tool 2 tests, 133 total passed locally).
- Executed live read-only evaluation across representative 260 sample files against live Baserow.

### 2026-09-14 — Required CI failed after Builder READY_FOR_REVIEW handoff

- GitHub CI run #41 on PR #19 head `bdf5074` failed one portal integration test (`1 failed, 132 passed`).
- Diagnosed local `.env` leakage into the test path: the portal constructs its live provider from production config, so the Builder's local credentials made the test pass while credential-free CI correctly failed.
- Reopened R-009 and added R-010. Tool 2 returned to `CHANGES_REQUESTED`; no user policy decision is required.
