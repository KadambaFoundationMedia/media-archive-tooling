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
Builder handoff head reviewed: `525e8ff176294ba3df35abd6c74326b5c865e9db`  
Last planning/review update: 2026-09-14

GitHub CI run #43 on handoff head `525e8ff...` passed the required `Python 3.12 tests` job with **135 passed, 2 warnings**, helper-script validation, and package build success. CI is therefore healthy. Acceptance is still blocked by semantic findings found during code review.

## Review checkpoint

The branch is current with `main` (`behind_by=0`) and PR #19 is mergeable. Planning/review inspected the actual provider, service, reconciliation engine, portal dependency-injection change, tests, and CI rather than relying on the Builder summary.

The R-010 portal test-isolation correction is valid: the portal now supports injected Media DB provider/service dependencies and credential-free CI passes. However, deeper live-state semantics remain inconsistent with the authoritative live-data amendment.

## Active review findings

### R-001 — Live-current Baserow policy still not fully implemented

Status: **REOPENED — BLOCKING**

The authoritative amendment requires **every independent Tool 2 database-dependent decision** to query current Baserow and explicitly forbids session-wide snapshot reuse and long-batch full-table reuse.

Current code still violates that rule in two places:

- `BaserowSnapshotProvider.load_snapshot()` keeps `_current_snapshot` and returns it on later calls when `force_refresh=False`;
- `MediaDatabaseReviewService.review_batch()` loads one snapshot once and reuses it for every file in the batch.

That means a collaborator update made after the first decision can remain invisible to later independent decisions.

Required correction:

- remove operational `_current_snapshot` reuse for current decisions;
- `review_file()` must perform a fresh live read/query for each independent review operation;
- `review_batch()` must process each file using per-decision live queries/read state rather than one session-wide full-table snapshot;
- persisted/audit snapshots may remain for history only and must never be current operational input;
- add regressions proving two sequential reviews see changed Baserow data and a batch does not reuse a stale first-read result.

### R-004 — Scripture comparison is structural but still too permissive for identity

Status: **REOPENED — HIGH / automatic-association risk**

The new parser removed simple string-prefix false matches, but `_compare_what()` currently marks any overlapping verse ranges as `AGREES`. Example: `BG-1-1-3` and `BG-1-3-5` overlap at verse 3 but are not the same specific WHAT. Because Rule 2/Rule 3 treat `AGREES` as exact high-specificity evidence, range overlap can incorrectly authorize `EXISTING_MEDIA_MATCH`.

The local scripture parser also accepts forms that conflict with the Tool 1 canonical grammar, including a dotted extra BG component (`BG 13.8.12`) as though it were a range, and it normalizes descending ranges with `min/max` rather than rejecting them.

Required correction:

- exact automatic-association predicates require exact canonical scripture identity, not merely overlapping verse coverage;
- range overlap may be retained as supporting/probable context if useful, but must not be the `AGREES` state consumed by Rule 2/Rule 3;
- reuse Tool 1 canonical scripture parsing/normalization where practical, or enforce the same grammar exactly;
- reject dotted extra numeric BG components and descending ranges rather than silently normalizing them;
- add regressions for overlapping-but-different ranges, `BG 13.8.12`, and descending ranges.

### R-011 — Live revalidation conflates database failure with "row absent" and can falsely confirm new media

Status: **OPEN — BLOCKING / collaborator-race correctness**

`fetch_media_row_live()` currently returns `None` for several materially different states: no credentials, HTTP 404, other HTTP failures, and transport exceptions. `apply_human_decision(confirm_existing)` treats `None` as proof that the row was deleted. The portal test added for R-010 now codifies the misleading `no longer exists in Baserow` result for an unconfigured provider.

More critically, `search_media_candidates_live()` returns `[]` both for a valid live search with zero matches **and** for no credentials / non-200 response / transport failure. `confirm_new` treats that empty list as a successful live no-match, then sets `database_state="LIVE_CURRENT"`, `live_read_complete=True`, `baserow_check_complete=True`, and finalizes `NEW_MEDIA_CANDIDATE`.

That directly violates the live-data amendment: database unavailable must remain distinct from valid live no-match, and failure must never produce a confirmed new-item decision.

Required correction:

- provider current-row and candidate-search APIs must distinguish at least `found`, explicit `not_found`/valid empty result, and `unavailable/failed`;
- only an explicit live 404 may mean a selected row no longer exists;
- missing credentials, auth/HTTP failure, timeout, transport error, or incomplete query must produce unavailable/incomplete state and `baserow_check_complete=false`;
- `confirm_new` must finalize only after a complete successful live search returns no plausible row;
- update the portal credential-absent test to expect database-unavailable/incomplete semantics, not a deletion claim;
- add a regression proving `confirm_new` cannot succeed when live search is unavailable.

## Resolved findings

- **R-002** — numeric score no longer authorizes automatic association; explicit predicates are used.
- **R-003** — country contradictions are considered in WHERE comparison.
- **R-005** — compatible partial Tool 1 dates no longer become full-date conflicts.
- **R-006** — travel corroboration no longer uses broad same-month matching.
- **R-007** — progressive Tool 3 routing is separated from immediate human review.
- **R-008** — fresh 260-file live Baserow evaluation completed.
- **R-009** — branch/PR/CI handoff is now healthy; CI run #43 passed.
- **R-010** — portal tests are dependency-injectable and credential-independent.

## Current verified tests / CI

GitHub Actions run #43 on head `525e8ff176294ba3df35abd6c74326b5c865e9db`:

```text
Python 3.12 tests: SUCCESS
pytest: 135 passed, 2 warnings
helper shell validation: PASS
uv build: PASS
```

The passing suite does not override the semantic findings above; new regressions are required for R-001, R-004, and R-011.

## Last live sample evaluation

The Builder's fresh live read-only evaluation across the 260 representative `sample-files/` reported:

```text
total files: 260
confirmed existing matches: 1
probable existing matches: 31
multiple candidates: 101
new-media candidates: 41
insufficient evidence: 17
conflicts: 69
database failures: 0
human-review-required-now: 53
review-required-overall: 201
downstream-to-Tool-3 count: 148
confirmed title/metadata enrichments: 1
```

This evaluation must be rerun after the reopened live-query and scripture-equivalence corrections because those changes can alter decision counts.

## Open questions / contradictions

None requiring user input. These are implementation-correctness issues under already-finalized policy.

## Next milestone

Builder resumes Tool 2 on PR #19 and addresses **R-001 (reopened), R-004 (reopened), and R-011**. It must add the required regression tests, rerun the complete suite, rerun the fresh 260-file live read-only evaluation, push the corrected head, and wait for the required GitHub CI to pass before returning `READY_FOR_REVIEW`.

Do not modify the finalized build plan or live-data amendment to fit the current implementation.
