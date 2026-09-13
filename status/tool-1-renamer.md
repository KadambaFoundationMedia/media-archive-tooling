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

Last planning/review repository checkpoint inspected: `5b86a781`  
Current implementation code reviewed: `f285a6d`  
Previous implementation baseline: `c4cf551`  
Fundamental-change review pending: no

Relevant builder commits since the previous implementation review:
- `f285a6d` — fix(renamer): resolve re-review findings R-011 through R-021 and update evaluation
- `5b86a781` — docs(status): record Tool 1 implementation HEAD f285a6d and review readiness

Project protocol commits made after the builder handoff are not Tool 1 implementation commits and do not change the reviewed implementation baseline.

## Builder-reported verification

Builder reports:
- 63 tests passing under Python 3.12.14 (`.venv/bin/pytest -v`, 1.03s)
- 260 real files evaluated from `sample-files/`
- 88 clean automatic proposals (33.8%)
- 172 files labelled as requiring human review (66.2%)
- 2 combination candidates held for splitting
- 0 collisions detected
- objective behavior categories: 61 automatic, 56 provisional, 131 review candidates, 12 unresolved

The detailed sample report records 247 review-reason triggers across the 172 flagged files:
- `WHERE is unresolved`: 141
- `WHAT is unresolved`: 80
- `WHEN is unresolved`: 13
- provisional WHEN: 7
- filename/folder year conflict: 5
- combination clue: 2

These trigger counts overlap; a file can have more than one.

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
- [ ] R-022 review-routing correction completed
- [ ] Corrected sample routing evaluation completed
- [ ] Accepted

## Resolved review history

R-001 through R-021 are considered resolved in implementation commit `f285a6d` unless a later regression reopens them. Their full descriptions remain recoverable from Git history and GitHub issue #1.

They covered CLI dry-run safety, unresolved WHAT behavior, `_edited` lifecycle, Baserow authority and write safety, Vedabase, online location lookup, sibling grammar, service boundaries, Python 3.12, documentation/checkpoints, scripture WHAT preservation, combination preservation, direct WHERE evidence, conservative fuzzy/geocoding behavior, regional dates, extension-agnostic processing, tracking-ID/length/transliteration safety, enrichment interfaces, finalization collisions, review validation, and objective sample reporting.

## Active review finding — 2026-09-13

### R-022 — Missing/downstream metadata is being conflated with immediate human review

Severity: **WORKFLOW / ACCEPTANCE BLOCKER**

The 260-file sample currently reports 172 files (66.2%) as requiring human review. Inspection of the actual review triggers shows that most of this is caused by the parser mechanically adding a human-review reason whenever a field is unresolved or provisional:

- 141 unresolved WHERE triggers;
- 80 unresolved WHAT triggers;
- 13 unresolved WHEN triggers;
- 7 provisional WHEN triggers;
- only 5 explicit filename/folder year conflicts;
- 2 combination clues intended for later Tools 5/6.

This does not match the progressive pipeline defined by the finalized build plan. Tool 1 is explicitly allowed to make safe partial/provisional improvements, preserve missing fields without inventing them, and hand unresolved evidence to later tools. Missing metadata is often **work for a later deterministic stage**, not a request for a human decision now.

Examples:

- missing WHERE can often continue to Tool 2/3 and later Renamer enrichment;
- unidentified class WHAT belongs to Tool 7 rather than immediate human review;
- provisional date selections may be used automatically when the selected interpretation and alternatives are retained;
- a combination clue should be routed to Tools 5/6 and should not by itself create a human-review task;
- a useful but incomplete filename may keep its source wording + tracking ID and continue through the pipeline.

The current implementation in `renamer/parser/engine.py` unconditionally adds review reasons for provisional/unresolved WHEN, unresolved WHAT, and provisional/unresolved WHERE, and `RenamePlanner` converts any non-empty review-reason list into `needs_review=True`. As a result, diagnostic incompleteness is being treated as a human queue.

Required correction:

1. Separate **diagnostic/enrichment state** from **human-review-required state**.
2. Do not mark a file for immediate human review merely because WHEN/WHAT/WHERE is missing when the file can safely continue and a later tool owns that enrichment.
3. Do not mark a provisional field for human review merely because it is provisional when the build plan explicitly permits that provisional selection for automatic processing.
4. Route downstream-owned work explicitly, e.g. missing class WHAT → Tool 7; combination → Tools 5/6; Media/WHERE/date enrichment → Tools 2/3/4 as appropriate.
5. Reserve human review for cases where a human choice is actually needed to proceed safely: unresolved competing interpretations, meaningful contradictions that downstream evidence cannot yet resolve, unsafe/collision/validation conditions, or explicit manual-review policy.
6. Keep all unresolved/provisional evidence visible in logs and the review portal even when it is not an immediate human task.
7. Add tests proving that incomplete-but-safe files continue without `needs_review=True`, while genuine ambiguity/conflict still enters the human queue.
8. Rerun the 260-file sample and report separate counts for at least:
   - safe automatic/continue;
   - downstream enrichment required;
   - human review required now;
   - blocked/error;
   - combination/downstream split routing where useful.

Do not optimize for an artificially low review percentage. The goal is to classify work correctly so the human queue contains genuine decisions rather than routine missing metadata.

No user/archive-policy decision is needed for R-022; it follows directly from the finalized progressive-processing rules.

## Known defects / limitations

Active finding: R-022.

The existing `172 human review` figure should **not** be treated as the expected production review burden until R-022 is corrected and the sample rerun.

## Open questions / contradictions

None currently requiring user input.

## Next milestone

Builder addresses R-022 without changing the finalized build plan, adds routing/regression tests, reruns the 260-file evaluation with the corrected categories, commits and pushes all work, records the final reachable HEAD, and returns the tool to `READY_FOR_REVIEW`.

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
