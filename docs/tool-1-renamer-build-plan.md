# Tool 1 — Renamer Build Plan

Status: **finalized requirements / ready for implementation**

Tracking issue: #1

Post-acceptance architecture amendment: `docs/baserow-access-boundary-amendment.md`

The amendment supersedes every requirement below that assigns Baserow access or a Baserow provider to Tool 1. Tool 1 retains its accepted parsing, evidence, naming, and commit behavior. It asks Tool 2 for the live Media check and Tool 3 for schedule/date evidence, commits the final filename for that processing stage, and then calls Tool 4 once to synchronize Baserow.

## 1. Purpose

Tool 1 is a fast, repeatable metadata interpretation and filename normalization tool for the media archive. It should rename files whenever the available evidence supports an improvement, without waiting for slower audio/content processing.

The same Renamer is reused at multiple pipeline stages:

1. Initial Renamer
2. Media Database Reviewer
3. Travel Schedule Reviewer
4. Media Database Updater
5. Renamer update/enrich
6. Content/audio tools
7. Renamer update/enrich/finalize

A filename is progressive: later passes may enrich or correct it as stronger evidence becomes available.

## 2. Canonical filename standard

Canonical structure:

```text
WHEN_WHO_WHAT_WHERE.ext
```

Rules:

- `WHEN`: `YYYY-MM-DD`
- `WHO`: always `KKS`
- `WHAT`: recording topic/content
- `WHERE`: canonical place plus lowercase ISO 3166-1 alpha-2 country code
- separator between WHEN/WHO/WHAT/WHERE: underscore `_`
- separator inside each field: hyphen `-`
- extension: lowercase
- canonical filename text: ASCII Latin
- no `UNKNOWN` placeholder
- missing date components remain explicit, e.g. `2019-09-DD`, `2018-MM-DD`, `YYYY-MM-DD`

Examples:

```text
2010-09-10_KKS_SB-1-4-5.mp3
2019-09-DD_KKS_Kirtan_Vrindavan-in.mp3
2011-05-24_KKS_Jaya-Radha-Madhava_Villa-Vrindavan-it.mp3
```

## 3. Processing tracking ID

Every non-finalized file in active processing receives a stable local suffix:

```text
_ID-xxxxxxxx
```

where `xxxxxxxx` is eight hexadecimal characters.

Example:

```text
2019-09-DD_KKS_Kirtan_Vrindavan-in_ID-a7c92e4b.mp3
```

Requirements:

- generate from a strong random/UUID source
- check for local collision before accepting
- assign once and preserve through later renames
- recognize and reuse an existing `_ID-xxxxxxxx`
- never append a second processing ID
- processing ID is local operational identity, not a permanent archive ID
- remove at finalization unless unresolved final collision rules require retention

If a filename cannot yet be meaningfully interpreted, retain its useful wording and add the processing ID rather than inventing metadata:

```text
R09_0004.MP3
→ R09_0004_ID-a7c92e4b.mp3
```

## 4. `_edited` handling

Recognize `_edited` case-insensitively when used as the reserved underscore suffix token.

Example:

```text
2012-05-13_KKS_BG-8-19-Sundayfeast_Sydney_edited.mp3
```

Behavior:

- parse the filename normally
- add/retain the processing ID while the file is in the workflow
- treat the file as already audio-processed
- still perform the required Baserow Media check
- after that check is complete, a later Renamer pass may remove `_edited`
- retain the processing ID until overall processing finalizes

Edited files skip Tools 5, 6, 8, 9, and 10. Tool 7 may still be used when WHAT genuinely requires resolution.

## 5. Operating modes

Implement one reusable Renamer engine with three modes.

### Initial

Fast interpretation using cheap evidence only:

- current filename
- parent/ancestor folders
- sibling filename grammar
- Baserow reference data
- cheap filesystem creation/modification timestamps as supporting clues only

Do not:

- decode audio/video
- transcribe
- inspect MP3 tags/deep embedded media metadata

### Enrich

Rerun the same engine using stronger evidence produced by other tools, including Media Database Reviewer, Travel Schedule Reviewer, Media Database Updater, Tool 7, and later processing stages.

### Finalize

When the orchestrator says processing is complete:

- render the best confirmed canonical filename
- remove processing-only annotations
- remove `_ID-xxxxxxxx`
- resolve final filename collisions
- validate before committing the rename

## 6. Parser architecture

Use deterministic/reference-driven parsing first. Do not make a local AI model a v1 dependency.

Recommended flow:

```text
raw filename/path
→ preserve original evidence
→ detect technical/process annotations
→ normalized working representation
→ analyze folder/collection grammar
→ extract structured spans
→ resolve WHEN
→ resolve WHAT
→ resolve WHERE
→ classify residual text
→ resolve candidate conflicts
→ build rename proposal
```

Input parsing must be lenient; conclusions must remain conservative.

### Tokenization

Do not split naively on spaces/hyphens/underscores. Recognize structured spans first because punctuation has multiple meanings.

Tolerate mixed separators including spaces, `_`, `-`, `.`, `/`, `\\`, commas, semicolons, colons, `+`, `&`, brackets, and repeated mixtures.

Match multi-word phrases before individual tokens.

## 7. Collection and sibling grammar

Analyze a directory as a collection before interpreting each file independently.

Example:

```text
Prague-Oct-2003/
  Lekce/
    A019 03-10-23 BG 3.12 Praha.mp3
    A020 03-10-24 SB 9.23.22 Praha.mp3
    A021 03-10-24 BG 4.38 Praha.mp3
```

The repeated structure can establish grammar such as:

```text
<source-id> <YY-MM-DD> <WHAT> <WHERE>
```

Sibling structure may be strong evidence. Sibling metadata itself is weaker and must not be copied blindly to another file.

Example: a sibling location must not automatically assign that location to every file in the folder.

## 8. Sequence/source identifiers

Numbers and codes must be typed by context.

Examples:

- `01`, `02`, `03` across a folder may be sequence indices, not days
- `A019`, `A020`, `A022F` are source/archive identifiers
- `A022F` remains opaque unless later evidence defines the suffix
- `R09_0004` is recorder/source-like and must not be decomposed into invented date semantics

Retain sequence/source metadata for diagnostics and possible future use even if it is removed from canonical filenames.

## 9. WHEN parsing

Archive recording years are restricted to 1993–2023.

Two-digit year expansion:

- `93`–`99` → 1993–1999
- `00`–`23` → 2000–2023
- `24`–`92` are invalid as two-digit recording years for this archive

Four-digit years outside the archive range must not be silently corrected.

Ambiguous numeric dates:

- eliminate impossible calendar interpretations
- known non-US context → prefer D-M-Y
- known US context → prefer M-D-Y
- unknown location → D-M-Y provisional default, retain credible M-D-Y alternative
- strong folder grammar may override the generic default

Example:

```text
10-9-10
selected provisional: 2010-09-10
retained alternative: 2010-10-09
```

### Multilingual months

Generate month aliases from Unicode CLDR/ICU/locales, with a project override asset for archive-specific spellings, typos, and transliterations.

Initial coverage should include at least:

- English
- French
- German
- Spanish
- Dutch
- Hindi
- Czech
- Slovak
- Russian

Examples:

```text
Sep → 09
September → 09
duben → 04
```

Use Unicode-aware matching. Recognize original script first and transliterate only when needed for matching/output.

## 10. WHAT resolution

Preserve a meaningful specific WHAT; never replace it with a broader category.

Examples:

```text
SB 1.4.5 → SB-1-4-5
Bhajans → Kirtan   (when supported by category_title)
JRM → Jaya-Radha-Madhava   (when supported by project reference data)
```

A specific WHAT remains more specific than the broad category.

Folder category is strong supporting context but must not flatten or override contradictory direct filename WHAT.

Example:

```text
/Kirtany/JRM.mp3
→ category context Kirtan, WHAT Jaya-Radha-Madhava
```

but:

```text
/Kirtany/SB 9.23.22.mp3
```

must retain the filename scripture WHAT and flag the folder-category conflict.

Tool 7 is responsible for resolving unidentified class WHAT. Tool 1 must not invent a class type merely to complete a filename.

## 11. Baserow `category_title`

Relevant fields:

- `category`
- `title_matching_terms`
- `folder_path`
- `color`

`title_matching_terms` is comma-separated.

Requirements:

- load all rows once per batch
- split terms safely
- build an in-memory normalized index
- match longer/more specific phrases before shorter tokens
- use case-insensitive normalized matching
- inspect all possible matches; never use first-match-wins
- support multiple matches/combination clues
- do not hard-code title terms in application code

`folder_path` is routing metadata for later file-placement tools. `color` is UI metadata.

## 12. Vedabase

Vedabase is the sole authority on scriptural/content references.

Use it only when a scripture-like WHAT candidate already exists and needs cheap validation. Do not use Tool 1 for open-ended content discovery.

Cache Vedabase lookups locally for 24 hours. If Vedabase is unavailable, mark validation pending/stale and continue the batch.

## 13. WHERE resolution

WHERE is entity resolution, not string cleanup.

Primary shared location knowledge comes from Baserow Media `place_location` and `Country` values.

Resolution order:

```text
candidate filename/folder text
→ exact canonical place match
→ normalized canonical place match
→ bounded fuzzy match
→ existing fact-checked Baserow context
→ online location lookup when needed
→ unresolved/review
```

Country behavior:

- Baserow stores human-readable country names
- filename stores lowercase ISO 3166-1 alpha-2

Examples:

```text
Italy → it
India → in
Czech Republic → cz
United Kingdom → gb
United States → us
```

Canonical filename spelling/capitalization comes from the resolved location entity, then is rendered to ASCII for the filename.

### Adding new places/countries

Before adding anything to Baserow:

- normalize
- exact search
- normalized search
- near-duplicate/fuzzy check

If a place/country is clearly new and confidently resolved, add the canonical value so collaborators can reuse it.

If a likely duplicate or ambiguity exists, do not create a new value; flag for review and continue processing.

Use normalized `place + country` identity to reduce duplicate risk.

### Online location lookup

Use an adapter/provider abstraction, not provider-specific code throughout the parser. Cache successful lookup results locally. The specific provider can be chosen during implementation based on reliability, terms, and rate limits.

## 14. Technical and residual annotations

Technical/status text must be typed separately from WHAT/WHERE.

Examples:

- `CORRUPTED`
- `edited`
- `recovered`
- `copy`
- `final`
- `HQ`
- `part-1`
- sequence/track markers

Do not force every leftover token into canonical metadata.

Example:

```text
A022F 03-10-25 SB 4.9.11 Nezkracena Farma KD.mp3
```

If `Nezkracena` is not confidently classified, preserve it as unclassified annotation/evidence rather than making it WHAT or WHERE.

## 15. Combination clues

Terms such as `and`, `with`, `&`, or `plus` may indicate a combination but must not automatically trigger a split.

Example:

```text
JRM and class 24/5/11 villa vrindavan.mp3
```

Tool 1 should record `possible_combination = true`, retain useful metadata, and keep the file moving.

Actual content discovery and cutting belong to Tools 5 and 6.

## 16. Resolution states

Do not invent opaque percentage confidence in v1.

Use field-specific qualitative states:

- `exact`
- `strong`
- `provisional`
- `ambiguous`
- `unresolved`

Automatic renaming may use exact, strong, and provisional selections when the selected interpretation and alternatives are preserved in evidence/logging.

Ambiguous/unresolved fields must not be invented.

## 17. Fuzzy matching

Use fuzzy matching only after exact and normalized matching.

Bound fuzzy search to the relevant dataset:

- location candidate → known places only
- WHAT candidate → relevant title/reference terms only
- month candidate → month alias table only

Do not compare every token against every available value.

Near-ties or suspicious fuzzy matches should become review candidates, not silent decisions.

## 18. AI/ML policy

Renamer v1 should not require a local AI model.

Start with:

- deterministic parsing
- Baserow/reference lookups
- collection grammar
- bounded fuzzy matching
- contextual candidate resolution

Design an optional candidate-ranking interface so a local ML/MLX model can be added later if sample evaluation demonstrates recurring failures that rules cannot solve reliably.

If AI is added later, prefer ranking parser-generated candidates over free-form filename generation.

## 19. ASCII rendering

Interpret first, render later.

Examples:

```text
Zürich → Zurich
Průhonice → Pruhonice
Málaga → Malaga
```

Baserow may retain proper human-readable Unicode spelling; canonical filenames are ASCII Latin.

## 20. Filesystem timestamps

Collect creation/modification timestamps cheaply if useful, but treat them only as supporting evidence. They are not recording dates by default.

Do not inspect embedded media tags in Tool 1.

## 21. Internal parser result

Each analyzed file should produce a structured result equivalent to:

```text
identity
  tracking_id
  original_filename
  original_path
  current_filename
  extension

context
  parent_folder
  ancestor_folders
  sibling_pattern_context
  filesystem timestamps

WHEN
  selected_value
  precision
  state
  alternatives[]
  evidence[]

WHO
  value = KKS

WHAT
  selected_value
  category
  state
  evidence[]

WHERE
  place_location
  country
  country_iso2
  state
  alternatives[]
  evidence[]

file_metadata
  edited
  corrupted
  source_sequence_id
  part_or_track_number
  possible_combination
  other_annotations[]

unclassified_text[]
conflicts[]
review_reasons[]
```

Use typed application models plus JSON-serializable logging. Exact programming-language types are an implementation detail.

## 22. Evidence provenance

Every selected value must retain why it was selected.

Evidence sources may include:

- direct filename
- immediate parent folder
- ancestor folder
- sibling grammar
- Baserow Media table
- Baserow `category_title`
- travel schedule
- Vedabase
- online location lookup
- cheap filesystem timestamps
- later-tool outputs

Keep alternatives and previous selections in local history when later evidence changes the chosen interpretation.

## 23. Rename planner

Separate interpretation from filesystem mutation.

Recommended structure:

```text
ParserResult
→ RenamePlanner
→ RenameProposal
→ validation
→ filesystem commit
```

The planner should construct the best justified current filename, retain the tracking ID, and refuse to invent missing metadata.

## 24. Batch execution

Use two phases.

### Analysis phase

- inspect directory structure
- infer sibling/folder grammar
- load/cached reference data
- parse every file
- generate rename proposals
- detect collisions
- emit dry-run results

### Commit phase

- validate proposals again
- apply safe renames
- update local registry/history
- record per-file failures without halting independent files

Dry-run mode is mandatory.

Analyzing the whole directory before committing prevents earlier renames from changing evidence needed by later files in the same collection.

## 25. Safety requirements

The Renamer must never:

- silently overwrite an existing file
- lose original filename/path evidence
- append a second tracking ID
- invent metadata merely to make a filename prettier
- stop an entire batch because one file is ambiguous
- create likely duplicate Baserow values without checking
- treat filename equality as proof of duplicate content

Failures should be isolated to the affected file and retryable.

## 26. Final collision handling

At finalization:

### No collision

Remove the processing ID.

```text
2011-05-24_KKS_SB-1-4-5_Vrindavan-in.mp3
```

### Fully identified, distinct recordings with identical canonical names

Leave the first unsuffixed and number later items:

```text
2011-05-24_KKS_SB-1-4-5_Vrindavan-in.mp3
2011-05-24_KKS_SB-1-4-5_Vrindavan-in-02.mp3
2011-05-24_KKS_SB-1-4-5_Vrindavan-in-03.mp3
```

### Still unresolved and colliding

Retain a unique identifier in the final name rather than pretending the recordings are fully distinguishable.

Duplicate-content decisions belong to a future fingerprint/hash tool.

## 27. Local processing registry

Use a per-device/per-batch local registry. It is not shared across collaborators.

SQLite is recommended.

Store at least:

- tracking ID
- current path
- current filename
- parser result
- selected WWWW values
- technical flags
- pending review status
- rename history
- tool/parser version
- timestamps

Baserow remains the shared collaboration surface; the local registry is operational state only.

## 28. Baserow integration boundary

Tool 1 must not construct a Baserow adapter or receive Baserow credentials. It requests live Media/reference evidence through Tool 2's read-only application service and requests mutations only by calling Tool 4 after the final filename for the current processing stage is committed.

The Baserow-agnostic Tool 2 interface used by Tool 1 may provide high-level operations such as:

```text
get_category_titles()
get_known_locations()
get_country_values()
find_place(...)
find_country(...)
```

Tool 1's parser must not care which transport Tool 2 uses underneath. Mutable Media state must follow the live-current policy; committed static parser aids may be cached only as non-authoritative parsing references.

## 29. Logging and evaluation

Structured logging is mandatory and is part of the implementation contract.

Primary detailed format: JSONL, one record per file/action.

Include at minimum:

- tracking ID
- original/current/proposed filename
- original/current path
- parser result
- evidence sources
- alternatives
- folder grammar used
- fuzzy match details
- Tool 2 lookup and Tool 4 synchronization outcomes
- online lookups
- Vedabase validation
- conflicts
- review reasons
- rename result/error
- processing duration
- parser/tool version

Also generate a simple CSV summary for human inspection.

The sample-evaluation workflow should distinguish:

- correct automatic interpretation
- correct provisional interpretation
- correctly unresolved
- incorrect automatic interpretation

Incorrect automatic interpretation is the most important regression category.

## 30. Golden/sample tests

Create a curated test corpus containing representative difficult cases, including at least:

```text
KKS-10-9-10 - SB 1.4.5.mp3
KKS Bhajans vrindavan sep 2019.mp3
JRM and class 24/5/11 villa vrindavan.mp3
R09_0004.MP3
2012-05-13_KKS_BG-8-19-Sundayfeast_Sydney_edited.mp3
```

and folder-level examples covering:

```text
KKS DUBEN 2008 MP3/
  07 KKS PRUHON.mp3
  08 KKS SB 3.1.26.mp3

Prague-Oct-2003/
  Lekce/
    A019 03-10-23 BG 3.12 Praha.mp3
    A020 03-10-24 SB 9.23.22 Praha.mp3
    A022F 03-10-25 SB 4.9.11 Nezkracena Farma KD.mp3
```

Tests should cover parsing, proposal generation, idempotency, collision safety, Baserow duplicate prevention, and finalization.

## 31. Performance requirements

Tool 1 must remain a fast pass over thousands of files.

Requirements:

- no media decoding/transcription
- no deep embedded tag inspection
- batch-load Baserow references
- cache normalized indexes in memory
- cache location lookups
- cache Vedabase for 24 hours
- scan each directory once for sibling grammar
- avoid repeated filesystem stat calls
- make online calls only for unresolved cases
- allow safe concurrency for independent files/API work where appropriate

Initial target platform is Apple Silicon macOS. Keep implementation portable enough for future platforms.

Python is a suitable initial implementation language because the tool is parser/data/integration heavy and benefits from mature date, Unicode, fuzzy-match, SQLite, testing, and HTTP libraries. Keep interfaces modular so later UI/orchestrator code is not coupled to parser internals.

## 32. Idempotency

Re-running the Renamer with unchanged evidence must not:

- change an already-valid current filename again
- assign a new tracking ID
- duplicate Baserow writes
- create new review records for the same unresolved condition unnecessarily

Later stronger evidence may legitimately produce a new enriched rename while preserving history and the same processing identity.

## 33. Builder progress and handoff protocol

Implementation progress must be durable in GitHub rather than dependent on direct AI-to-AI conversation.

Use GitHub issue #1 as the implementation thread.

At meaningful milestones, the builder should comment with:

1. completed work
2. commits/PR/files changed
3. tests run and results
4. observed parser behavior, especially incorrect automatic interpretations and correctly unresolved cases
5. open archive-policy decisions/blockers
6. next milestone

Do not silently change archive rules in this build plan. If implementation evidence suggests a specification change, propose it in the issue first.

The planning/review model can later inspect issue #1, the implementation PR/commits, test output, and sample logs to review progress and advise on parser changes.

## 34. Acceptance criteria

Tool 1 v1 is ready only when it demonstrates that:

1. thousands of filenames can be analyzed without decoding media
2. tracking IDs are assigned, preserved, and removed correctly
3. repeated runs are idempotent
4. folder/sibling grammar is used safely
5. specific WHAT information is preserved
6. known locations can resolve through structured Tool 2 read-only evidence without Tool 1 accessing Baserow
7. filename countries use lowercase ISO alpha-2
8. multilingual dates work
9. ambiguous dates retain alternatives
10. `_edited` behavior follows this specification
11. ambiguous files do not halt the batch
12. existing files are never silently overwritten
13. Tool 4 guards against duplicate Baserow creation through a fresh Tool 2 check
14. later evidence enriches filenames without changing processing identity
15. finalization applies the agreed collision scheme
16. diagnostics are detailed enough to diagnose sample failures without guessing
17. sample results are reported in issue #1 before the tool is considered accepted

## 35. Non-goals for Tool 1

Tool 1 does not:

- discover audio content by listening/transcription
- split combination recordings
- determine duplicate audio content
- boost gain
- trim class audio
- replace the responsibilities of the Media Database Reviewer or Travel Schedule Reviewer
- use folder location as workflow state

Those responsibilities belong to other tools and the future orchestrator.

## 36. User-directed practical naming correction (2026-09-17)

This post-acceptance correction is authoritative where it differs from earlier combination fallback behavior:

- dotted scripture aliases such as `S.B.` are valid evidence for `SB`;
- scripture punctuation is normalized to hyphens in filenames (`SB-1-19-31`);
- a secondary combination marker such as `with Radha Madhava` sets combination routing for Tools 5/6 but does not discard an exact primary class reference;
- generated and fallback filenames may contain only ASCII letters, digits, hyphens, underscores, and the single extension dot; parentheses and other punctuation are forbidden;
- `KKS_S.B. 1.19.31(with Radha Madhava)_Oslo_29.8.11.WMA` finalizes as `2011-08-29_KKS_SB-1-19-31_Oslo-no.wma` while remaining marked as a combination.
