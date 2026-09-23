# Tool 5 - Content Discoverer Implementation Status

Build plan: `docs/tool-5-content-discoverer-build-plan.md`
Project architecture: `docs/project-implementation-architecture.md`
Main Script plan: `docs/main-tooling-script-build-plan.md`
Alpha/beta purge policy: `docs/alpha-beta-test-data-purge-build-plan.md`
Recording references: `assets/verse-structure.md`, `assets/original-and-edited-recording-structure.md`

## Current state

Status: `NOT_STARTED`

Implementation branch: `tool-5-implementation`
Implementation PR: not created

## Builder action

Implement Tool 5 exactly as specified in the finalized build plan. Start with
`BUILD TOOL 5`, which must use the required Builder GitHub wrappers and create
or resume `tool-5-implementation` from current `main`.

Do not integrate Tool 5 into the Main Tooling Script's `processing` workflow
yet: that Phase 2 orchestration work is deferred until the next tools are
defined. Tool 5 must nevertheless expose its typed service, CLI, durable
registry result, and review-portal detail/filter so it is independently
testable now.

## Open questions / contradictions

None. Later Tool 6 planning will define the actual cutting execution and how
it consumes `process_by_tool_6`; Tool 5 must only write the safe handoff flag
and proposed reviewed boundaries.

## Planner reference review - 2026-09-23

The independent local `audio-editing` project was reviewed as a read-only
implementation reference. Tool 5 must preserve this project's in-situ archive
policy and remain independent, but the finalized plan now adopts compatible
patterns: source/configuration/transcript fingerprints, complete timeline
coverage including silence gaps, explicit Metal-to-CPU evidence, bounded
subprocess/JSON handling, source-change detection, and coarse review brackets
instead of automatic exact cut points. A video-derived adjacent MP3 is also
registered as a Tool 5 derivative so archive discovery cannot treat it as a
second independent input.
