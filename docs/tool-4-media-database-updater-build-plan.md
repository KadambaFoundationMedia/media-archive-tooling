# Tool 4 — Media Database Updater Build Plan

Status: **FINALIZED — implementation-ready when Tool 4 reaches its pipeline turn**

Tracking issue: #24

This document is the authoritative implementation specification for Tool 4. The implementation model must treat it as read-only and use `status/tool-4-media-database-updater.md` for progress, questions, review findings, and commit checkpoints.

Project-wide architecture: `docs/project-implementation-architecture.md`  
Project-wide Baserow authority policy: `docs/baserow-live-data-policy.md`  
Baserow access-boundary amendment: `docs/baserow-access-boundary-amendment.md`
Tool 1 build plan/status: `docs/tool-1-renamer-build-plan.md`, `status/tool-1-renamer.md`  
Tool 2 build plan/status: `docs/tool-2-media-database-reviewer-build-plan.md`, `status/tool-2-media-database-reviewer.md`  
Tool 3 build plan/status: `docs/tool-3-travel-schedule-reviewer-build-plan.md`, `status/tool-3-travel-schedule-reviewer.md`

---

## 1. Purpose

Tool 4 is the project's **Media Database Updater**, the only application tool with read-and-write Baserow access, and the only tool allowed to mutate Baserow rows, select options, or schema. Tool 2 separately has strictly read-only access for Media lookup/reconciliation and static-reference bootstrap/verification.

The Baserow `media` table is the searchable record of logical recordings that exist in the archive and/or are available through online formats such as YouTube, transcript pages, online audio, SoundCloud, archive.org, and other represented sources.

Tool 4 must:

- add a new `media` row when a renamed archive recording does not already exist;
- update/enrich an existing logical Media row when the archive file matches it and contributes new trustworthy metadata;
- preserve unrelated existing online-format/source metadata;
- detect duplicate/ambiguous existing rows instead of creating another duplicate;
- flag material contradictions for review instead of silently overwriting authoritative existing values;
- keep the Media row synchronized when Tool 1 later changes a committed filename/path because later tools discovered stronger WHAT/WHERE/WHEN information;
- make all writes idempotent, auditable, and race-safe against collaborator edits.

Tool 4 is not a duplicate detector based on media bytes and does not merge/delete duplicate rows automatically.

For a safely confirmed existing Media association, the relevant populated metadata currently stored in Baserow is leading and confirmed. Tool 4 fills trustworthy missing metadata, but it does not automatically overwrite contradictory populated database metadata; it preserves the value and flags the contradiction for review.

---

## 2. Pipeline position and reuse

The initial metadata sequence is conceptually:

```text
Tool 1 Renamer — find/interpret file and initial rename when evidence permits
→ Tool 2 Media Database Reviewer — live read-only Baserow lookup/reconciliation
→ Tool 3 Travel Schedule Reviewer — date/schedule corroboration
→ Tool 1 Renamer — combine evidence and commit final filename
→ Tool 4 Media Database Updater — fresh Tool 2 gate, live revalidation, Baserow synchronization
→ repeat the collaboration when later evidence produces another final committed filename
```

Tool 4 is reusable. In the initial flow, Tool 1 calls it once after Tool 1 has worked with Tools 2 and 3 and committed the final filename. If later processing discovers stronger WHAT/WHERE/WHEN evidence and Tool 1 commits another final renamed state, Tool 1 calls Tool 4 again.

A mere dry-run or uncommitted proposal must never mutate Baserow.

---

## 3. Authority boundary

Tool 4 owns every Baserow mutation and the direct reads needed to validate those mutations. Tool 2 owns strictly read-only lookup/reconciliation. Tools 1 and 3 perform no Baserow access.

- Tool 1 owns filename interpretation, canonical rendering, filesystem rename/commit behavior, and tracking identity.
- Tool 2 owns live read-only Media lookup/reconciliation and current candidate decisions.
- Tool 3 supplies contextual schedule evidence from a complete verified local reference produced through Tool 2's read-only boundary; it owns no Baserow provider.
- Tool 4 decides and executes safe create/update mutations from those structured results.
- Tool 4 owns its read/write credentials, write/schema mapping, exact-row precondition reads, uncertain-outcome reconciliation, and mutations.
- Tool 2 may own separately scoped read-only credentials/provider behavior; Tool 1 and Tool 3 must not receive Baserow clients/providers.
- Review-portal routes/templates/JavaScript must call application services; they must not issue Baserow operations directly.

Tool 4 may introduce a dedicated Baserow write adapter or extend/refactor the shared adapter boundary. Tool 2 must remain technically incapable of mutations. Tool 4 must use Tool 2's service for Media existence/candidate reconciliation rather than duplicating its matching rules.

---

## 4. Live-current rule is mandatory

The mutable Baserow Media database is continuously updated by collaborators. Tool 4 must follow `docs/baserow-live-data-policy.md` without exception.

### Before updating an existing row

Immediately before mutation:

1. fetch the target row live;
2. compare every field Tool 4 intends to modify, plus the relevant reviewed preconditions, against the state on which the proposed update was based;
3. if a relevant value changed, do not silently overwrite it;
4. return a stale/re-review conflict and require the decision to be recomputed;
5. only PATCH/write the minimal intended fields after the precondition succeeds.

A collaborator changing an unrelated field such as YouTube while Tool 4 is updating only `Filename` must not be overwritten and need not block the safe update when the relevant preconditions remain valid.

### Before creating a row

Immediately before create:

1. invoke Tool 2 for a fresh, complete current existence/candidate review;
2. validate the returned Tool 2 decision, completeness, database state, timestamp, and provenance;
3. if a collaborator has created a plausible row since the prior review, do not create another row;
4. reclassify to update/review as appropriate;
5. create only when the current complete Tool 2 decision still establishes `NEW_MEDIA_CANDIDATE` with sufficient evidence.

Database/network/schema failure is a blocked database state, never proof that no row exists.

---

## 5. Tool 2 is the create-vs-update gate

Tool 4 must not reimplement Media matching from scratch.

Tool 2 owns candidate I/O and reconciliation. Every accepted Tool 2 decision must identify the exact live read/provenance used to compute it. Tool 4 validates that contract and owns the subsequent exact-row/schema/write-precondition reads.

Conceptual routing:

```text
Tool 2 EXISTING_MEDIA_MATCH
    → prepare update of that row

Tool 2 explicit human-confirmed existing association
    → revalidate live
    → prepare update if still valid

Tool 2 NEW_MEDIA_CANDIDATE
    → fresh pre-create Tool 2 live review
    → create only if still NEW_MEDIA_CANDIDATE

PROBABLE_EXISTING_MEDIA
MULTIPLE_CANDIDATES
CONFLICT_WITH_EXISTING
INSUFFICIENT_EVIDENCE
DATABASE_UNAVAILABLE
    → no automatic create
    → no speculative existing-row update
    → continue/review/block according to state
```

Duplicate-looking rows remain visible as candidates. Tool 4 must never choose first-match-wins and must never merge/delete duplicate rows automatically.

---

## 6. Input contract

Primary input is structured current state, not a fresh filename reparse.

Conceptually a Tool 4 synchronization request contains:

```text
tracking_id
operation/request id
Tool 1 committed before/after path information
current actual filename
current full archive path
Tool 1 structured WHEN/WHO/WHAT/WHERE + resolution states + provenance
parent-folder context needed for title fallback
Tool 2 current review result / selected media_row_id where applicable
Tool 3 evidence where present
file extension / media format
previous synchronized Media row/path where known
review action provenance where applicable
```

The service must be callable directly from Python by the future orchestrator and by Tool 1. CLI and portal are adapters over the same application service.

Tool 4 must use structured field states/provenance. It must not assume that a syntactically complete date/location rendered into a filename is authoritative when Tool 1 still marks the value provisional.

---

## 7. Evidence eligible for automatic database writes

Baserow Media rows are durable shared metadata. Tool 4 therefore writes only trustworthy resolved evidence.

Automatically writable semantic evidence includes:

- exact/strong Tool 1 filename/path evidence that is safe under Tool 1 policy;
- metadata from a confirmed Tool 2 Media association when used to preserve/complete that same row;
- explicit human-approved corrections after live revalidation;
- later-tool evidence that Tool 1 has accepted into a committed non-provisional field state according to that tool's contract.

Travel-schedule-only or other provisional evidence must not be promoted into authoritative Media fields merely because it can be formatted as a date/location.

Provisional/ambiguous alternatives may be retained in Tool 4 audit/review evidence but are not automatic semantic writes.

---

## 8. One logical Media row may represent several formats

One Baserow `media` row represents one logical recording and may contain multiple related representations, for example:

- YouTube video URL;
- transcript URL;
- online audio link;
- archive audio/video file path;
- alternate links.

When an archive file matches an existing row, Tool 4 must update **only the fields relevant to the archive file/current metadata**.

It must not clear, reset, or replace unrelated populated fields merely because new-row defaults exist.

In particular, an update must preserve existing values for fields such as:

```text
Youtube
Youtube descr
Audio link
Thumb image
Transcriber
Alt. Links
Article Link
Themes
Transcript Archive link
Status Media
Status thumb
Status Transcript
```

unless a specific current Tool 4 request contains approved evidence to modify that exact field.

A later Tool 4 call may support such fields when another tool explicitly provides data, but current archive-file synchronization does not invent or clear them.

### Archive path collision between formats

`media_archive_path` is currently a single explicit archive-path field in the requirements. If a confirmed Media row already contains a different archive path that cannot be proven to be the previous path of the same tracked physical archive file, Tool 4 must not overwrite it merely because the incoming file is another format of the same logical recording. Preserve both pieces of evidence and flag the representation conflict for review until the schema/workflow explicitly supports it.

If Tool 1 tracking/audit proves that the existing path is simply the old path of the same file being renamed, updating it to the committed current path is safe.

---

## 9. Schema discovery and field allowlist

Tool 4 must inspect the current live Baserow table/schema representation needed for a write rather than scattering assumed raw IDs/types throughout application logic.

Requirements:

- use only columns that actually exist;
- map known semantic field names through one schema-mapping layer;
- validate writable field types before sending values;
- do not create arbitrary database columns;
- do not silently coerce a value into an incompatible Baserow field type;
- schema mismatch returns a clear blocked/review state with diagnostics.

The user has confirmed that `Created_on`, `Last modified by`, and `Last modified` are editable through the API in this database. Implement them according to the field/default rules below, while still validating the live schema before write.

---

## 10. Select-column policy

For every select/multi-select field, Tool 4 must submit valid live option IDs/values accepted by the current schema.

### Existing options

Prefer exact/canonical matching to the live option set. Static assets such as `assets/default_categories.json` may help interpret filename abbreviations but are not authority for what options currently exist in Baserow.

Examples of semantic category interpretation include:

```text
SB → Srimad Bhagavatam
BG → Bhagavad-gita / corresponding existing Baserow option
CC → Caitanya caritamrta / corresponding existing Baserow option
```

The exact value written must be an available live Baserow Category option.

### Adding country/location options

User policy explicitly permits Tool 4 to add legitimate new **Country** and **Place/location** select options when the resolved value is missing.

Before adding an option:

1. refetch the relevant field/options live;
2. normalize conservatively to avoid case/diacritic/whitespace duplicates;
3. if an equivalent option exists, reuse it;
4. if multiple similar options make identity ambiguous, do not guess — review;
5. append/create the new option without dropping or rewriting existing options;
6. verify the option exists after the schema mutation before assigning it to the Media row.

No other select field gets new options automatically in v1. Missing Category, Language, status, Tag-select, or other required options produce a schema-option mismatch/review rather than Tool 4 inventing taxonomy.

---

## 11. New Media row field mapping

A newly created archive Media row uses the current Baserow schema and the following requirements.

### Title

Priority:

1. explicit usable title represented in the committed Tool 1 filename/structured WHAT/title evidence;
2. usable title represented by the parent folder context;
3. otherwise the actual current filename as fallback.

Do not mistake a folder that contains only a year/location/technical grouping for a semantic title.

### Date

`Date` is the **recording date**, not the database-import date.

Write it only when Tool 4 has a trustworthy complete calendar date exactly representable as `YYYY-MM-DD`.

Do not invent missing month/day components and do not use today's date as the recording date.

### Incomplete recording dates

Baserow `Date` accepts an explicit complete date. If Tool 1 only knows a partial recording date such as a year, year-month, or canonical placeholder form such as `2019-09-DD`:

- leave `Date` empty;
- preserve the partial date in `Notes` using a deterministic Tool-4-owned marker, for example:

```text
Incomplete recording date: 2019-09-DD
```

- do not duplicate the same marker on rerun;
- when a later trusted full date becomes known and is written to `Date`, Tool 4 may remove/replace only its own previously generated incomplete-date marker while preserving all unrelated human/existing Notes text.

A complete but merely provisional schedule date is not eligible for `Date` until evidence policy permits it.

### Category

Derive semantic category from Tool 1 structured WHAT/category evidence and map it to a current available Category select option.

Do not create new Category options automatically.

### Tag

When a scripture verse/reference is represented in the committed filename/structured WHAT, add the verse/reference to `Tag` using the current field type and available options.

For ordinary numeric scripture references, preserve the meaningful verse components (for example SB 1.3.4 → `1.3.4`). For scripture structures that require a division/lila to remain unambiguous, retain that identifying component according to existing canonical project parsing rather than stripping information blindly.

If `Tag` is a select field and the required tag is not an available option, do not invent a new option under v1 policy; return a field-level schema/taxonomy review state.

If the field supports multiple values, add the new applicable tag without deleting existing tags.

### Language

Default to the existing live option representing **English** when no stronger language evidence exists.

Do not create a new Language option automatically.

### Status Media

Set to existing option `Not-started` for a new row.

### Status thumb

Set to existing option `Not-started` for a new row.

### Filename

Write the actual current committed archive filename.

### Youtube

Empty for a new archive-only row unless approved evidence for a YouTube URL is explicitly supplied by another workflow. Tool 4 does not search YouTube in v1.

### Youtube descr

Empty for a new row unless explicitly supplied.

### Audio link

Empty for a new row unless explicitly supplied.

### Notes

Start Notes with:

```text
Added from archive
```

Then add Tool-4-owned diagnostic metadata such as an incomplete recording-date marker when required.

If existing text is being preserved during enrichment, prepend `Added from archive` once and retain the previous text after it. Repeated synchronization must not duplicate the marker.

### Created_on

Set current calendar date as `YYYY-MM-DD` on create.

### Last modified by

Set current calendar date as `YYYY-MM-DD` as specified by the user/database contract.

### Last modified

Set current calendar date as `YYYY-MM-DD` on every Tool 4 mutation.

### Thumb image

Empty on create unless explicitly supplied.

### Transcriber

Empty on create unless explicitly supplied.

### imported_on

Set current calendar date as `YYYY-MM-DD` on create. When attaching archive metadata to an older existing online row, fill it only if the archive-import semantics apply and the field is currently empty; never reset an existing import date on every rename.

### Status Transcript

Set to existing option `Not-started` for a new row.

### Alt. Links

Empty on create unless explicitly supplied.

### Article Link

Empty on create unless explicitly supplied.

### Themes

Empty on create unless explicitly supplied.

### Transcript Archive link

Empty on create unless explicitly supplied.

### Media Archive link

The underlying field is currently `media_archive_link` (displayed as Media Archive Link) and contains a URL. Tools 1, 2, 3, and 4 do not possess this URL and must not invent or derive it from `media_archive_path`.

- on a new row, leave `media_archive_link` empty;
- on an existing row, preserve the field exactly whether it is empty or populated;
- if an unexpected request proposes a value for this field, do not write it; block/flag the unsupported input rather than overwriting the live value.

A populated value should not normally enter this workflow because none of Tools 1–4 can produce it. The preservation rule is nevertheless mandatory protection for existing rows.

### media_archive_path

Write the full actual archive filesystem path for the synchronized file.

### Location and country

When the live `media` schema exposes the existing Media location/country fields (currently semantically represented as `Place, location` and `Country`), write trustworthy resolved Tool 1 location/country evidence. Apply the country/location select-option policy above.

Do not write provisional schedule-only WHERE evidence automatically.

---

## 12. Existing-row update policy

Tool 4 uses field-specific merge behavior, not full-row replacement.

### Semantic metadata

For fields such as recording Date, Title, Category, WHERE/Country, scripture/reference metadata:

```text
incoming absent/provisional
    → no write

current database blank + trustworthy incoming value
    → fill field

current database equivalent to incoming value
    → no-op

current database populated + materially contradictory incoming value
    → preserve current value
    → record exact conflict
    → review; no silent overwrite
```

An explicit human-approved correction may overwrite a contradictory field only after Tool 4 re-reads the live row and confirms the reviewed precondition still holds.

### Archive linkage fields

`Filename` and `media_archive_path` may legitimately change after Tool 1 renames/moves the same tracked physical file.

When tracking/audit proves old path → new path continuity, Tool 4 may update those fields even though they are nonblank.

If continuity cannot be established and the field points to a different archive representation, do not overwrite it automatically.

### Additive fields

Multi-value fields such as Tags, when applicable, are unioned conservatively: add the new approved value and preserve all existing values.

### New-row defaults are not update defaults

Never reset an existing row's statuses, links, thumbnail, transcript state, language, or other populated values to new-row defaults during routine archive synchronization.

---

## 13. Notes merge behavior

Tool 4 must preserve human/existing Notes.

It may manage only deterministic lines it owns, including:

```text
Added from archive
Incomplete recording date: <canonical-partial-date>
```

Rules:

- `Added from archive` appears at the beginning at most once when archive linkage is first established;
- incomplete-date lines are idempotent;
- later resolution may remove/replace only Tool-4-generated incomplete-date lines;
- arbitrary existing notes must never be deleted, reformatted wholesale, or duplicated as a side effect of synchronization.

---

## 14. Tool 1 automatic synchronization hook

User policy: Tool 4 is called after Tool 1 has worked with Tools 2 and 3 and committed the final filename for the current processing stage. Tool 4 is not called for the initial/intermediate rename. When later processing produces another final Tool 1 rename, that later final committed state calls Tool 4 again.

Integration must occur through the application-service layer after the local Tool 1 commit succeeds.

Conceptually:

```text
Tool 1 proposed rename
→ user/automatic approval as required
→ filesystem commit succeeds
→ durable local sync event/request recorded
→ Tool 4 synchronize()
→ success OR pending/review state
```

A mere Tool 1 approval without filesystem/metadata commit does not write Baserow.

Tool 1 dry-run never writes Baserow.

Later Tool 1 ENRICH/final passes use the same hook when their committed metadata/path changes require database synchronization.

Other tools must not implement Baserow writers. Tool 2 remains the reusable read-only query/reconciliation service used by Tool 1 and Tool 4.

---

## 15. Failure after a successful filesystem rename

Filesystem and Baserow cannot be one atomic transaction. Do not roll back a successfully committed filesystem rename merely because the subsequent network/database synchronization failed.

Instead use a durable local synchronization/outbox state.

Conceptual states:

```text
PENDING_SYNC
SYNCING
SYNCED
REVIEW_REQUIRED
DATABASE_UNAVAILABLE
FAILED_RETRYABLE
FAILED_BLOCKED
```

Exact names are implementation details.

Requirements:

- record the Tool 4 request durably before/while attempting synchronization;
- expose pending/failed database sync visibly in status/portal;
- retries are safe and idempotent;
- a later retry starts from a fresh current Tool 2 review plus Tool 4's direct pre-write revalidation;
- no successful local rename is silently considered fully synchronized while Tool 4 remains pending.

---

## 16. Idempotency and uncertain network outcomes

Repeated calls with the same committed file state must converge without creating duplicate rows, duplicate notes, duplicate tags, or repeated taxonomy options.

Use stable request identity/fingerprints tied to Tool 1 tracking identity/current committed state where useful.

If a create/update HTTP request times out after Baserow may have processed it, the retry must **not blindly repeat the mutation**. Re-read/reconcile first:

- for create: run Tool 2's live existence/candidate check again and locate the possibly created row;
- for update: fetch the target row and determine whether the intended minimal changes are already present.

Only then decide whether another mutation is needed.

---

## 17. Write shape and data preservation

Prefer minimal PATCH-style writes containing only fields that actually need to change.

Do not send a full reconstructed row whose blank/default values could erase collaborator data.

For every write, preserve audit evidence:

```text
tracking_id / request id
operation type: create | update | no-op | conflict
Baserow table + row id
live-read time
precondition values used
field-level before/after values for fields Tool 4 changed
fields intentionally preserved
Tool 2 decision/reference
human review action when applicable
response/result or error
```

Secrets/tokens must never be logged.

---

## 18. Review semantics

Review is required for conditions such as:

- multiple plausible existing Media rows;
- duplicate-looking rows that cannot be safely disambiguated;
- material contradiction between trustworthy archive evidence and populated Media metadata;
- collaborator changed a relevant field after the proposed write was reviewed;
- ambiguous select-option identity;
- a required non-country/location select option is missing;
- an existing `media_archive_path` appears to represent a different archive file/format;
- live schema is incompatible with the specified field contract.

Review is not required merely because an optional new-row field is empty.

Portal review should show:

- current live Media value;
- incoming archive value and provenance;
- exact proposed field diff;
- fields that will remain untouched;
- candidate/duplicate evidence;
- stale/live-read status;
- actions to keep database value, apply an explicitly approved archive correction where allowed, choose the correct existing row when Tool 2 permits it, defer, or confirm a genuinely new row.

Every mutating human action must revalidate live state immediately before write. A stale approval is not permission to overwrite newer collaborator edits.

---

## 19. CLI and programmatic interface

Expose a reusable application service, conceptually:

```text
MediaDatabaseUpdaterService.synchronize(request)
MediaDatabaseUpdaterService.preview(request)
MediaDatabaseUpdaterService.retry_pending(...)
```

Exact class names are implementation details.

Recommended CLI surface:

```text
media-archive media-db-update ... --dry-run
media-archive media-db-update ... --commit
media-archive media-db-update --retry-pending ...
```

Standalone CLI mutation must require explicit commit intent. Dry-run/preview may perform live reads but may not mutate Media rows or select options.

Tool 1's already-confirmed filesystem commit workflow may invoke the Tool 4 commit service automatically as required by the integration policy; it does not require the user to separately type a second CLI command.

---

## 20. Review portal integration

Extend the existing localhost portal rather than creating a separate UI stack.

The portal should provide at least:

- Tool 4 sync status/filtering;
- create/update/no-op/review/pending/database-unavailable state;
- linked Media row ID when known;
- current vs proposed field diff;
- conflicts and duplicate/candidate evidence;
- pending retry/error information;
- safe review actions through application services.

Portal rendering code must not contain a second copy of merge/conflict policy.

---

## 21. Safety around production Baserow

Automated repository tests must never write to production Baserow.

Use hermetic fake/mock adapters for CI and, when available, an explicitly configured non-production test table/environment for transport integration testing.

A production write smoke test must be deliberately scoped to user-approved test/input rows and must never be performed merely because credentials are present in `.env`.

The normal Builder acceptance run must not bulk-create/update production rows while evaluating the representative archive sample.

---

## 22. Required implementation tests

The implementation must include focused regression/integration coverage for at least the following behaviors:

1. confirmed existing Tool 2 match updates only safe relevant fields;
2. existing online links/transcript/audio fields survive archive synchronization unchanged;
3. blank trusted semantic field is enriched;
4. equivalent semantic field is a no-op;
5. conflicting populated semantic field is preserved and routed to review;
6. explicit human overwrite is live-revalidated before write;
7. collaborator relevant-field change between review and write blocks stale write;
8. collaborator unrelated-field change is preserved and does not get erased by minimal PATCH;
9. current `NEW_MEDIA_CANDIDATE` creates one row with required defaults;
10. collaborator-created matching row between initial review and create prevents duplicate creation;
11. database failure never becomes a no-match/create decision;
12. multiple candidates never create first-match-wins or a new duplicate row;
13. repeated create/update synchronization is idempotent;
14. timeout/uncertain create outcome is reconciled before retry;
15. timeout/uncertain update outcome is reconciled before retry;
16. full trusted recording date writes `Date` as `YYYY-MM-DD`;
17. partial date leaves `Date` empty and writes one deterministic incomplete-date Notes marker;
18. later full date fills `Date` and safely removes/replaces only Tool 4's incomplete-date marker;
19. provisional schedule-derived date/location is not automatically written as authoritative Media metadata;
20. Title priority: filename title → parent-folder title → filename fallback;
21. category abbreviation maps only to a valid live Category option;
22. missing Category option does not get invented automatically;
23. scripture verse/reference produces the expected Tag behavior for the live field type;
24. existing multi-value Tags are preserved when adding an approved tag;
25. Language defaults to existing English option on new row;
26. `Status Media`, `Status thumb`, `Status Transcript` default to existing `Not-started` option on new row;
27. missing required status/language option blocks rather than inventing taxonomy;
28. new legitimate country option can be added safely and then assigned;
29. new legitimate location option can be added safely and then assigned;
30. equivalent country/location option is reused rather than duplicated;
31. ambiguous similar location options route to review;
32. select-option schema update preserves all existing options;
33. Notes begins with `Added from archive` exactly once and preserves existing human notes;
34. new-row timestamps/default dates are populated as specified;
35. existing `Created_on`/`imported_on` are not reset on ordinary rename updates;
36. `Last modified` and `Last modified by` follow the specified current-date write rule;
37. `media_archive_link` is empty on create, is never auto-derived, and any existing populated value is preserved exactly;
38. `media_archive_path` stores full current path;
39. same tracked file rename safely updates Filename/path from old to new;
40. different unproven archive representation does not overwrite existing path;
41. Tool 1's final successful commit after Tool 2/Tool 3 collaboration records/initiates Tool 4 synchronization;
42. Tool 1's initial/intermediate rename, mere approval, and dry-run do not write Baserow;
43. Tool 4 failure after rename leaves a durable pending sync rather than reverting the file;
44. retry of pending sync uses a fresh current Tool 2 review plus Tool 4 pre-write revalidation;
45. portal mutating action calls service layer and revalidates live state;
46. CLI dry-run performs zero mutations;
47. CLI explicit commit invokes the same service used by Tool 1/portal;
48. audit record contains before/after/provenance without credentials.
49. Tool 1 and Tool 3 production composition constructs no Baserow client/provider and receives no Baserow credentials;
50. Tool 2's provider is demonstrably read-only and cannot issue row/select-option/schema mutations;
51. Tool 3 runs offline from a verified schedule artifact produced through Tool 2's read-only boundary;
52. Tool 1 calls Tool 4 only after the final filename for the current stage is committed;
53. Tool 4 uses Tool 2's fresh current review as the create-vs-update gate and directly revalidates write preconditions.

The Builder may add more tests as implementation details warrant.

---

## 23. Representative evaluation before acceptance

Before Tool 4 is accepted, run a safe representative evaluation of the integrated Tool 1–4 flow over the project's 260-file sample set **without bulk-writing production Baserow**. Tool 2 performs the live read-only Media checks; Tool 3 consumes its verified static schedule reference; Tool 4 only previews the resulting create/update work.

The evaluation should report at least:

```text
total files
would-update existing rows
would-create new rows
no-op/already synchronized
review-required conflicts
duplicate/multiple-candidate blocked
insufficient-evidence blocked
database-unavailable
partial-date Notes cases
country/location option additions proposed
archive-path representation conflicts
```

For a representative subset, capture exact before/current/proposed field diffs and verify unrelated online/source fields remain untouched.

If a non-production Baserow write fixture/table is available, exercise actual create/update/select-option transport there. Production write smoke testing is separately controlled and must be explicitly scoped.

---

## 24. Definition of done

Tool 4 is implementation-complete only when:

- application service, Baserow write gateway, CLI, portal integration, and local durable sync state are implemented;
- Tool 4 is the sole writer/schema mutator and owns direct pre-write validation reads;
- Tool 2 remains strictly read-only and is the live create-vs-update/existing-item gate;
- Tool 1 and Tool 3 contain no operational Baserow access and receive no Baserow credentials/providers;
- Tool 3 consumes a complete verified schedule reference produced through Tool 2's read-only boundary;
- pre-update and pre-create race checks satisfy project policy;
- new-row defaults and partial-date Notes behavior match this plan;
- existing rows are minimally patched without clearing unrelated formats/sources;
- country/location option creation is safe and all other taxonomy remains bounded to existing options;
- Tool 1's final committed rename for each processing stage automatically creates durable Tool 4 sync work;
- failures are retryable/idempotent without rolling back successful filesystem commits;
- required tests pass under Python 3.12;
- representative 260-file write-preview evaluation is documented;
- package build/helper validations pass;
- implementation branch is pushed and PR opened;
- required GitHub `Python 3.12 tests` CI is green;
- status is `READY_FOR_REVIEW` with reachable commit/PR evidence.

Only the orchestrator/reviewer changes the tool from `READY_FOR_REVIEW` to `ACCEPTED` after independent review.

---

## 25. Builder boundaries

The Builder may choose ordinary implementation details such as class names, HTTP methods supported by the current Baserow API, internal model names, SQLite table names, retry backoff, and UI layout.

The Builder must **not** silently change:

- Tool 4 as the sole Baserow write/schema-mutation boundary;
- Tool 2 live read-only reconciliation as the create/update gate;
- Tool 3 schedule consumption through a verified artifact produced via Tool 2's read-only boundary;
- live pre-write race protection;
- the no-silent-overwrite conflict rule;
- full-date-only Baserow `Date` semantics;
- partial-date-in-Notes behavior;
- `media_archive_link` being unavailable to Tools 1–4, empty on create, and immutable through this workflow;
- `media_archive_path` full-path behavior;
- preservation of unrelated format/source fields;
- select-option policy, especially country/location-only automatic additions;
- automatic Tool 1 post-final-commit synchronization after Tool 2/Tool 3 collaboration;
- no production bulk-write during Builder evaluation;
- idempotency and durable pending-sync behavior.

Any actual contradiction discovered during implementation must be recorded as `Q-###` in `status/tool-4-media-database-updater.md` and unaffected work may continue. Do not edit this finalized plan to fit the implementation.

## 26. User-directed practical metadata correction (2026-09-17)

For the practical Tool 1–4 workflow:

- a pure scripture reference is the Baserow title in readable form (for example `SB-1-19-31` becomes `SB 1.19.31`);
- Category must use the exact equivalent option returned by the live schema (for example `Srimad-bhagavatam`);
- a text-type Tag receives the scripture verse as text, while a multi-select Tag remains option-bound;
- Language options are retrieved from the live schema; a new English recording uses the live `English` option and existing Language values are preserved on update;
- Notes begins with `Added from archive` and records the immutable original filename and original full path exactly once;
- a repeat rename of the same tracked file may update Filename and `media_archive_path` when the live row still contains the immediately previous committed filename/path.
