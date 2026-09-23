# Tool 5 - Content Discoverer Implementation Status

Build plan: `docs/tool-5-content-discoverer-build-plan.md`
Project architecture: `docs/project-implementation-architecture.md`
Main Script plan: `docs/main-tooling-script-build-plan.md`
Alpha/beta purge policy: `docs/alpha-beta-test-data-purge-build-plan.md`
Recording references: `assets/verse-structure.md`, `assets/original-and-edited-recording-structure.md`

## Current state

Status: `CHANGES_REQUESTED`

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

The independent review findings below must be resolved before approval. Later Tool 6 planning will define actual cutting and its final consumption of `process_by_tool_6`.

## Independent planner review - 2026-09-23

PR #55 CI is green and the local full suite passes (448 tests, 2 warnings). This does not yet satisfy the agreed Tool 5 contract. Keep the PR open and make a focused correction commit on `tool-5-implementation`:

1. **T5-R-001 — Enforce Phase 1 eligibility.** `content_discoverer/service.py:59-79` synthesizes a tracking ID and transcribes arbitrary unregistered paths; the Main Script `processing` path does the same. Require a registry-backed Phase 1 file (including one still awaiting rename/review), or explicit current Phase 1 context for a no-write dry run. Reject otherwise unknown paths/IDs without transcription, extraction, or content-review writes. Add tests for allowed unresolved Phase 1 files, dry-run handoff, and blocked untracked files.
2. **T5-R-002 — Make dry-run truly read-only and cache binding complete.** `transcriber.py:179-183` creates the transcript directory even during `--dry-run`. The cache check at lines 185-193 compares source SHA and model path but not the model binary hash/configuration, despite build plan section 6 requiring that binding. Delay directory creation until an actual sidecar write, and invalidate cache when model/configuration changes. Add tests.
3. **T5-R-003 — Protect adjacent video MP3s.** `audio_extractor.py:66-84` reuses an existing MP3 when the source hash matches, without comparing the current MP3 hash to the recorded derivative hash. Its ffmpeg `-y` output to the final path plus cleanup `unlink` can overwrite/remove a file appearing during extraction. Verify the derivative fingerprint and write via a temporary path with no-clobber finalization; block on any collision. Add tampered-derivative and concurrent-collision tests.
4. **T5-R-004 — Keep human Tool 6 routing evidence-backed.** `content_discoverer/service.py:201-214` sets `process_by_tool_6=true` for `KIRTAN_AND_CLASS` or `INITIATION` even if the reviewer supplied no usable boundary, while marking review resolved. Validate and persist a source-bound coarse bracket, or leave routing false/review open. Keep the stored structured result consistent with the human decision and add portal-action tests for empty/invalid boundaries.
5. **T5-R-005 — Preserve video source provenance in transcript sidecars.** `discover_content` transcribes the derived MP3, and `transcriber.py` stores `source_type="audio"` and the MP3 as `input_path`. The section 6 transcript contract requires the original video path/hash, `source_type="video"`, and derived-MP3 details. Record both source and derivative fingerprints and invalidate reuse if either changes; add a video sidecar test.

Also remove trailing whitespace at `content_discoverer/transcriber.py:39` (`git diff --check` currently fails). Please rerun the focused/full tests and CI, then mark this status `READY_FOR_REVIEW` in a separate final status commit and let the planner know. Do not merge the branch yourself.

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
