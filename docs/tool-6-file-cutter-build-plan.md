# Tool 6 — File Cutter Build Plan

Status: **FINALIZED — implementation-ready**

This plan incorporates the owner's Tool 6 decisions of 2026-09-23. The
Builder must read `BUILDER.md`, `docs/full-pipeline-workflow-amendment.md`,
`docs/tool-5-content-discoverer-build-plan.md`,
`docs/tool-7-class-type-discoverer-build-plan.md`,
`docs/tool-4-media-database-updater-build-plan.md`,
`assets/file-naming-convention.md`,
`assets/original-and-edited-recording-structure.md`, and
`status/tool-6-file-cutter.md` before implementation. The Tool 5 handoff
amendment in its plan is part of this build; the existing coarse-range-only
implementation does **not** yet satisfy this contract.

## 1. Purpose and first-build scope

Tool 6 replaces one confirmed `KIRTAN_AND_CLASS` working audio recording with
exactly two audio recordings: the singing portion and the class portion. The
archive is backed up independently. After a successful, verified split the
full-length working audio input is removed; Tool 6 must not retain a permanent
third copy. If any step before verified publication fails, retain the input.

The first build handles only this two-part content type. Tool 5 may recognize
initiation ceremonies and Vyasa-puja recordings, but Tool 6 must not cut them
using a two-part rule. They remain in place and are flagged for review/future
multi-part specifications. Do not infer their boundaries from the example
recording structure or invent their output categories.

Tool 6 owns local cutting and conservative leading-silence trimming. Tool 5
owns discovery of the **recording-specific, exact timestamp at which singing
ends**. Tool 1 owns both canonical output names and any committed renames.
Tool 4 is the only Baserow writer; it reuses the class item and creates or
matches a distinct singing item under its existing safety rules. The main
script orchestrates these service calls rather than duplicating their logic.

## 2. Tool 5 exact-cut handoff

The Tool 5 plan and implementation currently provide only a coarse gap
bracket. This build must upgrade the handoff to a typed, numeric
`singing_end_seconds` (or equivalent) with source duration/hash, timestamped
acoustic and any short-excerpt evidence, method/version, and confidence. This is a single
recording-specific cut point, not the timestamps from the illustrative asset.
Use the local audio around the transition as well as any targeted speech
evidence; text-only mention of a mantra or a convenient short-excerpt boundary
is not, by itself, evidence of the exact end of singing. Tool 5 must **not**
fully transcribe the combination before cutting. If Tool 5 cannot locate
that point reliably, it must not mark it high-confidence or authorize an
automatic cut. Preserve the coarse bracket as diagnostic evidence if useful,
but it is not a substitute for the exact numeric value.

Automatic Tool 6 cutting is allowed only when Tool 5 has confirmed
`KIRTAN_AND_CLASS`, its cut point is `HIGH` confidence, the point lies strictly
inside the verified source duration, the input/analysis fingerprints still
match, and there are no contradictory or pending human-review decisions. A
reviewer may instead confirm the content type and set/adjust a cut point in
the portal; record that as an audited human decision bound to the same source
fingerprint. A changed source invalidates both automatic and approved points.
Lower confidence, missing evidence, ambiguous transition, or absent source
stays intact and enters the Tool 6 review queue.

Tool 5 must also retain the owner's general recording patterns as recognition
guidance: standalone class; kirtan followed by class; initiation ceremony
(singing, introduction, class, name giving, mantra, singing); and Vyasa-puja
(singing, introduction, offerings, speaker address, singing). These patterns
are not fixed timestamps, and initiation/Vyasa-puja must not be auto-routed to
this two-part cutter.

## 3. Input, media format, and cut behavior

Operate on the current registered Phase 1/Tool 5 working file in place. Reuse
Tool 5's bounded timed evidence and source identity; do not fully transcribe
just to cut. For an audio input, both outputs keep the source's container/extension
(lowercase): a WMA yields two `.wma` files, an MP3 yields two `.mp3` files.
Do not silently convert every output to MP3. Select a compatible, high-quality
local FFmpeg encoding/cutting mode where exact boundaries require decoding and
re-encoding; record the command/codec/quality and verify the resulting files.

For a video source, Tool 5's registered adjacent extracted MP3 is the audio
input to Tool 6. Produce two MP3 audio outputs. Preserve the original video.
Remove the **owned full-length extracted MP3** only after both outputs are
published and verified; never remove an unrelated adjacent MP3. The Baserow
path/row treatment for retained video versus its new audio outputs is governed
by Section 6 below.

Cut at Tool 5's exact end-of-singing timestamp. The singing part covers the
start of the audio through that point; the class part starts at that point and
runs to the end. Detect and remove only **actual leading silence** from each
part. The owner confirmed that instrumental music and speech before the first
vocal should be **preserved**, not treated as silence. Do not use “no singing”
as permission to delete spoken introductions, prayers, soft speech, musical
lead-ins, ambient but meaningful content, or a class opening.
`assets/original-and-edited-recording-structure.md` describes
one recording and later desired edits; it is not a universal cutting schedule.
Tool 6 must not perform Tool 8's internal class edits. Record how much leading
silence was removed from each output. If a clean trim cannot be distinguished
from meaningful audio, retain the audio and flag the trim for review rather
than delete uncertain content.

Before live cutting, inspect duration, format/decoder support, source hash,
available disk space, and the proposed output names. Dry-run shows the cut
point, proposed names/paths, format, trim estimate (when reliably available),
review reason, and projected Tool 4 actions without writing media, registry
state, scratch, or Baserow data.

## 4. Names, locations, and tracking identity

Tool 6 gives the new parts' content metadata to Tool 1. Tool 1 alone renders
and commits valid filenames under `assets/file-naming-convention.md`: date,
speaker, separate WHAT, place, country, lowercase extension, no spaces or
forbidden punctuation. The class WHAT no longer contains the singing suffix;
the singing WHAT uses the confirmed mantra/kirtan title. For the corrected
Oslo example, the relevant forms are:

```text
2011-08-29_KKS_Jaya-radha-madhava_Oslo-no.wma
2011-08-29_KKS_SB-1-19-31_Oslo-no.wma
```

These are illustrative names, not a hard-coded parse result or substitute for
Tool 1's current naming policy. Apply Tool 1's accepted partial-date/location
rules: a still-incomplete WHEN/WHERE alone does not block a split when Tool 1
can safely render two distinct names. If the song/class identity or another
required naming decision remains unsafe, keep the input and send it to review
instead of inventing a title or second file identity. Preflight both output
paths. Never overwrite existing files, including case-insensitive collisions.

The new class output is the successor of the tracked combination input and
inherits its durable class lineage/Tool 4 row association where one exists.
The singing output has a distinct tracking ID and lineage back to the same
source. Persist the one-to-two mapping, original filename/path, source SHA-256,
cut point, trim offsets, output paths/hashes/durations, tool version, and
related Tool 5 evidence IDs. A repeated run must find the same completed split,
not split a child again or create another singing item.

Until Tool 11 has its own approved implementation, **both results stay beside
the source**. Record a durable `pending_tool_11_move` for the singing part (and
later applicable class move). Tool 6 must not invent the default destination
or implement a private organizer. Once Tool 11 is available, the main script
calls it using the latest Tool 1 names and WHAT/category metadata, then Tool 4
synchronizes final paths.

## 5. Safe publication, interruption, and Tool 7 handoff

Stage only the two outputs for the current file within a bounded scratch
budget on the same filesystem when possible. Verify both are decodable,
non-empty, have plausible durations relative to the cut and trims, and have
recorded hashes. Publish without clobbering either target. Only after both
outputs have their committed Tool 1 names, registry identities, and verified
durable content may the full-length audio source be removed. If publication
of the second output fails, roll back only an unchanged output owned by this
attempt and retain the original. A crash must
leave a recoverable manifest/state so retry can distinguish staged, partially
published, and completed splits without deleting unrelated files. No archive-
wide copy and no deletion based merely on matching filenames.

Associate Tool 5's bounded analysis and source provenance with both child
identities, mapping only relevant timed evidence to each output after the cut
and leading trim. Preserve an excerpt spanning the cut as ambiguous evidence;
do not call it a full transcript or silently assign it to both children. Tool
6 does **not** create child transcript sidecars. The verified class output
enters Tool 7 for its first complete transcript; the singing output skips
Tool 7. Tool 11 later moves the class transcript to its approved category
destination. Dry-run creates no evidence derivative.

## 6. Tool 4 synchronization and video-derived audio path

After a successful local split and Tool 1 naming, Tool 4 receives the class
successor and singing child as distinct identities. For an audio source, the
class retains the existing Media row if Phase 1 created/matched one; Tool 4
updates its committed filename/path/metadata. Tool 4 uses Tool 2's read-only
matching to find an existing singing row or creates one only when genuinely
new. If no class row was created in Phase 1, apply normal Tool 4 matching and
review gates; never fabricate a row ID. Preserve confirmed existing fields,
links, and duplicate protections. Failed/uncertain Baserow sync is durable
pending/review work and does not undo a verified local split.

For a retained original video, keep the existing class row's `Filename` and
`media_archive_path` identifying that video. Write the class MP3's **full
local filesystem path** to the separate `audio_file_path` column through Tool
4. Tool 4 must discover and validate the live column and its compatible value
type before writing; if it is absent or incompatible, preserve the video row
and leave the class-audio path pending review/sync rather than writing to
`Audio link` (a URL field) or overwriting the video path. When Tool 11 later
moves the class MP3, Tool 4 updates `audio_file_path` to its committed new
path. The singing MP3 is a separate logical Media item: Tool 4 matches or
creates its own row with its own `Filename` and `media_archive_path`. Do not
use the class row's `audio_file_path` for the singing part.

## 7. Review portal and CLI

Expose Tool 6 from a reusable service called by the main script and review
portal. Integrate it into `all`/`processing` after Tool 5 when available;
`renamer` remains Tools 1–4. Do not invoke Tool 11 until its accepted service
exists. Provide a focused CLI entry for one registered file and a read-only
`--dry-run` path for practical testing. Keep terminal output concise; put
technical FFmpeg/evidence detail in the existing single structured log. Show a
bounded progress indication or heartbeat during a long encode, as with Tool 5.

For every Tool 6-flagged case, the portal detail page must show the audio
waveform with a marked proposed cut point, time labels, a slider to move the
point precisely, and an embedded player that can seek/play before and after
it. Show confidence, why the file was flagged, source duration, current
selected time, and the projected two filenames. The reviewer can save an
approved corrected point and retry the split from the same page. Validate
the selected time against the source duration and fingerprint; audit reviewer,
old/new value, and time. Never cut from a slider movement alone. Show the two
resulting files and Tool 4 sync states after execution.

Generate a bounded waveform peak summary from the current registered source
or Tool 5 MP3 derivative. Avoid loading a whole recording into browser memory,
copying the archive file, or exposing arbitrary local paths. Existing portal
audio streaming may be reused, but WMA and other browser-unsupported formats
need a bounded on-demand playable preview/segment transcode so the owner can
actually listen. The portal stays loopback-only and does not auto-play. High-
confidence automatic cuts need no portal approval; the portal is the repair
path for flagged/uncertain cases.

## 8. Verification and acceptance

Add hermetic tests with fake Tool 1, Tool 4, FFmpeg, and registry boundaries
where appropriate. Cover at least:

1. Tool 5 provides an exact numeric singing-end timestamp and confidence,
   not merely a coarse range; low/ambiguous cases cannot auto-cut.
2. Audio outputs retain the input format (`.wma` to `.wma`), while a video-
   derived MP3 yields MP3 parts and the video remains untouched.
3. Singing and class outputs receive distinct Tool 1 names, identities,
   bounded evidence mappings, and row associations; only the class output
   proceeds to Tool 7 full transcription.
4. Only actual leading silence is trimmed; spoken class introductions are
   retained; a recording-specific example time is never hard-coded.
5. Exact cut, output-duration/decodability validation, source-change check,
   disk preflight, collision refusal, second-output failure, interruption,
   idempotent retry, and no permanent third audio copy after success.
6. Dry-run changes no archive file, registry, scratch, transcript, or Baserow
   state; Tool 4 is the sole Baserow writer and preserves unrelated fields.
7. Existing class row is updated, singing row is separate, and Tool 4 failure
   leaves durable pending sync without reversing the local split; for video,
   the video filename/path are preserved while `audio_file_path` follows the
   class MP3, with missing/wrong schema blocked safely.
8. Portal waveform, slider validation/audited approval, seekable playback
   including WMA preview, and safe retry; no arbitrary-path streaming.
9. Initiation/Vyasa-puja are not fed to this two-part cutter; Tool 11 move
   remains pending rather than being guessed.

Run focused and full pytest suites, package build, script checks, and required
GitHub CI. Use fake Baserow for automated tests. A practical smoke test should
first be a dry-run on the restored Oslo sample and, for destructive cut
verification, a **bounded copy of that one file in a temporary test folder**;
do not live-cut the owner's archive original or write live Baserow during
Builder verification. Record concise before/after duration, naming, hash,
and row-action evidence in `status/tool-6-file-cutter.md`.

The Builder works only on `tool-6-implementation`, commits and pushes all
changes, opens/updates a PR, and reports `READY_FOR_REVIEW` only after the
required evidence is complete. Planning/review independently checks the diff,
CI, practical behavior, and Tool 4 path/row safety before accepting/merging.
