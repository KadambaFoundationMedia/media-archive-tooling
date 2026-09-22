# Alpha/Beta Test-Data Purge — Build Plan

Status: **FINALIZED — implementation-ready scoped amendment**

This amendment is authoritative for the alpha/beta cleanup behavior requested
on 2026-09-22. It supplements the accepted Tool 4 and Main Tooling Script
plans; it does not alter their tool boundaries.

## 1. Purpose

During alpha/beta testing, a change to the code used by Tools 1–4 or the
Review Portal must start the local review experience from a fresh slate. The
operator must also be able to run one explicit cleanup command that removes
the local review state **and** the Baserow rows created by Tool 4 for testing.

The scope is test data only. Existing Baserow media, rows matched and updated
by Tool 4, manually created rows, schema, and select options must never be
deleted by this feature.

## 2. Ownership and implementation order

All Baserow deletion remains exclusively inside **Tool 4**. The Main Tooling
Script and Review Portal may request cleanup through Tool 4's typed service;
they may not call Baserow delete endpoints themselves.

Implement and merge in this order:

1. `BUILD TOOL 4` — provenance, safe test-row cleanup service, adapter, CLI
   service boundary, tests, and Tool 4 status/PR.
2. After that PR is merged, `BUILD MAIN SCRIPT` — runner `--purge`, automatic
   code-fingerprint cleanup, portal integration, reporting, tests, and Main
   Tooling Script status/PR.

Each implementation keeps its usual protected branch and PR. The Builder must
use the GitHub wrappers required by `BUILDER.md`.

## 3. What counts as a deletable test row

For the current alpha/beta phase, every successful Tool 4 **CREATE** made by a
live Main Tooling Script run is a test-created row. Tool 4 must record it in a
durable local test-row ledger immediately after verified live creation.

Each ledger record must include at least:

- Baserow table ID and row ID;
- Tool 4 tracking ID and run ID;
- creation timestamp and the Tool 4 request fingerprint;
- test session/fingerprint identifier;
- remote test marker expected in Notes;
- lifecycle state: `CREATED`, `PURGING`, `PURGED`, or `PURGE_BLOCKED`;
- error/readback details redacted through the existing secret-redaction path.

Tool 4 must add an unambiguous, idempotent machine-readable test marker to the
beginning of the created row's Notes, alongside the existing archive/original
filename/original path provenance. It must not alter Notes for an existing
matched row. The marker must identify the test session but must not include
credentials or local absolute paths beyond the already required archive
provenance.

Only a ledger entry with a verified matching remote marker authorizes deletion.
The following are never deletable through purge:

- Tool 4 `UPDATE`, `NOOP`, preview, pending, failed, or blocked results;
- a row without a ledger record;
- a ledger row whose table ID, row ID, or marker does not match live Baserow;
- a row whose marker was removed or materially changed by a human;
- any Baserow select option, field, schema, attachment, or unrelated row.

This alpha/beta classification must be explicit in the code and documented as
temporary project policy. A later production-release plan must deliberately
disable it; it must not silently continue after the project leaves testing.

## 4. Tool 4 cleanup service

Add a typed Tool 4 cleanup operation, e.g. `purge_test_rows`, and the minimal
write-adapter method needed to delete one verified Media row. It must not be a
generic public row-deletion API.

For every ledger entry, Tool 4 must:

1. fetch the current live row;
2. compare table ID, row ID, and the expected test marker;
3. delete only when all checks match;
4. read back or interpret a Baserow `404` as successfully absent;
5. persist a per-row outcome before moving to the next row.

On a network failure, ambiguous response, changed marker, table mismatch, or
any validation failure, leave the ledger record intact as `PURGE_BLOCKED` and
do not delete local evidence. The command must report each retained row ID and
reason without leaking a token.

The cleanup is idempotent: a successfully deleted row, or a row already
confirmed absent, is `PURGED` and is not retried as a failure.

## 5. Main command-line interface

Extend the simple launcher and its existing Python command without requiring a
file target for cleanup:

```sh
./run-media-archive.sh --purge
./run-media-archive.sh --purge --dry-run
```

`--purge` means a full alpha/beta cleanup request:

1. invoke Tool 4 to purge only the verified ledger rows;
2. if and only if every known test row is `PURGED`, clear all local registry
   review/run/proposal/sync state and reset the portal queue to zero;
3. retain the registry schema and append one redacted purge event to the single
   project log file;
4. print a concise total: Baserow rows deleted, already absent, blocked/failed,
   and local registry result.

`--purge --dry-run` must list the exact recorded Baserow row IDs that would be
deleted and the local state that would be cleared, but must make no filesystem,
registry, Baserow, schema, or select-option mutation.

No confirmation prompt is required. The command must exit non-zero if a live
cleanup cannot complete; then it must preserve the registry/ledger so the
operator can rerun `--purge`. It must never rename or restore media files.

Normal file targets are invalid with `--purge`; cleanup is a standalone
operation. `--review-only` remains a standalone way to open the portal.

## 6. Automatic fresh slate after relevant code changes

Persist a deterministic `review_data_fingerprint` in the local registry. It
must hash the sorted relative paths and contents of the runtime code that can
change review results:

- Tool 1/Renamer;
- Tool 2 Media Database Reviewer;
- Tool 3 Travel Schedule Reviewer;
- Tool 4 Media Database Updater;
- Main Tooling Script orchestration;
- Review Portal code/templates.

Do not include logs, registry data, tests, build output, `.env`, or unrelated
documentation. The calculation must work from the local working tree, so an
uncommitted behavior change is also detected.

At the start of a normal Main Tooling Script run and at Review Portal startup:

1. if no prior fingerprint exists, store the current fingerprint and continue;
2. if it matches, continue normally;
3. if it differs, run the same full cleanup path as `--purge` before exposing
   prior portal results or accepting new work;
4. only store the new fingerprint after remote test-row cleanup and local reset
   both succeed;
5. if cleanup cannot complete, do not display the old review queue as current;
   show `PURGE_BLOCKED` with the concise reason and require a successful
   `--purge` before processing or reviewing further files.

Use a durable registry lock/transaction so a portal process and runner cannot
purge or process the same registry concurrently. A running portal must refresh
to the empty queue after successful cleanup; it must not keep stale in-memory
counts.

## 7. Portal and terminal behavior

The portal starts independently as it does now. It takes no media-file
argument. After a successful manual or automatic purge it must show:

```text
0 total tracked files
0 requires human review
Fresh test slate
```

On a blocked cleanup, it must show a visible concise warning with the number of
rows retained and the command to retry. It must not show stale review entries
as usable current results.

Terminal output for a successful cleanup should be similarly concise, for
example:

```text
Alpha/beta purge complete
Baserow test rows: 3 deleted, 1 already absent, 0 blocked
Local review registry: cleared
```

## 8. Required tests and verification

Tool 4 must add hermetic tests for:

- successful CREATE ledger/Notes marker recording;
- update/noop/preview never appearing in the deletion ledger;
- correct marker + row ID deletion;
- missing/changing marker, wrong table, wrong row, and network failure blocking
  deletion while retaining ledger evidence;
- `404` idempotency and secret redaction;
- no generic delete API available to Tools 1–3 or the portal.

The Main Tooling Script/portal must add hermetic tests for:

- standalone `--purge` and `--purge --dry-run`;
- local state clears only after all remote cleanup outcomes succeed;
- no target is accepted with `--purge`;
- first fingerprint initialization, equal fingerprint no-op, changed fingerprint
  automatic cleanup, and blocked cleanup hiding stale portal data;
- independent portal startup with a fresh empty registry;
- concurrency/lock behavior and concise terminal/portal reporting.

Before handoff, run focused Tool 4, Main Tooling Script, and portal tests; the
full project suite; shell syntax validation; package build; and GitHub CI.
Perform one controlled live alpha/beta smoke test: create one marker-bearing
test row through Tool 4, run `--purge`, verify the row is absent in Baserow and
the portal shows zero items. Record row IDs only in the local redacted audit,
not in the committed documentation.

## 9. Acceptance criteria

This amendment is complete only when all of the following are true:

- a code change affecting review behavior cannot leave old portal results
  presented as current;
- `./run-media-archive.sh --purge` removes every safely provable test-created
  Baserow row and then clears the local review registry;
- no pre-existing or updated Baserow row can be deleted by the feature;
- a partial/uncertain cleanup fails closed and preserves recovery evidence;
- dry-run is completely non-mutating;
- portal starts independently without a file target and reliably reflects the
  fresh or blocked cleanup state;
- Tool 4 remains the sole Baserow reader/writer/deleter; and
- tests, full regression, package build, and required CI pass.
