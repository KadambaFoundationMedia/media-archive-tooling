# Tool 3 — Travel Schedule Reviewer Implementation Status

Build plan: `docs/tool-3-travel-schedule-reviewer-build-plan.md`  
Implementation issue: #22  
Project architecture: `docs/project-implementation-architecture.md`  
Project Baserow policy: `docs/baserow-live-data-policy.md`  
Implementation protocol: `docs/implementation-protocol.md`

## Current state

Status: `CHANGES_REQUESTED`

Implementation branch: `tool-3-implementation`  
Implementation PR: #26 — `Tool 3 — Travel Schedule Reviewer implementation`  
Builder handoff head reviewed: `04bf623eedb91bc13c148c9387f834f63cab26df`  
Current `main` at review: `8c29fd76bf20838918823b30c9ed4c1279680d0f`  
Last planning/review update: 2026-09-14

Committed walkthrough artifacts:
- Tool 3 authoritative walkthrough: `docs/tool-3-travel-schedule-reviewer-walkthrough.md`
- Repository root walkthrough: `walkthrough.md`
- Tool 1 historical walkthrough: `docs/tool-1-renamer-walkthrough.md`
- Tool 2 historical walkthrough: `docs/tool-2-media-database-reviewer-walkthrough.md`

## Review checkpoint

Last planning build-plan merge: `75e5fbc61bdfac063c4fd087fd5fe3dba708edc7`  
Builder handoff head: `04bf623eedb91bc13c148c9387f834f63cab26df`  
Planner opened PR #26 because the READY_FOR_REVIEW handoff claimed an open Tool 3 PR but none existed in GitHub.  
PR #26 CI run #67 (`Python 3.12 tests`) succeeded on the GitHub merge ref: **197 passed, 2 warnings**, helper validation PASS, package build PASS.  
Acceptance remains blocked by the findings below; final CI must run again after corrections and branch synchronization.

## Independent review findings

### R-001 — Confirmed Tool 2 Media authority is not actually enforced in Tool 3 decisions

`TravelScheduleEngine.evaluate()` recognizes a confirmed Tool 2 result, but the confirmed Media WHEN/WHERE values are not incorporated as authoritative decision inputs. `is_confirmed_media` and `t2_res` are passed into Cases A/B/C but are not used there. The default `travel-review` CLI/batch path also runs without obtaining current Tool 2 context.

This leaves a dangerous case: Tool 1 may be missing WHEN or WHERE while Tool 2 has a confirmed authoritative Media value, yet Tool 3 can select a contradictory schedule-only provisional value because it reasons only from the Tool 1 parser state.

Required correction:
- protect confirmed Tool 2 WHEN/WHERE as authoritative recording evidence even when Tool 1 has not already incorporated those values;
- do not allow schedule enrichment to contradict or downgrade them;
- for an independent Tool 3 run that uses current Media authority, obtain current Tool 2 context through the Tool 2 application service rather than historical persisted state;
- when current Media context is unavailable, continue only as explicitly schedule-only/provisional and record that state;
- add regressions for at least:
  1. local date missing + confirmed Media date A + unique schedule date B -> no enrichment to B;
  2. local place missing + confirmed Media WHERE A + unique schedule WHERE B -> no enrichment to B.

### R-002 — The required 260-file acceptance evaluation did not use current Tool 2 Media context

The committed walkthrough reports `Downstream from Tool 2: 260 (Media context unavailable in test env)`. That is a useful schedule-only smoke run, but it does not satisfy Build Plan Section 36, which requires:

```text
fresh Tool 1 structured population
→ Tool 2 current Media review context
→ verified static travel_schedule reference
→ Tool 3 review
→ Tool 1 provisional enrichment where allowed
```

Required correction:
- rerun the 260-file acceptance evaluation with working live Tool 2 Media access;
- document Tool 2 decision/state counts, the actual downstream/routed population, Tool 3 results, applied provisional enrichments, and zero high-authority overwrites;
- keep a database-unavailable run labeled as such rather than treating it as the acceptance evaluation.

### R-003 — A known immutable schedule reference can be silently replaced

`travel-reference init` calls `ensure_reference(force_bootstrap=True)`, and `travel-review --force-bootstrap` exposes the same replacement path. If a verified local reference already exists, these paths can overwrite it with different remote contents without first surfacing the checksum discrepancy.

The build plan states that a remote checksum difference is an unexpected reference change and must be surfaced for deliberate investigation/acceptance, not silently replace the known reference.

Required correction:
- normal Tool 3 review must never replace a verified reference;
- `travel-reference init` should be create/bootstrap semantics when no verified reference exists, or refuse/report when one already exists;
- an unexpected remote change must go through explicit verify plus deliberate acceptance semantics if replacement support is provided at all;
- add regression coverage proving a verified reference is not overwritten by routine review/init when the remote checksum differs.

### R-004 — Malformed explicit schedule end dates can authorize enrichment

In `TravelScheduleIndex._build_index()`, a non-empty but invalid `end_date` parses to `None`, after which the row is treated as though the end date were missing and therefore as a single-day visit at `start_date`.

A malformed explicit range endpoint is invalid schedule data, not equivalent to a missing end date, and must not authorize automatic provisional WHEN/WHERE enrichment.

Required correction:
- distinguish an actually missing end date from a present-but-invalid end date;
- retain malformed rows as diagnostic evidence where useful but exclude them from authorizing enrichment/corroboration that requires a valid interval;
- add a regression for valid start + malformed non-empty end.

### R-005 — Semantic candidate grouping can lose alias-equivalent provenance

`group_candidates_semantically()` groups on the raw normalized place token and raw end-date string rather than the canonical place alias/effective interval semantics used elsewhere. Later code may decide multiple candidates represent one canonical place/interval and then select only `candidates[0]`, losing contributing row IDs and schedule text.

Required correction:
- group/select equivalent visits using the same canonical place semantics and an effective end date (`missing end` equivalent to `end == start` when valid);
- whenever equivalent rows support one selected candidate, preserve all contributing Baserow row IDs and original schedule text/provenance;
- add alias-equivalent and missing-end-vs-explicit-single-day regressions.

### R-006 — Valid explicit ranges longer than 366 days are silently absent from date lookups

The date index only expands ranges when their span is `<= 366` days. Longer valid explicit ranges are not indexed by date/month/year, so Cases A/C can incorrectly return `NO_SCHEDULE_SUPPORT` for dates that are actually inside the explicit range.

The build plan defines inclusive explicit-range semantics and does not impose a one-year validity cap.

Required correction:
- support date containment for valid long explicit ranges without requiring unbounded per-day expansion (an interval structure/fallback lookup is fine);
- add a regression for a valid explicit range longer than 366 days and a query date inside it.

### R-007 — Final delivery/configuration hygiene is incomplete

At handoff the implementation branch was three commits behind `main`, and the status incorrectly claimed an open PR. PR #26 now exists because planning opened it. In addition, Tool 3 configuration depends on `BASEROW_TRAVEL_SCHEDULE_TABLE_ID` (or a legacy alias), but `.env.example` does not document the setting needed to bootstrap the reference.

Required correction before the next READY_FOR_REVIEW handoff:
- synchronize `tool-3-implementation` with current `main` using the normal approved Git workflow;
- add `BASEROW_TRAVEL_SCHEDULE_TABLE_ID=` with a concise comment to `.env.example`;
- update this status with the actual PR #26 and final branch head;
- push all corrections and wait for required GitHub CI success on the corrected head/merge ref.

## Implementation summary reviewed

The current implementation already has substantial correct structure worth preserving:

1. static-reference bootstrap and local SHA-256 integrity verification;
2. zero-network normal reuse of a verified schedule reference;
3. Tool 2/shared Baserow provider reuse with read-only pagination;
4. typed Travel Reviewer models and explicit decision states;
5. date/location candidate indexing and no-unconstrained-guess behavior;
6. provisional Renamer enrichment state preservation;
7. registry audit persistence;
8. CLI/reference commands and portal evidence display;
9. 40 Tool 3 tests plus accepted Tool 1/2 regression suites;
10. successful required CI on the initial PR #26 review ref.

These findings are corrections to the implementation/acceptance evidence, not a request to redesign Tool 3.

## Open questions / contradictions

None requiring user input. The Builder should resolve R-001 through R-007 on the existing Tool 3 implementation branch and PR #26.

## Next milestone

Builder addresses R-001 through R-007, synchronizes with latest `main`, updates the 260-file acceptance evidence using current Tool 2 Media context, pushes the corrected branch, waits for green required CI on PR #26, and returns `READY_FOR_REVIEW` for another independent review pass.
