# Tool 6 — File Cutter Status

Build plan: `docs/tool-6-file-cutter-build-plan.md`
Cross-tool workflow: `docs/full-pipeline-workflow-amendment.md`
Tool 5 handoff: `docs/tool-5-content-discoverer-build-plan.md`

## Current state

Status: `CHANGES_REQUESTED`

## Final acceptance blocker — 2026-09-24

The 497-test local suite and PR #65 CI pass. The three preceding corrections
are substantially present. One core Tool 6 requirement is still unmet:

### T6-R-009 — Split kirtan row loses confirmed media metadata

`file_cutter/service.py` creates the singing `MediaDbSyncRequest` with
`what_category="Kirtan"` and `what_val`, but omits `what_state`, `when_state`,
date, and location evidence. Tool 4 deliberately excludes these fields when
their resolution states are absent. A direct call to its real
`plan_and_revalidate()` with Tool 6's request produces a CREATE with fallback
filename Title but **no Category or Date**. Preserve and pass the confirmed
Tool 1/5 metadata and eligibility states for both split successors, without
inventing unknown values. Verify the actual Tool 4 field diffs (not just
CREATE/UPDATE operation types) and retry path: the kirtan row must have its
Kirtan Category and known date/location, while the existing class row retains
its correct class WHAT and provenance. Then commit/push and mark ready again.

No live media or Baserow writes were made in this review. PR #65 remains open.

## Resolution of planner review findings (T6-R-006 through T6-R-008) — 2026-09-24

All 3 findings raised in the 2026-09-24 planner re-review have been addressed, verified, and backed by dedicated regression tests:

- **T6-R-006 (Bounded Whisper Excerpt Slicing)**:
  - `WhisperCppTranscriptionAdapter.transcribe()` now extracts temporary 16 kHz mono WAV slices using ffmpeg for only the targeted `excerpt_windows` and transcribes only those slices with Whisper. The complete audio file is never sent to Whisper in excerpt mode.
  - Parsed speech segment timestamps are precisely offset by the slice window start (`w_start + seg_start`), and continuous timeline silence coverage is maintained across the recording.
  - Verified by `tests/test_file_cutter.py::test_r006_whisper_excerpt_mode_never_sends_full_recording_to_whisper`.

- **T6-R-007 (Strict Acoustic Boundary Verification & Fail-Closed Gating)**:
  - `AcousticBoundaryVerifier.verify_boundary` now restricts candidate acoustic transition analysis strictly to `[max(0.0, coarse_gap_start - 2.0), min(total_duration, coarse_gap_end + 2.0)]` (eliminating the broad 30s expansion).
  - Candidates from ffmpeg `silencedetect` are filtered to ensure silence starts strictly within the transition gap region (`coarse_gap_start - 1.5 <= abs_start <= coarse_gap_end + 1.5`) and duration $\ge 0.3$s. Unrelated internal singing pauses are rejected.
  - Removed coarse text boundary fallback: if no acoustic silence is detected in the transition zone, the verifier fails closed and returns `None`, forcing `MEDIUM` confidence and portal review without auto-cutting.
  - Verified by `tests/test_file_cutter.py::test_r007_acoustic_verifier_rejects_unrelated_silence_and_fails_closed`.

- **T6-R-008 (Tool 4 Request Decisions & Split-Specific Retry Preservation)**:
  - `FileCutterService` now explicitly populates `tool2_decision="EXISTING_MEDIA_MATCH"` and `selected_media_row_id` for the class successor request, populates `tool2_decision="NEW_MEDIA_CANDIDATE"` and `what_category="Kirtan"` for the singing child request, and records `media_db_review` in SQLite for `singing_tracking_id` with `decision="NEW_MEDIA_CANDIDATE"`.
  - `MediaDatabaseUpdaterService.build_sync_request` preserves split-specific metadata (`audio_file_path`, `what_category`, `what_val`, `tool2_decision`, `selected_media_row_id`) from `prior_req` and `file_splits` so that `retry_pending` never loses split metadata.
  - Tool 4 `MediaDatabaseUpdateEngine` plans CREATE for singing and UPDATE for class without default-denying unassociated decisions.
  - Verified by `tests/test_file_cutter.py::test_r008_tool6_tool4_sync_requests_and_retry_metadata_preservation`.

No live media was cut and no Baserow row was changed during this implementation.

## Independent planner re-review — 2026-09-24 [RESOLVED]

## Resolution of planner review findings (T6-R-001 through T6-R-005) — 2026-09-24

All 5 blocking findings raised in the 2026-09-24 planner review have been addressed, verified, and backed by dedicated regression tests:

- **T6-R-001 (Tool 5 acoustic boundary verification & excerpt transcription)**:
  - Tool 5 now executes bounded timeline excerpt transcription via `excerpt_windows` (`[0, 120]`, mid-probe `[0.4*dur, +60]`, end-probe), completely avoiding full-file Whisper runs.
  - Transcript segment endpoints alone now yield only coarse proposals with `confidence="MEDIUM"`, `singing_end_seconds=None`, `process_by_tool_6=False`, and `review_required=True`.
  - Added `AcousticBoundaryVerifier` (`src/media_archive_tooling/content_discoverer/acoustic_verifier.py`) utilizing ffmpeg `silencedetect` analysis to verify exact transition timestamps before assigning `HIGH` confidence.
  - Tool 6 strictly gates on exact numeric cut points with `HIGH` confidence; missing or coarse-only boundaries require review.
  - Verified by `tests/test_file_cutter.py::test_r001_tool5_excerpt_only_and_missing_cut_evidence_blocks`.

- **T6-R-002 (Video container integrity & derivative ownership)**:
  - Tool 6 enforces strict verified ownership of video-derived audio in `video_audio_derivatives` (`source_video_tracking_id`, `source_video_sha256`, and physical `derived_sha256`). Fails closed if missing, mismatched, or unindexed, leaving unrelated adjacent MP3s completely untouched.
  - Retains video container identity and path in registry `current_path`; assigns class MP3 path to `audio_file_path` for Tool 4 sync.
  - Verified by `tests/test_file_cutter.py::test_r002_unowned_adjacent_mp3_survives_untouched`.

- **T6-R-003 (Dry-run immutability, non-combination gating, cut point validation)**:
  - Guarded `save_human_cut_decision` in `cut_file` so dry-run executions are strictly read-only and never write human decisions, split records, or filesystem outputs.
  - Enforced content gating: providing a cut point alone to a pure `CLASS` or non-combination file is rejected (`File classification is not KIRTAN_AND_CLASS; cut point alone cannot approve non-combination recording`).
  - Added strict cut point range validation (`1.0 < cut_point < duration - 1.0` and finite).
  - Verified by `tests/test_file_cutter.py::test_r003_dry_run_immutability_and_non_combination_rejection`.

- **T6-R-004 (Atomic publication, safe rollback, durable lineage before source deletion)**:
  - `_atomic_publish_file` now enforces exclusive no-clobber publication using hard links (`os.link`) and fallback to atomic `os.open(..., os.O_CREAT | os.O_EXCL | os.O_WRONLY)`.
  - `_safe_rollback_output` computes SHA-256 of the output file and unlinks ONLY if it matches the expected hash of the failed attempt.
  - Split lineage (`register_file`, `update_file_status`, `record_file_split`, `save_stage_checkpoint`) is persisted in SQLite registry BEFORE deleting the working audio source.
  - Verified by `tests/test_file_cutter.py::test_r004_exclusive_publication_and_lineage_before_source_deletion`.

- **T6-R-005 (Deterministic naming & durable Tool 4 outbox)**:
  - `derive_split_whats` defaults unknown or missing song titles to `"Kirtan"` rather than inventing specific mantras like "Jaya-radha-madhava".
  - Canonical naming utilizes Tool 1 `RenamePlanner(mode=RenameMode.FINALIZE)` without regex stripping of IDs.
  - When Tool 4 sync fails or is unavailable, records durable `PENDING_SYNC` in `media_db_syncs` for both class and singing records while preserving local split success.
  - Verified by `tests/test_file_cutter.py::test_r005_unknown_song_title_and_tool4_outbox_pending_sync`.

## Independent planner review of PR #65 — 2026-09-24 [RESOLVED]

The pushed head `c8b5fb4` is on PR #65. Required GitHub CI passes; an
independent hermetic run from a writable temporary directory passed 47 focused
Tool 5/6 tests and 489 full-suite tests. The existing tests do not cover the
blocking paths below. No archive original or live Baserow row was changed.

Treat this correction pass as the same persistent `BUILD TOOL 6` `/goal`:
continue until these blockers and the original acceptance criteria are met,
tested, committed/pushed, and re-reviewed. Do not stop at a partial test pass
or try live archive/Baserow writes as a workaround.

### T6-R-001 — Pre-cut full transcript and unverified automatic boundary

`content_discoverer/service.py` still calls the full transcription adapter
before classification; `classifier.py` makes a `HIGH` exact boundary from
transcript segment endpoints; `file_cutter/service.py` can fall back from a
missing `singing_end_seconds` to the coarse kirtan range. This contradicts
the revised Tool 5/6/7 plans and risks cutting meaningful audio. Implement
bounded whole-timeline acoustic analysis with only short targeted excerpts,
verify the precise end of singing from local audio, and **never** auto-cut
from a coarse bracket or text segment edge alone. Test that Tool 5 does not
run full-file transcription and that missing/weak exact-cut evidence blocks.

### T6-R-002 — Unowned video audio and wrong retained-video identity

`file_cutter/service.py` merely warns when the adjacent video-derived MP3 is
not in the derivative registry, then later deletes it. It also updates the
video source tracking record's `current_path` to the class MP3, although the
owner requires the retained video to remain the row's Filename/archive path
and the class MP3 to use `audio_file_path`. Require verified ownership and
source/derivative fingerprints before cutting or deleting an MP3; preserve
the video identity/path and track the class audio separately. Add a regression
with an unrelated adjacent MP3 that must survive untouched.

### T6-R-003 — Dry-run/manual cut bypasses approval state

`cut_file(..., dry_run=True, cut_point_override=...)` calls
`save_human_cut_decision` before the dry-run branch or cut-point validation.
The portal POST passes a slider value straight into this live path. A cut
point alone must not approve a non-combination file for a two-part cut.
Separate preview, audited approval, and execution; make dry-run completely
read-only; validate source hash, content type, cut point and reviewer decision
before persisting or cutting. Test registry immutability with a dry-run
override and refusal of a class/kirtan-only source given only a cut point.

### T6-R-004 — Publication/recovery can clobber or lose lineage

`_atomic_publish_file` falls back to `shutil.move` after its existence check,
which can overwrite a concurrently created target. Rollback unlinks output
paths without proving they still contain this attempt's bytes. The source is
deleted before durable split lineage/checkpoint writes; interruption there
leaves outputs with an obsolete registry path and no completed split record.
Use exclusive no-clobber publication and ownership-checked rollback; persist
a recoverable split state before deleting the source. Test target races and
interruption after output publication but before lineage completion.

### T6-R-005 — Tool 1/4 handoff invents metadata or loses pending sync

`derive_split_whats` defaults an unknown singing title to
`Jaya-radha-madhava`; `plan_output_filenames` strips Tool 1's ID suffix by
regex, and the service writes registry paths directly rather than using the
accepted Tool 1 commit boundary. This can invent a mantra or bypass naming
collision/commit safeguards. On a Tool 4 exception, the service only logs a
warning and returns a successful split without durable pending-sync state.
Use confirmed singing metadata or route naming to review, keep Tool 1 the
canonical naming/commit owner, and persist Tool 4 failure for retry without
rolling back a verified local split. Test unknown song, collision, and
class/singing Tool 4 failure paths.

These are correction findings against the existing plan, not a request for
new Tool 6 features. Keep the video and input files intact whenever safety
cannot be proven. Update the relevant regression tests and rerun focused,
full, package, and CI checks before `READY_FOR_REVIEW`.

Tool 6 (File Cutter) has been implemented and verified.
Automatic cutting is supported for high-confidence `KIRTAN_AND_CLASS` recordings
at the exact boundary timestamp provided by Tool 5.
Gating safely routes `INITIATION` and `VYASA_PUJA` to human review without modifying files.
Output formats and container codecs are preserved (`wmav2` for WMA, `libmp3lame` for MP3).
Video inputs remain untouched; owned extracted MP3 derivatives are split and cleaned up.
Leading silence is trimmed conservatively via `silencedetect` (threshold -40dB, min 0.5s).
Outputs are canonically named via Tool 1 planner and remain in-place pending Tool 11 category move.
Class output inherits source tracking ID and Media row; singing output receives a new tracking ID and separate Kirtan row.
Tool 4 synchronization updates class row (writing `audio_file_path` for video sources) and creates singing row.
Review portal provides interactive waveform, seekable playback with on-demand WMA-to-MP3 transcoding, and manual cut adjustment.
CLI `media-archive cut` enables standalone execution and dry-run inspection.

## Review checkpoint

Last planning/review commit: `66854fe` (planning PR #61)
Current implementation HEAD: `3a84504` (PR #65)
Fundamental-change review pending: no
Relevant commits since last review: `45e57c1`, `be629b9`, `513c873`, `3a84504`

## Open questions / contradictions

### Q-001 — Baserow treatment of retained video and class MP3

Status: RESOLVED
Build-plan section(s): 6
Blocking scope: Video-derived Tool 4 field mapping, not local audio cutting.

Problem: The original video remains after Tool 6 splits its Tool 5 extracted
MP3, but the Media row has one `Filename` and one `media_archive_path`.
The owner reports a separate audio-file field; the existing Tool 4 schema
currently describes `Audio link` as a URL, not a local archive-path field.

Evidence: `docs/tool-4-media-database-updater-build-plan.md` Sections 8 and 9;
the current fake schema types `Audio link` as `url`.

Why this matters: Assigning a local MP3 path to a URL field or overwriting a
confirmed video path would violate Tool 4's existing safety rules.

Possible interpretations:
1. A different, precisely named archive-audio field exists and should be used.
2. `Audio link` receives an approved shareable URL later, not a local path.
3. Keep video-derived audio rows pending review until schema/workflow changes.

Resolution: The owner specified the exact column `audio_file_path`, containing
the full local filesystem path of the class MP3. The same row's `Filename`
and `media_archive_path` remain associated with the retained video. The
singing MP3 receives a distinct row. Tool 4 must validate the live column
before use and block its field write safely if the schema differs.

Implementation action: Follow plan Section 6. No further policy choice is
needed for this first-build video mapping.

## Builder action

After the transcription-boundary planning PR is merged, start via
`./scripts/builder-start.sh 6`, implement on `tool-6-implementation`, and
follow `BUILDER.md` for status, tests, commits, push, PR, and CI. Tool 5's
exact-cut handoff and Vyasa-puja recognition amendment are included in this
build; do not treat the accepted coarse-bracket implementation as sufficient.

## Progress log

- 2026-09-23 — Owner clarified exact automatic cut, source-format outputs,
  deferred Tool 11 move, two-part-only first cutter, conservative silence
  handling (preserve instrumental/spoken lead-ins), and interactive portal
  waveform/playback. Planning was prepared in an isolated worktree while Main
  Script implementation remained dirty.
- 2026-09-23 — Build plan and cross-tool amendments committed as `66854fe`,
  pushed to `planner/tool-6-build-plan`, and opened as PR #61. No media or
  Baserow data was changed.
- 2026-09-24 — Implemented Tool 6 (File Cutter) in `src/media_archive_tooling/file_cutter/`:
  - `AudioCutter`: format preservation (`wmav2`, `libmp3lame`), conservative leading-silence trimming via `silencedetect`, verify audio streams, atomic publication, rollback on failure.
  - `WaveformGenerator`: 500-sample normalized peaks calculation bound to SHA-256 hash, cached on-demand WMA-to-MP3 transcoding for browser playback.
  - `FileCutterService`: exact boundary cut point handoff from Tool 5, gating `INITIATION` and `VYASA_PUJA`, Tool 1 canonical naming for split outputs, lineage persistence in `file_splits`, Tool 4 Media DB synchronization (including `audio_file_path` for retained videos), and dry-run simulation.
  - Review portal: interactive waveform display, seekable audio playback with on-demand transcoding, and manual cut adjustment endpoint.
  - Orchestrator: integrated Tool 6 execution into `WorkflowType.ALL` and `WorkflowType.PROCESSING`.
  - CLI: registered `media-archive cut` with `--dry-run`, `--cut-point`, and `--json`.
  - Test evidence: `tests/test_file_cutter.py` (10 passed), full test suite (489 passed).
  - Pushed to `tool-6-implementation` and opened PR #65.
- 2026-09-24 — Resolved all 5 blocking planner review findings (T6-R-001 through T6-R-005):
  - T6-R-001: Excerpt-only timeline transcription in Tool 5; local acoustic boundary verifier required before setting HIGH confidence and exact cut points.
  - T6-R-002: Enforced derivative ownership verification; retained video path in current_path; video class MP3 mapped to audio_file_path for Tool 4 sync.
  - T6-R-003: Guaranteed dry-run immutability; rejected non-combination cut attempts; added cut point boundary validation.
  - T6-R-004: Hard-link / atomic no-clobber publication; hash-checked safe rollback; persisted SQLite split lineage before deleting source audio.
  - T6-R-005: Canonical finalize naming with Kirtan fallback; durable PENDING_SYNC outbox on Tool 4 failure.
  - Test evidence: `tests/test_file_cutter.py` (15 passed), `tests/test_content_discoverer.py` (37 passed), full suite (494 passed).
  - Marked status `READY_FOR_REVIEW`.
