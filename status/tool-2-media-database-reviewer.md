# Tool 2 — Media Database Reviewer Implementation Status

Build plan: `docs/tool-2-media-database-reviewer-build-plan.md`  
Authoritative live-data amendment: `docs/tool-2-media-database-reviewer-live-data-amendment.md`  
Project-wide Baserow policy: `docs/baserow-live-data-policy.md`  
Implementation issue: #2  
Implementation PR: #19  
Protocol: `docs/implementation-protocol.md`

## Current state

Status: `ACCEPTED`

Post-acceptance architecture note (2026-09-17): `docs/baserow-access-boundary-amendment.md` confirms Tool 2's accepted read-only Baserow lookup/reconciliation role. Tool 1 uses it while producing the final filename, and Tool 4 uses it for the fresh existing-item/candidate gate before synchronization. Tool 2 remains technically incapable of mutations.

Implementation branch: `tool-2-implementation`  
Implementation PR: #19  
Accepted implementation code/docs commit: `fb43685b529e69d10a1642498abe3c7d3775290e`  
Builder handoff/status head independently reviewed: `305b2e1d22ccbf04625b4b0b4d50db1500bc0e2e`  
Planning acceptance recorded: 2026-09-14

GitHub Actions run #57 on the reviewed PR head passed the required `Python 3.12 tests` job with **157 passed, 2 warnings**, helper-script validation, and package-build success.

Committed walkthrough artifacts:
- Tool 2 authoritative walkthrough: `docs/tool-2-media-database-reviewer-walkthrough.md`
- Repository root walkthrough: `walkthrough.md`
- Tool 1 historical walkthrough: `docs/tool-1-renamer-walkthrough.md`

## Acceptance decision

Tool 2 is accepted after independent review of the actual PR branch, correction commits, regression tests, live-data semantics, Tool 1 integration behavior, fresh 260-file live/dry-run evidence, and required GitHub CI.

Acceptance specifically verifies that:

- current Baserow state is consulted live for operational decisions; persisted rows remain audit/history only;
- automatic association requires explicit high-specificity evidence rather than a numeric score threshold;
- database failure cannot become a valid no-match/new-media decision;
- current-row human confirmation and new-media confirmation are live-revalidated;
- confirmed Tool 2 metadata automatically flows into Tool 1 Renamer Enrich through the supported service/CLI path;
- probable, multiple, conflicting, insufficient, and unavailable candidate metadata does not leak into filenames;
- completed live no-match may mark `baserow_check_complete=True` without inventing metadata;
- the Tool 1 ↔ Tool 2 enrichment path is idempotent;
- the review portal has a single enrichment-handoff owner and does not duplicate enrichment audit actions;
- contradictory defer-after-confirmed transitions are rejected, while unconfirmed defer state is cleared safely;
- Tool 2 remains read-only to Baserow; Tool 4 remains the only writer.

## Resolved findings

- **R-001** — session/batch snapshot reuse removed; current decisions request live state per operation.
- **R-002** — automatic association requires strict high-specificity exact evidence; partial dates, fuzzy places, generic WHAT and unrelated populated titles cannot auto-confirm. Regressions 47–51.
- **R-003** — country contradictions are considered in WHERE comparison.
- **R-004** — exact scripture identity/range grammar corrected.
- **R-005** — compatible partial Tool 1 dates no longer become full-date conflicts.
- **R-006** — travel corroboration no longer uses broad same-month matching.
- **R-007** — progressive Tool 3 routing is separated from immediate human review.
- **R-008** — representative live evaluation uses a fresh 260-file Tool 1 population.
- **R-009** — branch/PR/CI handoff is healthy.
- **R-010** — portal tests are dependency-injectable and credential-independent.
- **R-011** — explicit 404 is distinguished from database unavailability and transport failure cannot directly confirm new media.
- **R-012** — targeted live candidate retrieval is pagination-complete, covers the evidence routes needed for no-match decisions, and `confirm_new` re-runs complete live reconciliation. Regressions 52–55.
- **R-013** — normal Tool 2 review/CLI/batch path automatically hands safe confirmed/completed evidence to Renamer Enrich; confirmed title rendering, no-match `_edited` lifecycle, unconfirmed isolation, CLI bridge, and filename idempotency are covered by regressions 56–60 and the live smoke test.
- **R-014** — portal single-owner enrichment handoff established; contradictory deferral of confirmed associations is prohibited; deferral on unconfirmed records resets `renamer_enrichment`/selection state; defense-in-depth prevents deferred or unavailable records from being enriched. Regressions 61–63.
- **R-015** — committed Tool 2 walkthrough added and root walkthrough updated while preserving Tool 1 history.

## Final verified tests / CI

GitHub Actions run #57:

```text
Python 3.12 tests: SUCCESS
pytest: 157 passed, 2 warnings
helper shell validation: PASS
uv build: PASS
```

## Final live sample evaluation

Fresh live read-only evaluation across all 260 representative `sample-files/` against live Baserow:

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

### Confirmed Tool 1 → Tool 2 → Renamer Enrich evidence

- Tracking ID: `f7903be1`
- Current filename: `HH Kadamba Kanana Swami - SB 3.6.6 - Sweden - 27_8_15.mp3`
- Before Tool 2: `2015-08-27_KKS_SB-3-6-6_Sweden-se_ID-f7903be1.mp3`
- Live Baserow match: row `2335`, date `2015-08-27`, WHAT `SB 3.6.6`, place `Sweden`, title `SB 3.6.6 class`
- After Tool 2 handoff: `2015-08-27_KKS_SB-3-6-6-class_Sweden-se_ID-f7903be1.mp3`
- Registry state: `status="enriched"`, `needs_review=False`, `baserow_check_complete=True`
- Unconfirmed candidate isolation: 0 of 220 unconfirmed files received candidate title/location metadata.
- New-media lifecycle: all 39 `NEW_MEDIA_CANDIDATE` rows propagated `baserow_check_complete=True` without invented title/location metadata.

## Open questions / contradictions

None.

## Next milestone

Merge accepted PR #19 into protected `main`, close implementation issue #2 as completed, then proceed to Tool 3 planning/implementation when its definition is ready.

## Post-acceptance practical correction — country-only evidence

The `KKS DUBEN 2008 MP3/02 KKS. SB. 3.1.20.mp3` test established Czech Republic / `cz` without a city. Tool 2 previously skipped country comparison whenever the local city was blank, so unrelated April rows in the United Kingdom and Netherlands remained false date-only candidates.

Tool 2 now compares known countries independently of city availability. A foreign-country row supported only by a partial-date overlap is discarded as duplicate-search noise; direct identity or matching WHAT evidence still retains a contradictory row for review. This preserves the leading Baserow-country rule without requiring Tool 1 to invent a location.
