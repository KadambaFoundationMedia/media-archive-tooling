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
Builder handoff head: `0b349b998501b4f430c984e2b88f0247c0152fc8`  
Last planning/review update: 2026-09-14

All blocking findings (R-001, R-004, R-011) have been addressed and verified with targeted regressions (tests 42–46). Full test suite passes with **140 passed, 2 warnings**, helper scripts validate cleanly, and package build succeeds. Fresh 260-file live evaluation was executed against production Baserow.

## Review checkpoint

The branch is current with `main` and PR #19 is ready for orchestrator review.

### Resolved findings summary for this checkpoint

- **R-001 (Live-current Baserow policy fully implemented)**:
  - Removed operational `_current_snapshot` caching from `BaserowSnapshotProvider`.
  - Implemented per-decision targeted live querying in `load_snapshot(parser_result=...)` via `_fetch_targeted_media_rows()`.
  - `review_file()` and `review_batch()` now execute per-decision live queries; no batch-wide or session-wide full-table snapshot is reused across independent decisions.
  - Added regression `test_42` (sequential reviews see live collaborator additions) and `test_43` (batch review queries live per item and sees updates).

- **R-004 (Scripture comparison canonical identity and strict grammar)**:
  - Enforced Tool 1 scripture grammar via `SB_REGEX`, `BG_REGEX`, `CC_REGEX`.
  - Rejected malformed dotted extra numeric components (`BG 13.8.12` -> `None`) and descending ranges (`BG 1.12-8` -> `None`).
  - In `_compare_what()`, exact canonical scripture identity (`lv1 == dv1 and lv2 == dv2`) is required for `FieldComparisonState.AGREES`. Overlapping but non-identical verse ranges (`BG 1.1-3` vs `BG 1.3-5`, `BG-01-01-02` vs `BG-01-01`) return `FieldComparisonState.CONFLICT` with detail explaining partial verse overlap does not establish identity.
  - Candidate retrieval retains partial verse overlap in reasons with partial score (15.0) for human inspection/routing without setting `what_match=True`, preventing Rule 2/Rule 3 auto-confirmation.
  - Updated `test_35` and added regression `test_44`.

- **R-011 (Live revalidation distinguishes database failure from row absence and guards confirm_new)**:
  - Defined `BaserowUnavailableError(RuntimeError)`.
  - `fetch_media_row_live(row_id)` raises `BaserowUnavailableError` on missing credentials, HTTP errors (non-200/non-404), or network timeouts; returns `None` ONLY on explicit HTTP 404.
  - `apply_human_decision("confirm_existing")` raises `RuntimeError("Media row ID {id} no longer exists in Baserow")` exclusively on explicit 404, and raises `RuntimeError("Cannot confirm Media row {id}: live database is unavailable: ...")` on `BaserowUnavailableError`.
  - `search_media_candidates_live()` raises `BaserowUnavailableError` on missing credentials or HTTP/transport failure.
  - `apply_human_decision("confirm_new")` requires a successful complete live search; on `BaserowUnavailableError`, it raises `RuntimeError("Cannot confirm new media candidate: live database search is unavailable: ...")` and refuses to finalize `NEW_MEDIA_CANDIDATE`.
  - Updated portal test `test_40` and added regressions `test_45` (`confirm_new` failure guard) and `test_46` (explicit 404 vs unavailable distinction).

## Active review findings

None. All review findings R-001 through R-011 are resolved.

## Resolved findings

- **R-001** — live-current Baserow policy implemented; operational caching removed; per-decision live querying used in review_file and review_batch.
- **R-002** — numeric score no longer authorizes automatic association; explicit predicates are used.
- **R-003** — country contradictions are considered in WHERE comparison.
- **R-004** — scripture comparison requires exact canonical identity for AGREES; partial range overlaps marked CONFLICT; Tool 1 regex grammar enforced.
- **R-005** — compatible partial Tool 1 dates no longer become full-date conflicts.
- **R-006** — travel corroboration no longer uses broad same-month matching.
- **R-007** — progressive Tool 3 routing is separated from immediate human review.
- **R-008** — fresh 260-file live Baserow evaluation completed.
- **R-009** — branch/PR/CI handoff is now healthy; CI run #43 passed.
- **R-010** — portal tests are dependency-injectable and credential-independent.
- **R-011** — live revalidation explicitly distinguishes 404 from database unavailable; confirm_new cannot succeed on database failure.

## Current verified tests / CI

Local verification on implementation head `0b349b998501b4f430c984e2b88f0247c0152fc8`:

```text
Python 3.12 tests: SUCCESS
pytest: 140 passed, 2 warnings
helper shell validation: PASS
uv build: PASS
```

## Last live sample evaluation

Fresh live read-only evaluation across the 260 representative `sample-files/` using per-decision targeted live Baserow querying:

```text
total files: 260
confirmed existing matches: 1
probable existing matches: 14
multiple candidates: 109
new-media candidates: 76
insufficient evidence: 39
conflicts: 21
database failures: 0
human-review-required-now: 9
review-required-overall: 144
downstream-to-Tool-3 count: 135
confirmed title/metadata enrichments: 1
```

Evaluation highlights:
- 1 confirmed existing match (`2014-08-04_KKS_BG-01-18_Leipzig-de.mp3` -> Baserow row 403, Rule 3 confirmed) with title/metadata enrichment applied.
- 76 new-media candidates: files with discriminating metadata (valid date + WHAT/place) with complete live check confirming zero candidate rows.
- 109 multiple candidate ambiguities and 14 probable matches routed downstream to Tool 3 travel schedule review without premature human blocker.
- Immediate human review required narrowed strictly to 9 files (irreducible conflicts or direct-identity contradictions).
- 0 database failures.

## Open questions / contradictions

None. All implementation and test requirements are finalized and verified.

## Next milestone

Handoff to Orchestrator for final review on PR #19.
