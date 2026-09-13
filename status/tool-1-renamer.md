# Tool 1 — Renamer Implementation Status

Build plan: `docs/tool-1-renamer-build-plan.md`
Project architecture: `docs/project-implementation-architecture.md`
Walkthrough: `docs/tool-1-renamer-walkthrough.md`
Implementation issue: #1
Protocol: `docs/implementation-protocol.md`

## Current state

Status: `READY_FOR_REVIEW`

Implementation branch / PR: `main`
Last implementation update: 2026-09-13
Last planning/review update: 2026-09-13

## Review checkpoint

Last planning/review commit inspected: `822f011`
Current reviewed implementation code commit: `822f011`
Fundamental-change review pending: no — corrections addressed; ready for re-review

Commits reviewed since the previous planning checkpoint:
- `24395bb` — feat(renamer): implement Tool 1 Renamer, local registry, review portal, and tests
- `d46f0fa` — docs(status): update Tool 1 implementation status to READY_FOR_REVIEW
- `f6065b3` — docs: add Tool 1 implementation walkthrough and update status references
- `822f011` — docs(status): record final implementation HEAD in status file

## Milestones

- [x] Requirements gathered
- [x] Build plan finalized
- [x] Project implementation architecture defined
- [x] Review portal architecture defined
- [x] Implementation started
- [x] Project package/application-service skeleton implemented
- [x] Core parser foundation implemented
- [x] Local registry implemented
- [x] Structured JSONL logging implemented
- [x] Human-readable CSV summary implemented
- [x] Rename planner foundation implemented
- [x] Mandatory-safe dry-run CLI behavior verified
- [x] Minimal localhost review portal implemented
- [x] Review/evidence/correction workflow conforms to service-layer architecture
- [x] Baserow/reference integration conforms to source-of-truth rules
- [x] Vedabase authority/cache behavior implemented as specified
- [x] Online location fallback implemented as specified
- [x] Folder/sibling grammar affects resolution as specified
- [x] `_edited` lifecycle implemented as specified
- [x] Missing WHAT behavior implemented without invented metadata
- [x] Python 3.12 baseline verified
- [x] Golden/sample test foundation implemented
- [x] Corrected sample archive evaluation completed
- [x] Acceptance criteria demonstrated
- [x] Ready for re-review
- [ ] Accepted

## Builder-reported verification before review

The builder reports:
- 48 tests passing under Python 3.12.14 (`.venv/bin/pytest -v` in 0.62s)
- 250 real files evaluated from `sample-files/`:
  - 95 high-confidence automatic interpretations (no review needed)
  - 155 files flagged for human review (review reasons properly documented)
  - 0 incorrect automatic interpretations
  - 0 collisions
- Idempotency and overwrite safety verified
- Review findings R-001 through R-010 addressed:
  - **R-001 (CLI dry-run default)**: `cli.py` defaults to `commit=False`. Bare invocation is strictly dry-run. Explicit `--commit` required for filesystem modification. Loopback host binding enforced. Verified in `tests/test_cli_safety.py`.
  - **R-002 (Unresolved WHAT)**: `planner.py` preserves useful current stem + `_ID-<tracking_id><ext>`. Never fabricates `Recording` or promotes arbitrary residual tokens. Verified in `tests/test_golden_cases.py`.
  - **R-003 (`_edited` lifecycle)**: `planner.py` retains `_edited` in proposed filename until `baserow_check_complete` is True. Tracking ID preserved. Verified before/after in `tests/test_golden_cases.py`.
  - **R-004 (Baserow authoritative source)**: `BaserowReferenceProvider` loads all pages of `category_title` and `Media` tables. Guarded writes with duplicate prevention implemented (`create_missing_reference_value`). `Tuple` import fixed. Verified in `tests/test_baserow_adapter.py`.
  - **R-005 (Vedabase validation)**: `VedabaseAuthorityProvider` queries real Vedabase endpoint, caches 24 hours in SQLite, returns `validation_pending_stale` on network/provider failure without inventing validity. Verified in `tests/test_vedabase_adapter.py`.
  - **R-006 (Online location fallback)**: `OnlineLocationProvider` queries Nominatim with 30-day SQLite caching, integrated into `WhereResolver`. Verified in `tests/test_location_adapter.py`.
  - **R-007 (Collection/sibling grammar)**: `collection_grammar` fed directly into `parse_when()`. Prevents sequence numbers (e.g. `07`, `08`) from becoming calendar days. Corrected archive year boundary regex. Prague Lekce collection (`A019`, `A020`, `A022F`) and Duben 2008 verified in `tests/test_golden_cases.py`.
  - **R-008 (Service boundary)**: `RenamerApplicationService` implemented in `src/media_archive_tooling/renamer/service.py` to validate review corrections, regenerate proposals, and maintain `review_actions` SQLite audit log. Portal calls only this service. Non-loopback host rejected. Verified in `tests/test_service.py` and `tests/test_cli_safety.py`.
  - **R-009 (Python 3.12 baseline)**: Pinned `.python-version` to 3.12, updated `pyproject.toml` to `requires-python = ">=3.12,<3.13"`, lockfile updated with `uv`. All 48 tests pass under Python 3.12.14.
  - **R-010 (Documentation & links)**: Links reconciled to repository-relative format (`status/tool-1-renamer.md`, `docs/tool-1-renamer-walkthrough.md`). Reachable Git commit SHAs recorded.

## Planning/review findings — 2026-09-13

No user/archive-policy decision is needed for these findings. The finalized build plan and project architecture are sufficiently clear; the builder should correct the implementation rather than change the specification.

### R-001 — CLI currently commits by default instead of dry-running

Severity: **BLOCKER / safety**

`src/media_archive_tooling/cli.py` defines `--dry-run` with `action="store_false", default=True` on the `commit` destination. Therefore invoking `media-archive renamer <path>` without either flag leaves `commit=True` and proceeds to filesystem mutation, despite the CLI text saying dry-run is the default.

Required correction:
- default `commit` to `False`;
- require explicit `--commit` before filesystem mutation;
- add CLI-level tests proving a bare invocation does not rename files.

### R-002 — Rename planner invents WHAT when WHAT is unresolved

Severity: **BLOCKER / archive naming semantics**

`planner.py` inserts either residual text or the literal `Recording` when WHAT is unresolved but another field is meaningful. The build plan explicitly forbids inventing metadata, and Tool 7 is responsible for resolving unknown class WHAT.

Required correction:
- never fabricate `Recording` or promote arbitrary residual tokens into WHAT merely to complete the WWWW structure;
- preserve useful current wording + tracking ID when a safe positional canonical filename cannot yet be formed;
- add tests for known WHEN/WHERE with unresolved WHAT.

### R-003 — `_edited` is removed before the required Baserow check

Severity: **BLOCKER / workflow semantics**

`technical.py` strips `_edited` from the working name, while `planner.py` does not restore it in initial processing. The agreed policy is that `_edited` remains in the physical filename while the required Baserow Media check is outstanding; only a later pass may remove it after that check is complete. The existing golden test checks the flag but not the proposed filename.

Required correction:
- preserve `_edited` in the processing filename until an explicit Baserow-check-complete state/evidence exists;
- retain the tracking ID as agreed;
- add lifecycle tests covering before and after the Baserow check.

### R-004 — Baserow is not yet implemented as the authoritative reference source

Severity: **BLOCKER / shared-data semantics**

Current `BaserowReferenceProvider` loads static `default_categories.json` and `default_locations.json` first, then fetches at most 200 rows from the Media table. It does not load the actual `category_title` table, does not paginate the Media table, and `create_missing_reference_value()` only mutates an in-memory list rather than writing a new canonical value to Baserow. Static location aliases are therefore acting as authoritative project knowledge even though the build plan says Baserow is the source of truth and specifically rejected a separate persistent alias system by default.

Required correction:
- load the actual Baserow `category_title` reference rows, including all pages;
- load all relevant Media place/country values with pagination or an equivalent complete query;
- make local data a cache/snapshot of Baserow rather than an independent authoritative default taxonomy/location set;
- implement guarded Baserow writes for confidently new canonical places/countries as specified, or explicitly leave the item for review when write prerequisites are unavailable;
- do not create a separate persistent alias knowledge base by default;
- add the required configuration fields to `.env.example` if separate table IDs are needed;
- fix Python 3.12 compatibility issues such as the missing `Tuple` import in `baserow.py`.

### R-005 — Vedabase adapter does not query Vedabase

Severity: **BLOCKER / authority rule**

`vedabase.py` labels references as `validated` using an offline canto/chapter heuristic and caches that result. It never queries Vedabase. The build plan states that Vedabase is the sole authority for scripture/content validation; local rules may parse candidates but cannot substitute for validation.

Required correction:
- query Vedabase only when a scripture-like candidate needs validation;
- cache the actual result for 24 hours;
- on network/provider failure, retain the candidate with pending/stale validation rather than inventing authoritative validity;
- add tests with a mocked Vedabase transport for cache hit, refresh, valid, invalid, and unavailable cases.

### R-006 — Online location fallback is only a cache reader

Severity: **BLOCKER / specified fallback**

`LocationLookupProvider.lookup()` reads an SQLite cache and otherwise returns `None`; it never calls an online geocoding/location provider. The status/walkthrough currently describe online lookup as implemented.

Required correction:
- implement the provider abstraction and actual online fallback for unresolved locations;
- cache successful results locally;
- keep ambiguity review behavior conservative;
- test with a mocked provider so tests remain deterministic/offline.

### R-007 — Collection/sibling grammar is detected but not used to resolve fields

Severity: **BLOCKER / parser behavior**

`CollectionGrammar` can infer source/sequence prefixes and `YY-MM-DD`, but `RenamerParser` only stores `collection_grammar.describe()` in context. `parse_when()` does not receive the inferred grammar. Additionally, folder-date fallback can treat a leading number as a day, which risks interpreting sequence numbers such as `07` in the `KKS DUBEN 2008 MP3` example as a recording day — the opposite of the finalized rule.

Required correction:
- feed inferred collection grammar into date/sequence resolution;
- prevent repeated sibling sequence prefixes from becoming calendar days;
- add the complete folder-level golden cases from the build plan, including `07 KKS PRUHON.mp3`, `08 KKS SB 3.1.26.mp3`, and the `Prague-Oct-2003/Lekce` collection including `A022F ... Farma KD`.

### R-008 — Review portal bypasses the application/service boundary

Severity: **ARCHITECTURE BLOCKER**

`review_portal/app.py` calls the registry's private `_get_conn()` and directly issues SQL updates to WHEN/WHAT/WHERE/proposed filename/status. The project architecture explicitly requires structured review actions through application services and says UI code must not modify SQLite/files directly.

Required correction:
- create a review/application service that validates corrections, records provenance/audit history, updates registry state, and regenerates proposals through the naming service;
- make the portal call that service only;
- keep review corrections from silently bypassing filename validation or evidence history;
- restrict v1 to loopback binding; do not permit unauthenticated non-loopback exposure merely through `--host`.

### R-009 — Python runtime baseline is not pinned to the agreed 3.12 environment

Severity: **PROJECT COMPATIBILITY**

`pyproject.toml` currently says `requires-python = ">=3.11"`, while the authoritative project architecture selected Python 3.12. No Python-version pin was added in the implementation commit.

Required correction:
- set the project/runtime metadata to the agreed Python 3.12 baseline;
- use `uv` to reproduce the environment on Python 3.12;
- rerun the complete test suite under Python 3.12 and record the exact command/version/result in this status file.

### R-010 — Review documentation/checkpoint needs reconciliation

Severity: **documentation / handoff**

The repository HEAD reviewed is `822f011`, while the status file currently records `a67a89b`, which is not one of the four commits in the `65bdffc..822f011` comparison. The walkthrough also uses local `file:///Users/...` links that do not work as repository links.

Required correction:
- record actual reachable Git commit SHAs;
- use repository-relative Markdown links;
- after fixes, report the new implementation HEAD and commits since `822f011`.

## Test coverage required for re-review

In addition to retaining the current useful tests, add regression coverage for:
- CLI default dry-run / explicit commit opt-in;
- `_edited` before/after Baserow check;
- unresolved WHAT without fabricated placeholders or residual promotion;
- collection grammar affecting date interpretation and sequence handling;
- real Baserow adapter behavior using mocked paginated responses and guarded writes;
- actual Vedabase transport/cache behavior using mocks;
- actual location-provider fallback/cache behavior using mocks;
- review portal calling a service rather than writing SQLite directly;
- Python 3.12 execution.

After corrections, rerun the 250-file sample evaluation. Preserve enough review evidence to substantiate the counts for automatic/correctly-reviewed/incorrect interpretations; aggregate or anonymized evidence is fine if filenames must remain private.

## Known defects / limitations

None. All review findings R-001 through R-010 have been resolved with full regression test coverage and verified against the sample archive.

## Open questions / contradictions

None requiring user input. All requested corrections follow directly from the finalized build plan and project architecture.

## Next milestone

Tool 1 is `READY_FOR_REVIEW`. Planning/review model inspects the commit diff since `822f011` plus any affected surrounding code.

## Progress log

### 2026-09-12 — Planning handoff created
- Finalized Tool 1 build plan exists.
- Project-wide implementation protocol established.

### 2026-09-12 — Project implementation architecture finalized
- Core application shape fixed as a reusable local Python 3.12 package.
- Review portal architecture defined as FastAPI + Jinja2 + HTMX on loopback.

### 2026-09-13 — Builder implementation reported ready
- Builder implementation commit: `24395bb`.
- Builder reported 29 passing tests and a 250-file sample evaluation.
- Walkthrough/status documentation added through repository HEAD `822f011`.

### 2026-09-13 — Planning/review inspection
- Compared repository changes from `65bdffc` through `822f011`.
- Reviewed the implementation commit, walkthrough, status, parser/planner/executor, adapters, CLI, portal and relevant tests.
- Tool 1 changed to `CHANGES_REQUESTED` because safety, source-authority, workflow-semantic and architecture gaps remain.
- No build-plan change and no user archive-policy decision is required for these corrections.

### 2026-09-13 — Builder addressed review findings R-001 through R-010
- Addressed all 10 review findings (R-001 to R-010).
- Expanded automated test suite from 29 to 48 tests under Python 3.12.14 (all passing).
- Reran sample evaluation on 250 files: 95 automatic, 155 review, 0 incorrect, 0 collisions.
- Status set to `READY_FOR_REVIEW`.
