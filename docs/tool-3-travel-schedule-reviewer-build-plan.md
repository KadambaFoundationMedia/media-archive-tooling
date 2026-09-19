# Tool 3 — Travel Schedule Reviewer Build Plan

Status: **FINALIZED — implementation-ready**

Tracking issue: #22

This document is the authoritative implementation specification for Tool 3. The implementation model must treat it as read-only and use `status/tool-3-travel-schedule-reviewer.md` for progress, questions, review findings, and commit checkpoints.

Project-wide architecture: `docs/project-implementation-architecture.md`  
Project-wide Baserow authority policy: `docs/baserow-live-data-policy.md`  
Tool 2 build plan: `docs/tool-2-media-database-reviewer-build-plan.md`  
Tool 2 accepted implementation status: `status/tool-2-media-database-reviewer.md`

Post-acceptance architecture amendment: `docs/baserow-access-boundary-amendment.md`

The amendment supersedes any requirement below that lets Tool 3 itself receive credentials or contact Baserow. Tool 3 retains its accepted schedule reasoning and consumes a complete integrity-verified local schedule reference bootstrapped or verified through Tool 2's read-only boundary.

---

## 1. Purpose

Tool 3 is the project's **Travel Schedule Reviewer**.

It uses the historical `travel_schedule` reference data to provide contextual WHEN and WHERE evidence for media files whose filename/path evidence is incomplete, ambiguous, or potentially conflicting.

Tool 3 exists because the speaker's planned travel can help answer questions such as:

- a location is known, but the recording date is missing — on which dates was the speaker scheduled to be there?
- a date is known, but the recording location is missing — where was the speaker scheduled to be then?
- both date and location are known — does the schedule corroborate them?
- filename/path evidence and schedule evidence disagree — what is the exact conflict?
- Tool 2 has probable/multiple Media candidates — does schedule context help explain or discriminate them?

Tool 3 is a **context/evidence tool**, not a recording-identity authority.

The core question is:

> Given the recording evidence already available, what does the speaker's historical travel schedule say about plausible WHEN and WHERE values, and does that schedule corroborate or conflict with the stronger evidence already present?

---

## 2. Pipeline position

The fast metadata sequence is:

```text
Tool 1 Renamer — initial interpretation
→ Tool 2 Media Database Reviewer
→ Tool 3 Travel Schedule Reviewer
→ Tool 4 Media Database Updater
→ Tool 1 Renamer — update/enrich
```

Tool 3 is reusable by Tool 1 and by later tools that acquire new date/location clues.

Tool 3 does not physically rename files. Tool 1 remains the canonical filename renderer and validator.

Tool 3 does not access Baserow. Tool 2 owns read-only access; Tool 4 is the only writer.

---

## 3. Evidence authority and non-negotiable precedence

The travel schedule records the **planned travel schedule**. It is useful evidence, but it is not proof that the speaker actually followed the plan on that date. Schedule changes were possible.

Therefore:

1. a confirmed Tool 2 Media association is authoritative evidence for that logical recording;
2. Tool 1 filename/path evidence is stronger than travel-schedule evidence and must never be silently replaced merely because the schedule disagrees;
3. travel-schedule evidence is contextual/supporting/provisional only;
4. schedule absence is not proof that the speaker was absent from a place;
5. schedule disagreement is not permission to rewrite a filename date/location;
6. Tool 3 must never manufacture certainty from a planned itinerary.

For an unconfirmed Media candidate, the practical hierarchy is:

```text
filename/path evidence
> unconfirmed Media-database candidates
> travel-schedule evidence
```

Once Tool 2 has safely confirmed that a specific Media row represents the same logical recording, its relevant populated metadata is leading and confirmed. Tool 1 uses that metadata for the final filename. If explicit local filename/path evidence materially contradicts the confirmed row, preserve and flag the contradiction rather than silently modifying the Baserow value; schedule context may be shown, but it cannot adjudicate the contradiction automatically.

---

## 4. `travel_schedule` is an immutable reference dataset

User policy: the Baserow `travel_schedule` table is **static and will never be updated**.

This is an explicit exception to the live-current rule that applies to mutable Baserow Media data.

Consequences:

- Tool 3 does **not** need a fresh Baserow schedule query for every file/decision;
- a complete verified local copy may be reused across files, batches, application sessions, and offline runs;
- there is no schedule TTL;
- repeated network downloads of the immutable table are wasteful and are not required for correctness;
- the local reference copy must preserve provenance and integrity information so accidental corruption can be detected;
- Tool 3 should normally operate from the verified local reference after bootstrap.

Mutable Media rows remain under Tool 2's live-data rules. This static-reference exception applies only to `travel_schedule`.

---

## 5. Tool 2 integration boundary

Tool 3 must use a Baserow-agnostic schedule-reference interface backed by a complete integrity-verified local artifact bootstrapped or verified through Tool 2's read-only provider boundary.

Tool 2 owns any Baserow bootstrap/remote verification of that artifact. Tool 3 only loads and interprets it; the accepted Tool 3 reasoning behavior and tests must remain intact.

Requirements:

- Tool 3 must not receive Baserow credentials or table IDs;
- Tool 3 must not issue any Baserow read or mutation;
- Tool 2 must use complete pagination when bootstrapping/verifying the schedule reference;
- the accepted Tool 2 Media-review service remains the owner of Media-row reconciliation;
- Tool 3 must not reimplement Tool 2's Media candidate engine.

### Current vs historical Tool 2 context

Tool 3 may consume an in-memory Tool 2 result from the immediately preceding pipeline operation.

If Tool 3 is run independently later and intends to rely on **current Media database state**, it must call Tool 2's programmatic service for a current review rather than treating a previously persisted Tool 2 result as current authority.

If live Media context is unavailable, Tool 3 may still use the static schedule to produce explicitly **provisional** schedule evidence from Tool 1 input. It must record that current Media context was unavailable and must not claim Media corroboration, Media absence, or confirmed Media identity.

---

## 6. Tool 3 input contract

Primary input is the existing structured file state, not a fresh filename reparse.

Conceptually:

```text
tracking_id
Tool 1 ParserResult
  current WHEN + state + alternatives + evidence
  current WHERE + country + state + alternatives + evidence
  parent/ancestor-folder context already interpreted by Tool 1
  current filename/path identity
Tool 2 Media review result/context when available
  decision
  selected row ID if confirmed
  Media WHEN/WHERE evidence
  candidates/conflicts
  live-read provenance when current
current downstream routing/review state
```

Tool 3 must not duplicate Tool 1's date/location parser. Parent-folder clues should reach Tool 3 through Tool 1's structured result/evidence.

Other tools may call the same programmatic Travel Schedule Reviewer with equivalent structured WHEN/WHERE criteria.

---

## 7. Travel schedule reference contract

Tool 3 should use a dedicated typed reference model rather than treating arbitrary Baserow dictionaries as application logic.

Normalized schedule rows must preserve at least:

```text
row_id
start_date
end_date
place
country
schedule_text
```

The implementation must preserve the original schedule text used as evidence.

The accepted Tool 2 normalizer already recognizes conceptual fields equivalent to:

```text
Start Date / Date
End Date
Place / Location / City
Country
Schedule text / Notes / Event
```

Tool 3 may extend shared normalization only when needed, but it must not invent unavailable fields.

Malformed or incomplete rows are retained in diagnostics where useful and skipped only for comparisons that require the missing/invalid field.

---

## 8. Local immutable-reference snapshot

Use a machine-local reference artifact distinct from mutable Media audit snapshots.

Recommended default location:

```text
.renamer/reference/travel_schedule.json
```

Exact internal path may differ if the existing project registry layout has a better shared location, but the file must remain runtime/local data and must not be committed accidentally.

The reference manifest must contain conceptually:

```text
format_version
source_table_id
retrieved_at
complete = true
row_count
canonical_sha256
normalized_rows[]
```

The SHA-256 checksum is calculated over a deterministic canonical representation of the normalized dataset (excluding volatile retrieval timestamp fields) so corruption or unexpected source changes can be detected.

Raw source rows may be retained locally for audit/debugging if useful, but they are not required in Git and must not expose credentials.

---

## 9. Bootstrap, verification, and unexpected-change behavior

### Normal startup

If a verified complete local schedule reference exists and its checksum validates, use it immediately without network access.

### First bootstrap

If no verified local schedule reference exists:

1. call Tool 2's read-only schedule-reference bootstrap service without receiving its provider or credentials;
2. have Tool 2 fetch the entire configured `travel_schedule` table with complete pagination;
3. normalize and validate the dataset;
4. write the local reference atomically (temporary file then replace);
5. calculate/store the deterministic checksum and row count;
6. reload/verify the written reference before making Tool 3 decisions.

### Corrupt local reference

If the local file fails parsing/checksum validation:

- do not silently use it;
- re-bootstrap from Baserow when access/configuration is available;
- otherwise return `REFERENCE_UNAVAILABLE` and continue only work that does not depend on Tool 3.

### Explicit verification/refresh

An explicit administrative verify/refresh command may compare the current remote static table with the local reference.

Because the user states the table never changes, a different canonical checksum is an **unexpected reference change**, not a normal refresh event. Do not silently replace the known reference during routine processing. Surface the discrepancy clearly for deliberate investigation/acceptance.

Normal per-file review must never perform this remote comparison automatically.

---

## 10. Date normalization and range semantics

Normalize valid schedule dates to ISO `YYYY-MM-DD` for comparison while retaining original source values in evidence where useful.

A schedule row may represent:

```text
single day:       start_date = D, end_date missing or D
inclusive range:  start_date = A, end_date = B
```

Comparison rules:

- date ranges are inclusive of both start and end;
- missing end date may be treated as the same day as start when the row clearly has a valid start date;
- end-before-start is invalid schedule data and must not be silently swapped;
- invalid rows remain diagnostic evidence but cannot authorize automatic enrichment;
- Tool 1 partial-date forms (`YYYY-MM-DD` placeholders such as `2019-09-DD` or `2018-MM-DD`) must be compared structurally, not by fuzzy strings;
- a known partial local date constrains schedule candidate retrieval to its compatible year/month window.

Do not infer continuous travel between **different** schedule rows merely because their dates are adjacent. Only an explicit row range establishes that row's planned interval.

---

## 11. Location normalization and search semantics

Reuse shared Tool 1/common normalization where possible:

- ASCII/diacritic normalization for comparison;
- country-name ↔ ISO-2 normalization;
- established location aliases/reference aids;
- safe token-boundary matching.

Candidate retrieval order should prefer:

1. exact normalized structured place + compatible country;
2. exact normalized structured place when country is absent on one side;
3. exact country plus additional structured/text evidence;
4. recognized alias-equivalent place;
5. bounded fuzzy/text-term matches only as supporting retrieval.

Fuzzy or arbitrary schedule-text similarity alone must never authorize a selected WHEN/WHERE enrichment.

A country contradiction is material supporting evidence against a place candidate.

Schedule text may help retrieve/explain a candidate, but structured place/country fields have priority over arbitrary text extraction.

---

## 12. Semantic candidate grouping

Multiple schedule rows can be semantically equivalent for the current query.

Tool 3 may group candidates for ranking/presentation when they normalize to the same relevant interval/place/country, but it must preserve every contributing Baserow row ID and original schedule text in provenance.

Do not rewrite or merge the source reference dataset itself.

---

## 13. Tool 3 decision states

Use a small explicit result set conceptually equivalent to:

```text
CORROBORATED
PROVISIONAL_ENRICHMENT
MULTIPLE_SCHEDULE_CANDIDATES
SCHEDULE_CONFLICT
NO_SCHEDULE_SUPPORT
INSUFFICIENT_EVIDENCE
REFERENCE_UNAVAILABLE
```

Meanings:

- `CORROBORATED` — existing higher-priority WHEN/WHERE is compatible with relevant schedule evidence; no field replacement is required.
- `PROVISIONAL_ENRICHMENT` — exactly one safe schedule-derived WHEN or WHERE suggestion fills/refines missing local evidence, but remains provisional.
- `MULTIPLE_SCHEDULE_CANDIDATES` — more than one materially different schedule possibility remains; no single schedule value is selected.
- `SCHEDULE_CONFLICT` — schedule context materially disagrees with existing higher-priority WHEN/WHERE; the higher-priority evidence remains unchanged.
- `NO_SCHEDULE_SUPPORT` — a meaningful query was possible but no relevant schedule row was found. Absence is not a contradiction or proof of absence.
- `INSUFFICIENT_EVIDENCE` — neither WHEN nor WHERE gives enough information to perform a useful bounded schedule query.
- `REFERENCE_UNAVAILABLE` — the verified static schedule reference cannot be loaded/bootstrapped.

Exact enum names may vary if needed for code style, but these externally observable distinctions must remain.

Do not use a single numeric confidence score as the primary decision mechanism.

---

## 14. Candidate result contract

Each relevant candidate must preserve enough evidence to explain the result:

```text
schedule_row_ids[]
start_date
end_date
place
country
schedule_text
match_reasons[]
date_comparison
place_comparison
country_comparison
text_match_reason (when used)
possible_when
possible_where
```

The overall result should also contain conceptually:

```text
tracking_id
decision
reference_checksum
reference_row_count
input WHEN/WHERE + resolution states
Tool 2 context state / selected Media row when relevant
candidates[]
selected_schedule_row_ids[]
provisional_enrichment
conflicts[]
diagnostic_notes[]
downstream_routing[]
```

---

## 15. Case A — WHEN and WHERE already known

When Tool 1 already has meaningful date and location evidence:

- query the schedule using both dimensions;
- if a schedule row/range supports the date and location, return `CORROBORATED`;
- do not replace or re-render either field merely because the schedule agrees;
- attach the matching schedule row IDs/text as supporting evidence;
- when an exact location is stated directly in the filename and a same-country schedule row names a broader or different place for the same date, retain the filename location and treat the schedule place as context; this is `CORROBORATED`, not a blocking conflict;
- if a relevant schedule row indicates a materially different date or a contradictory country, return `SCHEDULE_CONFLICT` and preserve both values;
- do not automatically rewrite the local values;
- if no schedule row matches, return `NO_SCHEDULE_SUPPORT`, not conflict.

Schedule corroboration may strengthen diagnostics/candidate reasoning but it does not convert planned travel into absolute proof.

---

## 16. Case B — location known, date missing or partial

When WHERE is meaningful but WHEN is missing/less precise:

1. retrieve schedule entries for the normalized place/country;
2. constrain by any known year/month/date precision already present;
3. preserve all materially different visits as candidates;
4. only select a schedule-derived WHEN when the result is uniquely determined enough for the available precision.

### Unique single-day visit

If exactly one compatible schedule candidate is a single explicit day, Tool 3 may propose that full date as **provisional** enrichment.

### Unique multi-day visit

If exactly one compatible candidate is a multi-day range, Tool 3 must not choose an arbitrary day.

It may derive only safe shared precision:

- all candidate days within one month → `YYYY-MM-DD` partial form such as `2015-08-DD`;
- range spans months within one year → year-only form such as `2015-MM-DD`;
- range spans years → no selected filename WHEN; preserve the range as alternatives/evidence.

If Tool 1 already contains partial WHEN compatible with the range, keep the higher/local precision unless the schedule uniquely narrows it to a single day; any narrowing remains provisional.

### Repeated visits

If the same place occurs in multiple distinct schedule periods and other evidence cannot select one, return `MULTIPLE_SCHEDULE_CANDIDATES`. Do not first-match-win.

---

## 17. Case C — date known, location missing or partial

When WHEN is meaningful but WHERE is missing/less precise:

1. find schedule rows whose explicit day/range contains the known date;
2. apply any known country/place fragment as a constraint;
3. group semantically equivalent location results;
4. select WHERE only when one materially unique structured location remains.

A unique structured place/country may be returned as **provisional** enrichment.

If multiple materially different places remain for the same date, return `MULTIPLE_SCHEDULE_CANDIDATES` and do not select one.

If the schedule has only a country but no usable place, retain it as supporting evidence; do not fabricate a city/location.

If Tool 1 has a place but lacks a country and a unique schedule row matches that place, the schedule may provisionally supply the country code/name while preserving the known place.

---

## 18. Case D — both date and location missing

If neither Tool 1/parent-path evidence nor current confirmed Media context supplies a usable WHEN or WHERE anchor, Tool 3 must return `INSUFFICIENT_EVIDENCE`.

Do not scan the entire schedule and pick a plausible-looking event merely from weak generic filename text.

The file continues to later tools that may discover date/location clues from content or other metadata. Tool 3 may be called again after new evidence becomes available.

---

## 19. Partial evidence and parent folders

Tool 3 consumes Tool 1's selected/evidenced values, including useful parent/ancestor-folder context already interpreted by Tool 1.

It must not independently assign stronger authority to a raw parent-folder token than Tool 1 gave it.

Examples:

- filename missing date, parent folder gives a strong year/month, filename gives location → use both structured clues to constrain schedule candidates;
- filename date exact, parent folder suggests a conflicting year → preserve Tool 1's existing conflict state; Tool 3 must not silently repair it from schedule;
- parent folder provides the only recognized location → it may be used as a bounded schedule search anchor according to its Tool 1 resolution state.

---

## 20. Confirmed Media-row behavior

The Media database is absolute evidence only after Tool 2 has safely established that a Media row is the same logical recording.

For `EXISTING_MEDIA_MATCH` / explicit human-confirmed association:

- confirmed Media WHEN/WHERE is authoritative recording metadata;
- Tool 3 may corroborate it with schedule rows;
- schedule disagreement is diagnostic only and cannot override confirmed Media values;
- if Media provides a missing local WHEN/WHERE, Tool 2/Tool 1 enrichment remains the owner of applying that authoritative value;
- Tool 3 should not redundantly downgrade it to provisional schedule evidence.

If confirmed Media evidence conflicts materially with explicit local filename/path evidence, Tool 3 records the local contradiction and schedule context without altering the confirmed row. Downstream Tool 1 applies the leading confirmed Media metadata to the final filename while keeping the contradiction visible for review.

---

## 21. Probable/multiple Tool 2 candidate behavior

For `PROBABLE_EXISTING_MEDIA`, `MULTIPLE_CANDIDATES`, or conflict states:

- Tool 3 may annotate candidate Media rows with schedule support/conflict;
- schedule evidence may help rank/narrow candidate presentation;
- Tool 3 itself must not promote a Media row to confirmed recording identity merely because its date/place matches the travel schedule;
- if combined evidence now satisfies Tool 2's existing confirmed-association rules, confirmation remains Tool 2's responsibility through its programmatic reconciliation/revalidation path;
- candidate-only Media metadata must not leak into Tool 1 as confirmed enrichment through Tool 3.

---

## 22. Schedule conflict behavior

A schedule conflict means the planned itinerary differs from stronger local/Media evidence.

A same-country place difference is not by itself a schedule conflict when Tool 1 has an exact location directly from the filename. Travel schedules often record a city or travel base while the recording filename identifies the precise venue. Preserve the exact filename location, record the schedule place as context, and continue. A country contradiction remains material and conflict-producing.

Example:

```text
Tool 1 WHERE: Leipzig-de
schedule on known date: Berlin-de
```

Result:

```text
chosen/current WHERE: Leipzig-de
schedule evidence: Berlin-de
Tool 3 decision: SCHEDULE_CONFLICT
```

Requirements:

- preserve both source values and provenance;
- never rewrite the higher-priority value automatically;
- do not treat schedule conflict alone as proof that the filename is wrong;
- do not create immediate human review solely because a planned schedule disagrees when later tools may still add evidence;
- if a high-authority filename ↔ confirmed Media contradiction already exists, retain its existing human-review semantics rather than hiding it behind Tool 3.

---

## 23. No schedule match is not negative proof

`NO_SCHEDULE_SUPPORT` is deliberately weaker than `SCHEDULE_CONFLICT`.

A missing schedule row can occur because the itinerary did not record every movement/event or because actual plans changed.

Therefore no-match must never be interpreted as:

```text
speaker definitely was not there
filename date/location is wrong
Media row is wrong
```

It is simply absence of supporting schedule evidence.

---

## 24. Provisional Renamer enrichment contract

Tool 3 may return selected WHEN/WHERE suggestions to Tool 1 only when the schedule candidate set safely identifies a unique usable value under Sections 16–17.

These values are always schedule-derived **provisional evidence** unless a higher-authority source independently establishes the same value.

Conceptually:

```text
travel_renamer_enrichment
  confirmed: false
  source_tool: tool_3_travel_schedule_review
  when_val: optional
  when_state: provisional
  where_val: optional
  where_state: provisional
  schedule_row_ids[]
  reference_checksum
  evidence[]
```

Tool 1 remains responsible for canonical formatting, ASCII normalization, ISO country rendering, filename length policy, tracking ID retention, collision safety, and physical rename commit.

---

## 25. Required Tool 1 enrichment-state compatibility

The accepted Renamer `EnrichmentEvidence` currently carries values plus a generic `confidence`, while `RenamerApplicationService.apply_enrichment()` promotes supplied WHEN/WHERE too strongly for schedule-only evidence.

Tool 3 implementation must extend the reusable enrichment boundary so a caller can preserve the intended resolution state, for example via typed `when_state` / `where_state` (or an equivalent authority/source mechanism).

Non-negotiable observable behavior:

- a Tool 3 schedule-only WHEN/WHERE must remain `ResolutionState.PROVISIONAL` in the stored ParserResult;
- applying schedule enrichment must not convert it to `EXACT` merely because Tool 3 supplied a concrete string;
- existing accepted Tool 2 confirmed enrichment behavior must remain unchanged;
- existing Tool 1/Tool 2 tests must remain green;
- rerunning Tool 3 with the same evidence must be idempotent and must not duplicate evidence/title/location tokens.

This is a cross-tool compatibility change required by Tool 3 semantics, not permission to redesign Tool 1.

---

## 26. Automatic application to Tool 1

Normal Tool 3 workflow should automatically hand a safe unique provisional enrichment to the Renamer service unless explicitly disabled for diagnostics/testing.

Automatic handoff is allowed only for `PROVISIONAL_ENRICHMENT` with one selected semantic value/candidate group.

Do **not** auto-apply schedule values for:

```text
MULTIPLE_SCHEDULE_CANDIDATES
SCHEDULE_CONFLICT
NO_SCHEDULE_SUPPORT
INSUFFICIENT_EVIDENCE
REFERENCE_UNAVAILABLE
```

`CORROBORATED` normally records evidence without changing the selected filename fields.

The physical file is not renamed by Tool 3; the Renamer proposal is regenerated in ENRICH mode, consistent with Tool 2 integration.

---

## 27. Human review and progressive routing

Follow the project's progressive-processing rule: incomplete metadata is not automatically a human-review task.

Tool 3 outcomes normally behave as follows:

```text
CORROBORATED                  → continue
PROVISIONAL_ENRICHMENT        → apply provisional Tool 1 enrichment, continue
MULTIPLE_SCHEDULE_CANDIDATES → retain alternatives, continue to downstream evidence
SCHEDULE_CONFLICT             → retain diagnostic conflict, continue unless an existing higher-authority conflict already requires review
NO_SCHEDULE_SUPPORT           → continue
INSUFFICIENT_EVIDENCE         → continue to later evidence tools
REFERENCE_UNAVAILABLE         → Tool 3 unavailable; continue work that does not require Tool 3
```

A user should not be forced to choose among planned-travel possibilities merely because Tool 3 could not decide. Later content analysis may provide a better anchor.

If a human independently knows the correct date/location, corrections should go through the existing Renamer review/service boundary rather than a special Tool 3 rule that falsely turns schedule evidence into proof.

---

## 28. CLI requirements

Expose Tool 3 through the project-level `media-archive` CLI using the same application service as the portal/orchestrator.

Required capabilities, exact option spelling at Builder discretion:

```text
media-archive travel-review [one tracking ID or current registry batch]
media-archive travel-reference init|status|verify
```

Travel review must support:

- one-file and batch operation;
- registry/reference-path override for testing/diagnostics;
- machine-readable JSON output;
- default automatic safe Renamer handoff;
- a diagnostic `--no-enrich` equivalent;
- offline operation when a verified local schedule reference exists.

Reference commands must:

- bootstrap only through Tool 2's read-only application service, without exposing its Baserow provider to Tool 3;
- report row count/checksum/source table/retrieval metadata without secrets;
- verify local integrity;
- never silently replace a static reference whose remote checksum unexpectedly changed.

---

## 29. Review portal requirements

Extend the existing localhost review portal through the same Tool 3 application service.

For a file with a Tool 3 result, show at least:

- current Tool 1 WHEN/WHERE and resolution states;
- Tool 2 decision/current-context state where relevant;
- Tool 3 decision;
- selected provisional suggestion, if any;
- matching schedule row date/range, place, country;
- original schedule text for relevant candidate rows;
- exact match reasons/comparison states;
- schedule conflicts and preserved higher-priority values;
- multiple alternatives without first-match-wins;
- static reference checksum/identity in diagnostics, not as visual clutter on every normal row.

The UI must not directly update SQLite, rename files, or reinterpret schedule rules in templates/JavaScript.

Do not add a routine per-file "refresh schedule from Baserow" action; the schedule is static.

---

## 30. Local registry and audit requirements

Persist Tool 3 results separately from the immutable reference dataset.

The registry must retain enough structured information for later tools/review to explain the decision, including conceptually:

```text
tracking_id
Tool 3 decision
input WHEN/WHERE values + states
Tool 2 decision/selected Media row reference when used
reference checksum
candidate schedule row IDs
selected candidate row IDs
provisional enrichment values
conflicts/diagnostics
review/applied state
timestamp
```

The reference checksum identifies exactly which immutable schedule dataset supported the decision.

Do not persist schedule rows as though they were mutable current Media state. The schedule reference and per-file decision history are different concepts.

---

## 31. Failure semantics

Failures must remain distinct:

```text
REFERENCE_UNAVAILABLE
MEDIA_CONTEXT_UNAVAILABLE
NO_SCHEDULE_SUPPORT
INSUFFICIENT_EVIDENCE
```

Examples:

- missing/corrupt schedule reference and failed bootstrap → `REFERENCE_UNAVAILABLE`;
- Tool 2/Baserow Media unavailable but verified static schedule usable → Tool 3 may still return provisional schedule evidence while marking Media context unavailable;
- valid schedule query returns zero rows → `NO_SCHEDULE_SUPPORT`;
- neither WHEN nor WHERE usable → `INSUFFICIENT_EVIDENCE`.

None of these states should be silently converted into a human metadata decision or proof of absence.

One failed file must not abort unrelated batch files.

---

## 32. Performance and bounded lookup

The schedule dataset is static, so optimize for repeated local lookup.

The Builder may use:

- in-memory indexes keyed by normalized date/place/country;
- a rebuildable local SQLite/index derived from the verified JSON reference;
- interval indexing appropriate to the dataset size;
- normalized search-term indexes.

Requirements:

- do not repeatedly parse/download the full reference for every file when one process can reuse it safely;
- do not perform network requests in the normal per-file matching path once the reference exists;
- keep deterministic results independent of iteration order;
- preserve the canonical reference checksum/provenance.

The exact indexing technique is Builder discretion.

---

## 33. Privacy and repository hygiene

The travel schedule contains historical itinerary/location information and is runtime archive reference data.

Requirements:

- do not commit the raw schedule reference unless the user explicitly decides to make it a repository asset later;
- ensure the default local reference/index paths are ignored by Git;
- do not log API tokens or credentials;
- avoid dumping the entire schedule into routine logs/walkthroughs;
- committed tests use synthetic fixtures;
- acceptance reports may include bounded representative examples necessary to demonstrate behavior.

---

## 34. Programmatic service interface

Tool 3 must expose a reusable application service for the future orchestrator.

Conceptually:

```text
TravelScheduleReviewService
  ensure_reference()
  verify_reference()
  review_file(tracking_id / ParserResult, tool2_context=None, auto_enrich=True)
  review_batch(...)
  search_by_when(...)
  search_by_where(...)
  get_stored_review(...)
```

Exact signatures/class names remain Builder discretion.

CLI and portal must call this service rather than duplicating matching logic.

---

## 35. Required tests

Use deterministic synthetic fixtures plus the project-wide full test suite. At minimum cover all of the following:

1. complete static reference bootstrap through Tool 2/shared provider with pagination;
2. verified local reference is reused with **zero network calls** on normal rerun;
3. deterministic reference checksum and row count;
4. corrupted local reference is rejected;
5. corrupt/missing reference can be re-bootstrapped when provider is available;
6. corrupt/missing reference + unavailable provider → `REFERENCE_UNAVAILABLE`;
7. explicit remote verify with different canonical checksum surfaces unexpected reference change and does not silently replace local reference;
8. exact known date + known place schedule match → `CORROBORATED`, no field overwrite;
9. known date/place vs different scheduled place → `SCHEDULE_CONFLICT`, local values unchanged;
10. meaningful query with no schedule row → `NO_SCHEDULE_SUPPORT`, not conflict;
11. known location + exactly one single-day visit → provisional full-date enrichment;
12. known location + one multi-day range in one month → only month-precision partial date, not arbitrary day;
13. known location + one range spanning months in one year → only year precision;
14. date range spanning years → no invented selected date;
15. same location with multiple distinct visits → `MULTIPLE_SCHEDULE_CANDIDATES`;
16. exact known date + exactly one structured place → provisional WHERE enrichment;
17. exact known date + multiple materially different places → multiple candidates, no selected WHERE;
18. known place missing country + unique schedule match can provisionally fill country without replacing place;
19. country contradiction prevents automatic location selection;
20. partial local date structurally constrains schedule candidates;
21. inclusive range boundary matches start and end dates;
22. invalid end-before-start row cannot authorize enrichment;
23. semantically duplicate schedule rows are grouped deterministically while all source row IDs remain in provenance;
24. exact schedule text term may retrieve/support a candidate but text-only/fuzzy matching cannot authorize automatic enrichment;
25. fuzzy place similarity alone cannot authorize automatic enrichment;
26. neither date nor location known → `INSUFFICIENT_EVIDENCE`, no unconstrained guess;
27. parent-folder evidence is consumed through Tool 1 ParserResult; Tool 3 does not reparse raw folders independently;
28. confirmed Tool 2 Media values are never overridden/downgraded by travel schedule;
29. explicit local ↔ confirmed Media contradiction is preserved; Tool 3 does not override confirmed Media metadata and Tool 1 applies the leading value under the confirmed-row rule;
30. probable/multiple Tool 2 candidate metadata does not leak into confirmed Renamer enrichment through Tool 3;
31. historical stored Tool 2 result is not treated as current Media authority on an independent Tool 3 run that requires current Media context;
32. Media context unavailable + static reference available may still produce explicitly provisional schedule evidence and records Media unavailability;
33. Tool 3 schedule-only enrichment remains `ResolutionState.PROVISIONAL` in Tool 1 registry;
34. existing Tool 2 confirmed enrichment still retains its accepted stronger state semantics after the enrichment-contract extension;
35. only safe unique `PROVISIONAL_ENRICHMENT` auto-hands off to Renamer;
36. multiple/conflict/no-support/insufficient/unavailable states do not change Tool 1 selected fields;
37. rerunning Tool 3 is idempotent and does not duplicate evidence/proposal tokens;
38. CLI batch works offline using verified reference;
39. portal displays Tool 3 evidence/candidates and uses service boundaries rather than direct mutations;
40. batch isolates one-file errors and continues.

The Builder should add further regression tests discovered during implementation/review.

---

## 36. Representative acceptance evaluation

Before `READY_FOR_REVIEW`, run Tool 3 safely across the project's current 260 representative sample files using:

```text
fresh Tool 1 structured population
→ Tool 2 current Media review context
→ verified static travel_schedule reference
→ Tool 3 review
→ Tool 1 provisional enrichment where allowed
```

This evaluation must not mutate Baserow or commit physical filesystem renames.

Report at least:

```text
total files reviewed
files entering Tool 3 from Tool 2/downstream routing
CORROBORATED count
PROVISIONAL_ENRICHMENT count
  WHEN enrichments
  WHERE enrichments
MULTIPLE_SCHEDULE_CANDIDATES count
SCHEDULE_CONFLICT count
NO_SCHEDULE_SUPPORT count
INSUFFICIENT_EVIDENCE count
REFERENCE_UNAVAILABLE count
Media-context-unavailable count
number of schedule enrichments applied to Tool 1
number of high-priority local/confirmed-Media values overwritten (must be 0)
```

Provide representative before/after proposed filenames for schedule-enriched files when such examples exist, showing clearly that schedule-derived fields remain provisional in structured state.

Also demonstrate at least one corroboration/conflict/multiple/no-support case from the sample if available. If a category does not occur naturally, synthetic regression coverage is sufficient and the evaluation report should say so rather than fabricate an example.

---

## 37. Acceptance criteria

Tool 3 is acceptable only when all of the following are true:

1. finalized build-plan behavior is implemented through reusable Python services;
2. no Baserow access or mutation exists in Tool 3;
3. schedule bootstrap/verification calls Tool 2's read-only service without passing credentials/provider behavior into Tool 3;
4. the complete `travel_schedule` table can be bootstrapped once into a verified immutable local reference;
5. normal per-file review uses the verified reference without network access;
6. schedule reference integrity is protected by deterministic checksum/provenance;
7. an unexpected remote schedule change is surfaced rather than silently accepted;
8. confirmed Media metadata is never overwritten by schedule/local evidence, and contradictory local evidence remains visible for review;
9. schedule absence is not treated as proof of absence;
10. known-location/missing-date and known-date/missing-location cases behave according to Sections 16–17;
11. both-missing input never triggers an unconstrained schedule guess;
12. multiple schedule possibilities never use first-match-wins;
13. schedule-derived Tool 1 enrichment remains provisional;
14. accepted Tool 1 and Tool 2 behavior/tests remain intact;
15. CLI and portal use the same Tool 3 application service;
16. static schedule data remains local/uncommitted unless explicitly authorized later;
17. all required tests and full project pytest suite pass;
18. package build/helper CI checks pass;
19. representative 260-file evaluation is completed and documented;
20. implementation is committed/pushed to `tool-3-implementation`, PR is open/up-to-date, required GitHub CI is green, and status is `READY_FOR_REVIEW` before orchestrator review.

---

## 38. Out of scope

Tool 3 does **not**:

- prove actual physical presence from a planned itinerary;
- write/update/delete Baserow rows;
- replace Tool 2 Media reconciliation;
- parse/rewrite raw filenames independently of Tool 1;
- analyze audio content;
- infer GPS/location from audio;
- use arbitrary web search/YouTube as travel evidence;
- infer continuous presence between separate schedule rows;
- force immediate human review for ordinary missing metadata;
- choose among high-authority filename vs confirmed-Media contradictions;
- physically rename/move/cut/trim/gain-process media files.

---

## 39. Builder implementation order

Recommended implementation sequence:

1. read this plan, Tool 3 status, architecture/protocol, static-schedule policy, accepted Tool 1/2 code/tests;
2. define typed Travel Schedule reference/result models and static-reference integrity contract;
3. expose/reuse a complete schedule bootstrap operation through Tool 2's read-only application-service boundary;
4. implement local reference bootstrap/load/verify/checksum behavior;
5. implement deterministic date/range/location normalization and indexes;
6. implement candidate retrieval + decision engine for Sections 15–23;
7. add registry persistence/audit;
8. extend Tool 1 enrichment contract minimally so schedule-derived fields remain provisional;
9. implement application service + safe automatic Renamer handoff;
10. add CLI and portal surfaces;
11. add all required deterministic tests and protect accepted Tool 1/2 behavior;
12. run full pytest/build/helper checks;
13. run/document the safe 260-file Tool 1 → Tool 2 → Tool 3 evaluation;
14. commit/push, open/update PR, wait for required CI, then set `READY_FOR_REVIEW`.

---

## 40. Definition of done

Tool 3 is done when another component can ask:

> "Given what we already know about this recording's date/location, what does the immutable historical travel schedule support, contradict, or suggest?"

and receive a deterministic, explainable structured answer that:

- never overstates planned travel as proof;
- preserves stronger filename/Media evidence;
- safely suggests missing WHEN/WHERE only when uniquely supported;
- keeps all schedule-only enrichment provisional;
- works efficiently offline from a verified static reference;
- feeds Tool 1 and later tools without duplicating their responsibilities.
