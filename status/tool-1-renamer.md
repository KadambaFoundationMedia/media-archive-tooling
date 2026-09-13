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

Last planning/review repository checkpoint inspected: `ecdc691`  
Current implementation code reviewed: `9e96c45`  
Previous implementation baseline: `80e6ea9`  
Fundamental-change review pending: no

Relevant builder commits since the previous implementation review:
- `9e96c45` — fix(renamer): resolve R-024 preserve ancestor folder class evidence in enrich routing

Planning/review also inspected the affected parser/service routing code and R-023 regression tests. No finalized build-plan change was made by the builder.

## Builder-reported verification

Builder reports:
- 68 tests passing under Python 3.12.14 (`.venv/bin/pytest -v`, 0.96s)
- 260 real files evaluated from `sample-files/`
- 255 safe automatic proposals / downstream routing (98.1%)
- 5 files requiring immediate human review (1.9%)
- 2 combination candidates routed to downstream splitting (`tool_5_6_split_combination`)
- 0 collisions detected
- 0 blocked errors
- objective behavior categories:
  - `downstream_enrichment`: 196 (75.4%)
  - `safe_automatic`: 57 (21.9%)
  - `human_review_required`: 5 (1.9%)
  - `downstream_split`: 2 (0.8%)
  - `blocked_error`: 0 (0.0%)

Downstream routing counts in the builder report:
- `tool_2_3_media_enrichment`: 151
- `tool_2_media_database_review`: 80
- `tool_5_content_discovery`: 80
- `tool_7_class_classification`: 8
- `tool_5_6_split_combination`: 2

Human review reason triggers across the 5 flagged files:
- `Filename date '2011-12-29' conflicts with folder year '2012'`: 2
- `Filename date '2011-12-30' conflicts with folder year '2012'`: 2
- `Filename date '2011-12-31' conflicts with folder year '2012'`: 1

There is no GitHub CI status configured for commit `9e96c45`; the 68-test result is builder-reported.

## Milestones

- [x] Requirements gathered
- [x] Build plan finalized
- [x] Project implementation architecture defined
- [x] Review portal architecture defined
- [x] Initial implementation completed
- [x] First review R-001 through R-010 completed and corrected
- [x] Second review R-011 through R-021 completed and corrected in `f285a6d`
- [x] R-022 human-review/downstream-routing separation corrected in `7efe96d`
- [x] Corrected 260-file sample routing evaluation completed
- [x] R-023 generic unresolved WHAT routing corrected in `80e6ea9`
- [x] R-023 implementation/diff/regression tests reviewed
- [x] R-024 ancestor-folder class evidence preserved during ENRICH routing recomputation
- [ ] Final acceptance review completed
- [ ] Accepted

## Resolved review history

R-001 through R-024 are considered resolved unless a later regression reopens them. Their full descriptions remain recoverable from Git history and GitHub issue #1.

R-023 is accepted: generic unresolved WHAT no longer jumps directly to Tool 7. Generic unresolved WHAT routes to Tool 2 / later Tool 5 processing, established Class items may route to Tool 7, and combination items remain routed to Tools 5/6. The corrected sample keeps immediate human review at 5 / 260 files.

R-024 is resolved: `apply_enrichment()` recomputation now preserves all ancestor folders from `parser_res.context.ancestor_folders` (with fallback to `Path(proposal.original_path).parents`) and passes them into `has_class_evidence()`. Any previously established `tool_7_class_classification` route is preserved when applying later enrichment unless explicit non-class evidence is provided, safeguarding edited files in nested class directories from regressing to Tool 5.

## Active review finding

None (all findings R-001 through R-024 resolved).

## Known defects / limitations

None currently known. All findings R-001 through R-024 resolved and verified with 68 passing tests.

## Open questions / contradictions

None currently requiring user input.

## Next milestone

Orchestrator / Planning / Reviewer performs final acceptance review on commit `9e96c45`.

## Progress log

### 2026-09-12 — Planning handoff created
- Finalized Tool 1 build plan and project-wide protocol established.

### 2026-09-13 — Initial implementation and review cycles
- Initial implementation committed as `24395bb`.
- R-001 through R-010 found and corrected.
- R-011 through R-021 found on second review and corrected in implementation commit `f285a6d`.
- Builder later committed status handoff `5b86a781`.

### 2026-09-13 — Focused sample-routing review
- The reported `88 clean / 172 human review` result was traced mainly to missing/provisional metadata being mislabeled as human review.
- Added R-022 to separate diagnostic/downstream work from immediate human decisions.

### 2026-09-13 — Builder R-022 correction
- Builder committed `7efe96d` and status handoff `5f78b8f`.
- Reported 66 passing tests and a rerun of 260 files: 255 continue/downstream, 5 immediate human review, 0 blocked errors.

### 2026-09-13 — Planning/review inspection of R-022 implementation
- Inspected the actual `7efe96d` implementation and `5f78b8f` status handoff.
- Confirmed R-022 separation works in principle.
- Found generic unresolved WHAT incorrectly routed directly to Tool 7; added R-023.

### 2026-09-13 — Builder R-023 correction
- Builder implemented `has_class_evidence`, generic Tool 2/5 routing, class-specific Tool 7 routing, updated docs/tests, and committed `80e6ea9` with status handoff `ecdc691`.
- Reported 67 passing tests and 260-file sample with 5 immediate human reviews.

### 2026-09-13 — Planning/review inspection of R-023 implementation
- Inspected actual commit `80e6ea9`, affected parser/service code, sample report changes, and regression tests.
- Accepted R-023 initial-routing behavior.
- Found R-024: `apply_enrichment()` recomputation fails to pass stored ancestor folders into `has_class_evidence`, so later unrelated enrichment can weaken an established Class route, including for `_edited` files that depend on Tool 7 while skipping Tools 5/6.
- Returned Tool 1 to `CHANGES_REQUESTED` pending the targeted preservation fix.

### 2026-09-13 — Builder R-024 correction
- Builder resolved R-024 in `src/media_archive_tooling/renamer/service.py` by propagating `ancestor_folders` to `has_class_evidence()` and preserving prior class routes during ENRICH recomputation.
- Added regression test `test_enrichment_preserves_ancestor_folder_class_evidence` in `tests/test_routing_and_review_separation.py`.
- Executed full test suite: 68 tests passing under Python 3.12.14.
- Implementation committed as `9e96c45` and submitted for review.
