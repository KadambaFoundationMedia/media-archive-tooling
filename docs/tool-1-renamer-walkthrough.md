# Tool 1 — Renamer Implementation Walkthrough

Tool 1 (Renamer) has been implemented, verified, and updated in accordance with [tool-1-renamer-build-plan.md](tool-1-renamer-build-plan.md), [project-implementation-architecture.md](project-implementation-architecture.md), and the re-review findings R-011 through R-021.

---

## 1. Summary of Changes & Review Findings Addressed

### R-011: Baserow Reference Loading & Guarded Write Safety
- **Non-Reentrant Loading**: Made reference loading strictly non-reentrant using `_is_loading` guard and internal lookup helpers (`_internal_find_place`, `_internal_find_country`) without recursive `load_all_references()` loops.
- **Live-Over-Cache Authority**: Live Baserow API data takes immediate precedence over static seed snapshots.
- **Orphan Prevention**: Strictly refuses to write reference-only rows to the Media table when no dedicated location table is configured.
- **Near-Duplicate Check**: Validates `place + country` combination with rapidfuzz (ratio >= 85) to prevent near-duplicate records.

### R-012: Specific WHAT Preservation & Externalized Titles
- **Externalized Title Assets**: Added `assets/specific_titles.json` containing canonical titles, categories, and alias mappings.
- **Scripture Descriptors**: Preserves descriptive suffixes attached to scripture references (e.g., `BG-8-19-Sundayfeast` remains intact instead of truncating to `BG-8-19`).
- **Multiple Candidates**: Retains all matched category and title candidates in `WhatResult.candidates`.

### R-013: Unsplit Combination Candidates
- **Preserved Source Stem**: When `possible_combination` is detected (e.g. `JRM and class 24/5/11 villa vrindavan.mp3`), the planner retains the useful source stem + tracking ID rather than falsely emitting a single-recording canonical name before splitting by Tools 5/6.

### R-014: Direct Location Evidence Precedence
- Added `Krsna-Dvur` (`cz`) with aliases (`["farma kd", "farma-kd", ...]` to `assets/default_locations.json`.
- Direct filename evidence in `WhereResolver` now evaluates first and takes precedence over ancestor folder context (`Praha`).

### R-015: Fuzzy & Online WHERE Calibration
- **Near-Tie Ambiguity**: When multiple candidates score within `near_tie_margin` (5.0), `WhereResolver` sets `ResolutionState.AMBIGUOUS` with alternative candidates.
- **Candidate Filtering**: Bounded online lookup filters out non-location stop words, short tokens (<4 chars), numbers, and technical noise, capped at 2 queries per file.
- **Rate Limiting**: `LocationLookupProvider` enforces a minimum 1.0s interval between outgoing queries.

### R-016: Date Disambiguation & Folder Conflict Detection
- Derived preliminary location context before date resolution to determine regional format (`us_context = True` for `us`, defaulting to non-US `D-M-Y`).
- In `RenamerParser`, added filename-vs-folder date conflict detection (e.g. December 29/30/31 2011 vs folder year 2012).

### R-017: Safety, Transliteration & File Handling
- **Transliteration**: Deterministic Cyrillic (`Лекция` $\rightarrow$ `Lektsiya`) and Devanagari (`कीर्तन` $\rightarrow$ `keertn`) transliteration in `to_ascii_latin()`.
- **Collision Retry**: Tracking ID generation checks local registry and retries on ID collision.
- **File Filtering**: Extension-agnostic handling (supports audio, video, `.txt`, `.srt`, `.pdf`) while ignoring system/temp artifacts (`.DS_Store`, `Thumbs.db`, `~$*`, `.*`).
- **128-Character Limit**: Enforces hard 128-char filename limit using safe abbreviations and review flagging.

### R-018: Structured Enrichment Evidence Input Path
- Added `EnrichmentEvidence` domain model in `models.py`.
- Implemented `RenamerApplicationService.apply_enrichment()` allowing Tools 2–4, 7, and Baserow to update file resolutions, clear resolved review reasons, and record audit history.

### R-019: Finalization & Collision Resolution
- Non-colliding finalization strips `_ID-xxxxxxxx` while preserving `_edited` if Baserow check is incomplete.
- In `resolve_batch_collisions()`, detects pre-existing files on disk at target paths and assigns `-02`, `-03` counters.

### R-020: Shared Proposal & Review Validator
- Implemented `validator.py` (`validate_calendar_date`, `validate_iso2_country`, `validate_canonical_filename`).
- Wired into `service.py` to strictly reject invalid dates (e.g. `2011-02-30`), unrecognized ISO2 codes, and non-canonical filename syntax.

### R-021: Objective Sample Evaluation
- Sample evaluation in `logger.py` uses objective confidence/behavior categories (`automatic_candidate`, `provisional_candidate`, `review_candidate`, `unresolved_candidate`).
- Created reproducible evaluation report in [docs/sample-evaluation-report.md](sample-evaluation-report.md).

---

## 2. Verification & Test Results

### Automated Regression Suite (63 Tests)
Executed full test suite under Python 3.12.14 via `.venv/bin/pytest -v`:

```text
tests/test_baserow_adapter.py (6 tests)
tests/test_batch_and_safety.py (4 tests)
tests/test_cli_safety.py (4 tests)
tests/test_enrichment_and_finalization.py (2 tests)
tests/test_golden_cases.py (8 tests)
tests/test_location_adapter.py (5 tests)
tests/test_portal.py (3 tests)
tests/test_service.py (2 tests)
tests/test_technical.py (3 tests)
tests/test_validator_and_safety.py (8 tests)
tests/test_vedabase_adapter.py (5 tests)
tests/test_what.py (4 tests)
tests/test_when.py (5 tests)
tests/test_where.py (4 tests)

======================== 63 passed, 2 warnings in 1.03s ========================
```

### Sample Archive Evaluation (`sample-files/`)
Executed dry-run analysis on the 260 media files in `sample-files/`:
- **Files Analyzed**: 260
- **Clean Automatic Proposals (No review flag)**: 88 (33.8%)
- **Flagged for Human Review**: 172 (66.2%)
- **Combination Candidates Held for Splitting**: 2 (0.8%)
- **Collisions Detected**: 0
- **Objective Categories**:
  - `review_candidate`: 131
  - `automatic_candidate`: 61
  - `provisional_candidate`: 56
  - `unresolved_candidate`: 12
- Full details documented in [docs/sample-evaluation-report.md](sample-evaluation-report.md).

---

## 3. Status
Status file updated: [status/tool-1-renamer.md](../status/tool-1-renamer.md) (marked `READY_FOR_REVIEW`).
