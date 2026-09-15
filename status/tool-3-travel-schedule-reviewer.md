# Tool 3 — Travel Schedule Reviewer Implementation Status

Build plan: `docs/tool-3-travel-schedule-reviewer-build-plan.md`  
Implementation issue: #22  
Project architecture: `docs/project-implementation-architecture.md`  
Project Baserow policy: `docs/baserow-live-data-policy.md`  
Implementation protocol: `docs/implementation-protocol.md`

## Current state

Status: `READY_FOR_REVIEW`

Implementation branch: `tool-3-implementation`  
Implementation PR: #26 — `Tool 3 — Travel Schedule Reviewer implementation`  
Builder handoff head: `85455cc0b1fd49852cbec021cbfb52b95b63da51`  
Third-round correction implementation commit: `85455cc0b1fd49852cbec021cbfb52b95b63da51`  
Second-round correction implementation commit: `0ef377fb8cad3ba3ebcb30443bfc6259b31daee8`  
Current `main` at review: `8c29fd76bf20838918823b30c9ed4c1279680d0f`  
Last planning/review update: 2026-09-15

## Review checkpoint

Last planning/review commit: `9b57a6886e082156822c60c04f98108502f6ef1e`  
Current implementation HEAD: `85455cc0b1fd49852cbec021cbfb52b95b63da51`  
Fundamental-change review pending: no  
Relevant commits since last review:
- `85455cc` — fix(travel-reviewer): resolve independent review findings R-013 and R-014

All review findings from Rounds 1, 2, and 3 (R-001 through R-014) are resolved in the implementation branch.
Full test suite passed (**219 passed, 2 warnings**). Shell script validation and package build offline passed.
Representative 260-file acceptance evaluation re-run with live Baserow context reported zero overwrites.

## Third-round independent review findings

### R-013 — Confirmed Media WHERE can still be downgraded to provisional or a real country conflict can be mislabeled as corroboration

Status: RESOLVED (in commit `85455cc`)

Resolution:
- Treated confirmed Media WHERE/country as an effective higher-authority constraint whenever the corresponding local dimension is missing (`eff_iso = local_iso or cm_country`).
- If schedule agrees with confirmed Media, Tool 3 records corroboration without emitting or applying redundant provisional enrichment for that dimension.
- If schedule disagrees with confirmed Media, Tool 3 returns and preserves `SCHEDULE_CONFLICT` with explicit provenance instead of converting to `CORROBORATED`.
- Computed Case-A place and country comparison states independently (`place_comparison=AGREES`, `country_comparison=CONFLICT` on same-place/different-country).
- Validated trailing country suffixes against recognized ISO-2 codes via `_get_valid_iso2_codes()`, ensuring non-ISO two-letter place suffixes (e.g. `Farma-KD`) are never truncated.
- Added 4 regressions in `tests/test_travel_reviewer.py`: `test_r013_confirmed_country_missing_locally_agreeing_schedule_corroborates_without_redundant_provisional_enrichment`, `test_r013_confirmed_country_missing_locally_conflicting_schedule_returns_schedule_conflict`, `test_r013_case_a_country_only_conflict_comparison_states`, and `test_r013_non_iso_two_letter_place_suffix_not_truncated`.

### R-014 — Arbitrary batch processing errors are still classified as `INSUFFICIENT_EVIDENCE`

Status: RESOLVED (in commit `85455cc`)

Resolution:
- Added `TravelReviewDecision.PROCESSING_ERROR` to unambiguously represent operational exceptions per file when the schedule reference itself is healthy, preventing misclassification as `INSUFFICIENT_EVIDENCE`, `REFERENCE_UNAVAILABLE`, or `NO_SCHEDULE_SUPPORT`.
- Preserves healthy reference checksum and row count in the review record, sets `review_required=True`, records error details in `review_reasons`, and isolates errors so subsequent batch files continue execution.
- Updated test 40 and `test_r012_batch_error_does_not_produce_reference_unavailable_when_reference_healthy` to assert `PROCESSING_ERROR`.
- Added regression `test_r014_batch_processing_error_distinct_from_insufficient_evidence_and_reference_unavailable`.

## Evidence already verified

- Complete pytest suite: **219 passed, 2 warnings** across the repository (Tool 1: 88, Tool 2: 63, Tool 3: 62, CLI: 6).
- Shell script syntax: `sh -n scripts/builder-start.sh scripts/review-tool-1.sh` passed.
- Package build offline: `uv build --offline` passed.
- Representative 260-file acceptance evaluation re-run with live Baserow context (`scripts/run_tool_3_evaluation.py`):
  - Total files reviewed: 260
  - Files entering from Tool 2 downstream routing: 133
  - Decision breakdown:
    - CORROBORATED: 36 (13.8%)
    - PROVISIONAL_ENRICHMENT: 44 (16.9% — WHEN: 19, WHERE: 25)
    - MULTIPLE_SCHEDULE_CANDIDATES: 2 (0.8%)
    - SCHEDULE_CONFLICT: 67 (25.8%)
    - NO_SCHEDULE_SUPPORT: 54 (20.8%)
    - INSUFFICIENT_EVIDENCE: 57 (21.9%)
    - REFERENCE_UNAVAILABLE: 0 (0.0%)
    - PROCESSING_ERROR: 0 (0.0%)
  - Overwritten high-priority local values: 0
  - Overwritten confirmed-Media authority values: 0
- Current branch is based on current `main` used by PR #26 (`8c29fd76bf20838918823b30c9ed4c1279680d0f`).
- PR #26 is open and mergeable.

## Open questions / contradictions

None requiring user input.

## Next milestone

Planning/review model conducts final independent review pass of PR #26 on branch `tool-3-implementation`.
