# Tool 7 — Class Type Discoverer Status

Build plan: `docs/tool-7-class-type-discoverer-build-plan.md`
Cross-tool workflow: `docs/full-pipeline-workflow-amendment.md`
Tool 5/6 handoff: `docs/tool-5-content-discoverer-build-plan.md`,
`docs/tool-6-file-cutter-build-plan.md`

## Current state

Status: `NOT_STARTED`

The owner confirmed that Tool 7 fully transcribes **every non-kirtan
recording**, including classes with clear existing WHAT, and uses the complete
transcript for category/verse discovery and later Tools 8–10. It skips
kirtan-only recordings and the singing child of a Tool 6 split. For a
combination, Tool 6 cuts first and Tool 7 transcribes the class child. Tool 5
uses bounded acoustic analysis/short targeted excerpts, not full-file
transcription. The owner also confirmed an existing lowercase Baserow
`description` column for a verified Vedabase scripture URL, written only by
Tool 4 after live schema validation.

## Builder action

Start Tool 7 through `./scripts/builder-start.sh 7` after the finalized
planning revision is on `main`. Prefer completing Tool 6 first so its child
identity and Tool 5 cut-evidence contracts are available. Work only on
`tool-7-implementation`; follow `BUILDER.md` for commits, push, PR, CI, and
review handoff. Never use SSH or read `.env` manually for GitHub access.

## Review checkpoint

Current implementation HEAD: none
Relevant implementation commits: none
Open policy questions: none for the first build

## Pre-build verification handoff — 2026-09-24

The owner asked to reduce repeated correction cycles. The Tool 7 plan now
opens with a concrete acceptance matrix and a builder self-review gate. Before
coding against a Tool 6 child, inspect its **merged implementation** and
record the actual output/lineage fields here. Tool 6 is currently being
built; do not interrupt or alter its branch. Map every acceptance row to a
test and record observed results before `READY_FOR_REVIEW`. Passing CI alone
does not prove the Tool 7 workflow, especially the full-transcript exception
for kirtan, post-cut class identity, and Tool 4-only `description` writes.
Treat the entire `BUILD TOOL 7` request as a persistent `/goal` under
`BUILDER.md`; continue until the acceptance and PR/CI handoff are complete or
a genuine blocker is documented. This is not permission for live archive or
Baserow mutation.
