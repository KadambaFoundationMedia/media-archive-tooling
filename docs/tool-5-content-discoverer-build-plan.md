# Tool 5 - Content Discoverer Build Plan

Status: **FINALIZED — revised Tool 5/7 transcription handoff (2026-09-24)**

This document is the authoritative implementation specification for Tool 5.
Progress, questions, review findings, verification evidence, and commit
checkpoints belong in `status/tool-5-content-discoverer.md`. The Builder must
not change this finalized plan to fit an implementation.

Required project context:

- `BUILDER.md`
- `docs/project-implementation-architecture.md`
- `docs/main-tooling-script-build-plan.md`
- `docs/alpha-beta-test-data-purge-build-plan.md`
- `docs/baserow-access-boundary-amendment.md`
- `docs/full-pipeline-workflow-amendment.md`
- `docs/tool-7-class-type-discoverer-build-plan.md`
- `assets/verse-structure.md`
- `assets/original-and-edited-recording-structure.md`
- accepted Tool 1-4 statuses and implementations

---

## 1. Purpose and scope

Tool 5 is the local **Content Discoverer**. It analyzes the actual audio of a
Phase 1 archive file to establish its broad content type, identify any opening
mantra/kirtan, locate a trustworthy singing-end cut point when applicable,
and record the next processing route. The owner moved **full transcription**
to Tool 7, after Tool 6's optional cut. Tool 5 may use audio/acoustic features
and short, targeted local transcription excerpts as evidence, but must not
transcribe the whole recording as its normal classification step.

It exists because filename, folder, Media database, and travel-schedule
evidence can still leave the nature of a recording unknown. Tool 5 supplies
new audio-derived evidence for a later Tool 1 final enrichment/rename and Tool
4 Baserow synchronization at the end of Phase 2.

Tool 5 must classify one of:

```text
CLASS
KIRTAN_AND_CLASS
KIRTAN
INITIATION
VYASA_PUJA
EVENT_OR_FESTIVAL_ADDRESS
HOME_PROGRAM
UNKNOWN_REVIEW
```

It must also separately report the opening/leading mantra type when detected:

```text
Jaya-radha-madhava
Jaya-Jaya-Sri-Caitanya
Nrishmadeva
Kirtan                 # maha-mantra or another singing-only kirtan
UNKNOWN
NONE
```

These two fields are distinct. For example, a `CLASS` may have an opening
`Jaya-radha-madhava`; a singing-only recording may be `KIRTAN` with mantra type
`Kirtan` or `UNKNOWN`.

Tool 5 starts after the Phase 1 Tools 1-4 decision sequence. It includes files
that could not yet be renamed because Phase 1 metadata is incomplete. It never
requires a Phase 1 rename or Baserow row as a precondition.

---

## 2. Boundaries and non-negotiable safety rules

Tool 5 is local, reusable service-layer code. The CLI and review portal must
call that service; neither may duplicate classification logic.

Tool 5 must:

- operate on the original archive file **in situ**;
- preserve the original media file, its filename, and its directory;
- save bounded, timestamped classification/cut evidence outside the archive;
- extract an MP3 beside a video only when video input requires it;
- create no Baserow reads, writes, schema mutations, or deletions;
- never rename a file - Tool 1 remains the canonical renderer and committer;
- never update Baserow - Tool 4 remains the sole writer/deleter;
- never call Tool 6, cut, trim, boost gain, or organize files;
- record review-required uncertainty in the existing local registry/portal;
- fail closed: an unavailable required local analysis runtime, extraction
  failure, unreadable media, or ambiguous classification cannot produce a
  confident type or downstream destructive action. A missing Whisper model
  blocks only decisions that need excerpt transcription; it need not block a
  result independently supported by adequate acoustic evidence.

Tool 5 must preserve the alpha/beta registry/fingerprint rules. Its source and
template changes must participate in review-data invalidation. Tool 5 itself
does not create Baserow test rows and must not bypass Tool 4's purge service.

---

## 3. Pipeline position and routing contract

The illustrative whole-project dependencies are:

```text
Phase 1: Tool 1 -> Tool 2 -> Tool 3 -> Tool 1 final proposal -> Tool 4
Phase 2: Tool 5 -> Tool 6 when a confirmed combination needs cutting
          -> Tool 4 creates/updates the singing item after a cut
          -> Tool 7 fully transcribes current non-kirtan files
          -> applicable Tools 8-10 -> latest Tool 1 naming
          -> Tool 11 move -> Tool 4 path synchronization
```

This is not a fixed invocation schedule: any accepted metadata discovered by
Tool 5 or a later tool may re-trigger Tool 1 filename evaluation and Tool 4
metadata synchronization, including when no filename changes. Tool 1 and Tool
4 may recur before or after the illustrated positions as evidence evolves.

The later Tool 6 replacement of a combination input with two outputs does not
change Tool 5's obligation to leave its input untouched. The exact Tool 6
cutting rules remain for its own finalized build plan. The user-confirmed
full-pipeline order and row-identity rules are recorded in
`docs/full-pipeline-workflow-amendment.md`.

Tool 5 must remain independently callable through a typed service and CLI,
and integrated into the Main Tooling Script's `all` and `processing` workflows
after Phase 1 eligibility checks. Tool 6 and Tool 7 integration follows their
separate finalized plans; Tool 5 records their routing decisions but does not
invoke them itself.

Tool 5 records declarative routing; it does not invoke pending tools:

| Classification | Required route state |
|---|---|
| `KIRTAN_AND_CLASS` | `process_by_tool_6=true`; Tool 7 follows the cut for the class output |
| `INITIATION` | retain multi-part ceremony evidence for review; no automatic two-part cutter route; proceed to Tool 7 full transcription |
| `VYASA_PUJA` | retain multi-part offering/address evidence for review; no automatic two-part cutter route; proceed to Tool 7 full transcription |
| `CLASS` | retain class evidence; route to Tool 7 for full transcription, even when WHAT is known |
| `KIRTAN` | retain kirtan/mantra metadata; no cutter route or full Tool 7 transcription |
| `EVENT_OR_FESTIVAL_ADDRESS` / `HOME_PROGRAM` | retain type metadata; proceed to Tool 7 full transcription |
| `UNKNOWN_REVIEW` or uncertain result | add to portal review; allow Tool 7 full transcription of an intact unknown unless a possible combination needs cut review first |

`process_by_tool_6` is a durable machine-readable handoff flag, not a folder
move. Tool 6 will consume it when its definition is accepted. The schema must
leave room for later routes without inventing behavior for Tools 6-11.

Every non-kirtan type, including initiation, Vyasa-puja, event/address, and
home program, proceeds to Tool 7 once any required Tool 6 decision is
resolved. An uncertain `UNKNOWN_REVIEW` remains in place but may use Tool 7
transcription as further evidence; it is not treated as kirtan for skipping
Tool 7. A suspected unresolved combination first needs cut review. After all relevant Phase 2 tools
are complete, Tool 1 consumes trustworthy Tool 5–11 metadata for a new final
naming pass; Tool 4 then synchronizes the
resulting metadata. Tool 5 must not perform either action directly.

---

## 4. Inputs, eligible media, and generated audio

Input is a registry-backed file record/proposal plus its current local path,
stable `tracking_id`, Tool 1 parser result, and Phase 1 result context when
available. Do not reimplement Tools 1-4.

Eligible media:

- audio formats already supported by the project, including MP3, WMA, WAV,
  M4A/AAC, FLAC, and OGG when the installed decoder supports them;
- supported video formats accepted by project discovery, including MP4, MOV,
  MKV, AVI, and M4V when `ffmpeg` can decode their audio stream.

For a video, Tool 5 must extract a local MP3 alongside the video, retaining its
exact basename and changing only the extension, for example:

```text
/archive/recording.mp4 -> /archive/recording.mp3
```

Use `ffmpeg` and a high-quality MP3 encode (`libmp3lame` quality mode 0 or the
best compatible local equivalent). Record the source video path, derived audio
path, codec/quality command summary, duration, and SHA-256 in the Tool 5
result. Never overwrite an unrelated existing MP3: only reuse it after
proving it was generated for the same source fingerprint; otherwise block for
review. A failed/missing `ffmpeg` is a blocked processing result, not a
fallback to an online service.

The derived MP3 is a Tool 5 video-audio derivative, not a new archive media
item. Persist its source-video tracking ID and fingerprints in the registry so
normal archive discovery skips it and cannot process it a second time as an
independent Phase 1 file. Tool 5 may use it internally for limited analysis; a
later explicit policy must decide whether it becomes a retained archive asset.

Audio input is used directly and no derivative audio file is created.

---

## 5. Local classification runtime

Tool 5 must inspect the **whole recording's structure without fully
transcribing it**. Use bounded acoustic/music/speech analysis across the
timeline, then short, targeted local Whisper excerpts where speech content is
needed to distinguish class, kirtan, combination, and the multi-part event
patterns. A fixed set of early windows alone cannot justify a confident
whole-file decision; adaptively inspect later portions and the transition
zone. Acoustic detection alone cannot identify the precise book/verse, and a
short transcript alone cannot establish the exact end of singing. Preserve
timestamped evidence and the method used for each claim.

Where excerpts are needed, reuse the project's local `whisper.cpp` adapter:
Large v3 Turbo, Metal first on Apple Silicon, local CPU fallback for `auto`,
hardware-aware threads, and flash attention when supported. Explicit `metal`
must not silently fall back. Reuse the accepted WMA-to-temporary-WAV decoder
path. Probe required components before use, record actual backend/fallback,
and never use an online transcription API or upload archive audio. A failed
required excerpt or unusable audio evidence yields a blocked/review result,
not a confident automatic Tool 6 route. CI uses fake adapters; it must not
require FFmpeg, Whisper, a model, or Metal.

The accepted old Tool 5 implementation currently transcribes full recordings.
That is now a **migration target**, not the desired behavior. Move/reuse the
full-transcript runtime and cache contract in Tool 7 rather than retaining
two full-transcription passes. Do not make Tool 6 wait for Tool 7 output:
Tool 7 runs after Tool 6.

---

## 6. Timed evidence artifact and safety

Tool 5 owns a bounded classification/cut-evidence artifact, **not** the
complete transcript at `.renamer/transcripts/<tracking-id>.json`. Tool 7
owns that full transcript sidecar. Tool 5's evidence must record tracking ID,
source path/hash/duration, video derivative identity when applicable,
classification and mantra, audio-feature ranges, any short excerpt text with
source timestamps, analysis/model/config versions, exact singing-end point
and confidence if available, and redacted failures. Store it atomically,
locally, and privately. It must be distinguishable from a full Tool 7
transcript so a later stage cannot mistake a few excerpts for complete
coverage. Historical full Tool 5 transcripts may be retained as provenance
but may not certify a new Tool 7 result, especially after a Tool 6 cut.

Bind cached evidence to current input SHA-256 and analysis configuration.
Preflight duration and scratch space; recheck the source fingerprint before
accepting a result. Reject changed sources, path/symlink escape, malformed or
oversized output, and unsupported decodes safely. Use argument-array
subprocess calls, bounded output/timeouts, and owned temporary files; do not
write excerpts into the archive or Git. Audio intervals not analyzed are
**unknown**, not implicitly silence or proof that no later class exists.

The independent `/Users/maced/dev/audio-editing` project remains a reference
for capability probing, Metal/CPU fallback, source/config digest binding,
timed cue normalization, and fixture-backed adapters. Tool 5 must not import
that project or require its private job database or working-root layout.

---

## 7. Content evidence and classification rules

The assets define a useful class pattern, not a rigid universal template. A
class can omit opening/oblation steps or interleave a long purport with the
speaker's explanation. Classification must combine targeted excerpt evidence,
timestamps, acoustic/music/singing indicators, and existing Tool 1
filename/folder clues as *supporting* evidence.
Filename words alone must never create a confident audio classification.

### 7.1 Recognizable class evidence

Look for time-bounded evidence such as:

- `Om namo bhagavate vasudeva`;
- `we are reading from` or book/canto/chapter/verse introductions;
- Sanskrit/roman verse recitation and audience response;
- word-for-word/synonyms, translation, purport;
- `Om ajnana timirandhasya` / guru oblations;
- sustained explanatory speech, questions, and answers.

Because Whisper transliteration of Sanskrit is imperfect, matching must be
normalization and alias based, preserve raw text, and attach evidence excerpts
and timestamps. It must not claim exact scripture verification; Tool 7 owns
later class-type/scripture discovery.

### 7.2 Mantra evidence

Recognize normalized variants and common transcription forms of:

- `Jaya Radha Madhava` (often followed by maha-mantra), typically before SB/BG
  classes;
- `Jaya Jaya Sri Caitanya` / Sri Panca-tattva, typically before CC classes;
- `Namaste Narasimhaya` / Nrsimha pranam;
- maha-mantra (`Hare Krishna ... Hare Rama ...`).

Store both the canonical mantra label and raw/timestamped supporting excerpts.
The label `Kirtan` is used for detected maha-mantra/general singing; do not
mislabel an uncertain song as a known named mantra.

### 7.3 Timed combination decision — Tool 6 handoff amendment

Inspect bounded classification windows across the **whole timeline**, using
short excerpts where needed. If unresolved, adaptively inspect later regions
rather than returning a confident result from a fixed early sample or running
full-file Whisper as a shortcut.

A confident `KIRTAN_AND_CLASS` requires distinct ordered time ranges:

1. an initial sustained kirtan/mantra range; and
2. later class-pattern/sustained explanatory speech evidence;
3. the recording-specific, **exact numeric timestamp at the end of singing**,
   with confidence and acoustic/excerpt evidence for the intended Tool 6
   handoff. A coarse transition bracket may be retained for explanation, but
   it does not authorize a cut. Analyze local audio around the transition; a
   excerpt-segment edge alone is insufficient evidence of the precise end
   of singing.

An `INITIATION` requires positive multi-part ceremony evidence, not merely a
filename word. Its common sequence is singing, introduction, class, name
giving, mantra, singing. A `VYASA_PUJA` commonly contains singing,
introduction, offerings, an address by the speaker, and singing. These are
recognition guides, not guaranteed segments or fixed timing rules. Neither
multi-part type enters the initial two-part Tool 6 cutter automatically;
retain evidence and flag it for review pending specific cutting rules.

A file whose filename suggests a combination but whose reviewed audio evidence is
only singing is `KIRTAN`, not a combination. It remains in place and receives
no cutter flag.

Tool 5's exact singing-end timestamp can authorize an **automatic** Tool 6 cut
only with `HIGH` confidence and a matching source fingerprint. Tool 6 cuts at
that timestamp and may conservatively trim actual leading silence from the
class output; Tool 5 does not need to supply a second class-start timestamp.
The first class output sample must be at the same singing-end timestamp: a
later acoustic speech onset, `Om namo bhagavate`, `we are reading from`, or
verse introduction is evidence about the recording, **not** permission to
skip the intervening audio. In the Sweden SB 3.6.6 benchmark in
`sample-files/cutting-samples/timings.md`, singing ends at 2:29.396 and class
speech begins at 2:30.189; a verse-introduction detection near 3:03 must
never move the Tool 6 class output start to 3:03. Preserve prayers, opening
announcements, and uncertain transition audio for later Tool 8 processing.
If the exact end is uncertain, Tool 5 must not guess from the coarse bracket:
retain the file and route it to the portal for waveform/playback review and a
human-adjusted cut point. Tool 5 itself never cuts or deletes media.

### 7.4 Confidence and review

Return a typed confidence (`HIGH`, `MEDIUM`, `LOW`, `BLOCKED`) and structured
evidence. Automatic `process_by_tool_6=true` requires `HIGH` confidence and a
validated exact singing-end timestamp. `MEDIUM`/`LOW`, competing types,
required excerpt/analysis failure,
ambiguous mantra, or a type contradicted by strong existing evidence requires
portal review but keeps the media in place.

The portal may let a human select a type/mantra/cut point later, but must retain
the automatic evidence and never rewrite a Tool 7 transcript. Human decisions are
durably audited and must be consumable by Tool 6 and the later Tool 1 pass.

---

## 8. Service, registry, CLI, and portal interfaces

Add a module such as `media_archive_tooling.content_discoverer` with typed
models, limited-excerpt adapter, classifier, service, and test double. Keep
Whisper/ffmpeg subprocess code behind adapters.

Persist Tool 5 result/history against `tracking_id` in the local registry. It
must contain the classification, confidence, mantra type, bounded evidence
artifact path/fingerprints, evidence ranges, the exact singing-end timestamp and
confidence when available, routing flags,
review state, runtime provenance, and redacted failures. Never put excerpt
text, raw paths beyond required existing provenance, model binaries, or secrets
in Baserow.

Provide a separate command that accepts a registry-backed tracking ID or media
path, supports `--dry-run`, `--device auto|metal|cpu`, `--model-path`, and
an explicit reanalyze option. It must print a concise result:

```text
Tool 5 - Content Discoverer
Type: Kirtan and Class (HIGH)
Mantra: Jaya-radha-madhava
Singing ends: 00:11:41 (recording-specific, verified)
Evidence: bounded timed audio/excerpt analysis
Route: process_by_tool_6
```

`--dry-run` may read/reuse valid existing evidence but must not create audio,
excerpts, registry, portal, Baserow, or filesystem mutations. If no valid
evidence exists, report the analysis that would be run without inventing a
classification.

Extend the review portal with a Tool 5 section on file detail and an actionable
filter for content-discovery review. Show type, confidence, mantra, timed
evidence/boundaries, local analysis provenance, `process_by_tool_6`, and
the reason for uncertainty. It must not auto-play audio or expose absolute
local paths to a network listener; portal remains loopback-only.

---

## 9. Required verification

Add hermetic fixtures and tests for at least:

1. class with a partial/abbreviated recorded class sequence;
2. `Jaya-radha-madhava` then a class, with a Tool 6 cutter flag, exact
   singing-end timestamp, audio evidence, and source-fingerprint binding;
3. CC/Panca-tattva and Nrsimha normalized mantra variants;
4. singing-only recording incorrectly labelled combination by filename;
5. initiation and Vyasa-puja with multiple distinct sections, both retained
   for review rather than fed to the two-part cutter;
6. event/festival address and home program classifications;
7. ambiguous/required-analysis-failed input: in place, review required, no route;
8. video MP3 extraction path, source fingerprint reuse, collision protection,
   and `ffmpeg` failure;
9. Metal success, auto CPU fallback, and explicit-metal failure;
10. bounded evidence reuse/invalidation/atomic write and complete provenance;
11. zero Baserow access/mutation by Tool 5;
12. no original-file rename/move/delete by Tool 5;
13. portal rendering and audited human review decisions;
14. dry-run zero mutation;
15. Tool 5 registry data is cleared by the existing alpha/beta fresh-slate/
    purge flow only after Tool 4 test-row cleanup succeeds.
16. source changes during analysis, symlink/path escape, malformed or
    oversized excerpt output, and derivative-MP3 discovery suppression;
17. no full-file Tool 5 transcription; Tool 7 receives the current non-kirtan
    file after an optional Tool 6 cut, and kirtan-only files skip Tool 7.

Before handoff run focused Tool 5/portal tests, the complete project suite,
shell checks, package build, and required GitHub CI. A practical local smoke
test may use a manually installed local model and a copy/sample file, but must
not create Baserow data or commit transcript/audio artifacts to Git. Record
only redacted, reproducible evidence in the status file.

---

## 10. Acceptance criteria

Tool 5 is ready for review only when it:

- classifies from bounded acoustic and targeted excerpt evidence without
  fully transcribing the source; records Mac backend/fallback provenance when
  short local Whisper excerpts are used;
- supports safe local audio extraction for video;
- classifies and distinguishes class, kirtan, combination, initiation, event,
  home program, and uncertainty from actual timed evidence;
- retains canonical mantra metadata and evidence;
- stores valid bounded timed evidence outside the archive and leaves full
  transcript creation to Tool 7;
- leaves Phase 1 media in place and makes no Baserow call or mutation;
- gives only high-confidence kirtan/class combinations with a validated exact
  singing-end timestamp a `process_by_tool_6` handoff;
- routes uncertainty to the local portal without pretending certainty;
- preserves Tool 1/Tool 4 ownership for later rename/Baserow synchronization;
- passes the required focused/full tests, package build, and CI; and
- is committed, pushed, and supplied in a Tool 5 PR under the Builder protocol.
