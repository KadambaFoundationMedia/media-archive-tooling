# Tool 2 — Media Database Reviewer Implementation Status

Build plan: `docs/tool-2-media-database-reviewer-build-plan.md`  
Authoritative live-data amendment: `docs/tool-2-media-database-reviewer-live-data-amendment.md`  
Project-wide Baserow policy: `docs/baserow-live-data-policy.md`  
Implementation issue: #2  
Implementation PR: #19  
Protocol: `docs/implementation-protocol.md`

## Current state

Status: `CHANGES_REQUESTED`

Implementation branch: `tool-2-implementation`  
Implementation PR: #19  
Builder R-013 implementation commit reviewed: `a44a8736c8171a65b3c2ff15348d67741b471c48`  
Builder handoff/status head reviewed: `cb42ed8a2a72f92abee354cd80ae1a2e96efe862`  
Last planning/review update: 2026-09-14

GitHub Actions run #55 on PR #19 passed the required `Python 3.12 tests` job with **154 passed, 2 warnings**, helper-script validation, and package-build success. The Tool 1 → Tool 2 → Renamer Enrich bridge is materially present, and the fresh 260-file live smoke evidence demonstrates the intended confirmed-match filename enrichment.

Acceptance is blocked by one final application-flow defect found during independent review plus one handoff-artifact correction.

## Active review findings

### R-014 — Portal human-decision path can double-apply enrichment and a deferred decision can retain/reapply stale confirmed enrichment

Status: **OPEN — BLOCKING / review-state and audit correctness**

R-013 changed `MediaDatabaseReviewService.apply_human_decision()` so safe completed results automatically call `apply_enrichment_to_renamer()` by default. The review-portal route still explicitly calls `apply_enrichment_to_renamer()` immediately after `apply_human_decision()`.

Consequences:

- a portal `confirm_existing` / `confirm_new` can apply the same enrichment twice and record duplicate `enrich` audit actions even though only one human action occurred;
- `RenamerApplicationService.apply_enrichment()` always records an `enrich` review action, so this is not merely harmless duplicate computation;
- more importantly, `apply_human_decision(action="defer")` changes the decision to `INSUFFICIENT_EVIDENCE` and sets `baserow_check_complete=False`, but does not clear a previously confirmed `result.renamer_enrichment`; because the R-013 auto-handoff condition is `result.renamer_enrichment.confirmed or result.baserow_check_complete`, a defer performed on a previously confirmed stored result can still reapply stale confirmed metadata after the decision has been deferred;
- the Tool 2 portal always exposes a `Defer Decision` action, including when a stored result exists.

Required correction:

- establish one single enrichment-handoff owner for the portal path; do not call the bridge twice;
- a deferred/unconfirmed Tool 2 decision must not trigger or preserve a newly applied confirmed handoff as though it were still confirmed;
- define the safe state transition for deferring a previously confirmed association. At minimum the current action must not reapply stale confirmed enrichment. If rollback of already-applied automatic enrichment is intentionally out of scope, prevent/disable the contradictory defer transition after confirmed enrichment rather than silently producing inconsistent state;
- add regressions proving one portal confirmation produces one enrichment audit event and that defer cannot trigger confirmed enrichment from stale stored result state;
- retain the R-013 automatic CLI/batch behavior and live-revalidation semantics.

### R-015 — Claimed walkthrough update is not present on the pushed PR

Status: **OPEN — HANDOFF ACCURACY / documentation**

The Builder handoff states that the detailed `walkthrough.md` artifact was updated. The pushed `tool-2-implementation` branch still contains the old **Tool 1 — Renamer Implementation Walkthrough**, including the historical 48-test / 250-file Tool 1 results. `walkthrough.md` is not changed by PR #19.

Required correction:

- update the committed walkthrough artifact to accurately document Tool 2 / R-013 current behavior and the verified 154-test + 260-file live end-to-end evidence, or create a clearly named Tool 2 walkthrough under `docs/` and reference it from status;
- do not overwrite useful Tool 1 history without preserving it in an appropriate Tool 1-specific document;
- ensure the final handoff names the actual committed artifact.

## Resolved findings

- **R-001** — session/batch snapshot reuse removed; current decisions request live state per operation.
- **R-002** — automatic association requires strict high-specificity exact evidence; partial dates, fuzzy places, generic WHAT and unrelated populated titles cannot auto-confirm.
- **R-003** — country contradictions are considered in WHERE comparison.
- **R-004** — exact scripture identity/range grammar corrected.
- **R-005** — compatible partial Tool 1 dates no longer become full-date conflicts.
- **R-006** — travel corroboration no longer uses broad same-month matching.
- **R-007** — progressive Tool 3 routing is separated from immediate human review.
- **R-008** — representative live evaluation uses a fresh 260-file Tool 1 population.
- **R-009** — branch/PR/CI handoff is healthy.
- **R-010** — portal tests are dependency-injectable and credential-independent.
- **R-011** — explicit 404 is distinguished from database unavailability and transport failure cannot directly confirm new media.
- **R-012** — targeted live candidate retrieval is pagination-complete, covers the evidence routes needed for no-match decisions, and `confirm_new` re-runs complete live reconciliation.
- **R-013** — normal Tool 2 review/CLI/batch path automatically hands safe confirmed/completed evidence to Renamer Enrich; confirmed title rendering, no-match `_edited` lifecycle, unconfirmed isolation, CLI bridge, and filename idempotency are covered by regressions 56–60 and the live smoke test.

## Current verified tests / CI

GitHub Actions run #55 on PR head `cb42ed8a2a72f92abee354cd80ae1a2e96efe862`:

```text
Python 3.12 tests: SUCCESS
pytest: 154 passed, 2 warnings
helper shell validation: PASS
uv build: PASS
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

### Confirmed Match End-to-End Enrichment Evidence

- Tracking ID: `f7903be1`
- Current filename: `HH Kadamba Kanana Swami - SB 3.6.6 - Sweden - 27_8_15.mp3`
- Before Tool 2: `2015-08-27_KKS_SB-3-6-6_Sweden-se_ID-f7903be1.mp3`
- Live Baserow match: row `2335`, date `2015-08-27`, WHAT `SB 3.6.6`, place `Sweden`, title `SB 3.6.6 class`
- After Tool 2 handoff: `2015-08-27_KKS_SB-3-6-6-class_Sweden-se_ID-f7903be1.mp3`
- Registry state: `status="enriched"`, `needs_review=False`, `baserow_check_complete=True`
- Unconfirmed candidate isolation: 0 of 220 unconfirmed files received candidate title/location metadata.
- New-media lifecycle: all 39 `NEW_MEDIA_CANDIDATE` rows propagated `baserow_check_complete=True` without invented title/location metadata.

## Open questions / contradictions

None requiring user input. R-014 and R-015 are implementation/handoff corrections.

## Next milestone

Builder addresses R-014 and R-015 on PR #19, adds the focused portal-state regressions, pushes the corrected head, and waits for required GitHub CI success before returning `READY_FOR_REVIEW`.

Do not merge PR #19 or start Tool 3 implementation until these final corrections are independently verified.
