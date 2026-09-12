# media-archive-tooling

Tools for processing media files in the archive.

## Build plans

Each finalized tool has its own implementation-ready Markdown build plan under `docs/`. The build plan is the specification the implementation model should follow. When a plan is finalized, this README should be updated with a short summary and implementation status.

### Tool 1 — Renamer

Status: **build plan finalized; implementation pending**

Build plan: `docs/tool-1-renamer-build-plan.md`

Implementation tracking: GitHub issue #1

The Renamer is a fast, repeatable filename interpretation and normalization tool. It assigns a stable temporary `_ID-xxxxxxxx` during processing, extracts and progressively enriches WHEN/WHO/WHAT/WHERE metadata from filenames, folders, Baserow reference data and later-tool evidence, handles ambiguous dates and multilingual archive naming patterns, resolves locations against shared Baserow data, and performs safe dry-run/commit renames without blocking the batch on unclear files. It deliberately avoids slow audio/content analysis; later passes reuse the same Renamer engine as stronger evidence becomes available.

## Project progress protocol

GitHub is the durable communication channel between planning/review and implementation models.

For each tool:

1. Create a dedicated build-plan Markdown file in `docs/`.
2. Create a GitHub implementation issue containing the milestone checklist.
3. The implementation model should post progress comments at meaningful milestones with completed work, commits/PRs, tests, observed behavior, blockers, and the next milestone.
4. Archive-policy changes must not be silently introduced during implementation; proposed specification changes should be raised in the tracking issue first.
5. The planning/review model can inspect the issue, commits/PR, tests and diagnostic logs to review progress and refine the plan when evidence warrants it.
6. When a tool is accepted, update this README with its final status and concise summary.

This process avoids relying on direct model-to-model memory and keeps the repository itself as the project record.
