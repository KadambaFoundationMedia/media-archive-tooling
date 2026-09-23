# Tool 5 - Content Discoverer Build Plan

Status: **FINALIZED - implementation-ready**

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
- `assets/verse-structure.md`
- `assets/original-and-edited-recording-structure.md`
- accepted Tool 1-4 statuses and implementations

---

## 1. Purpose and scope

Tool 5 is the local **Content Discoverer**. It analyzes the actual audio of a
Phase 1 archive file to establish its broad content type, identify any opening
mantra/kirtan, retain reusable timestamped transcription data, and record the
next processing route.

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
- save a reusable transcript outside the archive during active processing;
- extract an MP3 beside a video only when video input requires it;
- create no Baserow reads, writes, schema mutations, or deletions;
- never rename a file - Tool 1 remains the canonical renderer and committer;
- never update Baserow - Tool 4 remains the sole writer/deleter;
- never call Tool 6, cut, trim, boost gain, or organize files;
- record review-required uncertainty in the existing local registry/portal;
- fail closed: a missing transcription runtime/model, extraction failure,
  unreadable media, or ambiguous classification cannot produce a confident
  type or downstream destructive action.

Tool 5 must preserve the alpha/beta registry/fingerprint rules. Its source and
template changes must participate in review-data invalidation. Tool 5 itself
does not create Baserow test rows and must not bypass Tool 4's purge service.

---

## 3. Pipeline position and routing contract

The intended whole project sequence is:

```text
Phase 1: Tool 1 -> Tool 2 -> Tool 3 -> Tool 1 final proposal -> Tool 4
Phase 2: Tool 5 -> later selected audio tools -> Tool 11
          -> Tool 1 enrichment/final rename -> Tool 4 synchronization
```

The first Tool 5 implementation must be usable independently through a typed
service and CLI command. Per the user's later direction, also integrate Tool 5
into the Main Tooling Script's `all` and `processing` workflows while preserving
the Phase 1-to-Phase 2 order and the Phase 1 eligibility requirement. Tools 6-11
remain pending; Tool 5 only records their future routing decisions.

Tool 5 records declarative routing; it does not invoke pending tools:

| Classification | Required route state |
|---|---|
| `KIRTAN_AND_CLASS` | `process_by_tool_6=true` |
| `INITIATION` | `process_by_tool_6=true` (multi-part boundary review/cutting) |
| `CLASS` | retain class evidence; do not call Tool 7 yet |
| `KIRTAN` | retain kirtan/mantra metadata; no cutter route |
| `EVENT_OR_FESTIVAL_ADDRESS` / `HOME_PROGRAM` | retain type metadata; no invented later route |
| `UNKNOWN_REVIEW` or uncertain result | no automatic downstream route; add to portal review queue |

`process_by_tool_6` is a durable machine-readable handoff flag, not a folder
move. Tool 6 will consume it when its definition is accepted. The schema must
leave room for later routes without inventing behavior for Tools 6-11.

After all relevant Phase 2 tools are complete, Tool 1 consumes trustworthy Tool
5/6-11 metadata for a new final naming pass; Tool 4 then synchronizes the
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
independent Phase 1 file. Tool 5 may use it internally for transcription; a
later explicit policy must decide whether it becomes a retained archive asset.

Audio input is used directly and no derivative audio file is created.

---

## 5. Local transcription runtime

Use the project-local binding around Homebrew `whisper-cli` from `whisper.cpp`.
The production default is:

```text
model: large-v3-turbo GGML
default model path: ~/.cache/whisper.cpp/ggml-large-v3-turbo.bin
language: automatic detection
temperature: 0
context carry-over: disabled
timestamps: JSON segments
backend: auto (Metal first on Apple Silicon, local CPU fallback only)
flash attention: enabled when supported
threads: hardware-aware, six on the established M1 Max path while preserving CPU headroom
```

Required runtime behavior:

1. Probe tool/model availability before decode.
2. With device `auto`, attempt Metal and record the selected backend; if it
   fails, retry locally on CPU and record the exact fallback reason.
3. With explicit `metal`, fail rather than silently changing the requested
   execution contract.
4. A missing model, missing executable, non-zero decoder/transcriber exit,
   malformed JSON, or unusable timestamps is `TRANSCRIPTION_BLOCKED` and goes
   to review without any confident classification.
5. Never download a model automatically, use an online API, send archive audio
   outside the machine, or commit model files/transcripts to Git.
6. Preserve a compatibility adapter boundary for older private executable/model
   jobs, but do not make it the default path.

Hermetic tests must use a fake transcription adapter and fixture JSON. CI must
not require `whisper-cli`, a model, Metal, `ffmpeg`, or real audio.

---

## 6. Transcript artifact contract

The complete recording must be transcribed, not merely sampled. The transcript
artifact is a reusable local sidecar during processing:

```text
.renamer/transcripts/<tracking-id>.json
```

Do not write a transcript into the source archive directory. Tool 11 later
moves completed transcripts to:

```text
processed-files/transcriptions/<media-category>/
```

where `<media-category>` is the final media category. Until it is determined,
the temporary `.renamer/transcripts/` artifact is authoritative.

Artifact fields must include:

- contract/version and tracking ID;
- original/current input path and input SHA-256;
- source type (`audio` or `video`) and derived MP3 details if applicable;
- total duration and detected language(s), with confidence when supplied;
- timestamped normalized segments (`start_seconds`, `end_seconds`, `text`);
- raw-tool metadata: whisper.cpp version, model path/name, model SHA-256,
  selected backend, requested device, thread count, and fallback evidence;
- transcription timestamp and redacted errors/warnings;
- transcript SHA-256 and classification linkage/version.

Write atomically. Reuse a sidecar only when its input fingerprint and
transcription configuration/model fingerprint match; otherwise regenerate.
An existing valid transcript enables classification retry without rerunning
Whisper.

Before transcribing, calculate the input fingerprint and duration with safe
local preflight (`ffprobe`/decoder metadata). Recalculate the input fingerprint
after transcription; if it changed, discard/quarantine the result and block
classification as a changed source. Do not follow a symlink outside the
requested archive target and do not construct shell commands from filename
text. Subprocesses must use argument arrays, `-nostdin`, bounded output,
timeouts, and a minimal inherited environment.

Normalize the raw Whisper JSON into a bounded project segment contract with
timeline coverage from `00:00` to the measured end. Preserve explicit blank
intervals for non-speech/silence rather than silently omitting gaps. This lets
later Tools 6, 8, and 10 distinguish an observed silent/ambience interval from
an unreviewed part of the recording. Store private transcript artifacts with
owner-only permissions where the platform supports them.

### Reference implementation patterns (Audio-Editing review, 2026-09-23)

The independent `/Users/maced/dev/audio-editing` project is a **reference only**:
Tool 5 must not import it, share its private job database, or require its
working-root layout. The Builder should adapt these proven local patterns:

- `whisper.cpp` capability probing, hardware-aware thread selection, recorded
  real-time factor/load metrics, and visible Metal-to-CPU fallback;
- source SHA-256, analysis-profile/configuration digest, and transcript digest
  binding before cache reuse or later handoff;
- 1/2/5/10-minute plus tail windows as explanatory evidence derived from the
  full transcript, not as a substitute for it;
- normalized Sanskrit/mantra aliases while retaining raw excerpts and timing;
- strict malformed/oversized transcript rejection and fixture-backed adapters
  so CI never requires media hardware or model assets.

---

## 7. Content evidence and classification rules

The assets define a useful class pattern, not a rigid universal template. A
class can omit opening/oblation steps or interleave a long purport with the
speaker's explanation. Classification must combine transcript evidence,
timestamps, music/singing indicators from the local transcription output when
available, and existing Tool 1 filename/folder clues as *supporting* evidence.
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

### 7.3 Timed combination decision

Use the full transcript and inspect classification windows around 1, 2, 5, and
10 minutes. If unresolved, adaptively inspect later timestamped regions rather
than returning a confident result from a fixed four-window sample.

A confident `KIRTAN_AND_CLASS` requires distinct ordered time ranges:

1. an initial sustained kirtan/mantra range; and
2. later class-pattern/sustained explanatory speech evidence;
3. a boundary **review interval/range** and confidence for the intended Tool 6
   handoff.

An `INITIATION` requires positive multi-part ceremony evidence (for example
mantra/yajna/ceremonial language plus distinct discourse/singing portions),
not merely a filename word. It receives `process_by_tool_6=true` but its
proposed boundaries remain reviewable.

A file whose filename suggests a combination but whose full audio evidence is
only singing is `KIRTAN`, not a combination. It remains in place and receives
no cutter flag.

Tool 5 must never call the boundary an approved exact cut. The `process_by_tool_6`
handoff contains the source-bound coarse bracket between the last detected
kirtan/mantra evidence and first later class evidence; Tool 6 will refine it
and obtain the human decision required for actual cutting. No
combination/cutting handoff is allowed if even the coarse bracket is uncertain.
Mark it `UNKNOWN_REVIEW` (or another explicitly uncertain result) and route it
to the portal with the evidence needed for human review.

### 7.4 Confidence and review

Return a typed confidence (`HIGH`, `MEDIUM`, `LOW`, `BLOCKED`) and structured
evidence. Automatic `process_by_tool_6=true` requires `HIGH` confidence and a
validated boundary. `MEDIUM`/`LOW`, competing types, transcript failure,
ambiguous mantra, or a type contradicted by strong existing evidence requires
portal review but keeps the media in place.

The portal may let a human select a type/mantra/boundary later, but must retain
the automatic evidence and never rewrite a transcript. Human decisions are
durably audited and must be consumable by Tool 6 and the later Tool 1 pass.

---

## 8. Service, registry, CLI, and portal interfaces

Add a module such as `media_archive_tooling.content_discoverer` with typed
models, transcription adapter, classifier, service, and test double. Keep
Whisper/ffmpeg subprocess code behind adapters.

Persist Tool 5 result/history against `tracking_id` in the local registry. It
must contain the classification, confidence, mantra type, transcript artifact
path/fingerprints, evidence ranges, proposed cutter boundaries, routing flags,
review state, runtime provenance, and redacted failures. Never put transcript
text, raw paths beyond required existing provenance, model binaries, or secrets
in Baserow.

Provide a separate command that accepts a registry-backed tracking ID or media
path, supports `--dry-run`, `--device auto|metal|cpu`, `--model-path`, and
`--force-retranscribe`. It must print a concise result:

```text
Tool 5 - Content Discoverer
Type: Kirtan and Class (HIGH)
Mantra: Jaya-radha-madhava
Boundary: kirtan 00:00-11:41; class begins 12:23
Transcript: .renamer/transcripts/<tracking-id>.json
Route: process_by_tool_6
```

`--dry-run` may read/reuse an existing transcript but must not create audio,
transcript, registry, portal, Baserow, or filesystem mutations. If no valid
transcript exists, report the transcription/classification that would be run.

Extend the review portal with a Tool 5 section on file detail and an actionable
filter for content-discovery review. Show type, confidence, mantra, timed
evidence/boundaries, transcript runtime provenance, `process_by_tool_6`, and
the reason for uncertainty. It must not auto-play audio or expose absolute
local paths to a network listener; portal remains loopback-only.

---

## 9. Required verification

Add hermetic fixtures and tests for at least:

1. class with a partial/abbreviated recorded class sequence;
2. `Jaya-radha-madhava` then a class, with a Tool 6 cutter flag and precise
   boundary evidence;
3. CC/Panca-tattva and Nrsimha normalized mantra variants;
4. singing-only recording incorrectly labelled combination by filename;
5. initiation with multiple distinct sections;
6. event/festival address and home program classifications;
7. ambiguous/transcription-failed input: in place, review required, no route;
8. video MP3 extraction path, source fingerprint reuse, collision protection,
   and `ffmpeg` failure;
9. Metal success, auto CPU fallback, and explicit-metal failure;
10. transcript sidecar reuse/invalidation/atomic write and complete provenance;
11. zero Baserow access/mutation by Tool 5;
12. no original-file rename/move/delete by Tool 5;
13. portal rendering and audited human review decisions;
14. dry-run zero mutation;
15. Tool 5 registry data is cleared by the existing alpha/beta fresh-slate/
    purge flow only after Tool 4 test-row cleanup succeeds.
16. source changes during transcription, symlink/path escape, malformed or
    oversized Whisper output, and derivative-MP3 discovery suppression.

Before handoff run focused Tool 5/portal tests, the complete project suite,
shell checks, package build, and required GitHub CI. A practical local smoke
test may use a manually installed local model and a copy/sample file, but must
not create Baserow data or commit transcript/audio artifacts to Git. Record
only redacted, reproducible evidence in the status file.

---

## 10. Acceptance criteria

Tool 5 is ready for review only when it:

- transcribes complete local audio with the specified Whisper default and
  recorded Mac backend/fallback provenance;
- supports safe local audio extraction for video;
- classifies and distinguishes class, kirtan, combination, initiation, event,
  home program, and uncertainty from actual timed evidence;
- retains canonical mantra metadata and evidence;
- stores valid reusable temporary transcripts outside the archive;
- leaves Phase 1 media in place and makes no Baserow call or mutation;
- gives only high-confidence combinations a `process_by_tool_6` handoff;
- routes uncertainty to the local portal without pretending certainty;
- preserves Tool 1/Tool 4 ownership for later rename/Baserow synchronization;
- passes the required focused/full tests, package build, and CI; and
- is committed, pushed, and supplied in a Tool 5 PR under the Builder protocol.
