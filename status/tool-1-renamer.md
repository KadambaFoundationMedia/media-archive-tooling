# Tool 1 — Renamer Implementation Status

Build plan: `docs/tool-1-renamer-build-plan.md`  
Project architecture: `docs/project-implementation-architecture.md`  
Walkthrough: `docs/tool-1-renamer-walkthrough.md`  
Sample evaluation: `docs/sample-evaluation-report.md`  
Implementation issue: #1  
Protocol: `docs/implementation-protocol.md`

## Current state

Status: `READY_FOR_REVIEW`

Implementation branch / PR: `main`  
Last implementation update: 2026-09-13  
Last planning/review update: 2026-09-13

## Review checkpoint

Last planning/review repository checkpoint inspected: `7bcc9a1`  
Current implementation code reviewed: `7efe96d`  
Previous implementation baseline: `f285a6d`  
Fundamental-change review pending: no

Relevant builder commits since the previous implementation review:
- `7efe96d` — fix(renamer): resolve R-022 separate downstream routing from human review

Project protocol commits made after the builder handoff are not Tool 1 implementation commits and do not change the reviewed implementation baseline.

## Builder-reported verification

Builder reports:
- 66 tests passing under Python 3.12.14 (`.venv/bin/pytest -v`, 1.03s)
- 260 real files evaluated from `sample-files/`
- 255 safe automatic proposals / downstream routing (98.1%)
- 5 files requiring immediate human review (1.9%)
- 2 combination candidates routed to downstream splitting (`tool_5_6_split_combination`)
- 0 collisions detected
- 0 blocked errors
- objective behavior categories:
  - `downstream_enrichment`: 192 (73.8%)
  - `safe_automatic`: 61 (23.5%)
  - `human_review_required`: 5 (1.9%)
  - `downstream_split`: 2 (0.8%)
  - `blocked_error`: 0 (0.0%)

Downstream routing counts across the 255 unblocked files:
- `tool_2_3_media_enrichment`: 154
- `tool_7_class_classification`: 80
- `tool_5_6_split_combination`: 2

Human review reason triggers across the 5 flagged files:
- `Filename date '2011-12-29' conflicts with folder year '2012'`: 2
- `Filename date '2011-12-30' conflicts with folder year '2012'`: 2
- `Filename date '2011-12-31' conflicts with folder year '2012'`: 1

## Milestones

- [x] Requirements gathered
- [x] Build plan finalized
- [x] Project implementation architecture defined
- [x] Review portal architecture defined
- [x] Initial implementation completed
- [x] First review R-001 through R-010 completed and corrected
- [x] Second review R-011 through R-021 completed and corrected in `f285a6d`
- [x] 63-test regression suite reported passing under Python 3.12
- [x] 260-file objective sample evaluation produced
- [x] R-022 review-routing correction completed in `7efe96d`
- [x] Corrected sample routing evaluation completed (66 tests passing, 5 files in human review)
- [ ] Accepted

## Resolved review history

R-001 through R-022 are considered resolved in implementation commit `7efe96d` unless a later regression reopens them. Their full descriptions remain recoverable from Git history and GitHub issue #1.

They covered CLI dry-run safety, unresolved WHAT behavior, `_edited` lifecycle, Baserow authority and write safety, Vedabase, online location lookup, sibling grammar, service boundaries, Python 3.12, documentation/checkpoints, scripture WHAT preservation, combination preservation, direct WHERE evidence, conservative fuzzy/geocoding behavior, regional dates, extension-agnostic processing, tracking-ID/length/transliteration safety, enrichment interfaces, finalization collisions, review validation, objective sample reporting, and separation of downstream routing and diagnostic notes from human review (R-022).

## Active review findings

None. All review findings R-001 through R-022 resolved.

## Known defects / limitations

None currently blocking. Missing/downstream metadata is safely routed to downstream stages, and human review is reserved strictly for genuine contradictions and corruptions.

## Open questions / contradictions

None currently requiring user input.

## Next milestone

Reviewer verification of R-022 resolution, 66-test suite, and corrected 260-file sample evaluation results.

## Progress log

### 2026-09-12 — Planning handoff created
- Finalized Tool 1 build plan and project-wide protocol established.

### 2026-09-13 — Initial implementation and review cycles
- Initial implementation committed as `24395bb`.
- R-001 through R-010 found and corrected.
- R-011 through R-021 found on second review and corrected in implementation commit `f285a6d`.
- Builder later committed status handoff `5b86a781`.

### 2026-09-13 — Focused sample-routing review
- Reviewed the reported `88 clean / 172 human review` sample result.
- Found that the majority of review flags are unresolved fields rather than genuine human decisions.
- Added R-022 to separate downstream enrichment/diagnostic flags from immediate human review.
- Tool returned to `CHANGES_REQUESTED` pending corrected routing and sample evaluation.

### 2026-09-13 — R-022 downstream routing correction completed
- Separated diagnostic/enrichment state from human review in `engine.py`, `planner.py`, and `service.py`.
- Added `diagnostic_notes` and `downstream_routing` attributes to `ParserResult` and `RenameProposal`.
- Preserved original wording + tracking ID for missing WHAT (routed to `tool_7_class_classification`) and combination candidates (routed to `tool_5_6_split_combination`).
- Missing and provisional WHERE routed to `tool_2_3_media_enrichment` with `needs_review=False`.
- Updated `logger.py` with objective categories (`safe_automatic`, `downstream_enrichment`, `downstream_split`, `human_review_required`, `blocked_error`).
- Updated `detail.html` review portal template to display diagnostic notes and downstream pipeline routing separately from human review reasons.
- Added 3 regression tests in `tests/test_routing_and_review_separation.py` (total 66 passing).
- Reran dry-run evaluation on `sample-files/`: 255 safe automatic/downstream proposals (98.1%), only 5 genuine human review conflicts (1.9%).
- Committed implementation as `7efe96d`.
