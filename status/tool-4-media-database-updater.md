# Tool 4 — Media Database Updater Implementation Status

Build plan: `docs/tool-4-media-database-updater-build-plan.md`  
Implementation issue: #24  
Implementation PR: #27 — https://github.com/KadambaFoundationMedia/media-archive-tooling/pull/27
Project architecture: `docs/project-implementation-architecture.md`  
Project Baserow policy: `docs/baserow-live-data-policy.md`  
Implementation protocol: `docs/implementation-protocol.md`

## Current state

Status: `CHANGES_REQUESTED`

Implementation branch: `tool-4-implementation`  
Builder correction commit reviewed: `70c0c35467e41e3a63ec505ea98b7dc3f8fcb972`
Builder handoff tip reviewed: `9196826ada3c7d32900c79f009db7e6a424f4aec`
Base commit (`main`): `8ab7d81237e1b5c21976fe78ce55f284c7e61f96`
Previous independent review commit: `84cc147d34190c6cb34407b1d42898cfd1d283ee`
Last planning/review update: 2026-09-17

## Second independent review checkpoint

The correction branch materially improves Tool 4 and resolves several earlier defects, including the Notes/Tag race check, real calendar-date validation, Notes marker normalization, preview persistence, pending-state registry query, category normalization, and creation blocking when Tool 2 is absent or raises.

It is not yet safe to accept or merge. The second review reproduced remaining write-safety and production-integration failures that the 55 Tool 4 tests do not cover.

Verification at builder handoff tip `9196826`:

- full local suite: **276 passed, 2 warnings**;
- Tool 4 suite: **55 passed, 2 warnings**;
- helper syntax: `sh -n scripts/builder-start.sh scripts/review-tool-1.sh` PASS;
- package build: `uv build --offline` PASS;
- PR #27 exists, targets `main`, and is open/mergeable;
- GitHub Actions check `Python 3.12 tests` passed on exact head `9196826`;
- `git diff --check 84cc147..9196826` reports minor trailing-whitespace/EOF issues, which should be cleaned during correction.

Passing tests do not override the findings below. Several new tests verify helpers or dependency-injected paths but still do not exercise the real production composition and adversarial boundary cases.

## Required corrections from second review

### R-013 — Pre-create revalidation accepts incomplete/non-live Tool 2 results

`_commit_create()` now blocks a missing or throwing Tool 2 service, but it authorizes create from the `decision` value alone. It does not require the fresh result to establish `live_read_complete`, `snapshot_complete`/complete candidate retrieval, `baserow_check_complete`, or an acceptable live database state.

Independent reproduction supplied a fresh result with:

```text
decision=NEW_MEDIA_CANDIDATE
live_read_complete=False
snapshot_complete=False
baserow_check_complete=False
database_state=LIVE_PARTIAL_OR_FAILED
```

Tool 4 returned `SYNCED` and called `create_row`.

Required correction:

- validate the full fresh Tool 2 contract, not only the decision label;
- create only when the immediate result proves a complete live-current new-candidate check;
- classify incomplete/partial/unavailable results as blocked database state and never call `create_row`;
- add regressions for partial, incomplete, stale/injected, and internally inconsistent Tool 2 results.

### R-014 — Semantic state eligibility is still a denylist and provenance is dropped

The engine excludes only the literal states `provisional`, `ambiguous`, and `unresolved`. Missing, empty, or unknown states therefore remain writable even though Section 7 permits automatic semantic writes only from positively eligible exact/strong evidence.

Independent previews with each of `None`, `""`, and `"unexpected"` proposed all of `Date`, `Category`, `Tag`, `Country`, and `Place, location` as `SET`.

In addition, `build_sync_request()` reads `when_data.get("provenance")`, `what_data.get("provenance")`, and `where_data.get("provenance")`, but the Tool 1 models serialize those lists under `evidence`. All three request provenance fields are therefore `None` for real Tool 1 records.

Required correction:

- use a positive eligibility check: automatic semantic writes require exact/strong (or another explicitly defined trusted state), not merely absence from a denylist;
- fail closed for missing and unknown states;
- carry Tool 1 `evidence` into the Tool 4 structured provenance fields;
- add parameterized regressions covering exact, strong, provisional, ambiguous, unresolved, missing, and unknown states for WHEN, WHAT/Category/Tag, and WHERE.

### R-015 — Real `renamer --commit` creates Tool 4 without Tool 2

`run_renamer()` constructs `MediaDatabaseUpdaterService(registry, write_adapter)` without a `MediaDatabaseReviewService`. Every automatic create attempted after a real CLI rename therefore reaches the hard pre-create guard with no Tool 2 service and becomes `DATABASE_UNAVAILABLE` instead of performing the required fresh check and synchronization.

The dedicated `media-db-update` command and portal do construct Tool 2, so the composition is inconsistent. Existing tests inject a mock updater or test the dedicated Tool 4 command; none exercises the real `renamer --commit` composition.

Required correction:

- construct one correctly configured Tool 2 + Tool 4 service composition for every Tool 1 production commit path;
- reuse that composition rather than duplicating subtly different setup;
- add an end-to-end `run_renamer --commit` regression that reaches a fresh Tool 2 check and create/update path, plus failure-to-durable-retry coverage.

### R-016 — Global approval still authorizes unrelated fields; field approvals are incomplete

The status and PR say the global boolean was replaced, but `is_human_approved` remains in the model and broadly bypasses state/conflict safeguards throughout the engine. One request with `is_human_approved=True` independently overwrote contradictory `Date`, `Category`, `Country`, and `Place, location` values without field-specific approvals or reviewed preconditions.

The new field approval implementation also treats a missing `reviewed_precondition_value` as authorization. An approval containing only:

```json
{"Title": {"approved_value": "Approved replacement"}}
```

overwrote a populated collaborator Title and returned `SYNCED`. The service and portal do not provide the required durable field-specific actions/provenance for keep/apply/defer/confirm-new.

Required correction:

- remove the global overwrite authority (a legacy flag may be accepted only if it cannot authorize semantic writes);
- require a schema-validated field-specific approval, explicit approved value, exact reviewed precondition value, reviewer/action/timestamp provenance, and immediate live revalidation;
- distinguish an explicitly reviewed blank precondition from a missing precondition;
- apply only the approved field and never unlock other provisional/contradictory fields;
- expose the Section 18 field-specific review actions through the service-backed portal flow;
- add partial-approval, missing-precondition, blank-precondition, stale-precondition, and unrelated-field regressions.

### R-017 — Live schema mismatches can still be skipped or accepted as `SYNCED`

The live-schema code does not enforce the claimed contract:

- if a planned field disappears between planning and commit, payload construction logs and silently skips it, then writes the rest;
- `validate_field_schema()` has no rejection path for unsupported/incompatible live types such as `number` for `Title`;
- normalized duplicate/ambiguous field names are collapsed by a dictionary comprehension rather than blocked;
- Country/location schema options may be mutated before the complete payload has passed validation.

Independent reproductions:

```text
Date removed from live schema after planning -> SYNCED CREATE, row created without Date
Title live type changed to number          -> SYNCED CREATE, string "Talk" submitted
```

Required correction:

- resolve and validate every intended field against one fresh schema snapshot before any row or schema mutation;
- block the whole operation if an intended field is missing, renamed/ambiguous, duplicated after normalization, read-only, or has an incompatible/unsupported type;
- validate the complete payload before adding Country/location options;
- classify deterministic schema errors as `FAILED_BLOCKED`/review, never success or retryable transport failure;
- add adversarial create and update regressions for schema changes between plan/commit, missing fields, duplicate names, unsupported types, and no partial schema-option mutation.

### R-018 — Portal retry parity remains incomplete

The registry query correctly includes `DATABASE_UNAVAILABLE`, but the portal renders the Retry button only for `PENDING_SYNC` and `FAILED_RETRYABLE`. A database-unavailable item therefore remains non-retryable from the portal despite the status claim.

Required correction:

- include `DATABASE_UNAVAILABLE` in the portal retry control;
- render `PREVIEW` as a distinct non-error state;
- add portal assertions for the actual status, diff, and retry control—not only HTTP 200/card-heading presence.

### R-019 — Audit persistence still leaks secrets and lacks required provenance

`redact_secrets()` is applied only to selected result strings. `request_json` and `result_json` are persisted raw. Independent preview persisted both `Bearer TOPSECRETTOKEN` from `reviewer_notes` and `api_key=VERYSECRET` from a Notes diff into SQLite.

The audit provenance object contains only timestamp, tracking ID, rules version, three states, the global flag, and an approval count. It does not record the Tool 2 decision/reference, live-read timestamp, exact human action/provenance, or reviewed preconditions. The request fingerprint omits `current_path`, so a committed archive move can produce the same fingerprint even though `media_archive_path` must change.

Required correction:

- redact secrets recursively before durable audit/log persistence while keeping operational values only where required for the write;
- include the complete Section 17 provenance contract and field-specific human action/preconditions;
- fingerprint the full relevant committed state, including the current archive path and association context;
- add registry round-trip tests that inject secrets into request fields, approvals, Notes/diffs, exceptions, headers, and query-style strings.

### R-020 — Country mapping is not complete ISO-3166-1 data

`country_mapper.py` describes `ISO_TO_COUNTRY` as complete, but the runtime map contains only 55 codes (the bundled asset is similarly limited). Valid examples such as Ghana (`GH`) and Iceland (`IS`) return `None`; `build_sync_request()` can consequently fall back to the raw two-letter code and propose that as a Country option.

Required correction:

- use a complete, versioned authoritative ISO-3166-1 alpha-2 mapping;
- never treat an unknown two-letter code as a country display name or create it as an option;
- add coverage beyond the current project subset, including unknown/invalid codes and aliases.

### R-021 — Acceptance evidence must be regenerated after correction

The 260-file evaluation was run against the unresolved behavior above. Its representative entries contain `field_diffs` but omit the result-level `fields_preserved` evidence explicitly requested by the previous review, and the JSON does not identify the evaluated commit, run timestamp, schema/snapshot read timestamp, or configuration/reference provenance needed to establish which live-current run produced it.

Required correction:

- rerun the safe 260-file preview only after R-013 through R-020 are fixed;
- include the evaluated commit, run timestamp, relevant live/reference timestamps/identities, exact representative diffs, and intentionally preserved fields;
- retain the required summary counts and explain remaining conflicts;
- do not bulk-write production Baserow.

### R-022 — Final handoff metadata and tests must match the corrected head

The Builder's `READY_FOR_REVIEW` handoff named `68ece17` as current implementation HEAD even though the reviewed PR head was `9196826`. The next handoff must record the actual final pushed branch/PR head and CI for that exact head. Also clean the `git diff --check` findings.

Required correction:

- add meaningful regressions for every finding above;
- run the full suite, focused Tool 4 suite, helper syntax, offline package build, and corrected evaluation;
- push implementation first, then update status in the required second commit;
- wait for PR #27 CI on the final head and record the exact reachable head/check evidence;
- return `READY_FOR_REVIEW` only after all required corrections are complete.

## Next milestone

Antigravity Builder addresses R-013 through R-022 on `tool-4-implementation`, pushes all changes to PR #27, waits for CI on the exact final head, updates this status to `READY_FOR_REVIEW`, and hands the branch back for a third independent review. The planner/orchestrator will not merge PR #27 until that review passes.
