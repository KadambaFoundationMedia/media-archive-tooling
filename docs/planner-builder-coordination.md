# Planner / Builder Coordination

This project deliberately separates **planning/review/orchestration** from **implementation** so the repository stays coherent while chat/token usage stays low.

## Default ownership

- The planning/review model owns build plans, architecture decisions, archive-policy clarification, review findings, acceptance decisions, and central status/handoff coordination.
- The Builder is the default implementation model. It owns normal application/source-code implementation, implementation tests, local verification, commits, pushes, and implementation PR handoff.
- The user should normally only be asked for genuine archive-policy or user-facing decisions that cannot safely be inferred.

The planning/review model may directly make a small corrective or maintenance code change when that is materially more efficient, but this is an exception rather than the normal implementation path.

## Mandatory coordination for planner-authored source changes

Whenever planning/review changes application code, tests, scripts, schemas, or another implementation-facing artifact directly, it must keep the Builder's repository memory synchronized.

The planning/review model must:

1. make the change through the protected-main PR/CI workflow;
2. record the change in the affected tool's status/handoff context, including the PR or merge SHA, the behavioral purpose, and any shared interfaces/components affected;
3. if the change affects shared infrastructure or a later tool, add a pre-start coordination note to that later tool's status before Builder implementation begins;
4. preserve the distinction between planner-authored maintenance and Builder implementation history;
5. continue to use the Builder for substantive implementation unless there is a clear efficiency reason for a small direct correction.

## Builder responsibility after synchronization

After `./scripts/builder-start.sh <number>` synchronizes the repository, the Builder must treat current `main` plus the tool status as the authoritative implementation baseline.

Before editing overlapping/shared code, the Builder must read any **Planner-authored maintenance / coordination** notes in the tool status and inspect the referenced PR/diff when needed. The Builder must integrate with those changes rather than reverting, duplicating, or silently replacing them with an older implementation assumption.

If a planner-authored source change is visible in current `main` but the relevant status appears stale or contradictory, the Builder should preserve the current code, record the coordination discrepancy in the status, and avoid guessing an older intended state.

## Why this exists

The purpose is not bureaucracy. It keeps one central orchestration record while allowing the Builder to remain the consistent implementation agent. The planning/review model can still correct a small mistake quickly, but the next Builder session can reconstruct exactly what changed without relying on chat history.

## Current coordination checkpoint

As of 2026-09-13, Tool 1 post-acceptance maintenance has been merged through PR #16 / merge commit `8fb6b247d2fce2b64d8771210771970dcc8d53dc`.

Notable shared baseline changes since the original Tool 1 acceptance include:

- fresh per-target Tool 1 review snapshots and stable review counts;
- live Baserow structured-field and redirect handling corrections;
- scripture grammar/range validation corrections for BG, SB, and CC;
- review-portal usability improvements;
- batch review actions and a reusable safe `RenameCommitService` for reviewed proposals.

Later tools, especially Tool 2 because it extends the existing review portal and Renamer integration, must build on this current `main` baseline rather than the earlier accepted Tool 1 code snapshot.
