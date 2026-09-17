# Project-wide Baserow Access Boundary Amendment

Status: **AUTHORITATIVE — supersedes every conflicting earlier plan, amendment, status, README, and implementation statement**

User clarification date: 2026-09-17

Related policy: `docs/baserow-live-data-policy.md`

## 1. Non-negotiable access boundary

The allowed Baserow access is:

| Tool | Read | Write/schema mutation |
| --- | --- | --- |
| Tool 1 — Renamer | No | No |
| Tool 2 — Media Database Reviewer | Yes | No |
| Tool 3 — Travel Schedule Reviewer | No | No |
| Tool 4 — Media Database Updater | Yes | Yes |

Tool 2 is the read-only Media query/reconciliation boundary. Tool 4 is the only tool with read-and-write access and the only tool allowed to create, update, delete, or mutate Baserow rows, select options, or schema.

Tools 1 and 3 must not receive Baserow credentials/table IDs for operational access, construct Baserow clients/providers, or issue Baserow requests.

## 2. Required Tool 1–4 procedure

The processing sequence is:

```text
Tool 1 finds/interprets the media file
→ Tool 1 makes an initial rename when local filename/path evidence is sufficient
→ Tool 1 asks Tool 2 to check Baserow for a matching Media item
→ Tool 1 asks Tool 3 to check/corroborate the recording date from travel-schedule evidence
→ Tool 1 combines the accepted evidence and commits the final filename
→ Tool 1 calls Tool 4 once for that finalized filename/current file state
→ Tool 4 uses Tool 2 for a fresh existing-item/candidate query
→ Tool 4 revalidates the relevant live state and safely creates/updates Baserow
```

Tool 4 is not called for the initial/intermediate rename in this procedure. It is called after Tool 1 has worked with Tools 2 and 3 and committed the final filename.

Later pipeline stages may discover stronger WHAT/WHERE/WHEN evidence. When Tool 1 subsequently commits another final renamed state, it calls Tool 4 again for that new finalized state.

## 3. Tool responsibilities

### Tool 1

Tool 1 owns file discovery, filename/path interpretation, evidence combination, canonical rendering, filesystem rename/commit behavior, and tracking identity.

It invokes Tool 2 and Tool 3 to obtain their structured evidence. It performs no Baserow access. After it commits the final filename for the current processing stage, it calls Tool 4 with the final committed file state.

### Tool 2

Tool 2 owns read-only Baserow Media candidate retrieval and reconciliation. It may load the Baserow credentials and table IDs required for read-only operations and issue targeted, pagination-complete live reads.

Tool 2 must never create, update, delete, or mutate Baserow rows, select options, or schema. It returns typed decisions, candidates, completeness, database state, live-read timestamp, table/row identity, and provenance.

Once Tool 2 has safely confirmed that an existing Media row represents the same logical recording, the relevant populated metadata currently stored on that row is leading and confirmed. Tool 1 uses it when producing the final filename. Candidate-only or ambiguous rows do not gain that authority merely because they exist in Baserow.

Tool 2 is used twice where required:

1. by Tool 1 while producing the final filename;
2. by Tool 4 for the fresh create-vs-update/existing-item check before synchronization.

### Tool 3

Tool 3 owns travel-schedule reasoning and date/location corroboration. It does not access Baserow. It consumes a complete integrity-verified local `travel_schedule` artifact bootstrapped or verified through Tool 2's read-only Baserow boundary.

Travel-schedule evidence remains contextual. It may corroborate or narrow a recording date, but it must not silently override stronger contradictory filename/path evidence or a confirmed Media association.

### Tool 4

Tool 4 owns all Baserow mutations plus the direct reads required for schema validation, exact-row inspection, pre-write revalidation, uncertain-outcome reconciliation, and safe minimal patches.

For Media existence/candidate matching, Tool 4 uses Tool 2 rather than reimplementing Tool 2's reconciliation algorithm. Tool 4 validates the returned Tool 2 contract and defaults to blocking when the decision, completeness, database state, timestamp, or provenance is missing, unknown, stale, partial, or unavailable.

For a confirmed existing row, populated relevant Baserow metadata is leading. Tool 4 may fill blank fields with trustworthy new archive metadata, but it must not automatically replace a contradictory populated value. It flags that contradiction for review and preserves the current database value unless a separately authorized correction workflow explicitly permits the exact field change after live revalidation.

## 4. Freshness and race safety

Mutable Media decisions must use current Baserow state. Historical persisted rows/results are audit evidence only.

Before create, Tool 4 must:

1. invoke Tool 2 for a fresh complete live candidate review;
2. require a valid current `NEW_MEDIA_CANDIDATE` decision with complete provenance;
3. block if a plausible/current row exists or the read is incomplete/unavailable;
4. create only while the reviewed precondition remains valid.

Before update, Tool 4 must:

1. use Tool 2 to establish/revalidate the target association where required;
2. read the exact target row live through Tool 4's own write gateway;
3. compare every field it intends to modify and all reviewed preconditions;
4. send only the minimal validated patch;
5. preserve unrelated formats, URLs, attachments, and human metadata.

## 5. Composition and credentials

- Tool 1 composition may construct/invoke Tool 2 and Tool 3 services, then Tool 4 after the final rename.
- Tool 2 composition may construct only read-only Baserow access.
- Tool 3 receives only a local verified schedule artifact and no Baserow credentials/provider.
- Tool 4 composition may construct read/write Baserow access and Tool 2's read-only review service.
- UI routes and CLI commands call application services; browser/template code never contacts Baserow directly.

The read-only and read/write credentials should be scoped separately when Baserow supports that operational setup. Secrets must never be persisted in audit data or emitted in logs.

## 6. Required architecture tests

Tests must demonstrate:

1. Tool 1 and Tool 3 production composition constructs no Baserow client/provider and receives no Baserow credentials;
2. Tool 2 cannot perform create/update/delete/schema/select-option mutations;
3. Tool 4 uses Tool 2's current result as the create-vs-update gate;
4. Tool 4 independently revalidates the exact row and write preconditions immediately before mutation;
5. Tool 3 operates offline from a complete verified schedule artifact produced through Tool 2's read-only boundary;
6. Tool 1 calls Tool 4 only after the final filename/current stage state is committed;
7. a later final Tool 1 rename creates another durable Tool 4 synchronization request;
8. the full Tool 1–4 regression suite remains green.

## 7. Builder boundary

If older documents or code conflict with this amendment, this amendment wins.

Do not reinterpret "Tool 4 is the only tool with read-and-write access" as forbidding Tool 2's read-only access. Tool 2 may read; only Tool 4 may write. Preserve the already approved Tool 1–3 domain behavior while correcting composition and synchronization timing.
