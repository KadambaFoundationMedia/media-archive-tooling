#!/bin/sh
set -eu

TOOL="${1:-}"
REMOTE="${BUILDER_REMOTE:-origin}"

case "$TOOL" in
  ''|*[!0-9]*)
    echo "Usage: ./scripts/builder-start.sh <tool-number>"
    exit 2
    ;;
esac

# Always run from the repository root.
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERROR: builder-start.sh must be run inside the media-archive-tooling Git repository."
  exit 1
fi

ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"

BRANCH=$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)
if [ -z "$BRANCH" ]; then
  echo "ERROR: Builder checkout is in detached HEAD state. Check out the documented implementation branch before building."
  exit 1
fi

if ! git remote get-url "$REMOTE" >/dev/null 2>&1; then
  echo "ERROR: Git remote '$REMOTE' is not configured. Cannot synchronize builder state."
  exit 1
fi

printf '%s\n' "Synchronizing repository before reading builder status..."
git fetch --prune "$REMOTE"

UPSTREAM=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)
if [ -z "$UPSTREAM" ]; then
  if git show-ref --verify --quiet "refs/remotes/${REMOTE}/${BRANCH}"; then
    UPSTREAM="${REMOTE}/${BRANCH}"
  else
    echo "ERROR: Branch '$BRANCH' has no upstream and '${REMOTE}/${BRANCH}' does not exist."
    echo "Configure the correct branch/upstream before building."
    exit 1
  fi
fi

# Never hide or overwrite local work while synchronizing.
DIRTY=$(git status --porcelain --untracked-files=normal)
if [ -n "$DIRTY" ]; then
  echo "ERROR: Working tree is not clean. Builder will not pull or start from ambiguous local state."
  echo "Commit/push intended work, or deliberately resolve/discard it, then rerun builder-start.sh."
  printf '%s\n' "$DIRTY"
  exit 1
fi

LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_HEAD=$(git rev-parse "$UPSTREAM")
MERGE_BASE=$(git merge-base HEAD "$UPSTREAM")

if [ "$LOCAL_HEAD" = "$REMOTE_HEAD" ]; then
  printf '%s\n' "Repository branch is up to date with $UPSTREAM."
elif [ "$LOCAL_HEAD" = "$MERGE_BASE" ]; then
  printf '%s\n' "Local branch is behind $UPSTREAM; fast-forwarding before build..."
  git merge --ff-only "$UPSTREAM"
  printf '%s\n' "Repository updated to $(git rev-parse --short HEAD). Restarting builder briefing from updated files..."
  exec "$0" "$@"
elif [ "$REMOTE_HEAD" = "$MERGE_BASE" ]; then
  echo "ERROR: Local branch is ahead of $UPSTREAM with unpushed commit(s)."
  echo "Push the intended commits first, or deliberately reconcile the branch, then rerun builder-start.sh."
  exit 1
else
  echo "ERROR: Local branch and $UPSTREAM have diverged."
  echo "Do not build from stale/divergent state. Rebase/merge deliberately, resolve conflicts, push the result, then rerun builder-start.sh."
  exit 1
fi

# Identify the protected default branch. Prefer origin/HEAD, falling back to
# origin/main for this project.
DEFAULT_REF=$(git symbolic-ref --quiet --short "refs/remotes/${REMOTE}/HEAD" 2>/dev/null || true)
if [ -z "$DEFAULT_REF" ] && git show-ref --verify --quiet "refs/remotes/${REMOTE}/main"; then
  DEFAULT_REF="${REMOTE}/main"
fi

DEFAULT_BRANCH=""
if [ -n "$DEFAULT_REF" ]; then
  DEFAULT_BRANCH=${DEFAULT_REF#${REMOTE}/}
fi

# Feature branches must contain the latest default-branch planning/spec commits.
if [ -n "$DEFAULT_REF" ] && [ "$UPSTREAM" != "$DEFAULT_REF" ]; then
  if ! git merge-base --is-ancestor "$DEFAULT_REF" HEAD; then
    echo "ERROR: Current implementation branch does not contain the latest $DEFAULT_REF planning/specification commits."
    echo "Integrate $DEFAULT_REF into '$BRANCH' before building so review findings and protocol changes cannot be missed."
    exit 1
  fi
fi

STATUS=$(ls "status/tool-${TOOL}-"*.md 2>/dev/null | head -n 1 || true)
PLAN=$(ls "docs/tool-${TOOL}-"*-build-plan.md 2>/dev/null | head -n 1 || true)

if [ -z "$STATUS" ]; then
  echo "ERROR: No status file found for Tool ${TOOL}."
  exit 1
fi

if [ -z "$PLAN" ]; then
  echo "ERROR: No finalized build plan found for Tool ${TOOL}."
  exit 1
fi

STATE=$(sed -n 's/^Status: `\([^`]*\)`.*/\1/p' "$STATUS" | head -n 1)
[ -n "$STATE" ] || STATE="UNKNOWN"

# main is protected. For any state that permits implementation changes, a
# builder launched from main automatically creates/resumes the durable tool
# branch and restarts there. This keeps BUILD TOOL <n> as the only user command.
TOOL_BRANCH="tool-${TOOL}-implementation"
case "$STATE" in
  NOT_STARTED|IN_PROGRESS|BLOCKED_PARTIAL|CHANGES_REQUESTED)
    if [ -n "$DEFAULT_BRANCH" ] && [ "$BRANCH" = "$DEFAULT_BRANCH" ]; then
      printf '%s\n' "Protected default branch '$DEFAULT_BRANCH' detected; implementation must use '$TOOL_BRANCH'."

      if git show-ref --verify --quiet "refs/heads/${TOOL_BRANCH}"; then
        git checkout "$TOOL_BRANCH"
      elif git show-ref --verify --quiet "refs/remotes/${REMOTE}/${TOOL_BRANCH}"; then
        git checkout -b "$TOOL_BRANCH" --track "${REMOTE}/${TOOL_BRANCH}"
      else
        if [ -z "$DEFAULT_REF" ]; then
          echo "ERROR: Cannot identify the default branch to create '$TOOL_BRANCH'."
          exit 1
        fi
        git checkout -b "$TOOL_BRANCH" "$DEFAULT_REF"
        git push -u "$REMOTE" "$TOOL_BRANCH"
      fi

      printf '%s\n' "Implementation branch selected. Restarting builder briefing..."
      exec "$0" "$@"
    fi
    ;;
esac

printf '%s\n' "============================================================"
printf '%s\n' "MEDIA ARCHIVE BUILDER BRIEFING"
printf '%s\n' "Repository HEAD: $(git rev-parse --short HEAD)"
printf '%s\n' "Branch/upstream: ${BRANCH} -> ${UPSTREAM}"
printf '%s\n' "Tool: ${TOOL}"
printf '%s\n' "Current status: ${STATE}"
printf '%s\n' "============================================================"
printf '\nRead in this order:\n'
printf '  1. BUILDER.md\n'
printf '  2. %s\n' "$STATUS"
printf '  3. %s\n' "$PLAN"
printf '  4. docs/project-implementation-architecture.md\n'
printf '  5. docs/implementation-protocol.md\n'
printf '  6. Assets referenced by the build plan/status\n'
printf '  7. Relevant code and tests\n\n'

case "$STATE" in
  NOT_STARTED)
    printf '%s\n' "ACTION: Implement Tool ${TOOL} from the finalized build plan on this tool branch."
    ;;
  IN_PROGRESS)
    printf '%s\n' "ACTION: Resume the current milestone from the status file on this tool branch."
    ;;
  BLOCKED_PARTIAL)
    printf '%s\n' "ACTION: Continue unaffected work. Do not guess blocked policy decisions; maintain Q-### entries in the status file."
    ;;
  CHANGES_REQUESTED)
    printf '%s\n' "ACTION: Address every active R-### review finding in the status file, add required regression tests, rerun required evaluation, then update/commit/push on this branch."
    ;;
  READY_FOR_REVIEW)
    printf '%s\n' "ACTION: Stop implementation. Ensure all work is pushed, the implementation PR to main exists, and hand back for planning/review."
    ;;
  ACCEPTED)
    printf '%s\n' "ACTION: No implementation work is required unless a new task or revised build plan explicitly requests it."
    ;;
  *)
    printf '%s\n' "ACTION: Unknown status. Read the status file and implementation protocol before changing anything."
    ;;
esac

printf '\nProtected-main / PR / CI requirements:\n'
printf '%s\n' "  - Do not implement directly on protected main."
printf '%s\n' "  - Standard tool branch: ${TOOL_BRANCH}."
printf '%s\n' "  - Keep/open a PR from the tool branch to main for review."
printf '%s\n' "  - Required GitHub check before merge: Python 3.12 tests."
printf '%s\n' "  - A known failed required CI check blocks READY_FOR_REVIEW/merge."

printf '\nNon-negotiable:\n'
printf '%s\n' "  - Start only from synchronized remote repository state."
printf '%s\n' "  - Do not edit finalized build plans to fit the implementation."
printf '%s\n' "  - Record unclear/contradictory requirements as Q-### in the status file."
printf '%s\n' "  - Update status + real reachable branch HEAD before READY_FOR_REVIEW."
printf '%s\n' "  - Commit and push changes before handing back for review."
printf '%s\n' "  - Record the implementation PR in the status file."

printf '\nCurrent status file excerpt:\n'
printf '%s\n' "------------------------------------------------------------"
sed -n '1,240p' "$STATUS"
