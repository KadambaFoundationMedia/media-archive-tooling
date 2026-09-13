# Tool 1 — Renamer Implementation Walkthrough

Tool 1 (Renamer) has been implemented and verified in accordance with [tool-1-renamer-build-plan.md](tool-1-renamer-build-plan.md) and [project-implementation-architecture.md](project-implementation-architecture.md).

---

## 1. Summary of Changes

### Project Foundation & Infrastructure
- **Python 3.12 Runtime Baseline**: Pinned `.python-version` to 3.12, configured `pyproject.toml` to `>=3.12,<3.13`, and locked dependencies with `uv.lock`.
- **Command-Line Interface**: Created `media-archive` executable with commands `renamer`, `scan`, `review`, and `status`. Default is strictly dry-run; `--commit` is required for filesystem mutations. Review portal is restricted to loopback (`127.0.0.1`).
- **Reference Assets & Adapters**:
  - `assets/month_aliases.json`: Multilingual month name mapping across 9 languages (English, Czech, Slovak, German, Dutch, French, Spanish, Russian, Hindi) with archive-specific transliterations and typos.
  - `assets/country_codes.json`: Canonical country names to ISO 3166-1 alpha-2 lower-case mappings (`it`, `in`, `cz`, `nl`, `gb`, `us`, `se`, `no`, `rs`, etc.).
  - `assets/default_categories.json` & `assets/default_locations.json`: Seed snapshots.
  - `adapters/baserow.py`: Paginated `category_title` and `Media` loading, guarded Baserow writes with duplicate prevention, and typing fix.
  - `adapters/vedabase.py`: Scripture reference validation against Vedabase with 24-hour SQLite caching and `validation_pending_stale` network error handling.
  - `adapters/location.py`: Online location lookup provider with 30-day SQLite caching and conservative fallback.

### Core Renamer Engine & Models
- **Domain Models (`models.py`)**: Defined typed models for `ParserResult`, `WhenResult`, `WhatResult`, `WhereResult`, `FileMetadata`, `RenameProposal`, and `ResolutionState` (`exact`, `strong`, `provisional`, `ambiguous`, `unresolved`).
- **Technical & Tracking (`technical.py`)**: Suffix extraction and generation of stable 8-hex-character `_ID-xxxxxxxx` tags; case-insensitive `_edited` flag extraction; opacity preservation for source identifiers (`A019`, `A022F`, `R09_0004`).
- **WHEN Parser (`when.py`)**:
  - Enforces archive recording years (1993–2023).
  - Two-digit year expansion (`93–99` $\rightarrow$ `1993–1999`, `00–23` $\rightarrow$ `2000–2023`, `24–92` rejected).
  - Preserves explicit partial dates (`YYYY-MM-DD`, `2019-09-DD`, `2018-MM-DD`).
  - Multilingual month parsing with delimiter-aware boundaries.
  - Inferred collection grammar integration: sequence numbers across folder are prevented from becoming calendar days.
- **WHAT Resolver (`what.py`)**:
  - Recognizes scripture verses (`SB 1.4.5` $\rightarrow$ `SB-1-4-5`, `BG 3.12` $\rightarrow$ `BG-3-12`, `CC-Madhya-20-100`).
  - Preserves specific WHAT over broad categories (e.g. `JRM` $\rightarrow$ `Jaya-Radha-Madhava`).
  - Validates scripture references against Vedabase.
- **WHERE Resolver (`where.py`)**:
  - Entity resolution order: exact alias $\rightarrow$ normalized alias $\rightarrow$ fallback country match $\rightarrow$ bounded fuzzy match $\rightarrow$ online location lookup $\rightarrow$ unresolved.
  - Generates lowercase ISO alpha-2 country codes (`Vrindavan-in`, `Villa-Vrindavan-it`, `Praha-cz`).
  - Transliterates diacritics to clean ASCII Latin.
- **Collection & Sibling Grammar (`collection.py`)**: Inferred structural grammar across siblings in a directory.

### Planning, Registry & Application Service
- **Rename Planner (`planner.py`)**:
  - Unresolved WHAT preserves useful current wording + tracking ID; never fabricates `Recording` or promotes residual tokens.
  - `_edited` lifecycle: preserved in proposed processing filename until explicit `baserow_check_complete` state exists.
  - Formulates canonical `WHEN_WHO_WHAT_WHERE_ID-xxxxxxxx.ext` filenames.
- **Batch Executor (`executor.py`)**: Separates Analysis phase (dry-run proposals) from Commit phase (atomic filesystem rename, collision prevention, per-file failure isolation).
- **Local SQLite Registry (`registry.py`)**: Persists operational state, tracking IDs, parser results, rename history, and structured `review_actions` audit trail.
- **Application Service (`service.py`)**: `RenamerApplicationService` validates review corrections, regenerates proposals through the naming planner, and records audit history.
- **Review Portal (`app.py`)**: Localhost FastAPI portal interfacing exclusively through `RenamerApplicationService` on loopback.

---

## 2. Verification & Test Results

### Automated Test Suite
Ran 48 automated tests via `.venv/bin/pytest -v tests/` under Python 3.12.14:
- `tests/test_cli_safety.py` (4 tests): Bare invocation default dry-run, `--dry-run`, explicit `--commit`, loopback binding enforcement.
- `tests/test_baserow_adapter.py` (4 tests): Paginated loading of categories and media table, duplicate prevention, guarded writes.
- `tests/test_vedabase_adapter.py` (5 tests): URL builder, 24h caching, 404 handling, network failure fallback to `validation_pending_stale`, TTL refresh.
- `tests/test_location_adapter.py` (2 tests): Online geocoding lookup, 30-day SQLite caching, WhereResolver integration.
- `tests/test_service.py` (2 tests): Application service validation, proposal regeneration, audit trail.
- `tests/test_golden_cases.py` (8 tests): All build plan golden cases, `_edited` before/after Baserow check, unresolved WHAT wording retention, Prague Lekce collection, Duben 2008 sequence numbers.
- `tests/test_batch_and_safety.py` (4 tests): Dry-run mode, commit mode, idempotency, safety against overwrites.
- `tests/test_portal.py` (3 tests): Health check, dashboard rendering, detail update actions.
- `tests/test_technical.py` (3 tests): Tracking ID generation and reuse, `_edited` flag handling, source ID opacity.
- `tests/test_what.py` (4 tests): Scripture verse parsing, specific WHAT preservation, folder category conflicts.
- `tests/test_when.py` (5 tests): Year boundaries, 2-digit expansions, ISO dates, multilingual months, ambiguous numeric dates.
- `tests/test_where.py` (4 tests): Canonical place resolution, country ISO alpha-2, ASCII Latin transliteration, bounded fuzzy matching.

```text
======================== 48 passed, 2 warnings in 0.62s ========================
```

### Sample Archive Evaluation (250 Real Files)
Executed dry-run analysis on the 250 media files in `sample-files/`:
- **Files analyzed**: 250
- **High-confidence automatic resolutions (no review needed)**: 95 files
- **Correctly flagged for human review**: 155 files
- **Incorrect automatic interpretations**: 0
- **Collisions detected**: 0
- **Audit outputs generated**:
  - JSONL log: `.renamer/logs/renamer_20260913_114157.jsonl`
  - CSV summary: `.renamer/logs/renamer_20260913_114157_summary.csv`

---

## 3. Status
- Status file updated: [../status/tool-1-renamer.md](../status/tool-1-renamer.md) (marked `READY_FOR_REVIEW`).

