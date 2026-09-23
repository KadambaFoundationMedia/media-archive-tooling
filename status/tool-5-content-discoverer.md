# Tool 5 - Content Discoverer Implementation Status

Build plan: `docs/tool-5-content-discoverer-build-plan.md`
Project architecture: `docs/project-implementation-architecture.md`
Main Script plan: `docs/main-tooling-script-build-plan.md`
Alpha/beta purge policy: `docs/alpha-beta-test-data-purge-build-plan.md`
Recording references: `assets/verse-structure.md`, `assets/original-and-edited-recording-structure.md`

## Current state

Status: `READY_FOR_REVIEW`

Implementation branch: `tool-5-implementation`
Implementation PR: https://github.com/KadambaFoundationMedia/media-archive-tooling/pull/55

## Verification Summary
- Test Suite: 448/448 passed across repository.
- Dedicated Tool 5 Test Suite: 18/18 passed in `tests/test_content_discoverer.py`.
- Package Build: `uv build --offline` succeeded.
- Script Verification: `sh -n run-media-archive.sh scripts/builder-start.sh scripts/review-tool-1.sh` passed.
- Formatting & Hygiene: `git diff --check` passed clean.

## Builder action

Tool 5 Content Discoverer has been fully implemented and verified per plan and user specifications:
1. In-situ preservation and zero Baserow access guaranteed.
2. WhisperCpp transcription with Metal-to-CPU automatic fallback and explicit silence gap handling.
3. Audio extraction from video files with collision detection and derivative registration (`video_audio_derivatives`).
4. Classification engine for CLASS, KIRTAN_AND_CLASS, KIRTAN, INITIATION, EVENT_OR_FESTIVAL_ADDRESS, HOME_PROGRAM, and UNKNOWN_REVIEW with mantra detection.
5. Selective cutter handoff: only high-confidence multi-part recordings route to Tool 6 (`process_by_tool_6 = True`).
6. Main Tooling Script integration: integrated as Stage 6 in `--workflow all`, directly in `--workflow processing`, and omitted in `--workflow renamer`.
7. Review Portal audio streaming player (`GET /audio/{tracking_id}` with seek helpers) and content discovery review card.

## Open questions / contradictions

None. Later Tool 6 planning will define the actual cutting execution and how it consumes `process_by_tool_6`.

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
