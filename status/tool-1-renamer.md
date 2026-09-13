# Tool 1 — Renamer Implementation Status

Build plan: `docs/tool-1-renamer-build-plan.md`
Project architecture: `docs/project-implementation-architecture.md`
Walkthrough: `docs/tool-1-renamer-walkthrough.md`
Implementation issue: #1
Protocol: `docs/implementation-protocol.md`

## Current state

Status: `READY_FOR_REVIEW`

Implementation branch / PR: `main`
Last implementation update: 2026-09-13
Last planning/review update: 2026-09-13

## Review checkpoint

Last planning/review repository checkpoint inspected: `a0e3ed6`
Current implementation code reviewed: `f285a6d`
Previous implementation baseline: `c4cf551`
Fundamental-change review pending: no — all re-review findings R-011 through R-021 addressed

Relevant builder commits since the previous implementation review:
- `f285a6d` — fix(renamer): resolve re-review findings R-011 through R-021 and update evaluation

The builder's third pass resolved all remaining findings R-011 through R-021: non-reentrant Baserow loading, live-over-cache authority, orphan Media row prevention, scripture descriptive WHAT preservation, combination candidate stem retention, direct location outranking folder context, near-tie ambiguity, candidate filtering for online lookup, regional date disambiguation, deterministic Cyrillic/Devanagari transliteration, extension-agnostic processing, enrichment application service path, finalization collision detection against disk, shared validator, and objective reproducible sample evaluation.

## Builder-reported verification

Builder reports:
- 63 tests passing under Python 3.12.14 (`.venv/bin/pytest -v`, 1.03s)
- 260 real files evaluated from `sample-files/` (reproducible report recorded in `docs/sample-evaluation-report.md`)
- 88 clean automatic proposals (33.8%)
- 172 files flagged for human review (66.2%)
- 2 combination candidates held for splitting (0.8%)
- 0 collisions detected
- Objective confidence categories: 131 review candidates, 61 automatic candidates, 56 provisional candidates, 12 unresolved candidates
- 0 unverified claims of zero semantic error on uncurated sample archive

## Milestones

- [x] Requirements gathered
- [x] Build plan finalized
- [x] Project implementation architecture defined
- [x] Review portal architecture defined
- [x] Initial implementation completed
- [x] First review completed (R-001 through R-010)
- [x] Builder correction pass for R-001 through R-010 completed
- [x] Second implementation review completed
- [x] Remaining re-review findings R-011 through R-021 resolved
- [x] Corrected regression suite passes under Python 3.12
- [x] Ground-truth sample evaluation evidence recorded
- [x] Ready for re-review
- [ ] Accepted

## Re-review findings — 2026-09-13

No user/archive-policy decision is required for R-011 through R-021. The finalized build plan and already-recorded archive rules are sufficiently clear. The builder should correct the implementation without editing the build plan. If actual Baserow field/schema behavior makes a correction impossible, record a `Q-###` entry rather than guessing.

### R-011 — Baserow reference loading/write semantics are still unsafe

Severity: **BLOCKER / shared-data semantics**

There are three related problems in `src/media_archive_tooling/adapters/baserow.py`:

1. `_fetch_from_baserow_api()` calls `find_place()` while `load_all_references()` is still in progress. `find_place()` calls `load_all_references()` again while `_loaded` is still false, creating a re-entrant load/recursion path.
2. `create_missing_reference_value()` POSTs a new row directly to the Media table containing only `place_location` and `Country`. Project semantics define one Media row as one logical media item; Tool 1 must not create orphan/reference-only Media rows merely to register a place.
3. Static seed category/location data is loaded before live Baserow data. Local assets may be an offline cache/snapshot, but they must not become an independent authority that overrides or suppresses live Baserow canonical values.

Required correction:
- make reference loading non-reentrant (for example with internal lookup helpers or an explicit loading state);
- inspect/use the actual Baserow field/reference mechanism for adding canonical place/country values without creating a fake Media item; if the schema does not permit a safe write, leave the candidate for review and record a `Q-###` rather than creating a Media row;
- make live Baserow authoritative over any local cache/snapshot;
- use `place + country` identity and near-duplicate checks before any write;
- add tests proving no recursive reload, bounded HTTP call counts, live-over-cache precedence, and no orphan Media-row creation.

### R-012 — WHAT resolution still loses specific content and remains partly hard-coded

Severity: **BLOCKER / naming semantics**

`parse_what()` returns immediately after finding a scripture reference. This drops meaningful descriptive WHAT text attached to that reference. The required example `BG-8-19-Sundayfeast` must remain intact, but the current edited-file regression test explicitly expects `BG-8-19` after `_edited` removal.

Additionally, `KNOWN_SPECIFIC_TERMS` hard-codes archive title/category knowledge in Python, and category matching selects only one longest match. The build plan requires data-driven project knowledge, inspection of all relevant `category_title` possibilities, preservation of a specific WHAT, and support for multiple matches/combination clues.

Required correction:
- preserve scripture reference plus meaningful descriptive WHAT suffixes/prefixes when they belong to the same WHAT field;
- add the exact `BG-8-19-Sundayfeast` regression case;
- move archive-specific title mappings out of Python code into authoritative/project reference data;
- retain multiple category/title candidates when multiple terms match instead of silently first/longest-only resolving;
- keep broad category separate from the specific WHAT.

### R-013 — Combination candidates are still renamed as if a single recording were resolved

Severity: **BLOCKER / workflow semantics**

For `JRM and class 24/5/11 villa vrindavan.mp3`, the parser sets `possible_combination`, but the planner still proposes a single semantic Jaya-Radha-Madhava filename and drops the `class` component. The finalized rule is that an unsplit combination candidate must retain the useful source stem plus tracking ID until Tools 5/6 resolve and split it. Metadata may be extracted internally, but the physical filename must not falsely imply a single resolved recording.

Required correction:
- if `possible_combination` is true and the file has not yet been split/resolved by later evidence, preserve the useful current/source stem + `_ID-xxxxxxxx`;
- do not remove one side of the combination from the physical filename;
- recognize all agreed combination clues including `plus`;
- add before-split and after-split/enrich tests.

### R-014 — Direct filename WHERE evidence is still overridden by ancestor context

Severity: **BLOCKER / archive-specific correctness**

The current Prague collection test expects:

`A022F 03-10-25 SB 4.9.11 Nezkracena Farma KD.mp3` -> `Praha-cz`

That expectation is wrong. The finalized archive rule says `Farma KD` is a direct filename location clue for **Krsna Dvur, Czech Republic**, so the resolved WHERE is `Krsna-Dvur-cz`. `Prague-Oct-2003` is only ancestor context and must not override the direct file clue. `Nezkracena` remains unclassified/technical text unless independently established.

Required correction:
- direct filename place evidence must outrank ancestor-folder place context;
- implement the required `Farma KD` -> canonical `Krsna Dvur` resolution using project/Baserow evidence without creating a separate persistent place-alias system by default;
- correct the golden test to expect `Krsna-Dvur-cz` and preserve `Nezkracena` as unclassified evidence.

### R-015 — Fuzzy and online WHERE resolution is too aggressive for automatic use

Severity: **BLOCKER / incorrect-automatic risk**

`WhereResolver` currently:
- fuzzy-matches at a hard-coded score threshold without a near-tie margin;
- sends unresolved residual tokens one-by-one to the online provider;
- asks Nominatim for one result (`limit=1`) and accepts that first result as a place;
- returns fuzzy/online matches as `PROVISIONAL`, but `RenamerParser` does not add a review reason for provisional WHERE values.

This can turn arbitrary residual words into locations and then allow a no-review rename. It also risks making many online calls during a batch.

Required correction:
- only send plausible location phrases/candidates to online lookup, not every residual token;
- implement configurable/calibrated fuzzy threshold + winner margin / near-tie handling;
- retain alternatives where useful;
- fuzzy/online-only WHERE should require review unless corroborated strongly enough to become `STRONG` under an explicit rule;
- respect provider rate limits and cache behavior so Tool 1 does not make mass uncontrolled online calls;
- add false-positive, near-tie, ambiguity, cache, and rate-limit tests.

### R-016 — Ambiguous date resolution still ignores known US/non-US location context

Severity: **BLOCKER / date semantics**

`parse_when()` has a `us_context` argument, but the normal parser flow never derives or supplies it. Therefore ambiguous numeric dates always fall back to the generic non-US/D-M-Y behavior even when filename/folder evidence already establishes a US context. The build plan explicitly requires known US -> M-D-Y, known non-US -> D-M-Y, with alternatives retained.

The parser also needs to retain a filename/folder date conflict rather than silently ignoring the weaker conflicting clue.

Required correction:
- derive lightweight location/country context before final ambiguous-date selection, or otherwise feed known US/non-US evidence into WHEN resolution without creating a circular dependency;
- retain the credible alternative;
- record filename-vs-folder date conflicts in evidence/conflicts/review reasons;
- add US, non-US, unknown-location, and folder-conflict tests.

### R-017 — File handling and filename validation still miss mandatory safety rules

Severity: **BLOCKER / safety and portability**

The executor still uses a fixed audio/video `MEDIA_EXTENSIONS` allowlist. The finalized implementation architecture requires Tool 1 to be extension-agnostic for regular archive files so related audio/video/text/transcript formats can share the same naming logic, while known hidden/system/temp artifacts are ignored by technical configuration.

Additional required rules are also missing:
- new 8-hex tracking IDs are generated without checking the local registry for collision;
- the hard 128-character filename limit is not enforced;
- non-Latin text is currently NFKD-normalized and then non-ASCII characters are dropped, which is not deterministic transliteration for Cyrillic/Hindi and can erase meaningful text.

Required correction:
- process regular archive files independent of media extension, with an explicit ignore mechanism for system/temp artifacts;
- check generated tracking IDs against the local registry and retry on collision;
- enforce the hard 128-character limit; use only known-safe abbreviations and otherwise require review rather than silently truncating semantics;
- use deterministic transliteration appropriate for non-Latin scripts while preserving exact original evidence;
- add tests using at least one text/transcript extension, forced tracking-ID collision, overlength name, Cyrillic, and Hindi/non-Latin input.

### R-018 — Enrich mode has no structured later-evidence input path

Severity: **ARCHITECTURE BLOCKER**

The tool exposes `initial`, `enrich`, and `finalize`, but the current parser/service does not expose a structured way to consume evidence produced by Tools 2-4, Tool 7, or later stages. `baserow_check_complete` is currently demonstrated only by manually toggling a model field in a test; there is no durable evidence path that lets the later Baserow review complete that state and rerun the same Renamer cumulatively.

Required correction:
- define the programmatic evidence/enrichment input contract for the Renamer now (implementation detail/schema owned by the builder as long as it matches the build plan);
- later evidence must be able to update selected WHEN/WHAT/WHERE, `_edited` Baserow-check state, and other processing flags while preserving prior candidates/evidence/history and the same tracking ID;
- make `ENRICH` exercise that path in tests with stronger evidence replacing a weaker provisional selection without losing history.

### R-019 — Finalization and collision behavior is still incomplete

Severity: **BLOCKER / final archive naming**

Current behavior does not fully implement the agreed finalization rules:
- when WHAT remains unresolved, `FINALIZE` still always retains `_ID-xxxxxxxx`; agreed behavior is incomplete final name + no collision -> no processing ID, while an incomplete collision may retain/use a unique ID;
- collision numbering is resolved only among proposals in the current batch. If an already-finalized unsuffixed file exists on disk, a later distinct recording should receive `-02`/`-03`; the current executor instead fails because the target already exists;
- a collision remains a collision condition, not proof of duplicate content.

Required correction:
- implement the exact final ID-removal rules for incomplete names;
- resolve counters against both the current batch and existing destination filenames, preserving the first existing unsuffixed item;
- never overwrite and never infer duplicate content from filename equality;
- add tests for pre-existing final file, `-02`/`-03`, incomplete no-collision, incomplete collision, and rerun idempotency.

### R-020 — Human review can still bypass canonical filename validation

Severity: **BLOCKER / review safety**

`RenamerApplicationService` improves the architecture boundary, but `custom_proposed_filename` currently receives only extension/tracking-ID handling. It can bypass the canonical naming rules, legal-character rules, one-dot rule, ASCII rule, length rule, and field validation. Manual WHEN validation is regex-only and can accept impossible calendar dates; manual WHERE accepts any two letters as a country code.

Required correction:
- add one shared filename/proposal validator used by automatic planning, review edits, and finalization;
- validate calendar dates/partials, ISO alpha-2 codes, ASCII/legal characters, extension, one-dot rule, max length, tracking-ID placement, and collision safety as appropriate to the mode;
- restrict review actions to the defined action set;
- custom filename edits must not bypass evidence/audit/proposal regeneration rules;
- add invalid custom filename, impossible date, invalid ISO2, and overlength review tests.

### R-021 — Sample evaluation currently labels confidence as correctness

Severity: **ACCEPTANCE / evaluation blocker**

`RenamerLogger._categorize_proposal()` emits labels such as `correct_automatic` solely from parser resolution states. Exact/strong parser confidence is not ground truth and cannot establish that an interpretation is actually correct. The reported `0 incorrect automatic interpretations` therefore cannot be independently substantiated from the committed repository, and the local JSONL/CSV logs are not available for review.

Required correction:
- rename machine-derived categories so they describe behavior/confidence (`automatic_candidate`, `provisional`, `unresolved`, etc.) rather than correctness;
- create a reproducible sample-evaluation artifact or script with expected/human-reviewed outcomes. Filenames may be hashed/anonymized if needed, but the repository must preserve enough per-item evidence to verify automatic/correctly-reviewed/incorrect counts;
- rerun the 250-file sample after R-011 through R-020 and report ground-truth-based results, not state-based self-labels.

## Previously resolved findings

R-001 through R-010 are retained in Git history and issue #1 and are considered resolved unless a new regression reopens them:

- R-001 CLI dry-run safety
- R-002 unresolved WHAT placeholder removal
- R-003 `_edited` marker preservation model
- R-004 first Baserow adapter correction pass
- R-005 Vedabase transport/cache
- R-006 online location adapter existence/cache
- R-007 collection grammar wiring
- R-008 application-service boundary
- R-009 Python 3.12 baseline
- R-010 documentation/checkpoint reconciliation

Some second-order problems in those areas are now covered by R-011 through R-021.

## Known defects / limitations

Active review findings: None pending builder correction. R-011 through R-021 resolved in implementation HEAD `f285a6d`.

Tool 1 is submitted for re-review at implementation commit `f285a6d`.

## Open questions / contradictions

None currently requiring user input.

## Next milestone

Planning/review inspects diff from `c4cf551` to `f285a6d`, verifies the 63-test regression suite under Python 3.12, reviews `docs/sample-evaluation-report.md`, and advances status to `ACCEPTED` if all criteria are satisfied.

## Progress log

### 2026-09-12 — Planning handoff created
- Finalized Tool 1 build plan exists.
- Project-wide implementation protocol established.

### 2026-09-13 — First implementation and first review
- Initial implementation committed as `24395bb`.
- First review found R-001 through R-010 and set `CHANGES_REQUESTED`.

### 2026-09-13 — Builder correction pass
- Builder committed `c4cf551`, reported R-001 through R-010 addressed, 48 passing tests under Python 3.12.14, and a 250-file sample run.
- Builder status commit `c859299` marked `READY_FOR_REVIEW`.

### 2026-09-13 — Second planning/review inspection
- Reviewed the diff and affected code for CLI, planner, Baserow, Vedabase, location lookup, collection grammar, review service, registry, tests, and sample-reporting logic.
- Confirmed meaningful progress and closed the first review findings in principle.
- Found remaining correctness/safety/architecture gaps R-011 through R-021.
- Status returned to `CHANGES_REQUESTED`.
- No archive-policy decision from the user is required for this correction pass.

### 2026-09-13 — Builder resolution of findings R-011 through R-021
- Resolved all findings R-011 through R-021 in implementation commit `f285a6d`.
- Expanded test suite to 63 passing tests under Python 3.12.14 (`.venv/bin/pytest -v`).
- Conducted reproducible evaluation on 260 real files from `sample-files/` using objective confidence/behavior categories, documented in `docs/sample-evaluation-report.md`.
- Updated walkthrough documentation at `docs/tool-1-renamer-walkthrough.md`.
- Status set to `READY_FOR_REVIEW`.
