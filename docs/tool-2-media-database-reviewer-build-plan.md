# Tool 2 — Media Database Reviewer Build Plan

Status: **FINALIZED — implementation-ready**

Tracking issue: #2

This document is the authoritative implementation specification for Tool 2. The implementation model must treat it as read-only and use `status/tool-2-media-database-reviewer.md` for progress, questions, review findings, and commit checkpoints.

Post-acceptance architecture amendment: `docs/baserow-access-boundary-amendment.md`

The amendment confirms Tool 2 as the read-only Baserow Media lookup/reconciliation boundary. Tool 2 may read current Baserow data but must never mutate rows, select options, or schema. Tool 4 uses Tool 2 for existing-item/candidate checks and remains the only writer.

## 1. Purpose

Tool 2 is the project's **read-only Baserow Media lookup and reconciliation service**.

It queries current Media data so that other tools — especially the Renamer and Tool 4 — can:

- search for possible matching logical media items;
- compare Baserow metadata with information already interpreted from the filename/path;
- compare recording dates and detect contradictions;
- recover stronger or missing metadata from a confirmed Media row;
- return confirmed enrichment evidence to a later Renamer pass;
- supply a structured association/reconciliation result to Tool 3, Tool 4, the review portal, and the future orchestrator.

Tool 2 is not merely a UI review screen. It is a reusable application service with CLI and review-portal adapters.

The fast metadata sequence remains:

```text
Tool 1 Renamer (initial)
→ Tool 2 Media Database Reviewer
→ Tool 3 Travel Schedule Reviewer
→ Tool 4 Media Database Updater
→ Tool 1 Renamer (enrich)
```

## 2. Tool 2 and Tool 3 remain separate

Tool 2 and Tool 3 intentionally overlap at the edge because both may consult travel-related evidence, but they remain separate tools.

Tool 2 may read `travel_schedule` only as **supporting candidate-search/reconciliation context**. It may use date/place schedule evidence to narrow or explain Media candidates.

Tool 3 remains responsible for the deeper Travel Schedule Reviewer role, including interpreting schedule ranges/gaps, conflicts, uncertain presence, original schedule text, and producing dedicated structured travel conclusions.

Tool 2 must not silently absorb Tool 3's complete responsibility.

## 3. Core archive semantics

- One Baserow `media` row represents one **logical media item / recording**.
- One logical media item may have multiple related files, formats, attachments, URLs, or versions.
- A new format/version of the same recording belongs to the same logical Media row when identity is established.
- Filename equality is not proof of logical identity or physical duplication.
- Distinct recordings may legitimately share the same WHEN/WHAT/WHERE semantics.
- Physical duplicate detection, content fingerprinting, and audio-level duplicate proof are outside Tool 2.
- Existing fact-checked Baserow Media values are strong evidence.
- Contradictions are retained and surfaced; Tool 2 never silently rewrites either the filename evidence or the Baserow fact.

User clarification (2026-09-17): once Tool 2 safely confirms that an existing Media row represents the same logical recording, the relevant populated metadata currently stored in that row is leading and confirmed. Tool 1 should use that confirmed metadata when producing the final filename. This does not make probable, multiple, duplicate-looking, or otherwise unconfirmed candidate rows authoritative.

## 4. Baserow scope and read-only boundary

The Media database currently contains:

```text
media
users
category_title
travel_schedule
```

Tool 2 uses:

- `media` — primary logical media-item rows and metadata;
- `category_title` — canonical category/title reference data and matching support;
- `travel_schedule` — supporting date/place context for candidate lookup/reconciliation.

Tool 2 does **not** use `users` for the current workflow.

All Tool 2 Baserow operations are **read-only**.

Tool 2 must not create, update, merge, or delete Baserow rows. Tool 4 owns database mutation.

## 5. Baserow connectivity and configuration

Use the project's typed configuration and Baserow adapter abstraction.

Requirements:

- credentials, API URL/MCP endpoint, and table/database IDs come from the local `.env` configuration contract;
- secrets must never be committed or logged;
- REST API and/or MCP may be used behind the same provider boundary;
- transport choice must not change application semantics;
- live schema must be discovered when possible rather than scattering undocumented raw field names throughout Tool 2 logic;
- table pagination must be complete; never treat the first page as the entire database;
- rate limits/transient failures must be handled without corrupting batch state.

The implementation may choose the HTTP client and exact provider classes.

## 6. Live authority, local snapshot, and offline behavior

Baserow is authoritative shared state. Tool 2 local storage is only an operational cache/snapshot and review history.

At the beginning of a batch/session, Tool 2 should attempt to obtain a complete live snapshot of the relevant `media`, `category_title`, and `travel_schedule` data needed for deterministic matching.

Persist enough snapshot metadata to reproduce/explain a decision:

- retrieval timestamp;
- database/table identity;
- Baserow row IDs;
- raw values used by comparison;
- normalized semantic projection;
- schema/field mapping version or representation;
- snapshot completeness state.

Use snapshot states conceptually equivalent to:

```text
LIVE_COMPLETE
CACHED_STALE
UNAVAILABLE
```

Rules:

- successful live data overrides stale local cache;
- cached stale data may be used as supporting context when live access fails;
- stale cache must not be used to claim a complete no-match/new-item result;
- Baserow/network/schema failure must never be interpreted as "no matching media exists";
- `baserow_check_complete=true` requires a complete usable database review, not a failed live request hidden behind stale cache.

## 7. Schema mapping

Tool 2 must map Baserow fields to semantic roles through one adapter/configuration layer.

Known semantic roles include at least:

```text
media row ID
recording date / WHEN
media title
specific WHAT / scripture / content title where represented
category
place/location
country
filename/source filename
source identifiers
attachments/files
URLs
format/media-type information
fact-check / confirmation indicators where available
```

For `category_title`, map at least the available category/title/matching-term/reference fields.

For `travel_schedule`, map at least available start/end date, original schedule text, place/location, and country fields.

If a live schema does not expose a conceptual field, preserve the raw row and mark the role unavailable. Do not invent values.

## 8. Tool 2 input contract

Primary input is the structured result/state already produced by Tool 1, not a fresh filename reparse.

Conceptually the request contains:

```text
tracking_id
original/current path and filename
current best WHEN + state + alternatives/evidence
current best WHAT + category + state + alternatives/evidence
current best WHERE + country + state + alternatives/evidence
source/sequence identifiers
technical flags
edited flag
file extension/format
existing downstream routing/conflicts
```

Other tools may call the same programmatic lookup service with equivalent structured criteria.

Tool 2 must not duplicate the Renamer parser.

## 9. Primary use case

Given:

```text
2014-08-04_KKS_BG-1-18_Leipzig-de.mp3
```

Tool 2 searches for plausible Baserow Media candidates and compares their metadata with:

```text
WHEN  = 2014-08-04
WHAT  = BG-1-18
WHERE = Leipzig-de
```

If a confirmed candidate has title:

```text
Love in the Spiritual World
```

Tool 2 returns the **full title** as confirmed enrichment evidence.

The later Renamer may render:

```text
2014-08-04_KKS_BG-1-18-Love-in-the-Spiritual-World_Leipzig-de.mp3
```

Tool 2 does not directly rename the physical file. Tool 1 remains the canonical filename renderer/validator.

## 10. Candidate retrieval strategy

Candidate generation proceeds from strongest identity evidence to weaker semantic evidence.

Order:

1. exact direct source/media identity already represented in Baserow (unique source ID, canonical URL, attachment identity, explicit stored file identity, etc.);
2. exact normalized stored source filename/identifier when sufficiently discriminating;
3. exact full date + specific WHAT + exact normalized place/country;
4. exact full date + specific WHAT with independent corroboration from another meaningful field/reference;
5. exact/compatible date-centered retrieval followed by WHAT/category/location narrowing;
6. `category_title` matching/normalization support;
7. `travel_schedule` date/place corroboration as supporting evidence;
8. partial semantic combinations;
9. bounded fuzzy comparison only within a plausible candidate set.

Do not perform unconstrained fuzzy comparison of every file against every Media row/field when candidate narrowing is possible.

Every candidate must record why it was retrieved.

## 11. Automatic association rules

Tool 2 may automatically classify `EXISTING_MEDIA_MATCH` only when one of these conditions is satisfied and there is no material contradiction:

### A. Direct identity

A unique exact direct identity links the incoming file to one Media row.

### B. Unique high-specificity semantic signature

Exactly one candidate matches a high-specificity signature such as:

```text
exact full WHEN
+ specific WHAT/scripture reference
+ exact normalized WHERE/country
```

or:

```text
exact full WHEN
+ specific WHAT/scripture reference
+ one independent corroborating field/source identity/category/title reference
```

and no equally plausible competing row exists.

A high-specificity semantic association establishes a logical-media candidate, not physical duplication.

If two or more rows satisfy the same high-specificity signature, Tool 2 must not choose first-match-wins.

## 12. Probable/ambiguous association rules

Use `PROBABLE_EXISTING_MEDIA` when a candidate is plausible but the evidence does not meet the confirmed-match rule.

Use `MULTIPLE_CANDIDATES` when multiple rows remain comparably plausible.

Use `INSUFFICIENT_EVIDENCE` when the input is too sparse to support either an existing-row association or a trustworthy no-match/new-item conclusion.

Probable/ambiguous candidate metadata must **not** be emitted as confirmed Renamer enrichment.

These cases may continue to Tool 3 or other downstream evidence stages before human review is required.

## 13. Contradiction behavior

Tool 2 compares candidate metadata field-by-field and retains exact conflicting values/sources.

Example:

```text
filename WHEN: 2014-08-04
candidate Media WHEN: 2014-04-08
```

This is a material date contradiction.

Tool 2 must:

- retain the candidate if it remains otherwise plausible;
- record the conflicting field and both values;
- classify the field comparison as `CONFLICT`;
- avoid silently swapping day/month or correcting either value;
- not automatically use the candidate's title/location/etc. as confirmed enrichment while the association is unresolved;
- route the unresolved evidence onward (including Tool 3 where travel/date/place evidence may help) or to human review when downstream evidence cannot safely decide.

Contradiction is a flag/state, not permission to mutate Baserow.

## 14. Field comparison states

Each relevant field comparison uses states conceptually equivalent to:

```text
AGREES
DATABASE_MISSING
LOCAL_MISSING
CONFLICT
NOT_COMPARABLE
```

Blank values are not contradictions.

Examples:

- exact same date → `AGREES`;
- local WHERE known, Baserow place blank → `DATABASE_MISSING`;
- Baserow title present, local title absent → `LOCAL_MISSING`;
- incompatible dates → `CONFLICT`;
- one representation cannot be meaningfully compared → `NOT_COMPARABLE`.

## 15. Decision states

Tool 2 result uses a small explicit decision set:

```text
EXISTING_MEDIA_MATCH
PROBABLE_EXISTING_MEDIA
NEW_MEDIA_CANDIDATE
MULTIPLE_CANDIDATES
CONFLICT_WITH_EXISTING
INSUFFICIENT_EVIDENCE
DATABASE_UNAVAILABLE
```

Meaning:

- `EXISTING_MEDIA_MATCH` — confirmed enough to return selected Media metadata as Renamer enrichment.
- `PROBABLE_EXISTING_MEDIA` — likely association, but enrichment remains unconfirmed.
- `NEW_MEDIA_CANDIDATE` — complete live search found no plausible row and input was sufficiently discriminating.
- `MULTIPLE_CANDIDATES` — no safe unique selection.
- `CONFLICT_WITH_EXISTING` — a candidate association is materially contradicted by evidence.
- `INSUFFICIENT_EVIDENCE` — too little evidence to decide.
- `DATABASE_UNAVAILABLE` — complete database review could not be performed.

Do not expose a single opaque numeric confidence score as the primary decision mechanism.

## 16. Human review vs downstream routing

Follow the project's progressive-processing rule: missing or merely incomplete metadata is not automatically a human-review task.

Tool 2 should distinguish:

```text
confirmed automatic result
continue to downstream evidence
human review required now
blocked/database error
```

Examples:

- `PROBABLE_EXISTING_MEDIA` may continue to Tool 3 when travel/date/place evidence could strengthen or reject it;
- `MULTIPLE_CANDIDATES` may continue to Tool 3 if travel evidence can discriminate them;
- a material direct-identity contradiction or irreducible equally plausible candidates may require human review;
- `DATABASE_UNAVAILABLE` is a blocked database stage, not a human metadata decision;
- ordinary missing title/location is not by itself a human review task.

All candidates/conflicts remain visible in the review portal even when not yet queued for human action.

## 17. Evidence precedence

Default reconciliation precedence:

1. exact direct row-linked source identity;
2. explicit human-confirmed Tool 2 association;
3. existing fact-checked Baserow Media metadata;
4. exact/strong Tool 1 filename/path interpretation;
5. corroborating `category_title` and relevant `travel_schedule` evidence;
6. provisional Tool 1 interpretation;
7. weak filesystem/context clues.

This hierarchy ranks evidence; it does not authorize silent correction of a contradiction.

## 18. `category_title` usage

Use the live `category_title` table for:

- canonical category/title normalization;
- title matching terms where present;
- explaining known title/category terms;
- narrowing candidate Media rows;
- corroborating specific WHAT/title evidence.

A broad category must not flatten or replace a more specific WHAT/scripture/title.

## 19. `travel_schedule` usage and Tool 3 boundary

Tool 2 may use `travel_schedule` for bounded supporting checks such as:

- exact date/place agreement strengthening a Media candidate;
- a schedule place supporting a candidate when incoming WHERE is missing;
- a date/place mismatch explaining why a candidate stays probable/conflicting.

Tool 2 must preserve the original schedule evidence used.

Tool 2 must not infer continuous presence merely because a date falls between schedule entries.

Tool 3 performs full travel-schedule interpretation and may return stronger evidence to Tool 4 and the later Renamer.

## 20. Confirmed enrichment contract

Confirmed enrichment returned to the Renamer must be separated from candidate-only metadata.

Conceptually:

```text
renamer_enrichment
  confirmed: true | false
  media_row_id
  when_val
  what_val
  title_full
  where_val
  category
  source_identifiers
  evidence[]
```

Rules:

- only `EXISTING_MEDIA_MATCH` or an explicit human-confirmed association may set `confirmed=true`;
- candidate-only title/location/date values remain under the candidate record and are not silently copied into confirmed enrichment;
- full original Baserow title is always retained in evidence.

## 21. Long-title filename policy

User policy: **long Baserow titles are shortened automatically for filenames**.

The full Baserow title remains authoritative metadata and must never be altered in Baserow or discarded from Tool 2 evidence.

Tool 1 remains responsible for filename rendering. When confirmed title enrichment would cause the rendered filename to exceed the existing filename-length budget, the Renamer/common filename component logic must shorten the title deterministically.

Rules:

- preserve the full title in structured metadata/evidence;
- use the full normalized title when the filename fits the hard 128-character limit;
- when necessary, shorten only the title component first;
- shorten at whole-word boundaries; never cut a word in the middle;
- keep the leading informative title words in original order;
- use deterministic normalization so reruns produce the same result;
- record both `title_full` and the rendered/shortened title component plus a `length_budget`/compaction reason in diagnostics/history;
- do not invoke an LLM to summarize the title;
- do not send the file to human review merely because the title is long;
- if the mandatory non-title components themselves cannot satisfy the hard filename limit after existing safe abbreviations, follow the Renamer's existing overlength safety/review behavior.

The preferred short-filename convention remains aspirational; automatic title compaction is driven by the actual filename budget and hard 128-character ceiling, not by blindly forcing all title-enriched filenames below 25 characters.

## 22. `_edited` lifecycle

An `_edited` file still requires Tool 2's Baserow Media check.

Tool 2 outputs:

```text
baserow_check_complete = true | false
```

Only a completed usable database review, including a valid complete no-match result, sets it true.

Database/network/schema failure leaves it false.

A later Renamer pass may use this evidence to remove `_edited` according to Tool 1 policy.

## 23. New-media candidate policy

Tool 2 may return `NEW_MEDIA_CANDIDATE` only when:

- live database access/snapshot is complete;
- the search strategy was executed successfully;
- the input has enough discriminating evidence that a no-match conclusion is meaningful;
- no plausible existing candidate remains.

Sparse input produces `INSUFFICIENT_EVIDENCE`, not a false new-item conclusion.

Tool 2 itself does not create the new Media row.

## 24. Related formats and versions

When confirmed evidence shows the incoming file is another format/version of an existing logical media item, return:

- selected Media row ID;
- incoming format/extension;
- relevant existing attachments/files/URLs/formats;
- proposed Tool 4 enrichment/link action;
- conflicts if two physical files claim the same role/slot where that concept exists.

Do not create a second logical Media item merely because extensions differ.

## 25. No open-ended external discovery in v1

Tool 2 v1 does not perform open-ended YouTube/web searching to find recordings.

It may normalize and compare URLs/source IDs already present in Tool 1 state or Baserow rows.

External source discovery can be added later as an explicit tool/provider without changing Tool 2's core database-reconciliation role.

## 26. Structured result contract

The exact Python class names are builder discretion, but the result must be typed, JSON-serializable, persistable in the local registry, and consumable by Tool 3, Tool 4, Renamer Enrich, CLI, and review portal.

Conceptually:

```text
MediaDatabaseReviewResult
  tracking_id
  database_state
  database_snapshot_at
  snapshot_complete
  baserow_check_complete

  decision
  decision_state
  selected_media_row_id

  candidates[]
    media_row_id
    raw/normalized identity
    retrieval_reasons[]
    identity_evidence[]
    field_comparisons{}
    conflicts[]
    possible_enrichments[]
    category_title_context[]
    travel_schedule_context[]

  selected_field_evidence
    WHEN
    WHAT
    title
    WHERE
    category
    source_identifiers
    travel_context

  renamer_enrichment
    confirmed
    media_row_id
    when_val
    what_val
    title_full
    where_val
    category
    evidence[]

  proposed_tool4_action
    link_existing | enrich_existing | create_new | no_write | needs_review

  downstream_routing[]
  review_required
  review_reasons[]
  conflicts[]
  evidence[]
```

Candidate metadata and confirmed enrichment must be represented separately.

## 27. Local persistence and idempotency

Reuse the project local SQLite registry/cache infrastructure rather than creating an unrelated state system.

Persist enough to support:

- Tool 2 result by tracking ID;
- selected/confirmed Baserow row ID;
- snapshot metadata;
- candidate IDs and comparison outcomes;
- conflicts/review reasons;
- downstream routing;
- human association decisions;
- audit/history entries;
- later Tool 4 / Renamer Enrich consumption.

Rerunning Tool 2 with the same input and same Baserow snapshot must be deterministic and must not duplicate review history/events unnecessarily.

Human decisions remain local operational state until Tool 4 writes appropriate changes to Baserow.

## 28. Application-service boundary

Core matching/reconciliation logic must live in reusable Python application/domain services.

Required interfaces conceptually include:

```text
refresh/load database snapshot
review one structured media input
review a batch
search candidates from structured criteria
apply human candidate decision
retrieve stored review result
```

The builder owns exact class/function names.

The CLI, review portal, and future orchestrator must call these services; they must not duplicate matching rules.

## 29. CLI

Add Tool 2 under the existing `media-archive` executable, conceptually:

```text
media-archive media-db-review ...
```

Support at least:

- one file/tracking ID review;
- batch review;
- dry/read-only operation (Tool 2 is inherently no-write to Baserow);
- machine-readable result output suitable for diagnostics/tests;
- clear reporting of database unavailable vs valid no-match;
- optional snapshot refresh/reuse behavior as appropriate.

Exact CLI framework/options are builder discretion.

## 30. Review portal

Extend the existing localhost review portal; do not create a second independent UI.

A Tool 2 detail view should show:

- incoming file and current Tool 1 interpretation;
- candidate Media rows;
- candidate row IDs and retrieval reasons;
- date/WHAT/title/category/place/country/source fields;
- clear `AGREES`, missing, and `CONFLICT` comparisons;
- `category_title` support when relevant;
- `travel_schedule` support when relevant;
- available attachments/formats/source references where useful;
- proposed decision and Tool 4 action;
- full confirmed title and the eventual filename-enrichment preview/compaction diagnostic when available.

Structured human actions:

```text
Confirm existing row
Choose another candidate row
Confirm new media item
Defer / insufficient evidence
```

The portal stores decisions locally through the application service. It never writes Baserow directly.

## 31. Batch behavior

- One ambiguous/error file never blocks unrelated files.
- Baserow reads are batched/cached; thousands of local files must not trigger repeated full-table downloads per file.
- Candidate analysis may be concurrent where safe, but snapshot semantics must remain deterministic.
- Database outage blocks/degrades only the database-dependent stage.
- Complete no-match and database unavailable must remain distinct.
- Read-only reruns are safe and idempotent.

## 32. Logging and diagnostics

Per-file structured diagnostics must include:

- tracking ID/input summary;
- snapshot timestamp/state;
- candidate Media row IDs;
- retrieval/match reasons;
- tables/evidence sources used;
- field comparison states;
- conflicts and alternatives;
- selected decision;
- automatic vs human-confirmed association;
- confirmed Renamer enrichment returned;
- downstream routing;
- proposed Tool 4 action;
- database/provider errors.

Incorrect automatic existing-row associations are high-severity defects in evaluation.

## 33. Required tests

At minimum implement tests for:

1. read-only Baserow adapter behavior using configuration without exposing secrets;
2. complete pagination for all required tables;
3. schema mapping for `media`, `category_title`, `travel_schedule`;
4. live-over-cache authority;
5. stale cache not producing complete no-match/new-item;
6. unique direct identity → `EXISTING_MEDIA_MATCH`;
7. exact full date + scripture WHAT + place unique candidate → confirmed match;
8. exact date + specific WHAT + corroborating field unique candidate → confirmed match;
9. two equally plausible rows → `MULTIPLE_CANDIDATES`, never first-match-wins;
10. partial candidate → `PROBABLE_EXISTING_MEDIA`, no confirmed enrichment;
11. Leipzig example finds the correct candidate and returns full title `Love in the Spiritual World`;
12. later Renamer enrichment renders `BG-1-18-Love-in-the-Spiritual-World` correctly;
13. long confirmed Baserow title is preserved in evidence and automatically shortened at word boundaries only as needed to satisfy filename length;
14. title shortening is deterministic across reruns;
15. `2014-08-04` filename vs `2014-04-08` Media date → explicit conflict and no automatic title enrichment;
16. blank database field is `DATABASE_MISSING`, not conflict;
17. local missing field is `LOCAL_MISSING` and may be enrichment when association confirmed;
18. `category_title` corroborates/normalizes without flattening specific WHAT;
19. `travel_schedule` corroborates a candidate without being treated as continuous-presence proof;
20. valid complete no-match → `NEW_MEDIA_CANDIDATE` when input is discriminating;
21. sparse no-match → `INSUFFICIENT_EVIDENCE`;
22. database unavailable → `DATABASE_UNAVAILABLE` and `baserow_check_complete=false`;
23. valid complete no-match → `baserow_check_complete=true`;
24. `_edited` lifecycle handoff to Renamer;
25. related format maps to same logical Media row when identity is established;
26. human confirms candidate and result becomes confirmed enrichment with audit provenance;
27. human confirms new media candidate locally without writing Baserow;
28. idempotent rerun against same snapshot;
29. batch continues when one item errors/ambiguous;
30. Tool 4 handoff carries row ID/snapshot timestamp so Tool 4 can re-check live state before mutation.

## 34. Sample evaluation requirements

Before acceptance, evaluate Tool 2 against a representative sample of actual archive files and the real read-only Baserow database (or a safely captured representative snapshot when live evaluation is impractical).

Report at least:

```text
total files
confirmed existing matches
probable existing matches
multiple candidates
new-media candidates
insufficient evidence
conflicts
database failures
human-review-required-now
downstream-to-Tool-3 count
confirmed title/metadata enrichments
```

Manually inspect a meaningful sample of automatic confirmed associations, especially title enrichments and date/location matches.

Do not label algorithmic confidence as semantic correctness without ground truth/human verification.

## 35. Acceptance criteria

Tool 2 is acceptable only when all of the following are demonstrated:

1. Baserow access is read-only.
2. `media`, `category_title`, and `travel_schedule` are available through the provider/snapshot layer; `users` is not unnecessarily queried.
3. Complete pagination/schema mapping is verified.
4. Live Baserow is authoritative over cache; offline/incomplete states cannot masquerade as valid no-match.
5. Candidate retrieval is bounded/explainable and does not use first-match-wins.
6. Direct and unique high-specificity matches can be confirmed automatically under the rules above.
7. probable/multiple/conflicting candidates remain unconfirmed and cannot leak metadata into Renamer enrichment.
8. material date/metadata contradictions are preserved and flagged.
9. confirmed Baserow metadata can enrich the later Renamer pass.
10. full Baserow titles are preserved while overlong filename title components shorten automatically, deterministically, and at word boundaries.
11. Tool 2 and Tool 3 boundaries are preserved: travel schedule is supporting context in Tool 2, full schedule review remains Tool 3.
12. `_edited` Baserow-check lifecycle works correctly.
13. complete no-match, sparse evidence, and database unavailable are distinct states.
14. related formats can associate with one logical media item without being declared duplicates.
15. CLI and review portal use the shared application service.
16. local result/review history is idempotent and auditable.
17. Tool 4 handoff contains enough row/snapshot evidence for a live re-check before write.
18. required tests pass under the project Python 3.12 + `uv` environment.
19. representative sample evaluation is documented and reviewed.
20. implementation commits are reviewed through the accepted HEAD with no unauthorized fundamental changes.

## 36. Builder implementation order

Recommended implementation sequence:

1. read this finalized plan, project architecture, implementation protocol, and Tool 2 status;
2. inspect existing Tool 1 result/enrichment/registry interfaces and reuse compatible structures;
3. implement typed Baserow read/snapshot/schema provider for all three tables;
4. implement normalized semantic row projections and field comparison;
5. implement candidate retrieval/narrowing and decision rules;
6. implement confirmed-vs-candidate enrichment separation;
7. implement local persistence/audit/idempotency;
8. integrate confirmed enrichment with Renamer Enrich, including deterministic long-title compaction;
9. add CLI adapter;
10. extend the existing review portal;
11. add full mocked regression suite;
12. run real/snapshot sample evaluation;
13. update status, commit, push, verify reachable HEAD, and mark `READY_FOR_REVIEW` only when the plan is satisfied.

## 37. Builder discretion

The builder may choose ordinary implementation details that do not change behavior, such as:

- exact Python class/module names;
- HTTP/MCP transport implementation;
- SQLite table/index details;
- query batching/chunk sizes;
- fuzzy matching library/threshold implementation, provided near-ties remain safe and explainable;
- CLI option names;
- portal layout/CSS;
- test helpers/fixtures;
- equivalent maintained dependencies.

The builder must raise a `Q-###` in the status file instead of guessing if the live Baserow schema or behavior makes an archive/data-semantics requirement impossible or contradictory.

## 38. Category-title lookup requested by Tool 1 (2026-09-25)

Tool 1 may ask Tool 2 for a live, read-only `category_title` lookup while
finalizing an uncertain title. Return a typed result with matched row ID,
matched `title_matching_terms` term, exact `category` value, read timestamp,
and completeness/ambiguity status. Use whole terms/phrases, prefer specific
matches, and do not infer a unique category from a tie or incomplete read.
For `2012-01-02_KKS_CC-Talk_Simhachalam_de.mp3`, `CC` must find the owner's
`category_title` row 5 and category `Caitanya-caritamrta`. Tool 1 gets this
evidence through Tool 2, without direct Baserow access. Tool 4 consumes and
revalidates it before writing the existing Media Category select option. See
Tool 4 plan Section 27; keep Tool 2 strictly read-only.
