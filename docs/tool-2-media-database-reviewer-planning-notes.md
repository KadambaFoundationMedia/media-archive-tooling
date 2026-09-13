# Tool 2 — Media Database Reviewer Planning Notes

Status: **DRAFT PLANNING NOTES — not a finalized build plan and not an implementation handoff**

These notes capture the current planning direction for Tool 2. They must not be treated as authoritative implementation requirements until converted into `docs/tool-2-media-database-reviewer-build-plan.md` and explicitly marked finalized.

## 1. Purpose

Tool 2 reviews each incoming/local archive file against the authoritative Baserow `Media` table and determines whether the file most likely belongs to an existing logical media item, represents a new logical media item, or requires human review because the database evidence is ambiguous or contradictory.

Tool 2 is a **read/reconcile/review stage**, not the Baserow mutation stage. Tool 4 (Media Database Updater) performs writes after Tool 2 and Tool 3 have produced structured evidence.

The intended fast metadata sequence remains:

```text
Tool 1 Renamer (initial)
→ Tool 2 Media Database Reviewer
→ Tool 3 Travel Schedule Reviewer
→ Tool 4 Media Database Updater
→ Tool 1 Renamer (enrich)
```

## 2. Core archive semantics already fixed

- One Baserow Media row represents one **logical media item / recording**.
- One logical media item may have multiple related files or formats.
- A new format/version of the same recording should enrich the existing Media row rather than create a duplicate media item.
- Filename equality is not proof of duplicate content or identity.
- Distinct recordings may legitimately share the same canonical WWWW filename semantics.
- Physical duplicate detection, audio fingerprinting, and content-level duplicate proof are outside Tool 2.
- Existing fact-checked Baserow Media values are strong evidence and must not be overwritten by weaker filename/folder inference.
- Contradictions are retained and surfaced for review rather than silently resolved.

## 3. Read-only boundary

Tool 2 should not create, update, merge, or delete Baserow Media rows.

It may:

- read the Media table and required schema/reference information;
- build a local batch snapshot/cache;
- normalize rows into a semantic comparison representation;
- produce candidate matches;
- compare fields;
- record structured local review decisions;
- produce a proposed action for Tool 4.

Tool 4 must re-check the selected Baserow row/current version before writing so a stale Tool 2 snapshot cannot silently overwrite collaborator changes.

## 4. Baserow is authoritative shared state

The Media table is the shared cross-device source of truth. Tool 2 local state is only operational evidence and review state.

At batch start, Tool 2 should obtain a complete enough Media snapshot for deterministic matching. The builder may choose pagination, indexed server-side retrieval, or a hybrid cache strategy, but the algorithm must not accidentally treat a partial first page as the full database.

The snapshot should record at least:

- retrieval timestamp;
- source table/database identity;
- row IDs;
- raw row values used for comparison;
- normalized semantic projection;
- any schema/field mapping used.

## 5. Schema handling

Do not assume undocumented Baserow field names in application logic.

Known semantic concepts include at least:

- recording date / WHEN;
- title/topic/scripture/class WHAT where represented;
- category where represented;
- place/location;
- country;
- file/format/attachment/path/URL/source identifiers where represented;
- any existing filename or media-source identity fields useful for deterministic matching.

The implementation should obtain the live Media schema when possible and map raw field names to semantic roles through one adapter/configuration layer. If the live schema lacks a useful role, preserve the raw row rather than inventing a field.

## 6. Tool 2 input

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

## 7. Candidate retrieval order

Candidate discovery should proceed from strongest identity evidence to weaker semantic similarity.

Recommended order:

1. exact explicit media/source identity already represented in a Baserow row, such as a unique stored URL/source ID/file reference/attachment identity when available;
2. exact normalized source filename or known source identifier when the database stores it and the value is sufficiently discriminating;
3. exact/high-specificity semantic combinations such as WHEN + specific WHAT + WHERE;
4. partial combinations such as WHEN + WHAT, WHAT + WHERE, or partial date + specific WHAT + WHERE;
5. bounded normalized/fuzzy candidate generation only within plausible rows.

Do not query or fuzzy-compare every file against every field indiscriminately when indexed candidate retrieval can narrow the set.

## 8. Identity decision policy

A unique direct identity match may be automatically classified as the existing logical media item when no contradictory row evidence exists.

Semantic equality alone must **not** prove logical identity. In particular, a row with the same canonical WHEN/WHAT/WHERE may still represent a distinct recording. Therefore a semantic-only match should normally remain a probable candidate requiring review unless additional independent identity evidence makes it safe.

Multiple equally plausible rows must never be resolved by first-match-wins.

## 9. Proposed review decisions

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

## 10. Field-by-field reconciliation

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
- Baserow WHAT specific, local WHAT unresolved → `LOCAL_MISSING`; keep Baserow as stronger evidence for later Renamer enrichment;
- fact-checked Baserow date conflicts with provisional filename date → `CONFLICT`, with Baserow preferred as stronger evidence but the conflict preserved.

Blank values are not contradictions.

## 11. Evidence precedence

Default precedence for Tool 2 comparison:

1. explicit/direct row-linked source identity;
2. human-confirmed Tool 2 review decision;
3. existing fact-checked Baserow Media fields;
4. exact/strong Tool 1 filename/path interpretation;
5. provisional Tool 1 interpretation;
6. weak filesystem/context clues.

Tool 2 may flag a possible Baserow error when strong direct evidence contradicts a row, but it must not silently rewrite the database. Tool 4 should require human confirmation before replacing contradictory fact-checked values.

## 12. `_edited` requirement

An `_edited` file still requires the Baserow Media review.

Tool 2 should explicitly output whether the required Baserow check was completed successfully. Conceptually:

```text
baserow_check_complete = true | false
```

Only a completed database review (including a valid no-match result) may set this true. A database/network/configuration failure must leave it false.

The later Renamer may use this evidence to remove `_edited` according to the Tool 1 policy.

## 13. New media candidate policy

A complete search that yields no plausible existing row may classify the file as `NEW_MEDIA_CANDIDATE`.

However, sparse metadata can create false no-match conclusions. When the file has too little discriminating metadata, use `INSUFFICIENT_EVIDENCE` or require review rather than claiming a new item with false certainty.

Tool 2 itself does not create the new row.

## 14. Related formats and versions

When direct/confirmed evidence establishes that the incoming file is another format or related file for an existing logical media item, Tool 2 should record:

- matched Media row ID;
- incoming format/extension;
- existing formats/references known from the row;
- proposed enrichment for Tool 4;
- any conflict such as two different physical files claiming the same slot/role.

Do not create a second logical Media item merely because the extension differs.

## 15. No open-ended external search in v1

Tool 2 v1 should not perform open-ended YouTube/web searching merely to find a possible recording.

It may normalize and compare source URLs/IDs already present in the file context or Baserow row. A future explicit source-discovery requirement can use the configured YouTube adapters without changing Tool 2's basic database-reconciliation role.

## 16. Proposed structured result

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
    WHERE
    category
    source_identifiers

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

## 17. Human review portal

Extend the existing local review portal rather than creating a second UI.

A Tool 2 review screen should show:

- incoming file and current Tool 1 WWWW interpretation;
- candidate Baserow rows;
- clear agreements, missing fields, and conflicts;
- the evidence that caused each candidate to be retrieved;
- available formats/source references on each candidate row when relevant;
- proposed decision/action.

Human actions should be structured, for example:

```text
Confirm existing row
Choose another candidate row
Confirm new media item
Defer / insufficient evidence
```

Review actions are stored locally with provenance and consumed by Tool 4. The portal does not write Baserow directly.

## 18. Batch behavior

- One ambiguous file must not block the batch.
- Candidate generation/reconciliation should be read-only and safe to rerun.
- Local review decisions should be idempotent and auditable.
- Database unavailability should defer only the affected database-dependent stage; it must not corrupt existing local evidence.
- Snapshot/schema errors should be visible in logs/status rather than silently producing `NEW_MEDIA_CANDIDATE`.

## 19. Logging and diagnostics

Per-file logging should include enough information to evaluate false matches and false no-matches:

- candidate row IDs;
- match reasons;
- field comparison states;
- selected decision;
- whether the decision was automatic or human-confirmed;
- database snapshot/version timestamp;
- review reasons/conflicts;
- proposed Tool 4 action.

Evaluation should explicitly count incorrect automatic existing-row matches as a high-severity error.

## 20. Testing direction

The finalized plan should include mocked Baserow tests for:

- complete pagination / no partial-table assumption;
- schema mapping;
- unique direct identity match;
- same WWWW but two distinct candidate rows → review, never first-match-wins;
- multiple formats belonging to one row;
- Baserow missing field → enrichment candidate;
- Baserow conflict with provisional Tool 1 metadata;
- sparse file metadata and no candidates;
- valid no-match vs database unavailable;
- `_edited` Baserow-check-complete lifecycle;
- human selection of an existing row;
- human confirmation of new item;
- idempotent rerun;
- stale snapshot handoff requiring Tool 4 re-check before mutation.

## 21. Current planning conclusion

The current recommended role is deliberately conservative:

> Tool 2 answers **"What does the existing Media database say about this logical recording, and which row—if any—can we safely associate with it?"**

It does not rename files, inspect audio, deduplicate physical media, consult travel schedules, or mutate Baserow.

No user/archive-policy decision has been identified yet that requires interrupting planning. The next planning pass should refine candidate identity rules, minimum evidence for automatic decisions, the cross-tool result contract with Tool 4, and acceptance criteria before finalization.
