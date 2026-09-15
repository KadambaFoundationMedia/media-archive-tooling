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
Builder handoff head reviewed: `97d6b927b5ad76c3324a1310d09ffbb5ad0eff86`  
Correction implementation commit reviewed: `5a460da6b4576209cae221cf00fe5f589e6bbb2d`  
Current `main` at review: `8c29fd76bf20838918823b30c9ed4c1279680d0f`  
Last planning/review update: 2026-09-15

## Review checkpoint

The Builder resolved the first review findings R-001 through R-007 substantially and synchronized the implementation branch with current `main` (`ahead 8`, `behind 0`). PR #26 is open and mergeable.

Required CI run #69 (`Python 3.12 tests`) succeeded on PR merge ref `e49b9d0bb84816c31419291c36ffd69d4455dde0`:
- **204 passed, 2 warnings**;
- helper shell validation PASS;
- package build PASS.

The revised 260-file evaluation also used live Tool 2 Media context and reports 260 Tool 2 reviews, 133 downstream Tool 3 routes, zero Media-context-unavailable results, 44 provisional Tool 3 enrichments, and zero *locally exact* values overwritten.

Acceptance remains blocked by the second-round findings below. These are targeted correctness/audit/documentation corrections, not a Tool 3 redesign.

## First-round findings R-001 through R-007

Builder corrections reviewed as substantially resolved:
- R-001 confirmed Tool 2 Media authority integration added;
- R-002 live Tool 2 context added to the representative evaluation;
- R-003 routine/init replacement of a verified static schedule reference blocked;
- R-004 malformed explicit end dates excluded from valid interval use;
- R-005 canonical alias/effective-interval grouping added;
- R-006 valid long explicit ranges supported without per-day expansion;
- R-007 branch synchronization and `BASEROW_TRAVEL_SCHEDULE_TABLE_ID` configuration documentation corrected.

## Second-round independent review findings

### R-008 — Confirmed Media WHERE authority is still incomplete for structured locations

The new Media-authority guard is directionally correct, but confirmed Media WHERE is handled as a hyphen-delimited string and parsed with `split("-")[0]`. That truncates valid hyphenated places such as `New-York`, `Villa-Vrindavan`, `Krsna-Dvur`, or `Serbia-summer-camp`.

The guard also compares only the place token when suppressing schedule WHERE enrichment. It does not protect the confirmed Media country, so a same-named place in a different country can pass the guard. `confirmed_media_country_iso` is recovered from a selected candidate when available, but is not robustly derived from confirmed `renamer_enrichment.where_val` itself.

A further boundary case remains: when local Tool 1 has no usable date/place anchor and confirmed Media supplies WHERE but not WHEN, the confirmed Media WHERE is not used as the bounded Case-B location anchor, so Tool 3 can incorrectly return `INSUFFICIENT_EVIDENCE` even though authoritative Media context supplies a usable location.

Required correction:
- parse/represent confirmed Media WHERE structurally rather than truncating at the first hyphen;
- protect both canonical place and country from contradictory schedule enrichment;
- derive confirmed country safely even when only confirmed Renamer enrichment is available;
- use confirmed Media WHERE as a schedule anchor when it is the only usable location anchor, without applying it as provisional Tool 3 evidence;
- add regressions for a hyphenated confirmed place, same-place/different-country schedule evidence, and confirmed-Media-WHERE-only input.

### R-009 — Structured-location uniqueness, provenance, and deterministic selection are incomplete

Case C decides uniqueness using only `canonical_place`, ignoring country. Two schedule rows for the same place name on the same date but in different countries can therefore be treated as one unique location and one arbitrary candidate can be auto-selected.

Cases B/C can also collapse the final semantic value while selecting only `candidates[0]`, losing row IDs/text from other candidates that support the same selected value. Because candidate order follows input/reference iteration order, this can make selected provenance order-dependent. The public `search_by_when()` / `search_by_where()` helpers additionally call `group_candidates_semantically()` without the engine index, so alias grouping there differs from the main engine.

Required correction:
- use structured location identity `(canonical place, compatible country)` when deciding unique WHERE;
- same-place/different-country alternatives must remain multiple/ambiguous and must not auto-enrich;
- when multiple rows/candidates support one selected semantic value, preserve the union of contributing row IDs and schedule text/provenance;
- make candidate/group output and selected provenance deterministic independent of input iteration order;
- pass the engine index to service search helpers so canonical alias behavior is consistent;
- add reversed-input-order and same-place/different-country regressions.

### R-010 — Static-reference checksum does not cover all decision-bearing normalized data

`compute_canonical_sha256()` hashes row ID, dates, place, country, and schedule text, but omits stored `country_iso2`. The engine uses `country_iso2` for compatibility/conflict decisions. A local reference whose `country_iso2` is accidentally corrupted can therefore still pass checksum validation and alter Tool 3 decisions.

Required correction:
- either include `country_iso2` in the canonical normalized checksum, or deterministically recompute/validate it from `country` when loading so corrupted derived values cannot become trusted decision input;
- add a regression that tampers with `country_iso2` and proves the corrupted reference cannot be accepted unchanged.

### R-011 — Tool 3 audit and candidate explainability contract is incomplete

`TravelReviewResult` snapshots Tool 2 context state and selected Media row ID, but not the Tool 2 decision used by Tool 3. The build plan requires the Tool 3 audit record to preserve the Tool 2 decision/selected row reference when used; relying on the separate mutable Tool 2 review record is insufficient because that record can later be replaced by another live review.

Candidate explainability is also incomplete outside Case A: Cases B/C generally leave `match_reasons`, `date_comparison`, `place_comparison`, and `country_comparison` empty, and the portal does not display comparison states. The build plan requires these candidate evidence fields and portal comparison information.

Required correction:
- snapshot the Tool 2 decision used in each Tool 3 result/audit record (plus existing context state/selected row);
- populate meaningful match reasons and applicable date/place/country comparison states for Cases B/C;
- expose the stored Tool 2 decision/current-context and candidate comparison states in the Tool 3 portal card where relevant;
- add audit/portal regression coverage.

### R-012 — Failure classification, acceptance metric, and walkthrough accuracy need final correction

`review_batch()` currently converts any per-file exception into `REFERENCE_UNAVAILABLE`, even when the static reference is healthy (for example, a missing tracking ID). The build plan requires `REFERENCE_UNAVAILABLE` to mean the verified schedule reference cannot be loaded/bootstrapped; unrelated processing failures must not be mislabeled as a reference failure.

The 260-file evaluation comment/claim says it verifies that local **and confirmed Media** high-authority values were not overwritten, but `overwritten_high_authority` only compares final state with initial Tool 1 exact WHEN/WHERE. It does not independently verify final state against authoritative confirmed Tool 2 WHEN/WHERE values.

The committed walkthroughs also contain stale/incorrect delivery details: they claim Tool 3 is already “independently verified” before orchestrator acceptance; document CLI options as `--db-path`, `--ref-path`, and `--tracking-id` although the implementation uses `--registry-path`, `--reference-path`, and a positional tracking ID; describe a candidate “score” not present in the model/portal; and retain representative tracking IDs from the earlier evaluation instead of the current evaluation JSON.

Required correction:
- do not classify arbitrary per-file processing errors as `REFERENCE_UNAVAILABLE`; preserve a truthful distinct failure diagnostic/state;
- extend the acceptance evaluation to compare final Tool 1 state against any confirmed Tool 2 authoritative WHEN/WHERE actually used, and report that count separately or as part of the zero-overwrite assertion;
- correct walkthrough/status claims, CLI syntax, score wording, and representative IDs so committed documentation matches current code/evaluation;
- update this status with the actual final branch head, rerun required CI, and return `READY_FOR_REVIEW`.

## Current verified evidence retained

The second handoff does demonstrate substantial progress and should be preserved:
- branch is synchronized with current `main`;
- PR #26 exists and is mergeable;
- CI #69 is green with 204 tests;
- static reference routine overwrite protection is in place;
- malformed ranges and >366-day ranges have dedicated handling;
- live Tool 2 context was used in the revised 260-file run;
- accepted Tool 1/Tool 2 regression suites remain green.

## Open questions / contradictions

None requiring user input. Builder should resolve R-008 through R-012 on the existing `tool-3-implementation` branch / PR #26.

## Next milestone

Builder addresses R-008 through R-012, pushes the corrected branch, reruns the representative acceptance evaluation where required, waits for green required CI, and returns `READY_FOR_REVIEW` for another independent review pass.
