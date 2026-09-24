# Tool 6 — File Cutter Status

Build plan: `docs/tool-6-file-cutter-build-plan.md`
Cross-tool workflow: `docs/full-pipeline-workflow-amendment.md`
Tool 5 handoff: `docs/tool-5-content-discoverer-build-plan.md`

## Current state

Status: `CHANGES_REQUESTED`

## Independent planner review of PR #65 — 2026-09-24

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
