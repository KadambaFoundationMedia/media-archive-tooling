# Builder Git Sandbox Policy

Status: **ACTIVE — project-wide Builder execution rule**

This policy exists because the Builder's command sandbox can have different permissions from the repository owner's normal terminal. In particular, the Builder has encountered both outbound SSH denial and denial of writes inside `.git/` even though the same repository is healthy and ordinary Git commands work from the owner's terminal.

## Recognized sandbox symptoms

Treat any of the following as an execution-environment restriction, not as repository corruption:

```text
ssh: connect to host github.com port 22: Operation not permitted
cannot open '.git/FETCH_HEAD': Operation not permitted
cannot lock ref 'ORIG_HEAD'
Unable to create '.git/ORIG_HEAD.lock': Operation not permitted
cannot read/write '.git/HEAD', '.git/index', refs, or lock files because of EPERM/EACCES
```

## Mandatory response

On the first such error, **stop the Git synchronization/mutation sequence immediately**.

Do not continue with `merge`, `pull`, `checkout`, `reset`, `rebase`, `commit`, `push`, or another Git write based on stale remote-tracking refs after a failed fetch.

Do not attempt to repair the repository by deleting, recreating, rewriting, changing permissions/ownership, clearing extended attributes, or otherwise manipulating files under `.git/`.

In particular, the Builder must never manually remove or rewrite:

```text
.git/HEAD
.git/FETCH_HEAD
.git/ORIG_HEAD
.git/index
.git/refs/*
.git/*.lock
```

`FETCH_HEAD` is disposable Git metadata, but deleting it does not solve a sandbox that cannot recreate/write Git metadata. `HEAD`, `ORIG_HEAD`, the index, refs, and lock files are repository state owned by Git itself and must not be hand-repaired by the Builder.

## Allowed recovery path

If the Builder environment provides an explicit elevated/approved command-execution mode, it may retry the **same ordinary Git command** through that approved mode. It must not change the command into a `.git` surgery workaround.

If elevated/approved Git execution is unavailable, report exactly:

```text
GIT_SANDBOX_BLOCKED
```

and stop Git operations. The repository owner/orchestrator can then synchronize from a normal terminal or another Git-capable environment.

Do not change the repository remote from SSH to HTTPS merely to bypass the first error unless the repository owner explicitly chooses to do so. SSH port 22 denial and `.git` lock denial are separate sandbox restrictions; changing transport does not solve the latter.

## Safe owner-side synchronization

When owner-side synchronization is required, the repository owner may run ordinary Git commands from the normal terminal, for example:

```sh
git status
git fetch --prune --no-write-fetch-head origin
git switch tool-<number>-implementation
git merge --ff-only origin/tool-<number>-implementation
```

Only use the actual active tool branch. Do not reset/discard local work automatically.

After owner-side synchronization, the Builder may resume application work from the synchronized working tree. If Git writes are still sandbox-blocked at completion, the Builder must again report `GIT_SANDBOX_BLOCKED`; it must not claim `READY_FOR_REVIEW` while changes are uncommitted or unpushed.

## Coordination rule

This policy does not weaken the remote-synchronization requirement. It changes only **who performs Git mutations when the Builder sandbox is not permitted to do so**.

The Builder still must work from current durable repository state, and planning/review still verifies the actual pushed PR head and CI before acceptance.
