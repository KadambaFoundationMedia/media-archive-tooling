# Tool 2 — Live Baserow Data Amendment

Status: **FINALIZED — authoritative amendment to `docs/tool-2-media-database-reviewer-build-plan.md`**

Tracking issue: #2

Project-wide authority policy: `docs/baserow-live-data-policy.md`

This amendment was added after clarification that the shared Baserow Media database is continuously changed by external collaborators. It supersedes any Tool 2 build-plan wording that permits stale/cached Baserow rows to stand in for current live data.

All Tool 2 requirements not changed here remain exactly as defined in the original finalized build plan.

## 1. Sections superseded

This amendment replaces or narrows the original plan's cache/snapshot semantics in sections 6, 23, 26–35 wherever they conflict with this document.

In particular, the original concepts of:

```text
CACHED_STALE
cached stale data as supporting database context
session-wide snapshot reuse
optional snapshot reuse from the CLI
Baserow reads are batched/cached across a long batch
acceptance evaluation from a captured snapshot instead of live Baserow
```

must **not** be implemented as current-state semantics.

## 2. Live-read rule

Every independent Tool 2 database-dependent decision must query current Baserow data as part of that operation.

Tool 2 must not load a persisted Media/category/schedule cache and treat it as current database state.

A live response may be reused while computing the same decision. It must not become an authoritative cache for later independent decisions merely because it was recently fetched.

## 3. Local persistence is audit/history only

Tool 2 still persists enough evidence to explain and reproduce what it saw at the time of a decision, including:

```text
baserow_read_at
Baserow table/row IDs
raw live values used
normalized values used
schema/field mapping representation
candidate comparisons
human decisions
conflicts and routing
```

This persisted evidence is historical/audit state. A subsequent Tool 2 operation must re-read Baserow rather than use the historical values as its current input.

## 4. Database availability states

Use live-current semantics conceptually equivalent to:

```text
LIVE_CURRENT
LIVE_PARTIAL_OR_FAILED
DATABASE_UNAVAILABLE
```

Exact names remain builder discretion.

There is no `CACHED_STALE` state that can produce a usable current Media decision.

If the live query required for a decision fails:

- do not return current confirmed database enrichment;
- do not return a current confirmed existing-row association from cache;
- do not return `NEW_MEDIA_CANDIDATE` from cache;
- set `baserow_check_complete=false`;
- return/route a database unavailable or incomplete provider state.

## 5. Candidate retrieval

Candidate retrieval should use efficient live Baserow queries rather than a long-lived full-table snapshot.

Prefer, where supported:

1. direct row/source-ID/URL/file-identity lookup;
2. server-side filtered queries by date/WHAT/location/category;
3. complete pagination of the narrowed live candidate result;
4. bounded fuzzy comparison only after live narrowing.

Tool 2 may issue a broader complete live query when necessary for correctness, but a full table downloaded at the start of an application session must not be assumed current for later decisions.

## 6. Human review revalidation

The review portal may display a locally stored Tool 2 result so the user can inspect it.

However, when the user chooses:

```text
Confirm existing row
Choose another candidate row
Confirm new media item
```

Tool 2 must perform a fresh live revalidation before finalizing that decision.

If relevant Baserow data changed since the displayed result was produced:

- refresh/recompute the affected comparison;
- do not silently confirm against the old row values;
- show/return the changed state and require the decision to be made against current data when the change is material.

## 7. `NEW_MEDIA_CANDIDATE`

`NEW_MEDIA_CANDIDATE` is valid only when a complete live search for the current decision found no plausible existing row and the incoming evidence is sufficiently discriminating.

An older stored no-match result is not reusable proof that the item is still new.

## 8. Confirmed enrichment

Confirmed Baserow-derived enrichment must carry the live-read provenance from which it was derived.

Conceptually the result should include:

```text
baserow_read_at
live_read_complete
selected_media_row_id
confirmed enrichment values
source row evidence
```

The original result contract field name `database_snapshot_at` should be implemented as a **live-read/audit timestamp**, not as permission to reuse a saved database snapshot. Builders may rename it to `baserow_read_at` or an equivalent clearer name.

## 9. Local persistence and idempotency

Rerunning Tool 2 must perform new live reads. Idempotency means the same input plus equivalent current Baserow responses produce equivalent results and do not create duplicate history/events unnecessarily.

It does **not** mean reruns reuse the earlier database result without checking Baserow.

## 10. Application-service boundary

Replace the original conceptual `refresh/load database snapshot` service with live-current responsibilities such as:

```text
query current candidates
read current row
review one structured media input against live Baserow
review a batch using per-decision live reads
revalidate a candidate/current no-match before human confirmation
retrieve historical stored review result for display/audit
```

The exact interface remains builder discretion.

## 11. CLI behavior

The CLI must clearly distinguish current live review from historical stored output.

Do not offer an option whose semantics are "reuse cached Baserow snapshot as if current".

It is acceptable to expose historical results for diagnostics/audit, but current review commands must perform live reads.

## 12. Batch behavior

For large batches:

- do not download one full database snapshot and assume it remains current throughout a long batch;
- process each file/decision with current targeted queries;
- use server-side filtering/direct lookup to avoid needless full-table downloads;
- reuse one response within the same decision when appropriate;
- one database failure must not corrupt unrelated local state;
- database unavailable and valid live no-match remain distinct.

## 13. Tool 4 handoff and collaborator races

Tool 2 must pass enough provenance for Tool 4 to detect staleness, including at least row ID and the live-read timestamp/evidence used for the Tool 2 decision.

Tool 4 must always re-read the target current row immediately before any update and must re-run a fresh live existence check before creating a new row.

If collaborator changes make the reviewed Tool 2 decision stale, Tool 4 must stop that write and require re-review rather than overwriting or duplicating collaborator work.

## 14. Required test amendments

Replace the original cache-oriented tests with at least:

1. live Baserow read is required for every current Tool 2 decision;
2. persisted historical rows are not substituted when a live read fails;
3. live failure → `DATABASE_UNAVAILABLE`/incomplete and `baserow_check_complete=false`;
4. valid current live no-match → `NEW_MEDIA_CANDIDATE` only with discriminating evidence;
5. a previously stored no-match is ignored as current proof on rerun;
6. candidate row changes between initial review and human confirmation → fresh comparison/reconfirmation required;
7. candidate disappears between display and confirmation → old association is not finalized;
8. new matching row appears after an earlier no-match → rerun/confirmation sees it;
9. confirmed enrichment records live-read provenance;
10. historical audit evidence remains retrievable without becoming an operational data source;
11. large-batch implementation uses bounded live queries rather than stale full-table session reuse;
12. Tool 4 handoff contains row/live-read evidence sufficient for mandatory pre-write revalidation.

All original candidate-matching, contradiction, title-compaction, routing, `_edited`, related-format, and portal tests remain required.

## 15. Sample evaluation amendment

Tool 2 acceptance must include a safe **live read-only evaluation against the current Baserow database**.

A captured fixture/snapshot remains useful for deterministic automated tests, but it cannot substitute for the live acceptance evaluation because the policy being tested is specifically that current collaborator state is consulted.

The live evaluation must remain read-only and must not expose credentials or private data in committed test artifacts.

## 16. Acceptance criteria amendment

Tool 2 is not acceptable unless all of the following are demonstrated in addition to the original unaffected criteria:

1. no persisted Baserow row cache is used as current database authority;
2. current decisions perform live Baserow reads;
3. human confirmation revalidates relevant live state;
4. database failure cannot be hidden behind stale data;
5. `NEW_MEDIA_CANDIDATE` comes only from a current complete live search;
6. confirmed enrichment records its live-read provenance;
7. local stored Baserow values are audit/history only;
8. Tool 4 receives enough provenance to re-read and compare immediately before mutation;
9. acceptance includes live read-only Baserow evaluation.
