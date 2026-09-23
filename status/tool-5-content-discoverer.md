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
- Test Suite: 460/460 passed across repository.
- Dedicated Tool 5 Test Suite: 30/30 passed in `tests/test_content_discoverer.py`.
- Package Build: `uv build --offline` succeeded.
- Script Verification: `sh -n run-media-archive.sh scripts/builder-start.sh scripts/review-tool-1.sh` passed.
- Formatting & Hygiene: `git diff --check` passed clean.
- Remote CI: GitHub Actions workflow `CI/Python 3.12 tests` passed green on PR #55.

## Builder action

Tool 5 Content Discoverer has been fully implemented, verified, and updated to address all planner review findings:
1. In-situ preservation and zero Baserow access guaranteed.
2. WhisperCpp transcription with Metal-to-CPU automatic fallback and explicit silence gap handling.
3. Audio extraction from video files with collision detection and derivative registration (`video_audio_derivatives`).
4. Classification engine for CLASS, KIRTAN_AND_CLASS, KIRTAN, INITIATION, EVENT_OR_FESTIVAL_ADDRESS, HOME_PROGRAM, and UNKNOWN_REVIEW with mantra detection.
5. Selective cutter handoff: only high-confidence multi-part recordings route to Tool 6 (`process_by_tool_6 = True`).
6. Main Tooling Script integration: integrated as Stage 6 in `--workflow all`, directly in `--workflow processing`, and omitted in `--workflow renamer`.
7. Review Portal audio streaming player (`GET /audio/{tracking_id}` with seek helpers) and content discovery review card.

### Resolution of Independent Review Findings (2026-09-23)
- **T5-R-001 (Phase 1 Eligibility Enforced)**: Unregistered files/IDs cannot be processed by Tool 5 or Main Script `processing` workflow without first undergoing Phase 1 registration. Raises `Phase1EligibilityError` or flags `REVIEW_REQUIRED` without running transcription/extraction. Dry runs require registered file or explicit valid tracking ID.
- **T5-R-002 (Read-Only Dry-Run & Complete Cache Binding)**: Dry-run directory creation eliminated. Sidecar cache checks enforce model binary SHA-256 (`model_sha256`), thread configuration, contract/classification version, source type, and derivative fingerprints. Removed trailing whitespace.
- **T5-R-003 (Protect Adjacent Video MP3s)**: Video-to-audio extraction checks recorded derivative hash against on-disk MP3. Raises `AudioExtractionCollisionError` on tampered derivatives or concurrent file appearance during extraction; uses atomic temporary file swap (`.tmp_extract_*` via `os.replace`).
- **T5-R-004 (Evidence-Backed Human Tool 6 Routing)**: Review portal human decisions for `KIRTAN_AND_CLASS` or `INITIATION` require verified coarse bracket boundaries. Missing or invalid boundaries keep `process_by_tool_6 = False` and review open (`review_required = True`). Persisted SQLite row and `result_json` stay synchronized.
- **T5-R-005 (Video Source Provenance in Transcript Sidecars)**: Video transcripts explicitly record `source_type="video"`, original video path, original video SHA-256, and full `derived_mp3_details` (including derived MP3 SHA-256). Transcript cache reuse is invalidated if either the source video or the derived MP3 changes.

## Open questions / contradictions

None. All review findings T5-R-001 through T5-R-005 have been resolved and verified with dedicated test coverage. Later Tool 6 planning will define actual cutting and its final consumption of `process_by_tool_6`.

## Independent planner review - 2026-09-23

PR #55 CI is green and the local full suite passes (460 tests, 2 warnings). Findings T5-R-001 through T5-R-005 have been addressed:

1. **T5-R-001 — Enforce Phase 1 eligibility.** [RESOLVED]
2. **T5-R-002 — Make dry-run truly read-only and cache binding complete.** [RESOLVED]
3. **T5-R-003 — Protect adjacent video MP3s.** [RESOLVED]
4. **T5-R-004 — Keep human Tool 6 routing evidence-backed.** [RESOLVED]
5. **T5-R-005 — Preserve video source provenance in transcript sidecars.** [RESOLVED]

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
