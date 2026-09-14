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
Builder handoff head reviewed: `5ff2be9150397c1c072cf8451e192e8dbd07c0e2`  
Primary R-002/R-012 correction commit reviewed: `1158ad5fc9a88c7a400a62f68037e388be3ffba4`  
Last planning/review update: 2026-09-14

GitHub CI run #50 on PR #19 passed the required `Python 3.12 tests` job with **149 passed, 2 warnings**, helper-script validation, and package-build success. The R-002 and R-012 corrections are materially present and their regressions pass.

Acceptance is nevertheless blocked by the final Tool 1 ↔ Tool 2 integration test requested by the user. The domain pieces exist, but the normal automatic Tool 2 review path does not currently hand confirmed enrichment back to the Renamer.

## Active review findings

### R-013 — Tool 1 ↔ Tool 2 confirmed-enrichment bridge is not wired into the normal workflow

Status: **OPEN — BLOCKING / end-to-end integration gap**

The finalized Tool 2 plan requires confirmed Media metadata to be consumable by Renamer Enrich, and the user explicitly requires the combined Tool 1 + Tool 2 workflow to double-check parsed filenames against live Media candidates and enrich the proposed filename when a confirmed Media match is found.

Current implementation has the individual components but not a complete normal workflow:

- `MediaDatabaseReviewService.review_file()` and `review_batch()` perform live reconciliation and persist Tool 2 results, but they do not apply confirmed enrichment to the Tool 1 proposal.
- `MediaDatabaseReviewService.apply_enrichment_to_renamer()` exists and correctly constructs `EnrichmentEvidence` and calls `RenamerApplicationService.apply_enrichment()`.
- The review portal calls `apply_enrichment_to_renamer()` after an explicit human Media action, so human-confirmed candidates can flow into Tool 1.
- The standard `media-archive media-db-review` CLI only reviews/persists/prints results. It does not call the enrichment bridge for automatic `EXISTING_MEDIA_MATCH` results or completed usable no-match results.
- The standard Tool 1 `media-archive renamer ...` command does not invoke Tool 2. Therefore an automatic confirmed Tool 2 match found during normal CLI/batch use can remain stored as Tool 2 evidence without updating the Renamer proposal/title.

Required correction:

- Provide a supported application-service/CLI workflow that performs Tool 2 review from Tool 1 structured registry state and then automatically hands safe completed Tool 2 evidence to Renamer Enrich.
- For an automatic `EXISTING_MEDIA_MATCH`, the resulting Tool 1 proposal must contain the confirmed Baserow metadata/title according to Tool 1 canonical rendering and length policy.
- A valid completed no-match may propagate `baserow_check_complete=true` (including the `_edited` lifecycle) without inventing title/location metadata.
- `PROBABLE_EXISTING_MEDIA`, `MULTIPLE_CANDIDATES`, unresolved conflicts, `INSUFFICIENT_EVIDENCE`, and `DATABASE_UNAVAILABLE` must not copy candidate-only metadata into the filename.
- Keep Baserow read-only. This integration may update the local registry/proposed filename only; Tool 4 remains the Baserow writer.
- Do not require a developer to hand-call an internal Python method to obtain the enrichment. The normal supported Tool 2 command/service path must expose the handoff.
- Preserve live-current semantics: the Tool 2 decision used for automatic enrichment must come from the current live read, never a persisted Baserow cache.

Required regression/integration coverage:

1. Tool 1 initial proposal → Tool 2 live confirmed existing row with title → automatic Renamer ENRICH proposal contains the title and `baserow_check_complete=true`.
2. A probable/multiple/conflicting candidate does not alter the Tool 1 WHAT/title.
3. A completed live no-match marks the Baserow check complete but does not invent enrichment values.
4. The supported CLI/batch path exercises the bridge, not only a direct unit call to `apply_enrichment_to_renamer()`.
5. Rerunning the same completed result is idempotent and does not duplicate title text in WHAT or the proposed filename.

Required live smoke test before acceptance:

- use a fresh Tool 1 registry for the 260 representative `sample-files/`;
- run Tool 2 against live Baserow;
- for at least the confirmed existing match currently observed in the live sample evaluation, capture the Tool 1 proposed filename before Tool 2 and after Tool 2 handoff and verify the confirmed Baserow title/metadata is rendered by the Renamer;
- verify no probable/multiple/conflict candidate metadata is silently inserted into filenames;
- keep the test read-only to Baserow and dry-run for filesystem renames.

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

## Current verified tests / CI

GitHub Actions run #50 on PR head `5ff2be9150397c1c072cf8451e192e8dbd07c0e2`:

```text
Python 3.12 tests: SUCCESS
pytest: 149 passed, 2 warnings
helper shell validation: PASS
uv build: PASS
```

Passing CI does not close R-013 because the current suite tests the components but not the automatic Tool 1 → Tool 2 → Renamer Enrich workflow.

## Last live sample evaluation

The Builder's latest fresh live read-only Tool 2 evaluation across the 260 representative `sample-files/` reported:

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

These counts establish that there is at least one real confirmed existing match suitable for the required live end-to-end enrichment smoke test, but the previous evaluation did not prove that the standard Tool 2 workflow actually updated the Tool 1 proposed filename.

## Open questions / contradictions

None requiring user input. R-013 is an implementation/integration requirement consistent with the finalized Tool 2 plan and the user's explicit acceptance test.

## Next milestone

Builder addresses **R-013** on PR #19, adds the end-to-end regressions, runs the fresh live/dry-run Tool 1 + Tool 2 sample smoke test, records the before/after confirmed-match filename evidence, pushes the corrected head, and waits for required GitHub CI to pass before returning `READY_FOR_REVIEW`.

Do not start Tool 3 implementation until Tool 1 + Tool 2 end-to-end enrichment is independently verified and Tool 2 is accepted.
