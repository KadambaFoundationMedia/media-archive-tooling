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
- Sample evaluation in `logger.py` uses objective confidence/behavior categories.
- Created reproducible evaluation report in [docs/sample-evaluation-report.md](sample-evaluation-report.md).

### R-022: Separation of Downstream Routing & Diagnostic Notes from Immediate Human Review
- **Separation of Concerns**: Incompleteness or downstream metadata ownership (missing WHERE, unidentified class WHAT, combination splitting) is separated from `needs_review=True`.
- **Domain Model Extension**: Added `diagnostic_notes: List[str]` and `downstream_routing: List[str]` to `ParserResult` and `RenameProposal`.
- **Pipeline Routing**:
  - Missing or provisional WHERE routes to `tool_2_3_media_enrichment` with `needs_review=False`.
  - Missing WHAT routes to `tool_7_class_classification` with `needs_review=False`, retaining descriptive wording + tracking ID.
  - Combination candidates route to `tool_5_6_split_combination` with `needs_review=False`, retaining source stem + tracking ID.
- **Genuine Human Review Queue**: Reserved strictly for actual contradictions (e.g. filename date in 2011 conflicting with container folder year 2012), unresolvable ties, and corruptions.
- **Objective Pipeline Categories**: `safe_automatic` (61), `downstream_enrichment` (192), `downstream_split` (2), `human_review_required` (5), `blocked_error` (0).
- **Review Portal**: Updated `detail.html` to clearly distinguish diagnostic notes and downstream routing from human review reasons.
- **Regression Suite**: Added `tests/test_routing_and_review_separation.py` verifying that safe partial improvements proceed without human review while genuine conflicts block.

---

## 2. Verification & Test Results

### Automated Regression Suite (66 Tests)
Executed full test suite under Python 3.12.14 via `.venv/bin/pytest -v`:

```text
tests/test_baserow_adapter.py (6 tests)
tests/test_batch_and_safety.py (4 tests)
tests/test_cli_safety.py (4 tests)
tests/test_enrichment_and_finalization.py (2 tests)
tests/test_golden_cases.py (8 tests)
tests/test_location_adapter.py (5 tests)
tests/test_portal.py (3 tests)
tests/test_routing_and_review_separation.py (3 tests)
tests/test_service.py (2 tests)
tests/test_technical.py (3 tests)
tests/test_validator_and_safety.py (8 tests)
tests/test_vedabase_adapter.py (5 tests)
tests/test_what.py (4 tests)
tests/test_when.py (5 tests)
tests/test_where.py (4 tests)

======================== 66 passed, 2 warnings in 1.03s ========================
```

### Sample Archive Evaluation (`sample-files/`)
Executed dry-run analysis on the 260 media files in `sample-files/`:
- **Files Analyzed**: 260
- **Safe Automatic Proposals (No human review)**: 255 (98.1%)
- **Flagged for Immediate Human Review**: 5 (1.9%)
- **Combination Candidates Routed to Split**: 2 (0.8%)
- **Collisions Detected**: 0 (0.0%)
- **Objective Behavior Categories**:
  - `downstream_enrichment`: 192 (73.8%)
  - `safe_automatic`: 61 (23.5%)
  - `human_review_required`: 5 (1.9%)
  - `downstream_split`: 2 (0.8%)
  - `blocked_error`: 0 (0.0%)
- Full details documented in [docs/sample-evaluation-report.md](sample-evaluation-report.md).

---

## 3. Status
Status file updated: [status/tool-1-renamer.md](../status/tool-1-renamer.md) (marked `READY_FOR_REVIEW`).
