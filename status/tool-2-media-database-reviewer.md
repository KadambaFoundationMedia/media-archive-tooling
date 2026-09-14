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
Builder handoff head reviewed: `1ecb95c461c1391256acc8deb6562f0f4ff025a8`  
Primary correction commit reviewed: `0b349b998501b4f430c984e2b88f0247c0152fc8`  
Last planning/review update: 2026-09-14

GitHub CI run #45 on the handoff head passed the required `Python 3.12 tests` job with **140 passed, 2 warnings**, helper-script validation, and package-build success. The branch is current with `main` (`behind_by=0`) and PR #19 is mergeable.

The latest Builder corrections successfully remove session-wide snapshot reuse, tighten scripture range identity, and distinguish explicit row absence from Baserow unavailability. Acceptance is still blocked by two deeper correctness findings found during independent review.

## Review checkpoint

Planning/review inspected the actual correction commit, current provider/service/engine code, new tests 42–46, fresh live-evaluation report, branch divergence, and GitHub CI rather than relying on the Builder summary.

### Corrections verified from the previous round

- **R-001 freshness core fixed:** `_current_snapshot` operational reuse is removed and `review_batch()` now requests live state per file/decision.
- **R-004 scripture range core fixed:** exact canonical scripture identity is required for `AGREES`; overlapping-but-different ranges no longer auto-match; dotted extra numeric and descending ranges are rejected.
- **R-011 availability-state core fixed:** missing credentials/HTTP/transport failures raise unavailable semantics; explicit HTTP 404 alone means a row is absent; `confirm_new` no longer treats transport failure as an empty successful search.
- **R-009/R-010 CI isolation remains healthy:** credential-free GitHub Actions now passes.

## Active review findings

### R-002 — Automatic-association predicates are explicit but still too permissive

Status: **REOPENED — BLOCKING / false-association risk**

The build plan permits automatic `EXISTING_MEDIA_MATCH` only from unique **high-specificity exact evidence** with no material contradiction. The current predicates use broad comparison state `AGREES`, which also represents compatibility/fuzzy evidence rather than exact identity.

Current problems:

- `_compare_dates()` returns `AGREES` for compatible partial dates such as `2015-02-DD` vs `2015-02-15`; Rule 2/Rule 3 then treat this as the required exact full WHEN.
- `_compare_places()` returns `AGREES` for substring/fuzzy location similarity; Rule 2 then treats this as exact normalized WHERE.
- Rule 2/Rule 3 do not explicitly require a sufficiently specific WHAT; a generic WHAT that happens to compare as `AGREES` can participate in automatic association.
- Rule 3 currently treats **any non-empty database title** as corroboration (`bool(title)`), even if the title provides no independent agreement with the local evidence. Mere field presence is not corroboration.

Required correction:

- automatic predicates must distinguish **exact/high-specificity evidence** from compatible/fuzzy/supporting evidence;
- Rule 2 requires exact full date + specific WHAT + exact normalized place/country;
- Rule 3 requires exact full date + specific WHAT + a genuinely matching independent corroborating field/reference, not merely a populated title/category field;
- compatible partial dates, fuzzy/substring locations, generic WHAT, and unrelated title presence may support ranking/probable state but must never authorize automatic `EXISTING_MEDIA_MATCH`;
- add negative regressions for partial-date agreement, fuzzy-place agreement, generic WHAT, and unrelated non-empty title; add positive regressions for genuinely corroborated Rule 3 evidence.

### R-012 — Targeted live candidate search is not completeness-preserving and can produce false `NEW_MEDIA_CANDIDATE`

Status: **OPEN — BLOCKING / duplicate-row risk**

The live-data amendment allows targeted querying only if the current decision is still based on a **complete live search of the relevant candidate space**. The current targeted provider and `confirm_new` revalidation can miss existing rows and then incorrectly conclude that the item is new.

Current problems:

- `_fetch_targeted_media_rows()` requests `size=100` for each Baserow `search=` query but does not follow the returned `next` pagination URL. A matching row on page 2+ is invisible.
- `search_media_candidates_live()` also returns only the first `size=100` page and does not paginate.
- `confirm_new` performs a fresh search using only `local_what or local_date`; when WHAT exists it does not rerun the full candidate strategy (date/source/file/location context). An existing row discoverable by date/place or another candidate route can therefore be missed.
- for partial dates ending in `DD`, `_fetch_targeted_media_rows()` deliberately skips the date query and does not add a place query. A partial-date + place input can still be treated by the engine as discriminating enough for `NEW_MEDIA_CANDIDATE`, even though the live candidate search may not have searched the discriminating place/date evidence.
- `load_snapshot()` can mark a snapshot `LIVE_CURRENT` when the Media table ID is absent but another table ID is configured. A Media no-match/new-item decision must never be considered complete when the authoritative Media table was not queried.

Required correction:

- every narrowed Baserow candidate query must paginate to completion (or otherwise use a server-side query proven complete for the criteria);
- `confirm_new` should reuse/rerun the same complete live candidate-retrieval/reconciliation path as a current Tool 2 review, and finalize only if that fresh review still yields a valid `NEW_MEDIA_CANDIDATE`;
- targeted retrieval must cover every evidence combination that can authorize a complete no-match/new-item decision; if evidence cannot be searched completely, return insufficient/unavailable rather than new;
- missing/unusable Media table configuration must yield database unavailable/incomplete for Media decisions;
- add regressions with a matching candidate on a second API page, a candidate discoverable by date/place but not WHAT, partial-date+place input, and missing Media table configuration.

## Resolved findings

- **R-001** — session/batch snapshot reuse removed; current decisions request live state per operation. (Completeness of targeted retrieval is tracked separately as R-012.)
- **R-003** — country contradictions are considered in WHERE comparison.
- **R-004** — exact scripture identity/range grammar corrected.
- **R-005** — compatible partial Tool 1 dates no longer become full-date conflicts. (They still must not count as exact association evidence; tracked under reopened R-002.)
- **R-006** — travel corroboration no longer uses broad same-month matching.
- **R-007** — progressive Tool 3 routing is separated from immediate human review.
- **R-008** — representative live evaluation uses a fresh 260-file Tool 1 population.
- **R-009** — branch/PR/CI handoff is healthy.
- **R-010** — portal tests are dependency-injectable and credential-independent.
- **R-011** — explicit 404 is distinguished from database unavailability and transport failure cannot directly confirm new media.

## Current verified tests / CI

GitHub Actions run #45 on head `1ecb95c461c1391256acc8deb6562f0f4ff025a8`:

```text
Python 3.12 tests: SUCCESS
pytest: 140 passed, 2 warnings
helper shell validation: PASS
uv build: PASS
```

Passing CI does not override the semantic findings above; the missing cases are not covered by the current suite.

## Last live sample evaluation

The Builder's latest fresh live read-only evaluation across the 260 representative `sample-files/` reported:

```text
total files: 260
confirmed existing matches: 1
probable existing matches: 14
multiple candidates: 109
new-media candidates: 76
insufficient evidence: 39
conflicts: 21
database failures: 0
human-review-required-now: 9
review-required-overall: 144
downstream-to-Tool-3 count: 135
confirmed title/metadata enrichments: 1
```

These counts must be rerun after R-002 and R-012 because both corrections can materially change automatic-match and new-media decisions.

## Open questions / contradictions

None requiring user input. These are implementation-correctness issues under already-finalized Tool 2 and live-Baserow policy.

## Next milestone

Builder resumes Tool 2 on PR #19 and addresses **R-002 (reopened)** and **R-012**. It must add the required regressions, rerun the complete test suite, rerun the fresh 260-file live read-only evaluation, push the corrected head, and wait for the required GitHub CI to pass before returning `READY_FOR_REVIEW`.

Do not weaken the finalized build plan or live-data amendment to fit the current implementation.
