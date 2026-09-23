# Tool 5 - Content Discoverer Implementation Status

Build plan: `docs/tool-5-content-discoverer-build-plan.md`
Project architecture: `docs/project-implementation-architecture.md`
Main Script plan: `docs/main-tooling-script-build-plan.md`
Alpha/beta purge policy: `docs/alpha-beta-test-data-purge-build-plan.md`
Recording references: `assets/verse-structure.md`, `assets/original-and-edited-recording-structure.md`

## Current state

Status: `ACCEPTED`

Downstream coordination (2026-09-23): The user-confirmed future sequence is
`docs/full-pipeline-workflow-amendment.md`. Tool 5 itself continues to
transcribe/classify in situ without deleting its input. Later Tool 6, only
after a confirmed combination route, replaces that working input with class
and singing outputs. The original full-length working file is not retained
after a successful split. The singing output gets a distinct Tool 4 Media row;
the class output keeps the existing class row. Final Tool 1 renaming precedes
Tool 11 moving and Tool 4 final updates. The exact cutting rules still need
a separate Tool 6 plan.

Implementation branch: `tool-5-implementation`
Implementation PR: https://github.com/KadambaFoundationMedia/media-archive-tooling/pull/55

## Planner-authored maintenance / coordination - 2026-09-23

Local alpha/beta reset after the user's sample-folder restore: the review portal was stopped, and the ignored `.renamer` runtime registry databases, reference caches, 29 transcript sidecars (including the WMA transcript cache), and 272 generated Tool 4 evaluation-media copies were deleted. Existing logs, the travel reference, and the runtime virtual environment were retained. The main registry had zero tracked files and zero test-row ledger entries immediately before deletion, so no Baserow rows were deleted. A future Tool 5 run must transcribe the restored WMA again rather than reuse the previous sidecar. This is a planner operational handoff; it does not change Tool 5 source code or specifications.

PR [#57](https://github.com/KadambaFoundationMedia/media-archive-tooling/pull/57), code commit `d558163`, adds bounded Tool 5 conversion/transcription heartbeats (every 5/30 seconds respectively), cache-hit notice, and per-stage elapsed times in transcript provenance. The Main Script and standalone human-readable CLI now show this progress; JSON CLI output remains machine-readable. Captured subprocess output remains off the terminal, with failures reported by the existing error path. The builder must preserve the optional progress callback when extending Tool 5 or adding later audio stages.

Measured on the user's 6,217-second WMA: FFmpeg WMA-to-16-kHz mono WAV decode took 3.12 seconds; the existing Metal transcription took about 185 seconds (about 33× realtime for the complete recording). Cached transcript reuse took 1.57 seconds. Conversion is not the bottleneck; no accuracy-reducing model/decoding settings were changed. Full local suite after this maintenance: 469 passed, 2 dependency warnings.

PR [#56](https://github.com/KadambaFoundationMedia/media-archive-tooling/pull/56), commit `b0cf379`, corrects a real Tool 5 transcription failure on the user's WMA sample. This Homebrew `whisper-cli` supports FLAC/MP3/OGG/WAV but not WMA; Tool 5 now decodes unsupported audio formats with FFmpeg to a temporary 16 kHz mono WAV before transcription. The archive input remains unchanged, and the temporary WAV is removed after the call. `TranscriptArtifact.input_path` and source SHA-256 still refer to the original media; `raw_metadata.audio_preprocessing` records the temporary decoder step. No Baserow or registry schema is changed. The Builder must preserve this input-adaptation behavior and provenance when continuing Tool 5 or related audio tools.

Verification: full local suite 466 passed; a 12-second excerpt from the reported WMA file successfully transcribed on Metal with temporary WAV preprocessing. The full 6,217-second WMA also completed through `ContentDiscovererService.discover_content(..., dry_run=True)`: `KIRTAN_AND_CLASS`, `HIGH`, `review_required=False`, transcript SHA-256 `866d1b6cc538262f701aff3662a49f78a7cb6cbe6c014871d0a9a9dddd9e5919`. No source file, registry content review, or Baserow row was changed by the dry-run test. The temporary excerpt was removed afterward.

## Verification Summary
- Test Suite: 463/463 passed across repository.
- Dedicated Tool 5 Test Suite: 33/33 passed in `tests/test_content_discoverer.py`.
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
- **T5-R-001 (Phase 1 Eligibility Enforced)**: Unregistered files/IDs cannot be processed by Tool 5 or Main Script `processing` workflow without first undergoing Phase 1 registration. Raises `Phase1EligibilityError` or flags `REVIEW_REQUIRED` without running transcription/extraction. Bare service or CLI dry runs require an existing registry record or explicit verified `phase1_context` from the current run; arbitrary untracked IDs are rejected.
- **T5-R-002 (Read-Only Dry-Run & Complete Cache Binding)**: Dry-run directory creation eliminated. Sidecar cache checks enforce model binary SHA-256 (`model_sha256`), thread configuration, contract/classification version, source type, and derivative fingerprints. Removed trailing whitespace.
- **T5-R-003 (Protect Adjacent Video MP3s)**: Video-to-audio extraction checks recorded derivative hash against on-disk MP3. Raises `AudioExtractionCollisionError` on tampered derivatives or concurrent file appearance during extraction; uses atomic no-clobber finalization (`os.link`) so an intruder file appearing at the finalization point survives byte-for-byte while temporary files are safely cleaned up.
- **T5-R-004 (Evidence-Backed Human Tool 6 Routing)**: Review portal human decisions for `KIRTAN_AND_CLASS` or `INITIATION` require verified coarse bracket boundaries. Parser rejects impossible clock fields (e.g. `>= 60` seconds/minutes) or out-of-order timestamps, and validates the entire bracket against actual media duration (`duration_seconds`). Short recordings reject out-of-range boundaries, keeping `process_by_tool_6 = False` and review open (`review_required = True`). Persisted SQLite row and `result_json` stay synchronized.
- **T5-R-005 (Video Source Provenance in Transcript Sidecars)**: Video transcripts explicitly record `source_type="video"`, original video path, original video SHA-256, and full `derived_mp3_details` (including derived MP3 SHA-256). Transcript cache reuse is invalidated if either the source video or the derived MP3 changes.

## Open questions / contradictions

All residual safety findings have been resolved. Later Tool 6 planning will define actual cutting and its final consumption of `process_by_tool_6`.

## Independent planner review - 2026-09-23

PR #55 CI is green and the local full suite passes (463 tests, 2 warnings). Findings T5-R-001 through T5-R-005 have all been addressed and verified:

1. **T5-R-001 — Enforce Phase 1 eligibility.** [RESOLVED]
2. **T5-R-002 — Make dry-run truly read-only and cache binding complete.** [RESOLVED]
3. **T5-R-003 — Protect adjacent video MP3s.** [RESOLVED]
4. **T5-R-004 — Keep human Tool 6 routing evidence-backed.** [RESOLVED]
5. **T5-R-005 — Preserve video source provenance in transcript sidecars.** [RESOLVED]

### Focused follow-up for the builder (2026-09-23) — Completed

- **T5-R-001:** `content_discoverer/service.py` requires actual Phase 1 context (`phase1_context`) from the current Main Script run, or an existing registry record. A bare unregistered service/CLI dry run cannot claim Phase 1 eligibility (negative test added; legitimate Main Script `all --dry-run` handoff verified).
- **T5-R-003:** `audio_extractor.py` finalizes with atomic no-clobber `os.link` and cleans up only the temporary file on collision. Intruder file test verified byte-for-byte preservation.
- **T5-R-004:** `apply_human_decision` and `validate_coarse_boundary` enforce real clock fields, ordered timestamps, and boundary verification against media duration (`duration_seconds`). Short-recording portal-action test added and verified.

All focused corrections were verified locally and in CI before planner acceptance below.

### Planner acceptance and final hardening - 2026-09-23

The planner independently reran the full suite after commit `3bf4772`: 464 tests passed (2 dependency warnings); script syntax and `git diff --check` passed; GitHub Actions CI passed on that commit. The planner made and pushed two small safety changes: no overwrite-capable fallback if atomic hard-link finalization is unavailable, and a matching typed `RenameProposal` requirement for an unregistered Phase 1 dry-run handoff. Regression tests cover both. The separate builder should retain these changes in future Tool 5 work. Tool 5 is accepted for merge; actual cutting remains Tool 6's responsibility.

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
