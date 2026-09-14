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
Builder handoff head: `1158ad5723b72bb9b81b85434444535352fa10e3`  
Primary correction commit: `1158ad5723b72bb9b81b85434444535352fa10e3`  
Last planning/review update: 2026-09-14

The branch has integrated `origin/tool-2-implementation` (including the Builder Git sandbox policy coordination), resolved all active blocking findings (**R-002** and **R-012**), expanded test coverage to **149 passed tests** (55 in `test_media_db_reviewer.py`), and completed a fresh live read-only evaluation across the 260 representative `sample-files/`.

## Active review findings

None. All findings are resolved.

## Resolved findings

- **R-001** — session/batch snapshot reuse removed; current decisions request live state per operation.
- **R-002** — automatic-association predicates tightened to strict high-specificity exact evidence:
  - Exact full date required for Rule 2 and Rule 3 (`len=10`, no `DD` wildcard, `local==db`, `AGREES`, no partial details). Compatible partial dates support ranking/probable state but never auto-confirm.
  - Specific WHAT required for Rule 2 and Rule 3 (`is_specific_what`). Generic tokens (`Lecture`, `Class`, `Bhajan`, `Kirtan`, `Seminar`, etc.) are prohibited from auto-association.
  - Exact normalized place and compatible country required for Rule 2 (`norm_lp == norm_dp`, no fuzzy/substring match).
  - Genuine corroboration required for Rule 3: database title must genuinely match local WHAT, scripture reference, or filename stem; or database category must match local category/scripture; or category title context must match; or travel schedule must corroborate. Mere presence of a non-empty title (`bool(title)`) is rejected.
  - Added negative regressions 47–50 and positive regression 51.
- **R-003** — country contradictions are considered in WHERE comparison.
- **R-004** — exact scripture identity/range grammar corrected.
- **R-005** — compatible partial Tool 1 dates no longer become full-date conflicts.
- **R-006** — travel corroboration no longer uses broad same-month matching.
- **R-007** — progressive Tool 3 routing is separated from immediate human review.
- **R-008** — representative live evaluation uses a fresh 260-file Tool 1 population.
- **R-009** — branch/PR/CI handoff is healthy.
- **R-010** — portal tests are dependency-injectable and credential-independent.
- **R-011** — explicit 404 is distinguished from database unavailability and transport failure cannot directly confirm new media.
- **R-012** — targeted live candidate search is completeness-preserving and eliminates false `NEW_MEDIA_CANDIDATE`:
  - `search_media_candidates_live()` and `_fetch_targeted_media_rows()` paginate through all pages via `while next_url:`.
  - Targeted retrieval queries cover `place`, `what`, `source_id`, `tracking_id`, `filename`, and `YYYY-MM` prefix for partial dates ending in `DD`.
  - Input discrimination tightened: a partial date ending in `DD` requires a specific WHAT; partial date + place without specific WHAT yields `INSUFFICIENT_EVIDENCE`.
  - Missing/unusable Media table ID in provider returns `DATABASE_UNAVAILABLE` with `complete=False`, preventing false `LIVE_CURRENT` and false `NEW_MEDIA_CANDIDATE`.
  - `confirm_new` re-runs full live reconciliation against fresh live Baserow data via `_load_snapshot_for_parser_res` and rejects if any candidate is found or database is unavailable.
  - Added regressions 52–55.

## Current verified tests / CI

Local verified test suite:

```text
pytest: 149 passed, 2 warnings (55 tests in test_media_db_reviewer.py)
helper shell validation: PASS (sh -n scripts/builder-start.sh scripts/review-tool-1.sh)
uv build: PASS (media_archive_tooling-0.1.0 built offline)
```

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

Key comparison with previous incomplete retrieval:
- `new-media candidates` reduced from 76 to 39 as paginated date/place/source search discovered candidates previously missed past page 1.
- `probable existing matches` increased from 14 to 20.
- `conflicts` and `human-review-required-now` accurately capture multi-field contradictions against discovered live rows.

## Open questions / contradictions

None.

## Next milestone

Orchestrator review of PR #19 after GitHub CI validates the pushed head.
