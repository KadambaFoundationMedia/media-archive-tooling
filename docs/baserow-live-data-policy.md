# Project-wide Baserow Live Data Policy

Status: **authoritative project-wide data-authority policy**

This policy applies to Tool 2 and every current or future tool that reads or writes Baserow archive data.

## 1. Why this policy exists

The Baserow Media database is shared operational state and is continuously updated by external collaborators. A local copy that was accurate moments or hours ago may already be outdated.

Therefore tools must **not assume mutable Baserow data is unchanged between operations** and must **not use persisted cached mutable Baserow rows as a substitute for a live read** when making a current archive decision.

### Immutable-table exception

The `travel_schedule` table is explicitly different: it is a **static historical reference table and will never be updated**. It is therefore not subject to current-state freshness requirements.

A complete, verified local snapshot/cache of `travel_schedule` may be reused as authoritative schedule evidence across sessions and batches. Re-fetching the same immutable table for every file or decision is unnecessary. Implementations should preserve provenance and snapshot integrity, but network availability is not required once a complete verified copy is available.

This exception applies only to `travel_schedule`. It does not weaken the live-current requirements for mutable Media/database state.

## 2. Live Baserow is the only source for current mutable database state

Any decision that depends on what mutable Baserow state currently contains must be based on a successful live Baserow read performed as part of that decision's operation.

This includes at least:

- whether a logical Media row already exists;
- which candidate row is the current match;
- current Media metadata used as confirmed enrichment;
- current `category_title` values used as database evidence;
- whether a new Media row should be proposed or created;
- whether an existing row can safely be updated;
- whether `_edited` has completed its required Baserow check.

A previous batch/session snapshot, persisted cache, exported copy, or prior review result of mutable data is **historical evidence**, not current database state.

`travel_schedule` is excluded from this rule because its contents are immutable by project policy.

## 3. No stale-cache fallback for authoritative mutable decisions

If a live Baserow read required for mutable database state fails, the database-dependent stage must report an unavailable/blocked state and continue only work that does not require current mutable Baserow state.

It must not silently substitute a cached copy and continue as if the mutable database had been checked.

In particular, stale/local mutable data must never be used to assert:

- `EXISTING_MEDIA_MATCH` as a current database association;
- `NEW_MEDIA_CANDIDATE` / no existing row;
- current confirmed title/date/location/category enrichment;
- `baserow_check_complete=true`;
- safe readiness for a Baserow write.

This failure rule does not apply to a complete verified local `travel_schedule` snapshot, because that table has no later/current version to become stale against.

## 4. Historical snapshots are allowed for audit, not reuse of mutable state

Tools may persist the exact live values that were used for a mutable-state decision so the decision can later be explained or audited.

Such evidence may include:

- live-read timestamp;
- table and row IDs;
- raw values returned by Baserow;
- normalized values used by the tool;
- schema/field mapping used;
- comparison outcomes and decision provenance.

These records are immutable **audit evidence**. They must not be loaded later as the operational source for a new current-state decision involving mutable Baserow data.

The implementation should name this distinction clearly. Avoid APIs whose names imply that an old mutable-data snapshot can be refreshed once and then trusted for an entire long-running workflow.

For `travel_schedule`, a persisted complete snapshot is not merely audit evidence: it may also serve as the operational static reference dataset, provided completeness/integrity is known.

## 5. Freshness unit: the decision, not the application session

For mutable Baserow data there is no project-wide time-to-live that makes a cached database row authoritative. Even a short TTL can race with collaborator edits.

For each independent mutable database-dependent decision:

1. query the relevant live Baserow data;
2. compute the decision from that response;
3. record the live-read timestamp/provenance;
4. if the decision is acted on later, re-read the affected current state before the action.

A response may of course be reused internally while calculating **that same decision**. This rule does not require duplicate HTTP calls for the same row within one atomic application-service operation.

`travel_schedule` is not freshness-scoped by decision. A complete verified schedule snapshot may be loaded once and reused indefinitely because the source table is immutable.

## 6. Tool 2 requirements

Tool 2 is read-only, but its conclusions can drive later enrichment and writes, so its mutable current-state checks are live-only.

- Candidate retrieval from mutable Media data must query live Baserow.
- A displayed candidate/result may be stored locally for review, but confirming/choosing a candidate must re-read the relevant row and refresh the comparison if it has changed.
- `NEW_MEDIA_CANDIDATE` requires a complete live Media search performed for the current decision.
- `DATABASE_UNAVAILABLE` is returned when a required current mutable review cannot be completed; cached mutable data does not downgrade this to a usable result.
- Confirmed Renamer enrichment from mutable Media data must identify the live read from which it came.
- Tool 2 local persistence stores mutable-data results, user choices, and audit provenance only; it is not a current Media database mirror.
- Tool 2 may expose `travel_schedule` through the same provider/service boundary, but the schedule portion may use a complete verified static snapshot/cache.

## 7. Tool 3 requirements

Tool 3 consults Baserow `travel_schedule`, but that table is an **immutable static reference dataset**.

Tool 3 therefore does **not** need a fresh network read for every file, batch, review, or decision. It may use a complete verified local snapshot/cache as authoritative schedule evidence.

Recommended behavior:

- acquire the complete schedule dataset once (or use a committed/packaged verified snapshot);
- preserve source provenance, row IDs, and a checksum/version marker for integrity/audit purposes;
- reuse that dataset across files, batches, sessions, and offline runs;
- fail only if no complete verified schedule dataset is available or if local integrity validation fails;
- do not reinterpret network unavailability as schedule uncertainty when a verified local copy already exists.

The schedule's **evidentiary strength** remains limited even though the data itself is static: it records planned travel and can corroborate or suggest WHEN/WHERE, but it is not absolute proof that a recording occurred at that place/time.

## 8. Tool 4 requirements

Tool 4 is the mutation boundary and must defend against collaborator races.

Before any update to an existing Baserow row, Tool 4 must:

1. fetch the target row live immediately before mutation;
2. compare the relevant current fields against the values/evidence that were reviewed;
3. if relevant state changed, do not overwrite the collaborator's changes silently; return a stale/re-review conflict;
4. only write after the live precondition check succeeds.

Before creating a new Media row, Tool 4 must perform a fresh live existence/candidate check so a row created by another collaborator since Tool 2 review is not duplicated.

Where Baserow exposes useful row update/version metadata it may be recorded and used as additional evidence, but a live pre-write read remains the required baseline.

## 9. Tool 1 and local reference aids

Tool 1 may use committed static dictionaries/seed assets as **non-authoritative parsing aids** when useful for filename interpretation.

Those aids must not be represented as proof of current mutable Baserow contents. If live mutable Baserow is unavailable, fallback reference data may support a provisional parser interpretation, but it cannot satisfy a mutable database-existence check or be labeled as current Media evidence.

A verified `travel_schedule` snapshot is different: it is authoritative as the static schedule dataset, while still carrying only the limited evidentiary strength defined for travel plans.

The existing Tool 1 reference provider's fallback behavior must not be reused by Tool 2 or Tool 4 as an authoritative provider for mutable database state.

## 10. Efficient access

Live-only for mutable state does not mean repeatedly downloading every mutable table for every file.

Implementations should prefer efficient current queries:

- server-side filters/searches;
- direct row-ID/source-ID lookups;
- complete pagination only for the narrowed live result set that requires it;
- bounded candidate queries;
- in-operation reuse of one live response while computing one decision.

A full mutable-table download at application start followed by long-lived local processing is not considered current-state validation for later independent mutable decisions.

For immutable `travel_schedule`, the opposite optimization is preferred: load or materialize the complete verified dataset once and reuse it rather than issuing redundant network queries.

## 11. Failure and review semantics

A live Baserow failure involving required mutable state is a provider/database availability condition, not a human metadata decision and not evidence of absence.

Tools should preserve distinctions such as:

```text
LIVE_CURRENT
LIVE_PARTIAL_OR_FAILED
DATABASE_UNAVAILABLE
```

Exact names are implementation details. The observable rule for mutable state is fixed: **no current mutable Baserow conclusion without a successful current Baserow read**.

For immutable `travel_schedule`, availability semantics are different: a verified local schedule snapshot remains usable even if Baserow is offline.

## 12. Testing requirement

Every tool that depends on mutable Baserow state must include race/freshness tests appropriate to its responsibility, including cases where live data changes after an earlier stored result.

At minimum, mutable-state tests must demonstrate that:

- persisted cached mutable rows are not used as a substitute for a failed required live read;
- a collaborator change between review/display and confirmation is detected by revalidation;
- Tool 4 refuses to overwrite relevant collaborator changes;
- Tool 4 rechecks for an existing Media row before creating a new one;
- historical audit evidence remains available without becoming an operational cache for mutable state.

Tool 3 instead requires static-dataset tests demonstrating that:

- a complete verified `travel_schedule` snapshot can be reused across independent decisions and sessions;
- offline/network failure does not invalidate an already verified schedule snapshot;
- incomplete or integrity-failed schedule snapshots are not treated as authoritative;
- schedule provenance/row IDs remain available for audit;
- static schedule evidence is never promoted to the evidentiary authority of a confirmed Media row.
