# Project-wide Baserow Live Data Policy

Status: **authoritative project-wide data-authority policy**

This policy applies to Tool 2 and every current or future tool that reads or writes Baserow archive data.

## 1. Why this policy exists

The Baserow Media database is shared operational state and is continuously updated by external collaborators. A local copy that was accurate moments or hours ago may already be outdated.

Therefore tools must **not assume Baserow data is unchanged between operations** and must **not use persisted cached Baserow rows as a substitute for a live read** when making a current archive decision.

## 2. Live Baserow is the only source for current database state

Any decision that depends on what Baserow currently contains must be based on a successful live Baserow read performed as part of that decision's operation.

This includes at least:

- whether a logical Media row already exists;
- which candidate row is the current match;
- current Media metadata used as confirmed enrichment;
- current `category_title` values used as database evidence;
- current `travel_schedule` values used as database evidence;
- whether a new Media row should be proposed or created;
- whether an existing row can safely be updated;
- whether `_edited` has completed its required Baserow check.

A previous batch/session snapshot, persisted cache, exported copy, or prior review result is **historical evidence**, not current database state.

## 3. No stale-cache fallback for authoritative decisions

If a live Baserow read fails, the database-dependent stage must report an unavailable/blocked state and continue only work that does not require current Baserow state.

It must not silently substitute a cached copy and continue as if the database had been checked.

In particular, stale/local data must never be used to assert:

- `EXISTING_MEDIA_MATCH` as a current database association;
- `NEW_MEDIA_CANDIDATE` / no existing row;
- current confirmed title/date/location/category enrichment;
- `baserow_check_complete=true`;
- safe readiness for a Baserow write.

## 4. Historical snapshots are allowed for audit, not reuse

Tools may persist the exact live values that were used for a decision so the decision can later be explained or audited.

Such evidence may include:

- live-read timestamp;
- table and row IDs;
- raw values returned by Baserow;
- normalized values used by the tool;
- schema/field mapping used;
- comparison outcomes and decision provenance.

These records are immutable **audit evidence**. They must not be loaded later as the operational source for a new current-state decision.

The implementation should name this distinction clearly. Avoid APIs whose names imply that an old snapshot can be refreshed once and then trusted for an entire long-running workflow.

## 5. Freshness unit: the decision, not the application session

There is no project-wide time-to-live that makes a cached database row authoritative. Even a short TTL can race with collaborator edits.

For each independent database-dependent decision:

1. query the relevant live Baserow data;
2. compute the decision from that response;
3. record the live-read timestamp/provenance;
4. if the decision is acted on later, re-read the affected current state before the action.

A response may of course be reused internally while calculating **that same decision**. This rule does not require duplicate HTTP calls for the same row within one atomic application-service operation.

## 6. Tool 2 requirements

Tool 2 is read-only, but its conclusions can drive later enrichment and writes, so its current-state checks are live-only.

- Candidate retrieval must query live Baserow.
- A displayed candidate/result may be stored locally for review, but confirming/choosing a candidate must re-read the relevant row and refresh the comparison if it has changed.
- `NEW_MEDIA_CANDIDATE` requires a complete live search performed for the current decision.
- `DATABASE_UNAVAILABLE` is returned when a current live review cannot be completed; cached data does not downgrade this to a usable result.
- Confirmed Renamer enrichment must identify the live read from which it came.
- Tool 2 local persistence stores results, user choices, and audit provenance only; it is not a current Media database mirror.

## 7. Tool 3 requirements

When Tool 3 consults Baserow `travel_schedule`, the schedule evidence for the current review must be fetched live.

Historical stored schedule rows may explain a previous decision but cannot be treated as the collaborator-current schedule on a later run.

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

Those aids must not be represented as proof of current Baserow contents. If live Baserow is unavailable, fallback reference data may support a provisional parser interpretation, but it cannot satisfy a database-existence check or be labeled as current Baserow evidence.

The existing Tool 1 reference provider's fallback behavior must not be reused by Tool 2 or Tool 4 as an authoritative database provider.

## 10. Efficient live access

Live-only does not mean repeatedly downloading every table for every file.

Implementations should prefer efficient current queries:

- server-side filters/searches;
- direct row-ID/source-ID lookups;
- complete pagination only for the narrowed live result set that requires it;
- bounded candidate queries;
- in-operation reuse of one live response while computing one decision.

A full-table download at application start followed by long-lived local processing is not considered current-state validation for later independent decisions.

## 11. Failure and review semantics

A live Baserow failure is a provider/database availability condition, not a human metadata decision and not evidence of absence.

Tools should preserve distinctions such as:

```text
LIVE_CURRENT
LIVE_PARTIAL_OR_FAILED
DATABASE_UNAVAILABLE
```

Exact names are implementation details. The observable rule is fixed: **no current Baserow conclusion without a successful current Baserow read**.

## 12. Testing requirement

Every Baserow-dependent tool must include race/freshness tests appropriate to its responsibility, including cases where live data changes after an earlier stored result.

At minimum, tests must demonstrate that:

- persisted cached rows are not used as a substitute for a failed live read;
- a collaborator change between review/display and confirmation is detected by revalidation;
- Tool 4 refuses to overwrite relevant collaborator changes;
- Tool 4 rechecks for an existing Media row before creating a new one;
- historical audit evidence remains available without becoming an operational cache.
