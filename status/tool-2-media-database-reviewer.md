# Tool 2 — Media Database Reviewer Implementation Status

Build plan: `docs/tool-2-media-database-reviewer-build-plan.md`  
Authoritative live-data amendment: `docs/tool-2-media-database-reviewer-live-data-amendment.md`  
Project-wide Baserow policy: `docs/baserow-live-data-policy.md`  
Implementation issue: #2  
Implementation PR: #19  
Protocol: `docs/implementation-protocol.md`

## Current state

Status: `READY_FOR_REVIEW`

Implementation branch: `tool-2-implementation`  
Implementation PR: #19  
Builder handoff head reviewed: `a44a8736c8171a65b3c2ff15348d67741b471c48`  
Primary R-013 correction commit reviewed: `a44a8736c8171a65b3c2ff15348d67741b471c48`  
Last planning/review update: 2026-09-14

Local verified test suite:
- `pytest`: **154 passed, 2 warnings** (60 tests in `test_media_db_reviewer.py`)
- `helper shell validation`: **PASS** (`sh -n scripts/builder-start.sh scripts/review-tool-1.sh`)

## Active review findings

None. All findings are resolved.

## Resolved findings

- **R-001** — session/batch snapshot reuse removed; current decisions request live state per operation.
- **R-002** — automatic-association predicates tightened to strict high-specificity exact evidence:
  - Exact full date required for Rule 2 and Rule 3 (`len=10`, no `DD` wildcard, `local==db`, `AGREES`, no partial details). Compatible partial dates support ranking/probable state but never auto-confirm.
  - Specific WHAT required for Rule 2 and Rule 3 (`is_specific_what`). Generic tokens (`Lecture`, `Class`, `Bhajan`, `Kirtan`, `Seminar`, etc.) are prohibited from auto-association.
  - Exact normalized place and compatible country required for Rule 2 (`norm_lp == norm_dp`, no fuzzy/substring match).
  - Genuine corroboration required for Rule 3: database title must genuinely match local WHAT, scripture reference, or filename stem; or database category must match local category/scripture; or category title context must match; or travel schedule must corroborate. Mere presence of a non-empty title (`bool(title)`) is rejected.
  - Regressions 47–50 (negative) and 51 (positive).
- **R-003** — country contradictions are considered in WHERE comparison.
- **R-004** — exact scripture identity/range grammar corrected.
- **R-005** — compatible partial Tool 1 dates no longer become full-date conflicts.
- **R-006** — travel corroboration no longer uses broad same-month matching.
- **R-007** — progressive Tool 3 routing is separated from immediate human review.
- **R-008** — representative live evaluation uses a fresh 260-file Tool 1 population.
- **R-009** — branch/PR/CI handoff is healthy.
- **R-010** — portal tests are dependency-injectable and credential-independent.
- **R-011** — explicit 404 is distinguished from database unavailability and transport failure cannot directly confirm new media.
- **R-012** — targeted live candidate retrieval is pagination-complete, covers all necessary evidence routes, handles partial-date discrimination, and enforces mandatory `media_table_id`. Regressions 52–55.
- **R-013** — Tool 1 ↔ Tool 2 confirmed-enrichment bridge wired into normal workflow:
  - `MediaDatabaseReviewService.review_file()` and `review_batch()` automatically hand confirmed Tool 2 evidence to `RenamerApplicationService.apply_enrichment()` when `result.renamer_enrichment.confirmed` or `result.baserow_check_complete`.
  - `compose_what_val` provides deterministic whole-token idempotency: preserves scripture prefixes (e.g. `BG-01-18`), incorporates database title, strips redundant repeated scripture prefixes from titles, and prevents re-duplication on subsequent reviews or compacted titles.
  - Safe unconfirmed state isolation: `PROBABLE_EXISTING_MEDIA`, `MULTIPLE_CANDIDATES`, `CONFLICT_WITH_EXISTING`, `INSUFFICIENT_EVIDENCE`, `DATABASE_UNAVAILABLE` leave proposals untouched in `pending` state and never copy candidate titles.
  - Completed live no-match (`NEW_MEDIA_CANDIDATE`) propagates `baserow_check_complete=True` (clearing `_edited` suffix) without inventing title or location values.
  - CLI `media-archive media-db-review` supports `--no-enrich` (enabled by default) and prints enriched proposed filenames.
  - Regressions 56–60.

## Current verified tests / CI

```text
pytest: 154 passed, 2 warnings (60 tests in test_media_db_reviewer.py)
helper shell validation: PASS (sh -n scripts/builder-start.sh scripts/review-tool-1.sh)
```

Passing test suite covers all requirements of R-013:
1. Tool 1 initial proposal → Tool 2 live confirmed existing row with title → automatic Renamer proposal contains title and `baserow_check_complete=true`.
2. Probable, multiple, conflicting, insufficient, and unavailable states do not alter Tool 1 proposal or insert candidate titles.
3. Completed live no-match marks `baserow_check_complete=true` and clears `_edited` suffix without inventing title/location metadata.
4. Supported CLI and batch review paths automatically exercise the enrichment bridge.
5. Rerunning review multiple times is strictly idempotent and does not duplicate titles or scripture references.

## Last live sample evaluation

Fresh live read-only evaluation across all 260 representative `sample-files/` against live Baserow database:

```text
total files: 260
confirmed existing matches: 1
probable existing matches: 20
multiple candidates: 99
new-media candidates: 39
insufficient evidence: 32
conflicts: 69
database failures: 0
human-review-required-now: 55
review-required-overall: 188
downstream-to-Tool-3 count: 133
confirmed title/metadata enrichments: 1
```

### Confirmed Match End-to-End Enrichment Evidence

- **Tracking ID**: `f7903be1`
- **Current Filename**: `HH Kadamba Kanana Swami - SB 3.6.6 - Sweden - 27_8_15.mp3`
- **Before Tool 2 (Tool 1 Initial Proposed Filename)**: `2015-08-27_KKS_SB-3-6-6_Sweden-se_ID-f7903be1.mp3`
- **Live Baserow Match**: Row ID `2335`, Date: `2015-08-27`, What: `SB 3.6.6`, Place: `Sweden`, Title: `SB 3.6.6 class`
- **After Tool 2 Handoff (Enriched Proposed Filename)**: `2015-08-27_KKS_SB-3-6-6-class_Sweden-se_ID-f7903be1.mp3`
- **Registry Record State**: `status = "enriched"`, `needs_review = False`, `baserow_check_complete = True`
- **Unconfirmed Candidate Isolation**: Exactly 0 of the 220 unconfirmed files received candidate title or location metadata; all 220 remained `status = "pending"`.
- **New Media Candidate Lifecycle**: Exactly 39 `NEW_MEDIA_CANDIDATE` files received `baserow_check_complete = True` with no invented title or location metadata.

## Open questions / contradictions

None.

## Next milestone

Orchestrator review of PR #19 after GitHub CI validates the pushed head.

