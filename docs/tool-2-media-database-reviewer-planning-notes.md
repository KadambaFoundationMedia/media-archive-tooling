# Tool 2 — Media Database Reviewer Planning Notes

Status: **DRAFT PLANNING NOTES — not a finalized build plan and not an implementation handoff**

These notes capture the current planning direction for Tool 2. They must not be treated as authoritative implementation requirements until converted into `docs/tool-2-media-database-reviewer-build-plan.md` and explicitly marked finalized.

## 1. Purpose

Tool 2 is the project's **read-only Baserow media lookup and reconciliation service**.

It queries the Media database for relevant media items and metadata so that other tools — especially the Renamer — can:

- search for possible matching media items;
- compare database metadata with information parsed from the filename/path;
- compare recording dates and detect contradictions;
- recover stronger or missing metadata from a matched Media row;
- use confirmed database metadata to enrich a later Renamer pass.

Tool 2 reviews each incoming/local archive file against the authoritative Baserow Media database and determines whether the file most likely belongs to an existing logical media item, represents a new logical media item, or requires human review because the database evidence is ambiguous or contradictory.

Tool 2 is a **read/reconcile/review stage**, not the Baserow mutation stage. Tool 4 (Media Database Updater) performs writes after Tool 2 and Tool 3 have produced structured evidence.

The intended fast metadata sequence remains:

```text
Tool 1 Renamer (initial)
→ Tool 2 Media Database Reviewer
→ Tool 3 Travel Schedule Reviewer
→ Tool 4 Media Database Updater
→ Tool 1 Renamer (enrich)
```

Tool 2 must expose a reusable programmatic lookup/review interface so the Renamer and later orchestrator can call it directly; the CLI and review portal are adapters around the same service.

## 2. Core archive semantics already fixed

- One Baserow Media row represents one **logical media item / recording**.
- One logical media item may have multiple related files or formats.
- A new format/version of the same recording should enrich the existing Media row rather than create a duplicate media item.
- Filename equality is not proof of duplicate content or identity.
- Distinct recordings may legitimately share the same canonical WWWW filename semantics.
- Physical duplicate detection, audio fingerprinting, and content-level duplicate proof are outside Tool 2.
- Existing fact-checked Baserow Media values are strong evidence and must not be overwritten by weaker filename/folder inference.
- Contradictions are retained and surfaced for review rather than silently resolved.

## 3. Baserow access and table scope

The Baserow Media database currently contains these tables:

```text
media
users
category_title
travel_schedule
```

Tool 2 uses:

- `media` — primary logical media-item rows and metadata;
- `category_title` — category/title reference data and matching/normalization support;
- `travel_schedule` — supporting date/place context when identifying or reconciling a media item.

Tool 2 does **not** need the `users` table for its current role.

Access requirements:

- use the Baserow API and/or MCP endpoint through the project Baserow adapter abstraction;
- credentials/configuration come from the local `.env` configuration contract;
- all Tool 2 Baserow operations are **read-only**;
- API/MCP transport choice must not change Tool 2 semantics;
- failures or incomplete access must be represented explicitly and must never be interpreted as "no matching media exists".

Tool 2 may use `travel_schedule` as supporting lookup evidence, but Tool 3 remains the dedicated Travel Schedule Reviewer responsible for deeper schedule reconciliation and structured travel-evidence conclusions. Tool 2 must not silently absorb or duplicate Tool 3's full responsibility.

## 4. Read-only boundary

Tool 2 must not create, update, merge, or delete Baserow rows.

It may:

- read the `media`, `category_title`, and `travel_schedule` tables and required schema/reference information;
- build a local batch snapshot/cache;
- normalize rows into semantic comparison representations;
- produce candidate matches;
- compare fields;
- record structured local review decisions;
- produce structured enrichment evidence for the Renamer;
- produce a proposed action for Tool 4.

Tool 4 must re-check the selected Baserow row/current version before writing so a stale Tool 2 snapshot cannot silently overwrite collaborator changes.

## 5. Baserow is authoritative shared state

The Baserow Media database is the shared cross-device source of truth. Tool 2 local state is only operational evidence and review state.

At batch start, Tool 2 should obtain a complete enough snapshot of the relevant tables for deterministic matching. The builder may choose pagination, indexed server-side retrieval, or a hybrid cache strategy, but the algorithm must not accidentally treat a partial first page as the full database.

The snapshot should record at least:

- retrieval timestamp;
- source database/table identities;
- row IDs;
- raw row values used for comparison;
- normalized semantic projections;
- any schema/field mapping used.

Live Baserow data remains authoritative over local cached copies when live access succeeds.

## 6. Schema handling

Do not assume undocumented Baserow field names in application logic.

Known semantic concepts include at least:

- recording date / WHEN;
- media title;
- title/topic/scripture/class WHAT where represented;
- category where represented;
- place/location;
- country;
- file/format/attachment/path/URL/source identifiers where represented;
- any existing filename or media-source identity fields useful for deterministic matching.

The implementation should obtain the live schema when possible and map raw field names to semantic roles through one adapter/configuration layer. If the live schema lacks a useful role, preserve the raw row rather than inventing a field.

The `category_title` and `travel_schedule` schemas must likewise be discovered/mapped through the adapter rather than scattering raw Baserow field names through matching logic.

## 7. Tool 2 input

The primary input for each file should be the current structured Tool 1 result plus local identity/state, conceptually:

```text
tracking_id
original/current path and filename
current best WHEN
current best WHAT
current best WHERE
category
source/sequence identifiers
technical flags
edited flag
file extension/format
Tool 1 evidence and alternatives
```

Tool 2 should not reimplement the Renamer parser. It consumes Tool 1 semantic output and may normalize Baserow values for comparison.

Other project tools may also call Tool 2's programmatic query interface with equivalent structured search criteria.

## 8. Primary use case and enrichment behavior

Example Renamer output:

```text
2014-08-04_KKS_BG-1-18_Leipzig-de.mp3
```

Tool 2 searches Baserow for the most plausible corresponding Media row. It compares the row's metadata with the filename-derived values, including at minimum the date, WHAT/title/category information, and location where available.

If a sufficiently strong/confirmed match contains useful metadata missing from the current filename, Tool 2 returns that metadata as structured enrichment evidence for a later Renamer pass.

For example, if the matching Media row has title:

```text
Love in the Spiritual World
```

then under the existing Tool 1 naming rules the title belongs in the WHAT field alongside the specific scripture reference. The later Renamer may therefore render:

```text
2014-08-04_KKS_BG-1-18-Love-in-the-Spiritual-World_Leipzig-de.mp3
```

Tool 2 itself does not directly rename the physical file. It supplies the confirmed metadata/evidence to the Renamer, which remains responsible for canonical filename rendering and validation.

## 9. Candidate retrieval order

Candidate discovery should proceed from strongest identity evidence to weaker semantic similarity.

Recommended order:

1. exact explicit media/source identity already represented in a Baserow row, such as a unique stored URL/source ID/file reference/attachment identity when available;
2. exact normalized source filename or known source identifier when the database stores it and the value is sufficiently discriminating;
3. exact/high-specificity semantic combinations such as WHEN + specific WHAT + WHERE;
4. date-centered retrieval using exact or compatible partial dates, then narrowing by WHAT/title/category/location;
5. supporting `category_title` reference matches;
6. supporting `travel_schedule` date/place context when useful for narrowing or explaining a candidate;
7. partial semantic combinations such as WHEN + WHAT, WHAT + WHERE, or partial date + specific WHAT + WHERE;
8. bounded normalized/fuzzy candidate generation only within plausible rows.

Do not query or fuzzy-compare every file against every field indiscriminately when indexed candidate retrieval can narrow the set.

The query strategy should prefer retrieving a small, explainable candidate set and then comparing those candidates field-by-field.

## 10. Identity decision policy

A unique direct identity match may be automatically classified as the existing logical media item when no contradictory row evidence exists.

A strong combination of independent semantic fields may also produce a high-quality candidate, but semantic equality alone must **not** prove physical duplication. In particular, a row with the same canonical WHEN/WHAT/WHERE may still represent a distinct recording.

Multiple equally plausible rows must never be resolved by first-match-wins.

A candidate with a **material contradiction** must not be treated as an automatically confirmed match merely because other fields agree.

## 11. Contradiction behavior

Tool 2 must explicitly compare candidate metadata against the incoming Tool 1 evidence and flag material contradictions.

Example:

```text
filename WHEN: 2014-08-04
candidate Media WHEN: 2014-04-08
```

This is a date contradiction and requires review/evidence resolution. Tool 2 must retain both values and their sources.

For a materially contradictory candidate:

- keep it in the candidate list when it remains otherwise plausible;
- record the exact conflicting fields and values;
- do not silently reinterpret one date as the other;
- do not automatically consume the candidate's title/location/etc. as confirmed enrichment merely because the row is close;
- allow later evidence or human review to confirm the correct association.

This prevents a plausible-but-wrong Media row from contaminating the filename.

## 12. Proposed review decisions

Tool 2 should produce one of a small set of explicit decisions, for example:

```text
EXISTING_MEDIA_MATCH
PROBABLE_EXISTING_MEDIA
NEW_MEDIA_CANDIDATE
MULTIPLE_CANDIDATES
CONFLICT_WITH_EXISTING
INSUFFICIENT_EVIDENCE
DATABASE_UNAVAILABLE
```

These are logical-media decisions, not physical duplicate classifications.

Each decision should also retain qualitative resolution state/evidence rather than an opaque user-facing percentage confidence.

`EXISTING_MEDIA_MATCH` means the candidate is safe to use as database enrichment evidence under the matching rules. `PROBABLE_EXISTING_MEDIA`, `MULTIPLE_CANDIDATES`, and `CONFLICT_WITH_EXISTING` must preserve uncertainty and not masquerade as a confirmed association.

## 13. Field-by-field reconciliation

For every candidate row, compare relevant semantic fields independently.

Suggested comparison states:

```text
AGREES
DATABASE_MISSING
LOCAL_MISSING
CONFLICT
NOT_COMPARABLE
```

Examples:

- local WHEN = `2012-05-13`, Baserow WHEN = `2012-05-13` → `AGREES`;
- local WHERE known, Baserow place blank → `DATABASE_MISSING` and possible enrichment for Tool 4;
- Baserow title/WHAT specific, local WHAT unresolved → `LOCAL_MISSING`; Baserow can become enrichment evidence when the candidate association is confirmed;
- fact-checked Baserow date conflicts with provisional filename date → `CONFLICT`, with both evidence paths retained for resolution.

Blank values are not contradictions.

## 14. Evidence precedence

Default precedence for Tool 2 comparison:

1. explicit/direct row-linked source identity;
2. human-confirmed Tool 2 review decision;
3. existing fact-checked Baserow Media fields;
4. exact/strong Tool 1 filename/path interpretation;
5. corroborating `category_title` and relevant `travel_schedule` evidence;
6. provisional Tool 1 interpretation;
7. weak filesystem/context clues.

This is a reconciliation hierarchy, not permission to overwrite contradictory facts automatically. Tool 2 may flag a possible Baserow error when strong direct evidence contradicts a row, but it must not silently rewrite the database. Tool 4 should require appropriate confirmation before replacing contradictory fact-checked values.

## 15. `category_title` usage

Tool 2 should use the Baserow `category_title` table as a live project reference source when comparing or interpreting Media rows and filename-derived WHAT/category evidence.

Uses include:

- canonical category/title normalization;
- title matching terms where present;
- explaining why a filename term corresponds to a known project title/category;
- helping narrow candidate Media rows;
- validating/enriching category/title evidence returned to the Renamer.

A broad category match must not replace a more specific Media title or scripture/content WHAT.

## 16. `travel_schedule` usage and Tool 3 boundary

Tool 2 may read `travel_schedule` to support candidate lookup and reconciliation, especially around recording date/place combinations.

Examples of appropriate Tool 2 use:

- the filename date/place agrees with a schedule entry and strengthens a Media candidate;
- the Media row has a place but the incoming filename is missing it, and schedule context provides corroboration;
- a date/place mismatch helps explain why a candidate should remain provisional/conflicting rather than confirmed.

However, Tool 2 should treat travel schedule evidence as supporting context, not automatically as proof of continuous presence.

Tool 3 remains responsible for the deeper Travel Schedule Reviewer role, including complete schedule interpretation, conflicts, gaps/ranges, and producing the stronger structured travel evidence used later by Tool 4 and the Renamer.

## 17. `_edited` requirement

An `_edited` file still requires the Baserow Media review.

Tool 2 should explicitly output whether the required Baserow check was completed successfully. Conceptually:

```text
baserow_check_complete = true | false
```

Only a completed database review (including a valid no-match result) may set this true. A database/network/configuration failure must leave it false.

The later Renamer may use this evidence to remove `_edited` according to the Tool 1 policy.

## 18. New media candidate policy

A complete search that yields no plausible existing row may classify the file as `NEW_MEDIA_CANDIDATE`.

However, sparse metadata can create false no-match conclusions. When the file has too little discriminating metadata, use `INSUFFICIENT_EVIDENCE` or downstream enrichment rather than claiming a new item with false certainty.

Tool 2 itself does not create the new row.

## 19. Related formats and versions

When direct/confirmed evidence establishes that the incoming file is another format or related file for an existing logical media item, Tool 2 should record:

- matched Media row ID;
- incoming format/extension;
- existing formats/references known from the row;
- proposed enrichment for Tool 4;
- any conflict such as two different physical files claiming the same slot/role.

Do not create a second logical Media item merely because the extension differs.

## 20. No open-ended external search in v1

Tool 2 v1 should not perform open-ended YouTube/web searching merely to find a possible recording.

It may normalize and compare source URLs/IDs already present in the file context or Baserow row. A future explicit source-discovery requirement can use the configured YouTube adapters without changing Tool 2's basic database-reconciliation role.

## 21. Proposed structured result

Conceptually:

```text
MediaDatabaseReviewResult
  tracking_id
  database_available
  database_snapshot_at
  baserow_check_complete

  decision
  decision_state
  selected_media_row_id

  candidates[]
    media_row_id
    identity_evidence[]
    field_comparisons
    conflicts[]
    possible_enrichments[]

  selected_field_evidence
    WHEN
    WHAT
    title
    WHERE
    category
    source_identifiers
    travel_context

  renamer_enrichment
    when_val
    what_val
    title
    where_val
    category
    evidence[]

  proposed_tool4_action
    link_existing
    enrich_existing
    create_new
    no_write
    needs_review

  review_required
  review_reasons[]
  conflicts[]
  evidence[]
```

Exact Python types are an implementation detail, but the output must be JSON-serializable and reusable by Tool 3, Tool 4, the later Renamer pass, CLI, and review portal.

Confirmed Media metadata that is safe for filename enrichment should be represented distinctly from merely possible candidate metadata so the Renamer cannot accidentally consume an unconfirmed candidate.

## 22. Human review portal

Extend the existing local review portal rather than creating a second UI.

A Tool 2 review screen should show:

- incoming file and current Tool 1 WWWW interpretation;
- candidate Baserow Media rows;
- Media title/category/date/place and other relevant metadata;
- clear agreements, missing fields, and conflicts;
- the evidence that caused each candidate to be retrieved;
- supporting `category_title` / `travel_schedule` context when relevant;
- available formats/source references on each candidate row when relevant;
- proposed decision/action;
- the metadata that would be sent back to the Renamer if the candidate is confirmed.

Human actions should be structured, for example:

```text
Confirm existing row
Choose another candidate row
Confirm new media item
Defer / insufficient evidence
```

Review actions are stored locally with provenance and consumed by Tool 4 / later Renamer enrichment. The portal does not write Baserow directly.

## 23. Batch behavior

- One ambiguous file must not block the batch.
- Candidate generation/reconciliation should be read-only and safe to rerun.
- Local review decisions should be idempotent and auditable.
- Database unavailability should defer only the affected database-dependent stage; it must not corrupt existing local evidence.
- Snapshot/schema errors should be visible in logs/status rather than silently producing `NEW_MEDIA_CANDIDATE`.
- Tool 2 should batch/cache Baserow reads so thousands of files do not cause unnecessary repeated full-table requests.

## 24. Logging and diagnostics

Per-file logging should include enough information to evaluate false matches and false no-matches:

- candidate row IDs;
- match reasons;
- tables/evidence sources involved (`media`, `category_title`, `travel_schedule`);
- field comparison states;
- selected decision;
- whether the decision was automatic or human-confirmed;
- database snapshot/version timestamp;
- review reasons/conflicts;
- Renamer enrichment evidence returned;
- proposed Tool 4 action.

Evaluation should explicitly count incorrect automatic existing-row matches as a high-severity error.

## 25. Testing direction

The finalized plan should include mocked Baserow tests for:

- read-only API/MCP access using `.env` configuration;
- complete pagination / no partial-table assumption;
- live schema mapping for `media`, `category_title`, and `travel_schedule`;
- unique direct identity match;
- exact date + scripture WHAT + place candidate lookup;
- title enrichment from a confirmed Media row;
- canonical Renamer enrichment for `BG-1-18` + `Love in the Spiritual World`;
- filename date `2014-08-04` vs Media date `2014-04-08` → explicit conflict, no automatic enrichment;
- same WWWW but two distinct candidate rows → review, never first-match-wins;
- multiple formats belonging to one row;
- Baserow missing field → enrichment candidate for Tool 4;
- Baserow conflict with provisional Tool 1 metadata;
- `category_title` supporting/normalizing a candidate without flattening specific WHAT;
- `travel_schedule` supporting a candidate without becoming proof of continuous presence;
- sparse file metadata and no candidates;
- valid no-match vs database unavailable;
- `_edited` Baserow-check-complete lifecycle;
- human selection of an existing row;
- human confirmation of new item;
- idempotent rerun;
- stale snapshot handoff requiring Tool 4 re-check before mutation.

## 26. Current planning conclusion

The current role is:

> Tool 2 answers **"What relevant Media database records exist for this file, which candidate is supported by the evidence, what does Baserow add or contradict, and what confirmed metadata can safely be returned to the Renamer?"**

It is a reusable read-only database-query/reconciliation service used by the Renamer and other tools.

It reads `media`, `category_title`, and `travel_schedule`; it does not use `users` for the current workflow. It may use travel-schedule rows as supporting context, while Tool 3 retains responsibility for full travel-schedule review.

It does not directly rename files, inspect audio, deduplicate physical media, or mutate Baserow.

No user/archive-policy decision has been identified yet that requires interrupting planning. The next planning pass should refine candidate identity thresholds/rules, the exact separation between automatic confirmed matches and review candidates, the Tool 2 → Renamer/Tool 4 result contract, and acceptance criteria before finalization.
