# Tool 4 — Media Database Updater Implementation Status

Build plan: `docs/tool-4-media-database-updater-build-plan.md`
Implementation issue: #24
Implementation PR: #27 — https://github.com/KadambaFoundationMedia/media-archive-tooling/pull/27
Project architecture: `docs/project-implementation-architecture.md`
Project Baserow policy: `docs/baserow-live-data-policy.md`
Baserow access-boundary amendment: `docs/baserow-access-boundary-amendment.md`
Project implementation protocol: `docs/implementation-protocol.md`

## Current state

Status: `CHANGES_REQUESTED`

Implementation branch: `tool-4-implementation`
Builder implementation commit reviewed: `9e2db4a02325c247d14d82f13af6cd60b2b980e5`
Builder handoff tip reviewed: `d32a04e61ee8bfd1ea5e688abee9f2f180fe4247`
Base commit (`main`): `8ab7d81237e1b5c21976fe78ce55f284c7e61f96`
Second independent review commit: `c829b983796beaa2c109d6bb9386e93f52db3df1`
Last planning/review update: 2026-09-17

## Third independent review checkpoint

The second correction round substantively fixed several findings:

- R-014 positive exact/strong semantic-state eligibility and Tool 1 evidence preservation;
- R-015 shared Tool 2 + Tool 4 production composition used by `run_renamer()`;
- R-017 missing, incompatible, read-only, and duplicate live-schema blocking before option/row mutation;
- R-018 `DATABASE_UNAVAILABLE` retry visibility and distinct `PREVIEW` rendering;
- R-020 complete ISO-3166-1 alpha-2 country mapping and invalid-code handling.

The branch is still not safe to accept or merge. Direct boundary testing reproduced fail-open create/update paths, broken portal approval actions, incomplete secret redaction, and invalid evaluation provenance.

Verification at handoff tip `d32a04e`:

- full local suite: **292 passed, 2 warnings**;
- focused Tool 4 suite: **71 passed, 2 warnings**;
- helper syntax: `sh -n scripts/builder-start.sh scripts/review-tool-1.sh` PASS;
- package build: `uv build --offline` PASS;
- `git diff --check c829b98..d32a04e` PASS;
- PR #27 is open, targets `main`, and is mergeable;
- GitHub Actions `Python 3.12 tests` passed on exact PR head `d32a04e`;
- working tree was clean and synchronized before this review update.

The passing suite does not cover the reproduced cases below.

## Required corrections from third review

### R-023 — Pre-create completeness verification still fails open

R-013 was only partially resolved. `_commit_create()` rejects completeness attributes when they are exactly `False`, but missing attributes resolve to `None` and pass. `database_state` also defaults to the invented value `LIVE_HEALTHY` and is checked with a denylist instead of requiring a known live-current state.

Independent reproduction returned a fresh object containing only:

```text
decision=NEW_MEDIA_CANDIDATE
```

Tool 4 returned `SYNCED` and called `create_row`. Internally inconsistent results with missing completeness attributes, `None` values, or unrecognized/database-unavailable state labels can therefore authorize creation.

Required correction:

- require `live_read_complete is True`, `snapshot_complete is True`, and `baserow_check_complete is True`;
- require `database_state` to be in an explicit allowlist of the actual Tool 2 live-current state values;
- reject missing, `None`, unknown, partial, stale, and unavailable values;
- validate the returned object as the Tool 2 result contract rather than accepting arbitrary attribute bags;
- add parameterized regressions for every missing/`None` attribute, `DATABASE_UNAVAILABLE`, unknown state, and the valid complete result.

### R-024 — Unknown Tool 2 decisions can update a selected row

The Tool 2 gate now routes to update whenever `selected_media_row_id` is present and `tool2_decision != "NEW_MEDIA_CANDIDATE"`. That condition includes empty, missing, stale, and completely unknown decisions, even without a validated `CHOOSE_ASSOCIATION` action.

Independent reproduction used:

```text
tool2_decision=BOGUS
selected_media_row_id=7
```

Tool 4 returned `SYNCED UPDATE`, called `patch_row`, and changed the row Filename from `old.mp3` to `new.mp3`.

Required correction:

- default-deny every decision other than a current `EXISTING_MEDIA_MATCH` or a separately validated explicit association decision;
- require `CHOOSE_ASSOCIATION` to carry association-specific reviewed candidate/precondition evidence and provenance;
- do not treat an arbitrary field approval with that action label as sufficient association authority;
- add regressions for `None`, empty, unknown, stale, every non-match Tool 2 decision, a stale selected ID, and a valid human-confirmed association.

### R-025 — Field approval parsing fails open and portal approval buttons are broken

R-016 was only partially resolved.

`MediaDbSyncRequest.get_approval()` calculates:

```text
bool(has_reviewed_precondition) OR key "reviewed_precondition_value" is present
```

Consequently a dictionary that explicitly says `has_reviewed_precondition=False` becomes `True` whenever it also contains the value key. `apply_field_approval()` always includes that key, so the explicit flag is not authoritative. Invalid action strings are also silently converted to `APPLY_CORRECTION`, which is an unsafe default.

The portal template submits `KEEP_DATABASE`, `APPLY_CORRECTION`, and `DEFER`, while the endpoint constructs `FieldApprovalAction(action)` whose values are lowercase. An actual POST from the rendered Apply button returned HTTP 400:

```json
{"detail": "Invalid field approval action: APPLY_CORRECTION"}
```

List/dictionary preconditions such as Tag values are also serialized through a plain hidden text input, losing their structured type.

Required correction:

- define and validate one strict field-approval input model at the service boundary;
- honor `has_reviewed_precondition=False` exactly and never infer it from key presence;
- reject missing/unknown actions instead of defaulting to apply;
- make portal form values and endpoint parsing use the same canonical enum values;
- preserve structured preconditions using a safe typed representation rather than Python/Jinja stringification;
- add real portal POST tests for keep/apply/defer, blank preconditions, Tag/list preconditions, explicit false, missing action, and invalid action.

### R-026 — Audit redaction remains key-blind and live-read timestamps are fabricated

R-019 was only partially resolved. Recursive redaction examines value text but not sensitive dictionary keys. Persisting this request:

```json
{"api_token": "VERYSECRET", "password": "HUSH"}
```

left both secrets unchanged in SQLite because the values did not themselves contain `Token ...` or `password=...` syntax.

`build_sync_request()` still never populates `tool2_timestamp` or `live_query_timestamp` from the stored Tool 2 review. `_enrich_result()` substitutes the current result time when the live-read timestamp is missing, making the audit claim a live-read time that was not actually recorded by the read.

Required correction:

- redact values based on sensitive key names (`authorization`, token/key/password/secret variants) as well as value patterns;
- cover nested dictionaries/lists and serialized JSON without losing valid JSON structure;
- populate Tool 2 reference/timestamps/database state from the actual review record;
- never substitute result time for an unknown live-read time—record it as unavailable;
- add registry round-trip tests for plain key-named secret values and audit assertions for the exact stored Tool 2 snapshot/read timestamp.

### R-027 — The 260-file evaluation identifies the wrong evaluated commit

`docs/eval_summary_tool4.json` reports:

```text
evaluated_commit=c829b983796beaa2c109d6bb9386e93f52db3df1
```

That is the planner's second-review status commit before Antigravity's implementation commit `9e2db4a`, not the corrected implementation being handed off. The evaluation appears to have run with uncommitted code changes while `git rev-parse HEAD` still named the old review commit. Its commit provenance therefore cannot establish which code produced the results.

The recorded `live_reference_info` also contains table IDs/path only; it does not establish the Tool 2 database state/read timestamp or reference checksums used by the run.

Required correction:

- commit the implementation first, then run the safe evaluation from a clean tree at that exact implementation commit;
- refuse to produce acceptance evidence from a dirty worktree;
- record the actual evaluated commit, clean-tree confirmation, Tool 2 live database state/read timestamp, and relevant reference identities/checksums;
- rerun after R-023 through R-026 are corrected and retain exact diffs plus preserved-field evidence;
- do not bulk-write production Baserow.

### R-028 — Final handoff protocol for the next review

Required correction:

- add meaningful regressions for R-023 through R-027;
- run the full suite, focused Tool 4 suite, helper syntax, offline package build, and corrected 260-file evaluation;
- keep all implementation and review work on `tool-4-implementation` / PR #27;
- commit and push implementation first, run evaluation from that clean implementation commit, then commit generated evidence;
- update this status in a final separate handoff commit with the exact branch/PR head;
- wait for required CI on that exact final head and record its check URL/result;
- return `READY_FOR_REVIEW` only after every correction is complete.

### R-029 — Enforce the clarified Tool 1–4 orchestration and Baserow access boundary

The user clarified the final orchestration after the third review. Tool 2 has read-only Baserow access; Tool 4 has read-and-write access and is the only writer; Tools 1 and 3 have no Baserow access. Tool 4 is called after Tool 1 has worked with Tools 2 and 3 and committed the final filename for the current processing stage, not after the initial/intermediate rename.

Required correction:

- preserve Tool 2's accepted live read-only candidate retrieval/reconciliation and make it technically incapable of mutation;
- ensure Tool 1 and Tool 3 receive no Baserow credentials/providers and originate no Baserow request;
- have Tool 3 consume a complete integrity-verified local `travel_schedule` artifact bootstrapped/verified through Tool 2's read-only boundary;
- have Tool 1 ask Tool 2 for the Media check and Tool 3 for recording-date/schedule evidence before committing the final filename;
- have Tool 1 call Tool 4 once after that final filename/current stage state is committed;
- have Tool 4 use Tool 2 for a fresh existing-item/candidate check, validate the complete Tool 2 result contract, then directly revalidate the exact row/schema/write preconditions;
- retain durable Tool 4 synchronization for every later final Tool 1 rename produced by stronger WHAT/WHERE/WHEN evidence;
- add architecture/integration tests for the access matrix and exact call ordering;
- preserve accepted Tool 1–3 domain behavior and keep the full regression suite green.

The controlling specification is `docs/baserow-access-boundary-amendment.md`. Where older finalized plans, statuses, README text, or implementation structure conflict with it, the amendment wins.

## Open requirement clarification

### Q-001 — `Media Archive link` source and current policy

The requirements re-shared on 2026-09-17 say `Media Archive link` should contain the shared-drive URL to the media file. The current finalized plan records a later decision that this URL is added manually and Tool 4 must leave the field empty/unchanged until a dedicated workflow exists.

The Builder must not guess or derive a URL from `media_archive_path`. The user must confirm whether the newly re-shared requirement supersedes the manual/deferred rule. If automatic population is restored, the plan also needs the authoritative source or mapping rule that produces the shared-drive URL.

## Next milestone

Antigravity Builder addresses R-023 through R-029 on `tool-4-implementation`, pushes the corrections to PR #27, waits for CI on the exact final head, updates this status to `READY_FOR_REVIEW`, and returns the branch for a fourth independent review. The planner/orchestrator will not merge PR #27 until that review passes.
