# Tool 6 — File Cutter Status

Build plan: `docs/tool-6-file-cutter-build-plan.md`
Cross-tool workflow: `docs/full-pipeline-workflow-amendment.md`
Tool 5 handoff: `docs/tool-5-content-discoverer-build-plan.md`

## Current state

Status: `READY_FOR_REVIEW`

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
