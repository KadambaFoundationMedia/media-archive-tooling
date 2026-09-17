# Tool 1 — Renamer Implementation Status

Build plan: `docs/tool-1-renamer-build-plan.md`  
Project architecture: `docs/project-implementation-architecture.md`  
Walkthrough: `docs/tool-1-renamer-walkthrough.md`  
Sample evaluation: `docs/sample-evaluation-report.md`  
Implementation issue: #1  
Protocol: `docs/implementation-protocol.md`

## Current state

Status: `ACCEPTED`

Post-acceptance architecture note (2026-09-17): Tool 1's accepted naming/domain behavior remains approved. `docs/baserow-access-boundary-amendment.md` requires Tool 1 to ask Tool 2 for the live Media check and Tool 3 for schedule/date evidence, commit the final filename for that stage, and then call Tool 4 once to synchronize Baserow. Tool 1 itself has no Baserow access. This integration refinement is tracked with the Tool 4 correction round and does not reopen the accepted naming rules.

Practical correction (2026-09-17): the user identified an accepted-parser gap. `S.B. 1.19.31` must resolve to `SB-1-19-31`; `with Radha Madhava` marks a combination for later Tools 5/6 without replacing the exact primary class name; and all proposed filenames must follow the strict punctuation-free archive grammar. The exact sample regression now expects `2011-08-29_KKS_SB-1-19-31_Oslo-no.wma`. These orchestrator-authored changes supersede the older raw-stem fallback for combinations and are recorded here for the Builder.

Original accepted implementation branch / PR: `main`  
Accepted implementation code commit: `9e96c4550977c59e9a1840cde6b4e53a5b80b638`  
Builder status handoff reviewed: `769c88709a1dd5f3b08daee1fad6b49cd769d0c1`  
Post-acceptance maintenance branch / PR: `tool-1-review-portal-fixes` / #5  
Post-acceptance maintenance code/docs head reviewed before this status update: `54c69d2939eb448da7abe64b430165ce94d34b9e`  
Last implementation update: 2026-09-13  
Last planning/review update: 2026-09-13

## Review checkpoint

Last planning/review repository checkpoint inspected: PR #5 head `54c69d2939eb448da7abe64b430165ce94d34b9e`  
Accepted implementation code reviewed: `9e96c45`  
Previous implementation baseline: `80e6ea9`  
Fundamental-change review pending: no

Relevant accepted/final builder commits:
- `9e96c45` — fix(renamer): resolve R-024 preserve ancestor folder class evidence in enrich routing
- `769c887` — docs(status): record Tool 1 implementation HEAD 9e96c45 and review readiness

Relevant post-acceptance maintenance PR:
- PR #5 — fixes repeated-review registry inflation and the first dashboard usability findings from real user inspection

Planning/review inspected the actual PR #5 diff across the review helper, CLI, tracking-ID reuse path, registry, review portal, template, README, and regression tests. The changes do not alter the finalized archive naming policy: `_ID-xxxxxxxx` remains part of Tool 1's internal/in-process filename identity; it is only hidden from the human-facing dashboard display.

## Final verification evidence

Original accepted builder evidence:
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

Downstream routing counts in the final builder report:
- `tool_2_3_media_enrichment`: 151
- `tool_2_media_database_review`: 80
- `tool_5_content_discovery`: 80
- `tool_7_class_classification`: 8
- `tool_5_6_split_combination`: 2

Human review reason triggers across the 5 flagged files:
- `Filename date '2011-12-29' conflicts with folder year '2012'`: 2
- `Filename date '2011-12-30' conflicts with folder year '2012'`: 2
- `Filename date '2011-12-31' conflicts with folder year '2012'`: 1

Post-acceptance PR #5 CI verification:
- GitHub Actions required check `Python 3.12 tests`: **PASS**
- Python: 3.12.14
- shell helper syntax validation: PASS
- `pytest -q`: **70 passed, 2 warnings in 4.22s**
- `uv build`: PASS

The representative `sample-files/` directory is local and is not present in GitHub CI, so the corrected 260-file dashboard count must be verified by re-running `./scripts/review-tool-1.sh` locally after PR #5 is merged. The code-level regression proves repeated scans of the same unchanged path reuse the same tracking ID and do not create extra registry rows.

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
- [x] Final acceptance review completed
- [x] Accepted
- [x] One-command local review helper added
- [x] R-025 repeated-review registry inflation corrected in PR #5
- [x] R-026 dashboard first-pass usability findings corrected in PR #5

## Resolved review history

R-001 through R-024 are resolved for the accepted v1 implementation. Their full descriptions remain recoverable from Git history and GitHub issue #1.

The final acceptance review confirmed:

- generic unresolved WHAT does not jump directly to Tool 7;
- established Class items can retain Tool 7 routing;
- combination items remain routed to Tools 5/6;
- ordinary missing/provisional metadata is downstream work rather than immediate human review;
- R-024 preserves ancestor-folder Class evidence during ENRICH recomputation, including `_edited` files receiving later Baserow/WHERE enrichment;
- no new fundamental architecture or archive-policy change was introduced in the final correction.

### R-025 — Repeated review scans inflated registry/file counts

Status: RESOLVED in PR #5.

Observed from the first real browser review: the 260-file sample showed 2,040 tracked rows and 841 human-review rows.

Root cause:
- a dry-run file without `_ID-xxxxxxxx` in its physical filename generated a new random tracking ID on every scan instead of reusing the registry identity already associated with the same path;
- the review helper also displayed the long-lived general operational registry, which contained rows from repeated historical scans and older pre-R-022 review classifications.

Resolution:
- the registry can now resolve an existing tracking ID by original/current physical path;
- parser identity resolution reuses that ID before generating a new one;
- `review-tool-1.sh` uses a dedicated per-target review registry under `.renamer/review/` and passes that exact registry into the portal;
- the helper self-restarts after a fast-forward so the newest review-helper behavior is used immediately after updating.

Regression coverage: repeated CLI dry-runs of the same unchanged path leave one registry row and retain the same tracking ID.

### R-026 — Review dashboard first-pass usability issues

Status: RESOLVED in PR #5.

Implemented from the first real portal walkthrough:
- dark/light mode switcher with local preference retention;
- sticky table header while scrolling;
- separate **Original filename** and **Proposed filename** columns;
- relative **Path** column so the source can be found inside the reviewed directory;
- batch row checkboxes, select-all, and selected-count feedback;
- removed the visible ID column;
- hides `_ID-xxxxxxxx` from the **displayed** proposed filename only;
- retains the real tracking ID internally and in the underlying proposal so Tool 1 safety/idempotency semantics are unchanged.

The detail page is intentionally left for a separate user-review pass after these dashboard fixes are verified.

## Active review findings

None.

## Known defects / limitations

None currently known that block Tool 1 v1 acceptance. The detail page has not yet received the same user-facing usability review as the dashboard; any concrete issues found there should be recorded as new post-acceptance findings rather than silently redesigning it.

## Open questions / contradictions

None currently requiring user input.

## Next milestone

Merge PR #5 after required CI, then re-run `./scripts/review-tool-1.sh` locally against `sample-files/` and confirm that the dashboard shows only the current 260-file review set and the expected small immediate-human-review queue. After the dashboard is verified, perform the separate detail-page usability pass. Tool 2 can remain queued until the user is satisfied with this Tool 1 review surface.

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
- Found R-024: ENRICH recomputation failed to preserve ancestor-folder Class evidence.
- Returned Tool 1 to `CHANGES_REQUESTED` pending the targeted preservation fix.

### 2026-09-13 — Builder R-024 correction
- Builder resolved R-024 in `src/media_archive_tooling/renamer/service.py` by propagating `ancestor_folders` to `has_class_evidence()` and preserving prior class routes during ENRICH recomputation.
- Added regression test `test_enrichment_preserves_ancestor_folder_class_evidence` in `tests/test_routing_and_review_separation.py`.
- Reported 68 tests passing under Python 3.12.14.
- Implementation committed as `9e96c45`; status handoff committed as `769c887`.

### 2026-09-13 — Final acceptance review
- Planning/review inspected the complete builder diff from `0ae4534` through `769c887`; only the targeted R-024 implementation, regression test, walkthrough/status documentation changed.
- Verified the R-024 implementation preserves parent/ancestor class evidence, prior Tool 7 routing, and the edited-file workflow without introducing a new archive-policy or architectural change.
- Confirmed no active R-### findings remain.
- Accepted Tool 1 v1 at implementation code commit `9e96c45`.

### 2026-09-13 — Post-acceptance review convenience
- Added `scripts/review-tool-1.sh` as a user-facing one-command review helper.
- The helper synchronizes the current branch when safe, prepares the locked environment, runs a dry-run against `sample-files/` by default, starts the localhost review portal, and opens it in the browser when supported.
- This is operational convenience only; Tool 1 archive behavior and the accepted build plan are unchanged.

### 2026-09-13 — First real portal walkthrough / PR #5
- User screenshot exposed accumulated registry rows (2,040 tracked / 841 review) and dashboard usability problems.
- R-025 traced the inflated counts to non-reused dry-run tracking IDs plus the review helper using the long-lived operational registry.
- R-026 captured the requested dashboard improvements.
- PR #5 implements both corrections without changing finalized naming semantics.
- Required GitHub CI passed at reviewed head `54c69d2`: 70 tests, shell validation, and package build all successful.

### 2026-09-17 — Orchestrator practical naming correction
- Compared the accepted implementation with the strict grammar and scripture normalization in the separate `Media-renaming` naming subsystem.
- Added dotted `S.B.` recognition, punctuation-safe fallback rendering, strict special-character validation, and exact primary-class naming for combination recordings.
- Combination detection/routing remains intact for downstream Tools 5/6.
- Added an exact regression for the Oslo sample and updated the older combination regression to the user-directed behavior.
