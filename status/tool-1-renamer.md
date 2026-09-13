# Tool 1 — Renamer Implementation Status

Build plan: `docs/tool-1-renamer-build-plan.md`  
Project architecture: `docs/project-implementation-architecture.md`  
Walkthrough: `docs/tool-1-renamer-walkthrough.md`  
Sample evaluation: `docs/sample-evaluation-report.md`  
Implementation issue: #1  
Protocol: `docs/implementation-protocol.md`

## Current state

Status: `CHANGES_REQUESTED`

Implementation branch / PR: `main`  
Last implementation update: 2026-09-13  
Last planning/review update: 2026-09-13

## Review checkpoint

Last planning/review repository checkpoint inspected: `5f78b8f`  
Current implementation code reviewed: `7efe96d`  
Previous implementation baseline: `f285a6d`  
Fundamental-change review pending: no

Relevant builder commits since the previous implementation review:
- `7efe96d` — fix(renamer): resolve R-022 separate downstream routing from human review
- `5f78b8f` — docs(status): record Tool 1 implementation HEAD 7efe96d and review readiness

Project protocol/bootstrap commits between the previous implementation baseline and `7efe96d` are not Tool 1 behavior commits.

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

Downstream routing counts in the builder report:
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
- [x] R-022 human-review/downstream-routing separation corrected in `7efe96d`
- [x] Corrected 260-file sample routing evaluation completed
- [ ] R-023 unresolved WHAT routing corrected
- [ ] Corrected routing tests/evaluation committed and pushed
- [ ] Accepted

## Resolved review history

R-001 through R-022 are considered resolved unless a later regression reopens them. Their full descriptions remain recoverable from Git history and GitHub issue #1.

R-022 is confirmed substantially correct: diagnostic/downstream work is now separated from immediate human review; unresolved/provisional values remain visible; ambiguous/conflicting/corrupted cases still enter the human queue; combination candidates route downstream instead of becoming human-review tasks; and the sample human-review count fell from 172 to 5 without simply discarding the unresolved evidence.

## Active review finding — 2026-09-13

### R-023 — Generic unresolved WHAT is incorrectly routed directly to Tool 7 as if the recording were already known to be a class

Severity: **WORKFLOW / CROSS-TOOL INTERFACE BLOCKER**

The R-022 implementation currently does this for every unresolved WHAT:

```text
WHAT is unresolved
→ downstream_routing += tool_7_class_classification
```

The new regression test explicitly asserts this behavior for `2012-05-13_Sydney.mp3`, even though that filename contains no evidence that the recording is a class.

This overstates what Tool 1 knows and conflicts with the fixed pipeline responsibilities:

- Tool 2 reviews the existing Media database and may supply missing WHAT from an existing logical media item;
- Tool 5 Content Discoverer determines whether content is Class, Mantra singing, or a combination when still unresolved later;
- Tool 7 resolves unidentified **class** WHAT/class type after the recording is known to be a class;
- Tool 1 must not invent a class classification merely to complete routing, just as it must not invent WHAT in the filename.

The sample report also describes Tool 2/3 enrichment as using an "audio transcript / recording context". Tool 2 is the Media Database Reviewer and Tool 3 is the Travel Schedule Reviewer; neither is the audio/content-discovery stage. That wording blurs the finalized tool boundaries.

Required correction:

1. Do not route a generic unresolved WHAT directly to Tool 7 unless existing evidence already establishes that the item is a class requiring Tool 7 classification.
2. Respect the fixed pipeline order. In the initial/fast metadata stage, Tool 2 may resolve WHAT from Media data. If WHAT remains unresolved into content processing, Tool 5 should establish Class/Mantra/Combination before Tool 7 is selected for class-specific resolution.
3. The exact internal routing labels are an implementation detail, but they must not imply an unproven content class or skip an earlier owning stage.
4. Correct the sample report/walkthrough wording so Tool 2/3 are described according to their actual Media-database/travel-schedule roles, not as transcription/audio-context tools.
5. Add regression tests proving:
   - a generic unresolved WHAT does **not** imply `tool_7_class_classification`;
   - a class already established by stronger evidence may route to Tool 7 when its class WHAT remains unresolved;
   - combination routing remains Tools 5/6;
   - unresolved/provisional metadata still does not re-enter the human queue merely because it needs downstream work.
6. Rerun the 260-file sample and report the corrected downstream-routing counts separately from human-review counts.

No user/archive-policy decision is required. The existing finalized workflow and Tool 1 rule that Tool 7 resolves unidentified **class** WHAT are sufficient.

## Known defects / limitations

Active finding: R-023.

The corrected `5 / 260` immediate-human-review count is plausible and the R-022 separation is accepted in principle. The remaining issue is the destination of some downstream work, not a reason to put those files back into human review.

## Open questions / contradictions

None currently requiring user input.

## Next milestone

Builder addresses R-023 without changing the finalized build plan, corrects routing/documentation/tests, reruns the 260-file routing evaluation, commits and pushes all work, records the final reachable HEAD, and returns Tool 1 to `READY_FOR_REVIEW`.

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
- Confirmed R-022 separation works in principle: unresolved/provisional metadata is no longer automatically treated as a human decision, while ambiguity/conflicts/corruption remain human-review conditions.
- Found one remaining cross-tool routing error: every unresolved WHAT is labeled for Tool 7 even when the recording has not been established as a class.
- Added R-023 and returned Tool 1 to `CHANGES_REQUESTED`.
