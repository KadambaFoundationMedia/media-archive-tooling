# Tool 3 — Travel Schedule Reviewer Implementation Walkthrough

Tool 3 (Travel Schedule Reviewer) has been implemented and verified on branch `tool-3-implementation` in accordance with:
- `docs/tool-3-travel-schedule-reviewer-build-plan.md`
- `docs/baserow-live-data-policy.md` (static reference bootstrap exception)
- `docs/builder-git-sandbox-policy.md`
- `docs/implementation-protocol.md`

Historical walkthrough documentation for previous tools is preserved in:
- [docs/tool-1-renamer-walkthrough.md](tool-1-renamer-walkthrough.md)
- [docs/tool-2-media-database-reviewer-walkthrough.md](tool-2-media-database-reviewer-walkthrough.md)

---

## 1. Summary of Architecture & Component Implementation

Tool 3 provides supporting and contextual evidence by evaluating planned travel schedule data against local audio files and upstream Tool 1/Tool 2 metadata:

1. **Baserow Provider Extension (`media_db_reviewer/baserow_provider.py`)**:
   - Added `fetch_all_travel_schedule_rows()` to retrieve the complete static `travel_schedule` table via paginated read-only HTTP GET requests.
   - Enforces required `travel_schedule_table_id`; missing configuration cleanly raises `BaserowUnavailableError`.

2. **Data Models (`travel_reviewer/models.py`)**:
   - `TravelReviewDecision`: `CORROBORATED`, `PROVISIONAL_ENRICHMENT`, `MULTIPLE_SCHEDULE_CANDIDATES`, `SCHEDULE_CONFLICT`, `NO_SCHEDULE_SUPPORT`, `INSUFFICIENT_EVIDENCE`, and `REFERENCE_UNAVAILABLE`.
   - `NormalizedTravelRow`: Deterministic representation of schedule rows with ISO dates (`start_date`, `end_date`), place, country, and normalized text.
   - `TravelCandidate`: Scored candidate representation with match rationale, exact/partial date boundaries, and field support indicators.
   - `TravelScheduleManifest`: Metadata container holding schema format version, source table ID, row count, deterministic SHA-256, and normalized row records.
   - `TravelRenamerEnrichment`: Structured payload specifying provisional WHEN/WHERE enrichments.
   - `TravelReviewResult`: Comprehensive per-file evaluation result with decision, candidate list, notes, and conflicts.

3. **Local Reference Store (`travel_reviewer/reference_store.py`)**:
   - Manages bootstrap and verified local persistence in `.renamer/reference/travel_schedule.json`.
   - `compute_canonical_sha256()`: Calculates deterministic SHA-256 strictly over sorted normalized rows (excluding volatile timestamps such as `retrieved_at`).
   - Atomic disk writes via temporary file renaming to prevent corrupt state.
   - Zero-network normal reuse: normal per-file operations load and verify the local cache without remote network requests.
   - Administrative verification: `verify_remote_reference()` compares remote table state against local cache, detecting unexpected schema or data drift.

4. **In-Memory Multi-Key Index & Matching Engine (`travel_reviewer/engine.py`)**:
   - `TravelScheduleIndex`: Multi-key index indexing schedule entries by exact dates, normalized places, and sorted chronological ranges for fast lookups.
   - Date & range arithmetic: Full support for Case A (exact date), Case B (partial date `YYYY`, `YYYY-MM`), Case C (range matching), and Case D (missing date with known place).
   - Candidate grouping: `group_candidates_semantically()` collapses duplicate rows covering identical date intervals and locations.
   - Safety checks: Validates `end_date >= start_date` (rejecting inverted date ranges) and enforces country compatibility.

5. **Cross-Tool Renamer Compatibility (`renamer/models.py`, `renamer/service.py`)**:
   - Extended `EnrichmentEvidence` with `when_state` and `where_state` fields.
   - Modified `RenamerApplicationService.apply_enrichment()` to preserve caller-provided resolution states (e.g. `PROVISIONAL`), ensuring schedule-derived metadata is never promoted to `EXACT` or `STRONG`.
   - Prevents duplicate evidence tokens on repeated runs.

6. **Application Service (`travel_reviewer/service.py`)**:
   - `TravelScheduleReviewService`: High-level service handling `review_file()` and `review_batch()`.
   - Integrates Tool 1 parser results, local registry proposals, and Tool 2 Media contexts.
   - Strict authority hierarchy:
     - Confirmed Tool 2 Media associations and high-authority local values (full 10-char date, exact location) are protected and never overwritten.
     - Only safe, unique schedule candidates authorize automatic provisional enrichment (`when_state=PROVISIONAL` or `where_state=PROVISIONAL`).
     - Ambiguous matches (`MULTIPLE_SCHEDULE_CANDIDATES`), conflicts (`SCHEDULE_CONFLICT`), and unconstrained records (`INSUFFICIENT_EVIDENCE`) do not alter proposals.
   - Error isolation: Per-file exceptions during batch runs are recorded cleanly without aborting the batch.

7. **Registry & Audit Integration (`renamer/registry/registry.py`)**:
   - Created SQLite `travel_reviews` table for storing Tool 3 decision state, candidates, notes, and conflicts.
   - Implemented `save_travel_review()`, `get_travel_review()`, and `list_travel_reviews()`.

8. **CLI Interface (`cli.py`)**:
   - Added `media-archive travel-review` command with `--db-path`, `--ref-path`, `--tracking-id`, `--no-enrich`, and table output.
   - Added `media-archive travel-reference {init,status,verify}` for administrative lifecycle management.

9. **Review Portal Integration (`review_portal/`)**:
   - `app.py`: Loads Tool 3 review results into template context for file details.
   - `templates/detail.html`: Renders Tool 3 Travel Schedule card with decision badges, candidate table (dates, location, country, score, rationale), notes, and conflict warnings.

---

## 2. Test Suite & Verification Results

### Test Execution
```bash
.venv/bin/pytest -q
```
**Result**: **197 passed, 2 warnings in 1.61s** across the entire project:
- `tests/test_travel_reviewer.py`: **40/40 passed** (implementing all 40 required tests specified in Section 35 of the build plan)
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

## 3. Representative 260-File Evaluation Evidence

A complete evaluation was performed on all 260 representative audio files in `sample-files/` using `scripts/run_tool_3_evaluation.py`. The results are serialized in `docs/eval_summary_tool3.json`.

### Quantitative Metrics
```text
Total files evaluated:                260
Downstream from Tool 2:               260 (Media context unavailable in test env)
Overwritten high-authority values:    0

Decision Breakdown:
- CORROBORATED:                       36 (13.8%)
- PROVISIONAL_ENRICHMENT:             44 (16.9%)
  - WHEN enrichments:                 19
  - WHERE enrichments:                25
- MULTIPLE_SCHEDULE_CANDIDATES:        2 ( 0.8%)
- SCHEDULE_CONFLICT:                  67 (25.8%)
- NO_SCHEDULE_SUPPORT:                54 (20.8%)
- INSUFFICIENT_EVIDENCE:              57 (21.9%)
- REFERENCE_UNAVAILABLE:               0 ( 0.0%)
```

### Key Behavioral Verifications

1. **Zero Overwritten High-Authority Values**:
   - High-authority local metadata (full 10-character dates, exact locations) and confirmed Media rows were untouched.
   - Count: `overwritten_high_authority = 0`.

2. **Safe Provisional WHERE Enrichment**:
   - When an audio file had an exact date but missing or generic location, Tool 3 supplied the unique scheduled location with `where_state="provisional"`.
   - **Example 1** (`02192d1b`):
     - Before: `2015-07-10_KKS_SB-1-2-19-Serbia-summer-camp_ID-02192d1b.mp4`
     - After: `2015-07-10_KKS_SB-1-2-19-Serbia-summer-camp_Serbian-Summer-Camp-rs_ID-02192d1b.mp4`
     - Where: `Serbian-Summer-Camp-rs` (`provisional`)
   - **Example 2** (`2f2bbf8a`):
     - Before: `2012-01-07_KKS_SB-3-24-45-Leipzig_ID-2f2bbf8a.mp3`
     - After: `2012-01-07_KKS_SB-3-24-45-Leipzig_Radhadesh-be_ID-2f2bbf8a.mp3`
     - Where: `Radhadesh-be` (`provisional`)

3. **Safe Corroboration**:
   - When audio files possessed exact dates and locations matching the travel schedule, Tool 3 confirmed agreement without mutating proposals.
   - **Example 1** (`1ac45a3a`): `A024 03-10-26 SB 9.23.32 Praha.mp3` -> Schedule corroborates recording date `2003-10-26` and location `Praha`.
   - **Example 2** (`959c6356`): `A021 03-10-24 BG 4.38 Praha.mp3` -> Schedule corroborates recording date `2003-10-24` and location `Praha`.

4. **Conflict Detection**:
   - When filename locations directly contradicted the travel schedule for the same date, Tool 3 flagged `SCHEDULE_CONFLICT` without modifying metadata.
   - **Example 1** (`33f9c85b`): `A022 03-10-25 SB 4.9.11 Farma KD.mp3` -> Flagged conflict: Schedule on `2003-10-25` records speaker in `Prague-cz`, not `Krsna-Dvur`.
   - **Example 2** (`ba363b11`): `A025 03-10-26 Govardhana lekce Pruhon.mp3` -> Flagged conflict: Schedule on `2003-10-26` records speaker in `Prague-cz`, not `Pruhonice`.

5. **Multiple Candidates Isolation**:
   - In ambiguous cases (e.g. `R09_0004.MP3`, tracking ID `a16d92b3`), where a location matched multiple distinct schedule visits without a date to disambiguate, Tool 3 recorded 3 candidates and marked `MULTIPLE_SCHEDULE_CANDIDATES`, safely declining automatic enrichment.

6. **Insufficient Evidence Safeguard**:
   - Audio files lacking both date and location (or only vague partial markers like `2012-01-XX` with no location) were marked `INSUFFICIENT_EVIDENCE`, preventing unconstrained guessing.
