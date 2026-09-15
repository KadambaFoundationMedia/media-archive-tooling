# Tool 3 — Travel Schedule Reviewer Implementation Status

Build plan: `docs/tool-3-travel-schedule-reviewer-build-plan.md`  
Implementation issue: #22  
Project architecture: `docs/project-implementation-architecture.md`  
Project Baserow policy: `docs/baserow-live-data-policy.md`  
Implementation protocol: `docs/implementation-protocol.md`

## Current state

Status: `CHANGES_REQUESTED`

Implementation branch: `tool-3-implementation`  
Implementation PR: #26 — `Tool 3 — Travel Schedule Reviewer implementation`  
Builder handoff head reviewed: `3abe6efebef31626740dcf04de77ade8b6b8a18f`  
Third-round correction implementation commit reviewed: `85455cc0b1fd49852cbec021cbfb52b95b63da51`  
Previous planning/review commit: `9b57a682400ecca8997e239006f71a4e2a2ea355`  
Current `main` at review: `8c29fd76bf20838918823b30c9ed4c1279680d0f`  
Last planning/review update: 2026-09-15

## Review checkpoint

The Builder substantially resolved R-013 and R-014. PR #26 remains open and mergeable, and the branch is based on current `main`.

Required CI run #73 (`Python 3.12 tests`) succeeded on the PR merge ref for Builder head `3abe6efebef31626740dcf04de77ade8b6b8a18f`:
- **219 passed, 2 warnings**;
- helper shell validation PASS;
- package build PASS.

The representative 260-file evaluation reports zero high-priority local overwrites, zero confirmed-Media overwrites, zero `REFERENCE_UNAVAILABLE`, and zero `PROCESSING_ERROR` results.

Acceptance is still blocked by two small fourth-round findings below. No user-policy decision or Tool 3 redesign is required.

## Findings R-001 through R-014

R-001 through R-014 are substantially resolved. In particular, the third-round corrections now:
- use confirmed Media country as a higher-authority constraint when the local country is missing;
- avoid redundant provisional schedule enrichment when schedule evidence agrees with confirmed Media;
- preserve a real same-place/different-country disagreement as `SCHEDULE_CONFLICT`;
- compute Case-A place/country comparison states independently;
- preserve non-ISO two-letter place suffixes such as `Farma-KD`;
- introduce a distinct `PROCESSING_ERROR` result for operational batch failures when the schedule reference is healthy.

## Fourth-round independent review findings

### R-015 — Batch operational errors are still not fully truthful or auditable

`TravelScheduleReviewService.review_batch()` now returns `PROCESSING_ERROR` when an item raises and a schedule engine can be loaded. However, the exception handler still calls `get_engine()` and changes the result to `REFERENCE_UNAVAILABLE` whenever that call returns `None`, even if the actual caught exception was unrelated to the schedule reference (for example, a missing tracking ID). `review_file()` already returns `REFERENCE_UNAVAILABLE` normally when the reference itself cannot be loaded, so an exception caught by the batch isolation handler must not be reclassified merely because the reference also happens to be unavailable.

The newly created per-item error result is also appended to the returned list but is **not persisted** through `_persist_review()`. That contradicts the Tool 3 local-registry/audit contract and the current status claim that the error is retained in the review record.

Required correction:
- classify an unrelated per-item exception caught by batch isolation as `PROCESSING_ERROR` regardless of reference availability; do not use a secondary `get_engine()` failure to relabel the original processing failure;
- reference metadata may be attached on a best-effort basis when already safely available, but it must not change the processing-error classification;
- persist the `PROCESSING_ERROR` result through the same Tool 3 audit boundary before continuing to the next item;
- add regressions for (a) a missing/invalid item while the reference is also unavailable and (b) retrieving the persisted processing-error review from the registry.

### R-016 — R-013 accidentally changes accepted Tool 2 country-normalization semantics

The R-013 correction changed the shared accepted Tool 2 helper `media_db_reviewer.engine._norm_country()`: previously any two-letter alphabetic value was preserved as an uppercase token and an otherwise-unmapped country string fell back to its uppercase form; the Tool 3 branch now accepts only recognized ISO-2 values and returns `None` for unmapped country strings.

That shared Tool 2 change is not required for Tool 3's strict WHERE-suffix fix, because `parse_structured_where()` now performs its own recognized-country validation. Leaving the Tool 2 change in place can silently turn previously comparable country evidence into `NOT_COMPARABLE` and changes an already accepted tool outside Tool 3's necessary compatibility surface.

Required correction:
- restore the accepted Tool 2 `_norm_country()` behavior from `main` (or an exactly equivalent behavior-preserving implementation);
- keep strict recognized-ISO suffix validation local to Tool 3 `parse_structured_where()` so `Farma-KD`/similar places remain intact;
- add a focused regression proving the Tool 3 suffix safety fix does not require changing Tool 2 normalization semantics;
- rerun the accepted Tool 1/Tool 2 regression suites and the full required CI.

## Verified evidence retained

The current handoff still demonstrates substantial completion and should be preserved:
- PR #26 is open and mergeable;
- current `main` remains `8c29fd76bf20838918823b30c9ed4c1279680d0f`;
- CI #73 is green with **219 passed, 2 warnings**;
- R-013 confirmed-Media authority regressions pass;
- R-014 healthy-reference processing-error regression and batch isolation pass;
- representative evaluation still reports 260 files, 133 Tool 2 downstream routes, 44 provisional enrichments, and zero authority overwrites.

## Open questions / contradictions

None requiring user input. Builder should resolve R-015 and R-016 on the existing `tool-3-implementation` branch / PR #26.

## Next milestone

Builder addresses R-015 and R-016, pushes the corrected branch, reruns the affected regression suites and required CI, updates this status with the actual final branch head, and returns `READY_FOR_REVIEW` for final acceptance review.
