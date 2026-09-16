# Tool 4 — Media Database Updater Implementation Status

Build plan: `docs/tool-4-media-database-updater-build-plan.md`  
Implementation issue: #24  
Project architecture: `docs/project-implementation-architecture.md`  
Project Baserow policy: `docs/baserow-live-data-policy.md`  
Implementation protocol: `docs/implementation-protocol.md`

## Current state

Status: `CHANGES_REQUESTED`

Implementation branch: `tool-4-implementation`  
Implementation PR: **missing** — the reported PR #27 does not exist; GitHub #27 is an issue
Builder handoff branch tip reviewed: `afdd358e9e89a629327b19b83fd8d434153461eb`
Implementation commit reviewed: `f9f87ab2ef4d01b1fc89389f4640ef777353f86b`
Base commit (`main` at implementation start): `8ab7d81237e1b5c21976fe78ce55f284c7e61f96`  
Last planning/review update: 2026-09-16

## Review checkpoint

The branch contains a substantial Tool 4 implementation, but it is not safe to accept or merge. Independent review reproduced database-write safety failures that the current 48 labeled tests do not cover.

Verification performed at branch tip `afdd358`:

- full local suite: **269 passed, 2 warnings**;
- helper syntax: `sh -n scripts/builder-start.sh scripts/review-tool-1.sh` PASS;
- package build: `uv build --offline` PASS after keeping the temporary UV cache outside the source tree;
- remote branch commits are reachable;
- GitHub has no Tool 4 implementation PR, so required PR CI/merge-ref evidence does not exist yet.

Passing tests do not override the findings below. Several tests inject dependencies or call internal methods in ways the production CLI/portal workflow does not.

## Independent review findings

### R-001 — A missing or failed mandatory pre-create Tool 2 recheck still creates a row

`MediaDatabaseUpdateEngine._commit_create()` treats the Tool 2 service as optional. When it is absent, creation proceeds. When `review_file(..., force_refresh=True)` raises, the exception is logged and creation still proceeds.

Independent reproduction with a Tool 2 service raising `BaserowUnavailableError("offline")` returned:

```text
SYNCED CREATE 1 ['fetch_table_fields', 'create_row']
```

This directly violates the mandatory fresh Tool 2 existence/candidate check and can create duplicates during a database/search failure. Current test 11 only starts with a stored `DATABASE_UNAVAILABLE` decision; it does not test failure of the immediate pre-create recheck. Tests 9, 13, 16, 17, 19, 21–30, 34, 38, 44, and 48 also permit create behavior without a Tool 2 service, normalizing the unsafe path.

Required correction:

- make a successful fresh Tool 2 `NEW_MEDIA_CANDIDATE` result a hard precondition for every create commit;
- block with `DATABASE_UNAVAILABLE`/retryable state when the service is absent, the live check fails, or its result is incomplete;
- never catch-and-continue to `create_row` after revalidation failure;
- add production-path regressions for absent Tool 2, Tool 2 exception/unavailability, and incomplete live result.

### R-002 — Pre-update revalidation can overwrite concurrent Notes and Tag edits

The race guard compares a fixed `RELEVANT_COLLABORATOR_FIELDS` set that omits `Notes` and `Tag`, even when the planned PATCH modifies those fields. A collaborator edit after planning is therefore overwritten by a stale payload.

Independent reproduction changed Notes from `Old human note` to `Collaborator new note` between preview and commit. Tool 4 returned `SYNCED` and replaced it with:

```text
Added from archive
Old human note
```

Required correction:

- immediately before PATCH, compare every field Tool 4 intends to modify against the reviewed/planned precondition, including Notes and Tag;
- preserve unrelated concurrent edits and route relevant concurrent edits to stale/re-review conflict;
- add Notes and multi-value Tag race regressions plus a regression proving unrelated-field changes remain safe.

### R-003 — Ambiguous and unresolved evidence is treated as automatically writable

The engine excludes only the literal state `provisional`. Complete-looking WHEN and WHERE values marked `ambiguous` or `unresolved` are written automatically. `MediaDbSyncRequest` does not carry `what_state`, so Category/WHAT trust cannot be enforced at all. Structured provenance required by the plan is also absent from the request.

Independent preview with `when_state="ambiguous"` and `where_state="ambiguous"` proposed writes to `Date`, `Country`, and `Place, location`.

Required correction:

- carry the Tool 1 resolution state and provenance for every writable semantic field;
- automatically write semantic data only from states/evidence explicitly eligible under Section 7 (normally exact/strong, confirmed Tool 2, or field-specific human approval);
- block or ignore ambiguous, unresolved, and provisional semantic evidence as appropriate;
- add regressions for every non-writable state across WHEN, WHAT/Category/Tag, and WHERE.

### R-004 — Tool 1 post-commit synchronization is not wired into real production commit paths

The new hook exists only on `RenameCommitService` when an updater service is explicitly injected. The real `media-archive renamer --commit` path uses `BatchExecutor.commit_proposals()` directly and records no Tool 4 outbox/sync. The real `media-archive review` setup calls `configure_review_context()` without an updater; review-portal commits therefore record `PENDING_SYNC` but do not attempt Tool 4. Current test 41 manually injects a mock updater and does not exercise either production path.

Required correction:

- route every successful Tool 1 production commit path through one shared post-commit/outbox integration;
- construct and inject the real Tool 4 service in the CLI/portal composition root where automatic synchronization is intended;
- keep dry-run/approval-only paths mutation-free;
- add end-to-end regressions for CLI batch commit, portal commit, unchanged committed state where applicable, failure-to-pending behavior, and no sync on dry-run/approval.

### R-005 — Tool 1 country/category values are not mapped to live Baserow semantics

`build_sync_request()` ignores `parser_result.where.country` and converts only a small hard-coded set of ISO codes. Unmapped valid countries become raw two-letter values such as `se`, `au`, `no`, or `za`, which may be created as bogus Country options. Category matching uses only case/diacritic normalization and does not implement the required semantic mapping between Tool 1 values and current Baserow options.

The committed 260-file evidence already exposes this defect:

- confirmed row 2335 has Country `Sweden`, while Tool 4 reports incoming Country `se` as a conflict;
- legitimate Tool 1 categories such as `Bhagavad Gita` and `Srimad Bhagavatam` are repeatedly reported missing against the live schema;
- the evaluation describes these as normal review cases instead of identifying a mapping defect.

Required correction:

- prefer the canonical country name already present in Tool 1 structured WHERE evidence and use a complete authoritative ISO/name mapping only as a fallback;
- map Tool 1 Category semantics to equivalent current live options without inventing taxonomy;
- compare equivalent title/category/country representations semantically so formatting alone does not create conflicts;
- add integration regressions using actual Tool 1 parser outputs (including Sweden, Australia, Norway, South Africa, BG, SB, and CC), not hand-normalized request fixtures.

### R-006 — Live schema discovery does not validate field existence or writable types

The engine builds payload entries for `Title`, `Date`, `Filename`, `media_archive_path`, `Notes`, timestamps, and other fields even when those fields are absent from `live_fields`. It does not validate most field types before sending values. A fake schema containing only `Title` still produced `SYNCED CREATE` with nine undeclared fields.

Production schema errors are generally classified as retryable transport failures rather than clear blocked/schema-review states.

Required correction:

- centralize semantic-to-live-field mapping and validate existence plus compatible writable type before mutation;
- never include a field not present in the live schema;
- classify deterministic schema/type/taxonomy mismatches as blocked/review, not transient network failure;
- add create and update regressions for missing, renamed, duplicate/ambiguous, and incompatible fields.

### R-007 — Human overwrite approval is global, not field-specific, and has no reviewed precondition

`is_human_approved=True` authorizes overwriting every contradictory semantic field in the request. The request does not identify which fields the human approved or retain the exact reviewed database values. The two live reads inside one commit call only detect changes during that call; they do not prove that the value reviewed by the human is still current. The portal exposes no Tool 4 actions for keep-database, apply a specific correction, choose an association, defer, or confirm-new with durable action provenance.

Required correction:

- model field-specific human intent and the exact reviewed precondition values/action provenance;
- revalidate those values live immediately before applying only the explicitly approved fields;
- never let one boolean authorize unrelated contradictory fields;
- implement the required service-backed portal review actions and regression coverage for stale approvals and partial approvals.

### R-008 — Portal preview does not persist/display a first preview and retry controls omit database-unavailable work

`MediaDatabaseUpdaterService.synchronize(commit=False)` saves a preview only when a prior sync row already exists. A first portal `Preview Sync` therefore redirects to a page that still shows `NOT_SYNCED` and cannot display the computed field diff. Current test 45 checks only that the static card heading is present.

`list_pending_media_db_syncs()` and the portal Retry button include only `PENDING_SYNC` and `FAILED_RETRYABLE`, excluding `DATABASE_UNAVAILABLE`, even though that is a normal retryable operational state.

Required correction:

- persist or otherwise carry the latest preview result so the portal shows the exact live current/proposed diff without claiming a committed sync;
- provide retry for all retryable states, including database-unavailable work;
- add assertions for rendered diff/status content and retry behavior, not merely HTTP 200/card presence.

### R-009 — Audit records do not satisfy the required write provenance contract

The durable request/result lacks a stable operation/request ID or committed-state fingerprint, Baserow table identity, explicit live-read timestamp for the decision, structured Tool 2 decision reference, field-resolution provenance, and durable human action provenance. The current credential test verifies only that one literal token is absent; it does not prove the required audit information is present or secrets are generically redacted.

Required correction:

- implement the Section 17 audit contract and stable request identity/fingerprint needed to explain/reconcile retries;
- record before/after for changed fields, intentionally preserved fields, live decision/precondition timestamps, Tool 2 reference, table/row identity, and human action when applicable;
- add round-trip registry tests plus generic credential/header/query-secret redaction tests.

### R-010 — Date and Notes helpers do not fully enforce the finalized rules

`_is_complete_date()` validates only the string shape; impossible values such as `2024-02-31` and `2024-99-99` return `True` and are eligible for `Date`. `merge_notes()` only checks whether Notes starts with `Added from archive`; if the marker already appears later, it prepends a duplicate instead of enforcing “at the beginning at most once.”

Required correction:

- validate a real calendar date before writing Baserow `Date`;
- normalize Tool-4-owned markers so `Added from archive` is first and appears at most once while unrelated Notes remain preserved;
- add leap-day/impossible-date and duplicate/out-of-position marker regressions.

### R-011 — The representative evaluation does not provide valid acceptance evidence yet

The current evaluation was run against the flawed mapping and safety behavior above. Its committed JSON retains only `fields_modified`, conflicts, and diagnostics for the first 20 items; it does not capture the required exact before/current/proposed field diffs for a representative subset or evidence that unrelated online/source fields remain untouched. The reported 246 “review-required conflicts” include false country/category/title conflicts exposed by the evaluation itself.

Required correction:

- rerun the 260-file write preview after functional corrections using fresh Tool 1 → live Tool 2 → Tool 3 → Tool 4 state;
- retain the required summary counts and exact representative diffs, including fields intentionally preserved;
- explain remaining conflicts as genuine archive/data decisions rather than normalization defects;
- do not perform bulk production writes.

### R-012 — The handoff claimed a non-existent PR and recorded a stale HEAD

GitHub’s pull list contains no Tool 4 implementation PR. `https://github.com/KadambaFoundationMedia/media-archive-tooling/pull/27` redirects to issue #27. The status also recorded `f9f87ab` as current HEAD even though the pushed branch tip was `afdd358`.

Required correction:

- create the required PR from `tool-4-implementation` to `main`, referencing this build plan and status file;
- keep all correction/review commits on that branch/PR;
- record the actual final pushed branch tip after the status update;
- wait for required `Python 3.12 tests` CI to pass on the corrected PR head/merge ref before returning `READY_FOR_REVIEW`.

## Verified implementation structure to preserve

The current branch has useful foundations that should be corrected rather than discarded:

- a dedicated Tool 4 package/service/write-adapter boundary;
- minimal-PATCH intent and preservation lists;
- an SQLite sync/outbox table;
- Tool 1 commit-service hook design;
- CLI and portal entry points;
- hermetic fake adapter and a broad initial regression suite;
- safe write-preview evaluation script;
- no evidence of automated bulk production mutation during review.

## Open questions / contradictions

None requiring user input. These findings are implementation corrections within the finalized plan.

## Next milestone

Builder addresses R-001 through R-012 on `tool-4-implementation`, adds meaningful production-path regressions, reruns the full suite/package/helper checks and corrected 260-file write preview, creates the real implementation PR, pushes every change, waits for required CI, updates this status with the actual final reachable HEAD/PR/evidence, and returns `READY_FOR_REVIEW` for another independent review pass.
