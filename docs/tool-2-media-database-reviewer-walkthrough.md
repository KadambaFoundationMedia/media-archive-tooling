# Tool 2 — Media Database Reviewer Implementation Walkthrough

Tool 2 (Media Database Reviewer) has been implemented and verified on branch `tool-2-implementation` (PR #19) in accordance with:
- `docs/tool-2-media-database-reviewer-build-plan.md`
- `docs/tool-2-media-database-reviewer-live-data-amendment.md`
- `docs/baserow-live-data-policy.md`
- `docs/builder-git-sandbox-policy.md`
- `docs/implementation-protocol.md`

Historical walkthrough documentation for Tool 1 (Renamer) is preserved in [docs/tool-1-renamer-walkthrough.md](tool-1-renamer-walkthrough.md).

---

## 1. Summary of Architecture & Component Implementation

Tool 2 acts as the read-only reconciliation bridge between local metadata parsed from audio filenames (Tool 1 Renamer output) and the central Baserow Media database:

1. **Snapshot Provider (`baserow_provider.py`)**:
   - Manages live read-only communication with Baserow API.
   - Strictly enforces read-only operations (`GET` only; Tool 4 is the sole authorized writer).
   - Provides targeted candidate retrieval by source ID, exact date, partial date (`YYYY-MM`), WHAT token, place token, and filename stem with complete pagination (`while next_url:`).
   - Enforces mandatory `media_table_id`; missing configuration safely returns `DATABASE_UNAVAILABLE`.
   - Normalizes Baserow fields (handles string/int dates, string/dict place objects, ID lists) and distinguishes 404 (row deleted) from network/service outages.

2. **Reconciliation Engine (`engine.py`)**:
   - Evaluates incoming file metadata against candidate Baserow rows.
   - Field comparators for WHEN (`_compare_dates`), WHAT (`_compare_what`), and WHERE (`_compare_places`).
   - Decision states: `EXISTING_MEDIA_MATCH`, `PROBABLE_EXISTING_MEDIA`, `NEW_MEDIA_CANDIDATE`, `MULTIPLE_CANDIDATES`, `CONFLICT_WITH_EXISTING`, `INSUFFICIENT_EVIDENCE`, and `DATABASE_UNAVAILABLE`.
   - Strict association rules:
     - **Rule 1**: Direct source ID match.
     - **Rule 2**: Exact 10-character date agreement + specific WHAT + exact normalized place + country compatibility.
     - **Rule 3**: Exact 10-character date agreement + specific WHAT + genuine corroboration (scripture, category title context, or travel schedule context). Unrelated populated titles rejected.
   - Deterministic title composition (`compose_what_val`): preserves scripture prefixes, strips redundant repeated scripture prefixes in database titles, and deduplicates tokens for idempotent re-runs.

3. **Application & Orchestration Service (`service.py`)**:
   - `review_file()`, `review_batch()`: orchestrate reconciliation and automatically invoke `apply_enrichment_to_renamer()` when safe (`auto_enrich=True`).
   - `apply_human_decision()`: handles operator choices (`confirm_existing`, `confirm_new`, `defer`) with live revalidation before finalizing.
   - Single-owner handoff: handles enrichment bridge handoff internally; prevents contradictory deferral of confirmed associations; clears candidate evidence on defer.

4. **Review Portal & CLI**:
   - Web UI (`review_portal/`): Jinja2 templates (`detail.html`, `index.html`) displaying field comparisons, candidate scores, and operator action forms with confirmed state protection.
   - CLI (`cli.py`): `media-archive media-db-review` supporting dry-run evaluation, `--no-enrich` flag, table previews, and confirmed match summaries.

---

## 2. Review Findings Addressed (R-001 through R-014)

- **R-001 (Session / Snapshot Reuse)**: Eliminated stale snapshot reuse across operations. Each review or human action validates against fresh live state.
- **R-002 (Predicate Strictness & Association Specificity)**: Enforced exact 10-character date agreement (`len=10`, no wildcards), specific WHAT (prohibiting generic tokens like `Lecture`, `Class`, `Bhajan`), exact normalized place, and genuine corroboration. Partial dates and fuzzy locations yield `PROBABLE_EXISTING_MEDIA` but never auto-confirm. Regressions 47–51.
- **R-003 (Country Contradictions)**: Added country contradiction detection in WHERE comparisons.
- **R-004 (Scripture Identity Grammar)**: Corrected canonical scripture verse and chapter range matching.
- **R-005 (Compatible Partial Dates)**: Partial dates ending in `DD` compatible with database dates yield `AGREES` with partial precision details rather than false conflicts.
- **R-006 (Travel Corroboration)**: Replaced broad same-month travel matching with exact/bounded schedule corroboration.
- **R-007 (Tool 3 Progressive Routing)**: Multiple plausible candidates route downstream to Tool 3 rather than blocking immediate operator review.
- **R-008 (Evaluation Population)**: Evaluated against fresh 260-file Tool 1 population.
- **R-009 (Branch & PR Hygiene)**: Managed PR #19 cleanly with linear history and GitHub Actions CI.
- **R-010 (Portal Test Dependency Injection)**: Made portal tests independent of external credentials via context configuration.
- **R-011 (HTTP 404 vs Database Unavailability)**: Explicit 404 indicates deleted row; network errors yield `DATABASE_UNAVAILABLE` and prevent false new media creation.
- **R-012 (Comprehensive Paginated Retrieval & No-Match Strictness)**: Paginated live searches past page 1; multi-route candidate queries; required specific WHAT on partial dates; re-reconciliation on `confirm_new`. Regressions 52–55.
- **R-013 (Confirmed-Enrichment Bridge)**: Wired automatic handoff to Renamer registry; implemented idempotent `compose_what_val`; isolated unconfirmed states; handled `NEW_MEDIA_CANDIDATE` lifecycle without metadata invention. Regressions 56–60.
- **R-014 (Portal Single Ownership & Safe Deferral)**:
  - Removed duplicate `apply_enrichment_to_renamer` call in `review_portal/app.py`, ensuring exactly one `enrich` audit action per confirmation.
  - Defined safe deferral transition: prohibited contradictory deferral of confirmed associations (`ValueError` and disabled portal button); reset `renamer_enrichment` and candidate selection on deferral of unconfirmed records so stale candidate metadata cannot trigger an enrichment handoff.
  - Defense-in-depth guard in `apply_enrichment_to_renamer` against deferred/insufficient states. Regressions 61–63.

---

## 3. Test Suite & Verification Results

### Test Execution
```bash
.venv/bin/pytest -v
```
**Result**: **157 passed, 2 warnings** across the repository (63 tests in `tests/test_media_db_reviewer.py`).

### Verification Checklist
- Helper shell validation: `sh -n scripts/builder-start.sh scripts/review-tool-1.sh` -> **PASS**
- Package build: `uv build` -> **PASS** (`dist/media_archive_tooling-0.1.0-py3-none-any.whl`, `dist/media_archive_tooling-0.1.0.tar.gz`)

---

## 4. Live Smoke Evaluation Evidence (260 Sample Files)

A fresh live dry-run evaluation was performed across all 260 representative audio files in `sample-files/` against the live Baserow database using a clean Tool 1 SQLite registry:

```text
total files evaluated: 260
confirmed existing matches: 1
probable existing matches: 20
multiple candidates: 99
new-media candidates: 39
insufficient evidence: 32
conflicts: 69
database failures: 0
human-review-required-now: 55
review-required-overall: 188
downstream-to-Tool-3 count: 133
confirmed title/metadata enrichments: 1
```

### Confirmed Match End-to-End Enrichment Evidence
- **Tracking ID**: `f7903be1`
- **Current Audio File**: `HH Kadamba Kanana Swami - SB 3.6.6 - Sweden - 27_8_15.mp3`
- **Before Tool 2 (Tool 1 Initial Proposed Filename)**:  
  `2015-08-27_KKS_SB-3-6-6_Sweden-se_ID-f7903be1.mp3`
- **Live Baserow Row ID 2335**:  
  `Date: 2015-08-27`, `What: SB 3.6.6`, `Place: Sweden`, `Title: SB 3.6.6 class`
- **After Tool 2 Handoff (Enriched Proposed Filename)**:  
  `2015-08-27_KKS_SB-3-6-6-class_Sweden-se_ID-f7903be1.mp3`
- **Registry Record State**:  
  `status = "enriched"`, `needs_review = False`, `baserow_check_complete = True`
- **Unconfirmed Candidate Isolation**:  
  Exactly 0 of the 220 unconfirmed files received candidate title or location metadata; all 220 remained `status = "pending"`.
- **New Media Candidate Lifecycle**:  
  All 39 `NEW_MEDIA_CANDIDATE` files received `baserow_check_complete = True` (clearing `_edited` suffix) with zero invented title or location metadata.
- **Audit Log Integrity**:  
  Confirmed portal actions record exactly 1 `media_db_confirm_existing` action and 1 `enrich` review action.

---

## 5. Git & Handoff State

- **Branch**: `tool-2-implementation` (PR #19)
- **Status Document**: [status/tool-2-media-database-reviewer.md](../status/tool-2-media-database-reviewer.md)
- **Tool 1 Historical Walkthrough**: [docs/tool-1-renamer-walkthrough.md](tool-1-renamer-walkthrough.md)
- **Tool 2 Committed Walkthrough**: [docs/tool-2-media-database-reviewer-walkthrough.md](tool-2-media-database-reviewer-walkthrough.md)
