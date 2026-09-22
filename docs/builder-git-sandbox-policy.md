# Builder Git Sandbox Policy

Status: **ACTIVE — project-wide Builder execution rule**

This policy exists because the Builder's command sandbox can have different permissions from the repository owner's normal terminal. In particular, the Builder has encountered both outbound SSH denial and denial of writes inside `.git/` even though the same repository is healthy and ordinary Git commands work from the owner's terminal.

The repository owner has explicitly selected the supported GitHub transport:
HTTPS plus the project-local `GITHUB_TOKEN` through the Builder wrappers. This
supersedes the earlier SSH transport for Builder work.

## Required transport: one wrapper path

Start a numbered build with `./scripts/builder-start.sh <number>`. It configures
the remote as `https://github.com/KadambaFoundationMedia/media-archive-tooling.git`
and installs a credential helper that contains no token.

Thereafter, the Builder must use only:

```sh
./scripts/builder-git.sh <git arguments>
./scripts/builder-gh.sh <gh arguments>
```

Do not use bare `git` for a remote/mutating Builder operation, bare `gh`, SSH,
`git@github.com:...`, `gh auth login`, `gh auth token`, `source .env`, `set -a`,
or a command that prints `GITHUB_TOKEN`. The wrappers never put the token in
Git config, terminal output, commit messages, or status files. They read it
only in a short-lived credential subprocess when an authenticated GitHub call
actually needs it.

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

The repository owner has already chosen HTTPS for Builder work. Do not change
the remote again. SSH port 22 denial and `.git` lock denial are separate
sandbox restrictions; HTTPS solves only the former.

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
