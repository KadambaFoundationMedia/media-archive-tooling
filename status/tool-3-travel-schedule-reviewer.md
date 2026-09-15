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
Correction implementation commit: `0ef377fb8cad3ba3ebcb30443bfc6259b31daee8`  
Current `main` at review: `8c29fd76bf20838918823b30c9ed4c1279680d0f`  
Last planning/review update: 2026-09-15

## Review checkpoint

Second-round independent review findings R-008 through R-012 have been resolved in full:

1. **R-008 (Media WHERE Authority & Structured Location Parsing)**:
   - Added `parse_structured_where()` to extract trailing 2-letter ISO country codes while preserving hyphenated places (e.g. `Villa-Vrindavan`, `Serbia-summer-camp`, `New-York`, `Krsna-Dvur`).
   - Extended `_apply_media_authority_guard()` to parse structured locations and protect both canonical place and country from contradictory schedule enrichment.
   - Derived `confirmed_media_country_iso` from confirmed Media WHERE.
   - Supported boundary case where confirmed Media WHERE acts as Case-B anchor without provisional WHERE enrichment when local lacks anchors.
   - Regression tests: `test_r008_hyphenated_confirmed_place_not_truncated`, `test_r008_same_place_different_country_media_guard_suppresses_enrichment`, `test_r008_confirmed_media_where_only_acts_as_case_b_anchor_without_provisional_where`.

2. **R-009 (Structured-Location Identity, Provenance, & Deterministic Grouping)**:
   - Used structured location identity `(canonical_place, country_iso)` in Case C; same place in different countries remains `MULTIPLE_SCHEDULE_CANDIDATES` and avoids auto-enrichment.
   - Preserved union of all contributing row IDs and texts across semantic candidates for the selected candidate.
   - Implemented deterministic candidate and group output with multi-key sorting independent of input iteration order.
   - Passed engine index to `search_by_when()` and `search_by_where()` service helpers for consistent alias grouping.
   - Regression tests: `test_r009_same_place_different_country_multiple_candidates_in_case_c`, `test_r009_reversed_input_order_deterministic_grouping_and_provenance`, `test_r009_union_of_row_ids_and_texts_preserved_for_selected_candidate`.

3. **R-010 (Canonical Checksum Inclusion & Validation of `country_iso2`)**:
   - Included `country_iso2` in `compute_canonical_sha256()` hashing.
   - Added deterministic recomputation and validation of `country_iso2` from `country` during reference loading, rejecting tampered references.
   - Updated `.renamer/reference/travel_schedule.json` canonical checksum to match the new definition.
   - Regression test: `test_r010_tampered_country_iso2_rejected_by_load_reference`.

4. **R-011 (Tool 2 Decision Snapshot & Candidate Explainability Contract)**:
   - Added `tool2_decision` snapshot to `TravelReviewResult` and SQLite schema migration in `registry.py`.
   - Populated candidate comparison states (`date_comparison`, `place_comparison`, `country_comparison`) and `match_reasons` for Cases B and C.
   - Displayed `tool2_decision` and candidate comparison states in detail portal template (`templates/detail.html`).
   - Regression tests: `test_r011_tool2_decision_snapshotted_in_result_and_registry`, `test_r011_candidate_comparison_states_populated_in_cases_b_and_c`.

5. **R-012 (Truthful Failure Classification & Live Evaluation Metrics)**:
   - Reclassified per-file batch errors in `review_batch()` as `INSUFFICIENT_EVIDENCE` with distinct diagnostics when reference is healthy (never misclassified as `REFERENCE_UNAVAILABLE`).
   - Extended representative 260-file acceptance evaluation to compare final state against both local high-authority values and confirmed Tool 2 Media values (`overwritten_high_authority = 0`, `overwritten_confirmed_media_authority = 0`).
   - Updated committed walkthroughs (`docs/tool-3-travel-schedule-reviewer-walkthrough.md`, `walkthrough.md`) to reflect correct CLI syntax (`--registry-path`, `--reference-path`, positional `TRACKING_ID`), remove candidate "score" references, align representative tracking IDs with current evaluation output, and clarify verification protocol wording.
   - Regression test: `test_r012_batch_error_does_not_produce_reference_unavailable_when_reference_healthy`.

## Test & Verification Evidence

- **Test Suite**: `.venv/bin/pytest -q` -> **214 passed, 2 warnings in 1.87s**
  - `tests/test_travel_reviewer.py`: **57/57 passed** (40 base + 7 Round 1 + 10 Round 2 regressions)
  - `tests/test_media_db_reviewer.py`: **63/63 passed**
  - `tests/test_renamer.py`: **88/88 passed**
  - `tests/test_cli.py`: **6/6 passed**
- **Shell Scripts**: `sh -n scripts/builder-start.sh scripts/review-tool-1.sh` -> **PASS**
- **Package Build**: `uv build --offline` -> **PASS**
- **Representative 260-File Evaluation**:
  - Total files reviewed: 260
  - Files entering from Tool 2 routing: 133
  - CORROBORATED count: 36
  - PROVISIONAL_ENRICHMENT count: 44 (WHEN: 19, WHERE: 25)
  - MULTIPLE_SCHEDULE_CANDIDATES count: 2
  - SCHEDULE_CONFLICT count: 67
  - NO_SCHEDULE_SUPPORT count: 54
  - INSUFFICIENT_EVIDENCE count: 57
  - REFERENCE_UNAVAILABLE count: 0
  - Media-context-unavailable count: 0
  - Schedule enrichments applied to Tool 1: 44
  - High-priority local values overwritten: 0
  - Confirmed-Media values overwritten: 0

## Next milestone

Independent review and verification of PR #26 for Tool 3 acceptance and merge into `main`.
