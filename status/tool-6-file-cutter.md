# Tool 6 — File Cutter Status

Build plan: `docs/tool-6-file-cutter-build-plan.md`
Cross-tool workflow: `docs/full-pipeline-workflow-amendment.md`
Tool 5 handoff: `docs/tool-5-content-discoverer-build-plan.md`

## Current state

Status: `NOT_STARTED`

The Main Script PR #60 was accepted and merged on 2026-09-24. Tool 6 may now
start after the Tool 5/7 transcription-boundary plan revision is on `main`.
Its build includes converting Tool 5 from full transcription to bounded
analysis/short excerpts, then cutting from verified audio evidence. Tool 6
must not require or split a pre-cut full transcript. Tool 7 later transcribes
the class child; the singing child skips full transcription. Read the revised
Tool 5 and Tool 7 plans before implementation.

The finalized plan specifies automatic high-confidence kirtan/class cutting
at the exact end-of-singing timestamp provided by Tool 5; conservative leading-
silence trimming; source-format audio outputs; Tool 1 naming; Tool 4 class-row
update and distinct singing-row synchronization; in-place outputs pending
Tool 11; and a portal waveform/player/adjustable cut point for flagged cases.
The original full-length working audio is removed only after both outputs are
verified. A video source remains; its owned full-length extracted MP3 is
removed after successful splitting into two MP3s. The video class row keeps
its `Filename` and `media_archive_path`; Tool 4 writes the class MP3's full
local path to `audio_file_path`, validating that column first. The singing
MP3 has its own row.

## Review checkpoint

Last planning/review commit: `66854fe` (planning PR #61)
Current implementation HEAD: none
Fundamental-change review pending: no
Relevant commits since last review: none

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
