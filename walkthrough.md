# Tool 3 — Travel Schedule Reviewer Implementation Walkthrough

Tool 3 (Travel Schedule Reviewer) has been implemented, refined, and verified against test suite and evaluation protocol on branch `tool-3-implementation` (PR #26) in accordance with:
- `docs/tool-3-travel-schedule-reviewer-build-plan.md`
- `docs/baserow-live-data-policy.md` (static reference bootstrap exception)
- `docs/builder-git-sandbox-policy.md`
- `docs/implementation-protocol.md`

Historical walkthrough documentation for previous tools is preserved in:
- [docs/tool-1-renamer-walkthrough.md](docs/tool-1-renamer-walkthrough.md)
- [docs/tool-2-media-database-reviewer-walkthrough.md](docs/tool-2-media-database-reviewer-walkthrough.md)
- [docs/tool-3-travel-schedule-reviewer-walkthrough.md](docs/tool-3-travel-schedule-reviewer-walkthrough.md)

---

## 1. Summary of Architecture & Component Implementation

Tool 3 provides supporting and contextual evidence by evaluating planned travel schedule data against local audio files and upstream Tool 1 / Tool 2 metadata:

1. **Baserow Provider Extension (`media_db_reviewer/baserow_provider.py`)**:
   - Added `fetch_all_travel_schedule_rows()` to retrieve the complete static `travel_schedule` table via paginated read-only HTTP GET requests.
   - Enforces required `travel_schedule_table_id`; missing configuration cleanly raises `BaserowUnavailableError`.

2. **Data Models (`travel_reviewer/models.py`)**:
   - `TravelReviewDecision`: `CORROBORATED`, `PROVISIONAL_ENRICHMENT`, `MULTIPLE_SCHEDULE_CANDIDATES`, `SCHEDULE_CONFLICT`, `NO_SCHEDULE_SUPPORT`, `INSUFFICIENT_EVIDENCE`, and `REFERENCE_UNAVAILABLE`.
   - `NormalizedTravelRow`: Deterministic representation of schedule rows with ISO dates (`start_date`, `end_date`), place, country, and normalized text.
   - `TravelCandidate`: Candidate representation with match rationale, exact/partial date boundaries, and field support indicators.
   - `TravelScheduleManifest`: Metadata container holding schema format version, source table ID, row count, deterministic SHA-256 (including `country_iso2`), and normalized row records.
   - `TravelRenamerEnrichment`: Structured payload specifying provisional WHEN/WHERE enrichments.
   - `TravelReviewResult`: Comprehensive per-file evaluation result with decision, candidate list, notes, conflicts, `tool2_decision`, and selected Media row ID.

3. **Local Reference Store (`travel_reviewer/reference_store.py`)**:
   - Manages bootstrap and verified local persistence in `.renamer/reference/travel_schedule.json`.
   - `compute_canonical_sha256()`: Calculates deterministic SHA-256 strictly over sorted normalized rows including `country_iso2` (excluding volatile timestamps such as `retrieved_at`).
   - Validates deterministic recomputation of `country_iso2` from `country` during reference loading.
   - Atomic disk writes via temporary file renaming to prevent corrupt state.
   - Zero-network normal reuse: normal per-file operations load and verify the local cache without remote network requests.
   - Administrative verification: `verify_remote_reference()` compares remote table state against local cache, detecting unexpected schema or data drift without silent replacement.
   - Deliberate acceptance semantics: `accept_remote_reference()` requires explicit administrator confirmation.

4. **In-Memory Multi-Key Index & Matching Engine (`travel_reviewer/engine.py`)**:
   - `TravelScheduleIndex`: Multi-key index indexing schedule entries by exact dates, normalized places, and sorted chronological ranges for fast lookups.
   - Structured location parsing: `parse_structured_where()` extracts trailing 2-letter ISO country codes while preserving hyphenated places (`Villa-Vrindavan`, `Serbia-summer-camp`, `New-York`, `Krsna-Dvur`).
   - Long range support: Unbounded date containment for valid ranges longer than 366 days via interval lookups without memory bloat.
   - Strict input validation: Detects malformed explicit range endpoints (`end_date` non-empty but unparseable) and inverted intervals (`end < start`), isolating them in `invalid_rows` so they cannot authorize enrichments.
   - Semantic candidate grouping: `group_candidates_semantically()` collapses duplicate rows using canonical place aliases and effective intervals (`eff_end = end or start`), preserving the complete union of contributing Baserow row IDs and schedule texts with deterministic multi-key ordering.
   - Safety guards: Structured location identity `(canonical_place, country_iso)` protects against conflating same-name places across different countries; media authority guard protects confirmed Media WHERE and WHEN.

5. **Cross-Tool Renamer Compatibility (`renamer/models.py`, `renamer/service.py`)**:
   - Extended `EnrichmentEvidence` with `when_state` and `where_state` fields.
   - Modified `RenamerApplicationService.apply_enrichment()` to preserve caller-provided resolution states (e.g. `PROVISIONAL`), ensuring schedule-derived metadata is never promoted to `EXACT` or `STRONG`.
   - Prevents duplicate evidence tokens on repeated runs.

6. **Application Service (`travel_reviewer/service.py`)**:
   - `TravelScheduleReviewService`: High-level service handling `review_file()` and `review_batch()`.
   - Passes engine index to public search helpers (`search_by_when`, `search_by_where`).
   - Live Media authority incorporation: Integrates `MediaDatabaseReviewService` to obtain current live Media review context when not provided.
   - Strict authority hierarchy:
     - Confirmed Tool 2 Media associations and high-authority local values (full 10-char date, exact location) are protected and never overwritten.
     - Only safe, unique schedule candidates authorize automatic provisional enrichment (`when_state=PROVISIONAL` or `where_state=PROVISIONAL`).
     - Ambiguous matches (`MULTIPLE_SCHEDULE_CANDIDATES`), conflicts (`SCHEDULE_CONFLICT`), and unconstrained records (`INSUFFICIENT_EVIDENCE`) do not alter proposals.
   - Truthful error classification: Per-file exceptions during batch runs are recorded cleanly as `INSUFFICIENT_EVIDENCE` with distinct diagnostics when reference is healthy (never misclassified as `REFERENCE_UNAVAILABLE`).

7. **Registry & Audit Integration (`renamer/registry/registry.py`)**:
   - SQLite `travel_reviews` table storing Tool 3 decision state, `tool2_decision`, candidates, notes, and conflicts.
   - Schema migration check supporting `tool2_decision TEXT` column.
   - Implemented `save_travel_review()`, `get_travel_review()`, and `list_travel_reviews()`.

8. **CLI Interface (`cli.py`)**:
   - Added `media-archive travel-review` command with `--registry-path`, `--reference-path`, positional `TRACKING_ID`, `--no-enrich`, and table output.
   - Added `media-archive travel-reference {init,status,verify}` for administrative lifecycle management with protections against overwriting existing verified references.

9. **Review Portal Integration (`review_portal/`)**:
   - `app.py`: Loads Tool 3 review results into template context for file details.
   - `templates/detail.html`: Renders Tool 3 Travel Schedule card with decision badges, `tool2_decision`, selected media row, candidate comparison states (`date_comparison`, `place_comparison`, `country_comparison`), rationale, notes, and conflict warnings.

---

## 2. Review Findings Addressed (R-001 through R-012)

### Round 1 Corrections (R-001 through R-007)
- **R-001 (Confirmed Tool 2 Media Authority)**: Incorporated confirmed Tool 2 Media WHEN/WHERE values as authoritative recording evidence in `TravelScheduleEngine.evaluate()`. Added `_apply_media_authority_guard()` preventing schedule evidence from contradicting, overwriting, or downgrading confirmed Media dates/locations. Wired `TravelScheduleReviewService` to obtain live Tool 2 context via `MediaDatabaseReviewService`. Added regressions `test_r001_local_date_missing_confirmed_media_date_no_contradictory_when_enrichment` and `test_r001_local_place_missing_confirmed_media_where_no_contradictory_where_enrichment`.
- **R-002 (Representative Evaluation with Live Media Context)**: Re-ran the 260-file acceptance evaluation with live Tool 2 Media access, recording exact Tool 2 decision breakdowns, 133 files routed downstream, zero database failures, and zero high-authority overwrites.
- **R-003 (Immutable Schedule Reference Replacement Protection)**: Updated `TravelReferenceStore.ensure_reference()` to never overwrite an existing verified reference. Updated `travel-reference init` CLI to refuse replacement of verified references, directing administrators to `verify`. Created `accept_remote_reference()` for deliberate acceptance. Added regression `test_r003_verified_reference_not_overwritten_by_init_when_remote_checksum_differs`.
- **R-004 (Malformed Explicit End Date Validation)**: Modified `TravelScheduleIndex._build_index()` to detect non-empty unparseable end dates, routing them to `invalid_rows` so they cannot be treated as 1-day visits or authorize enrichment. Added regression `test_r004_valid_start_with_malformed_nonempty_end_date_cannot_authorize_enrichment`.
- **R-005 (Canonical Place Alias & Interval Grouping)**: Updated `group_candidates_semantically()` to use `index.canonical_place()` and effective end dates (`eff_end = end or start`), preserving all contributing Baserow row IDs and schedule texts. Added regressions `test_r005_alias_equivalent_places_grouped_with_all_row_ids_and_text_preserved` and `test_r005_missing_end_vs_explicit_single_day_grouped_with_both_row_ids`.
- **R-006 (Unbounded Long Range Support)**: Extended `TravelScheduleIndex` with `self.long_ranges` and interval containment checks in `get_rows_by_date()`, `get_rows_by_month()`, and `get_rows_by_year()` for ranges spanning > 366 days. Added regression `test_r006_valid_explicit_range_longer_than_366_days_indexed_and_found`.
- **R-007 (Configuration & Branch Hygiene)**: Synchronized `tool-3-implementation` with current `main`, added `BASEROW_TRAVEL_SCHEDULE_TABLE_ID=` to `.env.example`, documented PR #26, and verified required GitHub CI checks.

### Round 2 Corrections (R-008 through R-012)
- **R-008 (Media WHERE Authority & Structured Location Parsing)**: Implemented `parse_structured_where()` to extract trailing 2-letter ISO country codes while preserving hyphenated places (`Villa-Vrindavan`, `Serbia-summer-camp`, `New-York`, `Krsna-Dvur`). Extended media authority guard to suppress schedule WHERE enrichment if canonical places differ or if both have known countries that differ. Supported confirmed Media WHERE as Case-B anchor without provisional WHERE enrichment when local lacks anchors. Added regressions `test_r008_hyphenated_confirmed_place_not_truncated`, `test_r008_same_place_different_country_media_guard_suppresses_enrichment`, and `test_r008_confirmed_media_where_only_acts_as_case_b_anchor_without_provisional_where`.
- **R-009 (Structured-Location Identity, Provenance, & Deterministic Grouping)**: Implemented structured location identity `(canonical_place, country_iso)` in Case C to prevent auto-selecting an ambiguous candidate when the same place name occurs in different countries on the same date. Ensured union of all contributing row IDs and texts across semantic candidates is preserved for the selected candidate. Implemented deterministic sorting for candidates and semantic groups independent of input iteration order. Passed `engine.index` to `search_by_when()` and `search_by_where()` service helpers. Added regressions `test_r009_same_place_different_country_multiple_candidates_in_case_c`, `test_r009_reversed_input_order_deterministic_grouping_and_provenance`, and `test_r009_union_of_row_ids_and_texts_preserved_for_selected_candidate`.
- **R-010 (Canonical Checksum Inclusion & Validation of `country_iso2`)**: Included `country_iso2` in `compute_canonical_sha256()` and added deterministic recomputation and validation of `country_iso2` from `country` during reference loading. Added regression `test_r010_tampered_country_iso2_rejected_by_load_reference`.
- **R-011 (Tool 2 Decision Snapshot & Candidate Explainability Contract)**: Added `tool2_decision` to `TravelReviewResult` and SQLite schema migration in `registry.py`. Populated candidate comparison states (`date_comparison`, `place_comparison`, `country_comparison`) and `match_reasons` for Cases B and C. Displayed `tool2_decision` and candidate comparison states in detail portal template. Added regressions `test_r011_tool2_decision_snapshotted_in_result_and_registry` and `test_r011_candidate_comparison_states_populated_in_cases_b_and_c`.
- **R-012 (Truthful Failure Classification & Live Evaluation Metrics)**: Reclassified per-file batch errors as `INSUFFICIENT_EVIDENCE` with distinct diagnostics when reference is healthy. Extended 260-file acceptance evaluation to verify and assert zero overwrites for both local high-authority values and confirmed Tool 2 Media values (`overwritten_high_authority = 0`, `overwritten_confirmed_media_authority = 0`). Updated walkthroughs, CLI syntax, score wording, and representative tracking IDs. Added regression `test_r012_batch_error_does_not_produce_reference_unavailable_when_reference_healthy`.

---

## 3. Test Suite & Verification Results

### Test Execution
```bash
.venv/bin/pytest -q
```
**Result**: **214 passed, 2 warnings** across the entire project:
- `tests/test_travel_reviewer.py`: **57/57 passed** (40 required base tests + 7 Round 1 regression tests + 10 Round 2 regression tests)
- `tests/test_media_db_reviewer.py`: **63/63 passed** (Tool 2 regression suite)
- `tests/test_renamer.py`: **88/88 passed** (Tool 1 regression suite)
- `tests/test_cli.py`: **6/6 passed**

### Helper Script Validation
```bash
sh -n scripts/builder-start.sh scripts/review-tool-1.sh
```
**Result**: **PASS** (Zero syntax errors).

### Package Build Verification
```bash
uv build --offline
```
**Result**: **PASS**
- `dist/media_archive_tooling-0.1.0-py3-none-any.whl`
- `dist/media_archive_tooling-0.1.0.tar.gz`

---

## 4. Representative 260-File Evaluation Evidence

A complete evaluation was performed on all 260 representative audio files in `sample-files/` using `scripts/run_tool_3_evaluation.py` with live Tool 2 Media Database Review reconciliation. The results are serialized in `docs/eval_summary_tool3.json`.

### Tool 2 Media Database Review Breakdown
```text
Total files evaluated:                260
- CONFLICT_WITH_EXISTING:              69 (26.5%)
- EXISTING_MEDIA_MATCH:                 1 ( 0.4%)
- INSUFFICIENT_EVIDENCE:               32 (12.3%)
- MULTIPLE_CANDIDATES:                 99 (38.1%)
- NEW_MEDIA_CANDIDATE:                 39 (15.0%)
- PROBABLE_EXISTING_MEDIA:             20 ( 7.7%)
- DATABASE_UNAVAILABLE:                 0 ( 0.0%)
```

### Tool 3 Travel Schedule Review Breakdown
```text
Total files evaluated:                260
Files entering from Tool 2 routing:   133
Media context unavailable:              0
Overwritten high-priority local:        0
Overwritten confirmed-Media authority:  0

Decision Breakdown:
- CORROBORATED:                        36 (13.8%)
- PROVISIONAL_ENRICHMENT:              44 (16.9%)
  - WHEN enrichments:                  19
  - WHERE enrichments:                 25
- MULTIPLE_SCHEDULE_CANDIDATES:         2 ( 0.8%)
- SCHEDULE_CONFLICT:                   67 (25.8%)
- NO_SCHEDULE_SUPPORT:                 54 (20.8%)
- INSUFFICIENT_EVIDENCE:               57 (21.9%)
- REFERENCE_UNAVAILABLE:                0 ( 0.0%)
```

### Key Behavioral Verifications

1. **Zero Overwritten High-Authority / Confirmed-Media Values**:
   - High-authority local metadata (full 10-character dates, exact locations) and confirmed Tool 2 Media rows were untouched.
   - Counts: `overwritten_high_authority = 0`, `overwritten_confirmed_media_authority = 0`.

2. **Safe Provisional WHERE Enrichment**:
   - When an audio file had an exact date but missing or generic location, Tool 3 supplied the unique scheduled location with `where_state="provisional"`.
   - **Example 1** (`bba9fafc`):
     - Before: `2015-07-10_KKS_SB-1-2-19-Serbia-summer-camp_ID-bba9fafc.mp4`
     - After: `2015-07-10_KKS_SB-1-2-19-Serbia-summer-camp_Serbian-Summer-Camp-rs_ID-bba9fafc.mp4`
     - Where: `Serbian-Summer-Camp-rs` (`provisional`)
   - **Example 2** (`d7986042`):
     - Before: `2012-01-07_KKS_SB-3-24-45-Leipzig_ID-d7986042.mp3`
     - After: `2012-01-07_KKS_SB-3-24-45-Leipzig_Radhadesh-be_ID-d7986042.mp3`
     - Where: `Radhadesh-be` (`provisional`)

3. **Safe Corroboration**:
   - When audio files possessed exact dates and locations matching the travel schedule, Tool 3 confirmed agreement without mutating proposals.
   - **Example 1** (`464488ed`): `A024 03-10-26 SB 9.23.32 Praha.mp3` -> Schedule corroborates recording date `2003-10-26` and location `Praha`.
   - **Example 2** (`c832d03a`): `A021 03-10-24 BG 4.38 Praha.mp3` -> Schedule corroborates recording date `2003-10-24` and location `Praha`.

4. **Conflict Detection**:
   - When filename locations directly contradicted the travel schedule for the same date, Tool 3 flagged `SCHEDULE_CONFLICT` without modifying metadata.
   - **Example 1** (`657dc673`): `A022 03-10-25 SB 4.9.11 Farma KD.mp3` -> Flagged conflict: Schedule on `2003-10-25` records speaker in `Prague-cz`, not `Krsna-Dvur`.
   - **Example 2** (`509d5ffd`): `A025 03-10-26 Govardhana lekce Pruhon.mp3` -> Flagged conflict: Schedule on `2003-10-26` records speaker in `Prague-cz`, not `Pruhonice`.

5. **Multiple Candidates Isolation**:
   - In ambiguous cases (e.g. `R09_0004.MP3`, tracking ID `7e7faea8`), where a location matched multiple distinct schedule visits without a date to disambiguate, Tool 3 recorded 3 candidates and marked `MULTIPLE_SCHEDULE_CANDIDATES`, safely declining automatic enrichment.

6. **Insufficient Evidence Safeguard**:
   - Audio files lacking both date and location (or only vague partial markers like `2012-01-XX` with no location, e.g. `e847e7ba`, `1f7f7b01`) were marked `INSUFFICIENT_EVIDENCE`, preventing unconstrained guessing.
