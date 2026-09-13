#!/bin/sh
set -eu

TOOL="${1:-}"

case "$TOOL" in
  ''|*[!0-9]*)
    echo "Usage: ./scripts/builder-start.sh <tool-number>"
    exit 2
    ;;
esac

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

printf '%s\n' "============================================================"
printf '%s\n' "MEDIA ARCHIVE BUILDER BRIEFING"
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
    printf '%s\n' "ACTION: Implement Tool ${TOOL} from the finalized build plan."
    ;;
  IN_PROGRESS)
    printf '%s\n' "ACTION: Resume the current milestone from the status file."
    ;;
  BLOCKED_PARTIAL)
    printf '%s\n' "ACTION: Continue unaffected work. Do not guess blocked policy decisions; maintain Q-### entries in the status file."
    ;;
  CHANGES_REQUESTED)
    printf '%s\n' "ACTION: Address every active R-### review finding in the status file, add the required regression tests, rerun the required sample evaluation, then update/commit/push the status as READY_FOR_REVIEW."
    ;;
  READY_FOR_REVIEW)
    printf '%s\n' "ACTION: Stop implementation. Ensure all work is pushed and hand back for planning/review."
    ;;
  ACCEPTED)
    printf '%s\n' "ACTION: No implementation work is required unless a new task or revised build plan explicitly requests it."
    ;;
  *)
    printf '%s\n' "ACTION: Unknown status. Read the status file and implementation protocol before changing anything."
    ;;
esac

printf '\nNon-negotiable:\n'
printf '%s\n' "  - Do not edit finalized build plans to fit the implementation."
printf '%s\n' "  - Record unclear/contradictory requirements as Q-### in the status file."
printf '%s\n' "  - Update status + real reachable commit HEAD before READY_FOR_REVIEW."
printf '%s\n' "  - Push changes before handing back for review."

printf '\nCurrent status file excerpt:\n'
printf '%s\n' "------------------------------------------------------------"
sed -n '1,220p' "$STATUS"
