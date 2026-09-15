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
Builder handoff head reviewed: `ff9af4a26142954b4adea2083f56bf04739e957f`  
Second-round correction implementation commit: `0ef377fb8cad3ba3ebcb30443bfc6259b31daee8`  
Current `main` at review: `8c29fd76bf20838918823b30c9ed4c1279680d0f`  
Last planning/review update: 2026-09-15

## Review checkpoint

The Builder substantially resolved R-008 through R-012. The following corrections were independently confirmed in the branch:

- structured confirmed-Media WHERE parsing now preserves normal hyphenated places;
- Case C uses place + country ambiguity checks and candidate grouping/provenance is deterministic;
- `country_iso2` is covered by reference integrity validation;
- Tool 2 decision is snapshotted in Tool 3 audit data and Case B/C explainability was expanded;
- the representative evaluation now separately checks confirmed-Media authority and reports zero overwrites;
- committed walkthrough/CLI documentation was corrected.

Required CI run #71 (`Python 3.12 tests`) succeeded on PR merge ref `fe78028619c5123c9c4aedae05364c6e0aca0afc` with **214 passed, 2 warnings**; helper shell validation and package build also passed.

Acceptance remains blocked by two targeted correctness findings below. No user policy decision is required.

## Third-round independent review findings

### R-013 — Confirmed Media WHERE can still be downgraded to provisional or a real country conflict can be mislabeled as corroboration

Confirmed Media values are authoritative and Tool 3 must not redundantly apply the same value as provisional schedule evidence. A remaining Case-A path violates that rule when Tool 1 already knows the place but is missing the country.

Concrete case:

```text
Tool 1: exact date + Springfield, country missing
Tool 2: EXISTING_MEDIA_MATCH, confirmed WHERE Springfield-AU
schedule: Springfield-AU
```

Current behavior can let Case A create provisional `Springfield-au`; `_apply_media_authority_guard()` permits it because it equals the confirmed Media value, and Tool 3 can then apply the country to Tool 1 as `PROVISIONAL`. The build plan requires Tool 2/Tool 1 to remain the owner of applying the authoritative Media value; Tool 3 should only record schedule corroboration.

A second variant is also wrong:

```text
Tool 1: exact date + Springfield, country missing
Tool 2: confirmed WHERE Springfield-AU
schedule: Springfield-US
```

Case A can first create provisional `Springfield-us`; the Media guard suppresses that value, but because Case A did not record a conflict, the guard converts the result to `CORROBORATED`. This hides a real schedule-vs-confirmed-Media country conflict.

There are two related structured-location correctness details to fix at the same time:

- Case A currently marks `place_comparison=CONFLICT` for a country-only disagreement even when the canonical place agrees, and can leave `country_comparison` unset. Field comparison states must be computed independently so same-place/different-country evidence is represented as place `AGREES`, country `CONFLICT`.
- `parse_structured_where()` describes its suffix as an ISO country code, but currently `_norm_country()` accepts any two alphabetic letters. Use the existing recognized ISO validation/reference so a hyphenated place ending in an arbitrary two-letter token is not accidentally truncated as a country suffix.

Required correction:

- treat confirmed Media WHERE/country as an effective higher-authority constraint whenever the corresponding local dimension is missing;
- if schedule agrees with an already-confirmed Media value, record corroboration but do not emit/apply redundant provisional enrichment for that dimension;
- if schedule disagrees with confirmed Media, return/preserve `SCHEDULE_CONFLICT` with explicit provenance instead of converting the result to `CORROBORATED`;
- compute Case-A place/country comparison states independently;
- validate trailing country suffixes against recognized ISO-2 codes;
- add regressions for confirmed country missing locally + agreeing schedule, confirmed country missing locally + conflicting schedule, Case-A country-only conflict comparison states, and a non-ISO two-letter place suffix.

### R-014 — Arbitrary batch processing errors are still classified as `INSUFFICIENT_EVIDENCE`

R-012 required a truthful distinct failure state for a per-file processing error when the schedule reference itself is healthy. The implementation correctly stopped returning `REFERENCE_UNAVAILABLE`, but now maps the same operational error to `INSUFFICIENT_EVIDENCE`.

That decision has a specific semantic meaning in the build plan: neither WHEN nor WHERE provides enough information for a bounded schedule query. A missing registry record, unexpected application exception, or other processing failure is not evidence insufficiency. The current regression `test_r012_batch_error_does_not_produce_reference_unavailable_when_reference_healthy` explicitly asserts `INSUFFICIENT_EVIDENCE`, so it currently locks in the wrong classification.

Required correction:

- introduce/use a truthful distinct processing-error outcome or equivalent structured batch-error representation that cannot be confused with `INSUFFICIENT_EVIDENCE`, `REFERENCE_UNAVAILABLE`, or `NO_SCHEDULE_SUPPORT`;
- keep the healthy reference checksum/provenance when available;
- preserve batch isolation so later files continue;
- update the regression to assert the distinct processing-error semantics;
- ensure CLI/portal rendering remains safe for the new outcome if an enum state is added.

## Evidence already verified

The following should be preserved while correcting R-013/R-014:

- R-001 through R-012 are otherwise substantially resolved;
- current branch is based on the current `main` used by PR #26;
- PR #26 is open and mergeable;
- CI #71 is green with 214 tests;
- Tool 1 and Tool 2 accepted regression suites remain green;
- 260-file live Tool 2 → Tool 3 evaluation reports 44 provisional enrichments, zero local high-authority overwrites, and zero confirmed-Media overwrites.

## Open questions / contradictions

None requiring user input.

## Next milestone

Builder addresses R-013 and R-014 on the existing `tool-3-implementation` branch / PR #26, adds the targeted regressions, updates this status with the actual final branch head, obtains green required CI, and returns `READY_FOR_REVIEW` for the final independent review pass.
