# Tool 7 — Class Type Discoverer Build Plan

Status: **FINALIZED — implementation-ready**

This is the authoritative Tool 7 specification. The Builder is a separate
Antigravity model, started by the owner with `BUILD TOOL 7`. It must use
`BUILDER.md`, `./scripts/builder-start.sh 7`, and
`status/tool-7-class-type-discoverer.md`; implementation belongs on
`tool-7-implementation`, not `main`. Read the current Tool 5 and Tool 6 plans,
`docs/full-pipeline-workflow-amendment.md`, the Tool 1/2/4 plans and statuses,
`assets/file-naming-convention.md`, `assets/default_categories.json`, and
`assets/verse-structure.md`, and
`assets/original-and-edited-recording-structure.md` before building. The
markdown verse asset is the
repository copy of the structure illustrated in the owner's
`audio-editing/assets/verse-structure.pdf`; do not require that separate
checkout at runtime.

## 1. Purpose and owner-confirmed scope

Tool 7 performs **full local transcription after Tool 5 classification and,
when needed, Tool 6 cutting**. It uses the transcript and recording-structure
markers to discover or corroborate the recording's WHAT/media category and
scripture reference. Its name is historical: it transcribes **every eligible
non-kirtan recording**, not only files with an unknown filename title or
unknown WHAT. A clear filename may remove the need for a category correction,
but it never exempts a non-kirtan recording from full transcription. The
transcript is reusable evidence for Tools 8–10 and Tool 11.
Tool 5's broad content type (`CLASS`, `KIRTAN`, combination, ceremony, etc.)
is distinct from Tool 7's more specific WHAT and live Baserow Category.

- A standalone class is fully transcribed even if its title, category, and
  verse are already known.
- For a confirmed `KIRTAN_AND_CLASS` cut, fully transcribe **only the class
  output**; never transcribe the discarded full-length audio merely to classify
  it, and do not fully transcribe the singing output.
- Fully transcribe uncut initiation, Vyasa-puja, event/festival address, home
  program, and other non-kirtan recordings. These may contain class-like
  sections, but Tool 7 does not invent a multi-part cut for them.
- A kirtan-only recording, including a singing output from Tool 6, **skips
  Tool 7 full transcription**. Tool 5's timed evidence and mantra/title
  metadata still apply. If its identity remains unclear, send it to review;
  do not silently run full Whisper or guess a scripture category.
- `UNKNOWN_REVIEW` classification must not be treated as kirtan to skip work.
  Tool 7 may fully transcribe an intact unknown recording to help resolve its
  type. If Tool 5 specifically suspects a combination but cannot establish
  the cut, keep it in place for cut review first so a full pre-cut transcript
  is not generated needlessly. Once resolved as kirtan-only, skip Tool 7.

Tool 7 analyzes only the current registered file or class successor. It does
not cut, rename, move, trim, boost, delete, or alter archive media. The main
script orchestrates Tool 1 and Tool 4 after Tool 7 produces trustworthy new
metadata, including when only Baserow metadata changes and no rename is
needed. Tool 7 never accesses Baserow itself; Tool 4 is the sole writer and
Tool 2 retains its read-only review role.

## 2. Position and lineage

```text
Phase 1 Tools 1–4
  -> Tool 5 broad content classification and exact singing-end evidence
  -> Tool 6 two-part cut, only for confirmed combinations
  -> Tool 7 full transcript for each current non-kirtan working recording
  -> applicable class-only Tools 8–10
  -> Tool 1 latest canonical filename -> Tool 11 final move -> Tool 4 path sync
```

This is a dependency sequence, not a limit on Tool 1/4 calls. After Tool 7
accepts new WHAT/category/verse evidence, immediately ask Tool 1 to
re-evaluate its filename and Tool 4 to synchronize Baserow-relevant metadata
under their existing approval, matching, and race guards. Repeat when later
tools or human review discover more. Preserve the existing class row identity
and the singing row's distinct identity. Tool 7 may run for a Phase 1 file
that could not yet be renamed; a prior final filename or Baserow row is not a
prerequisite. A blocked Tool 6 combination remains intact/review-required;
do not transcribe the full combination as a way around the unresolved cut.

The Tool 5 and Tool 6 plans are amended alongside this plan: Tool 5 may use
audio features and **short, targeted local transcription excerpts**, but no
longer produces a full-recording transcript. Tool 6 must not require or split
a Tool 5 full transcript. Its verified class output is the input to Tool 7.
An otherwise unknown intact recording can proceed to Tool 7 for type
evidence; an unresolved suspected combination waits for cut review.
Existing Tool 5 full-transcript artifacts from older runs are historical
evidence, not proof that the new stage completed.

## 3. Inputs, local runtime, and performance

Input is the current registered media path, tracking ID and immutable
lineage/source fingerprints, Tool 5 classification/evidence, optional Tool 6
split mapping and trim offsets, Tool 1 structured filename evidence, and any
confirmed Tool 2/4 metadata. Reuse existing project services and registry;
do not parse the filename or query Baserow privately.

Use local `whisper.cpp`/`whisper-cli` with **Whisper Large v3 Turbo** as the
default model. Reuse or refactor the accepted Tool 5 transcription adapter
instead of implementing a second divergent decoder. The established macOS
path uses Metal on Apple Silicon with hardware-aware threads and flash
attention where supported; `auto` may fall back to local CPU with a recorded
reason, while explicitly requested `metal` must fail rather than silently
switch backend. Probe executable, model, codec and free scratch space before
work. Decode unsupported formats such as WMA to a temporary 16 kHz mono WAV
using FFmpeg, preserving the archive source; video uses Tool 5's registered
audio derivative where available. Never download a model automatically or
upload audio/transcripts to any external transcription service.

Keep per-file scratch bounded and owned, clean it after success/failure, and
make interruption/retry safe. Report concise conversion/transcription progress
or heartbeat in CLI and portal; detailed timing, selected backend, fallback,
model/config hash, input hash, and real-time factor belong in the single
structured log. A repeated run may reuse a full transcript only when the
**current output's** path/identity and SHA-256, model, decoding configuration,
transcript contract version, and source/Tool 6 lineage match. A pre-cut
full-length transcript must not be mistaken for a transcript of the class
part. Tool 7 reanalysis of a cached transcript must be possible without
rerunning Whisper when only the marker rules change.

The separate `/Users/maced/dev/audio-editing` project is a **reference, not a
runtime dependency**. In particular, review its
`src/audio_editing/full_transcript.py` and
`src/audio_editing/transcription.py` for safe full-run, Metal/fallback,
fingerprint, and segment-normalization patterns. Adapt useful ideas into this
repository; do not import that project's private state or require its path
on another builder machine.

## 4. Full transcript artifact

Write the complete, timestamped transcript of the **current non-kirtan
recording** atomically to `.renamer/transcripts/<tracking-id>.json`, outside
the archive. Include source/current path and SHA-256, Tool 6 parent and trim
offsets when applicable, media duration, language detection, timestamped
segments with explicit gaps, model/backend/config provenance, artifact hash,
and analysis-version linkage. Do not replace a conflicting historical
artifact without preserving its provenance. Owner-only permissions are
preferred. Never commit transcripts, model files, or source audio to Git.

Tool 11 later moves the accepted transcript to
`processed-files/transcriptions/<media category>/` after the category is
resolved. If category is still unknown, keep it in the temporary location;
do not guess a destination. Tools 8–10 consume this artifact through a typed
interface rather than reading terminal output. If transcription fails or is
incomplete, record a retry/review state and do not present the transcript as
complete or silently run downstream transcript-dependent processing.

## 5. Category, structure, and verse discovery

Use `assets/verse-structure.md` and the recording patterns retained by Tool
5 as guides, **not a rigid sequence**. A class may introduce the book with a
phrase such as “we are reading from,” announce canto/chapter/verse, recite
Sanskrit, then use synonyms, translation, purport, and explanation. Some steps
may be absent, repeated, or separated. “Srila Prabhupada ki jaya” can mark a
class ending, but is not a mandatory endpoint or permission to trim media;
Tool 8 owns later class trimming.

Extract timed, raw excerpts and normalized candidates for book/type and
verse. Normalize common ASR spelling/transliteration variants without
discarding the original words. `SB 4.10.23` means canto 4, chapter 10,
verse 23; BG has chapter/verse; CC includes its lila/division. Support the
inclusive verse-range grammar in `assets/verse-structure.md`. A book mention
in an unrelated discussion or quoted story is weaker than an opening class
announcement corroborated by the following reading. A filename title is
supporting evidence, not proof against contradictory audio. Never infer an
exact verse from a category alone.

Map category semantics to the **current live Baserow Category options**
through Tool 4's schema-aware boundary. `assets/default_categories.json`
offers hints, not permission to invent a select option or treat its spelling
as authoritative. The existing confirmed Baserow value is leading. If audio,
filename, and Baserow conflict, preserve the confirmed value and request
human review; do not overwrite it automatically. Store field-specific
confidence and evidence so a confirmed category can be used even when the
verse remains uncertain.

## 6. Vedabase verification and Baserow `description`

For a proposed scripture reference, Tool 7 may make a **read-only** lookup at
Vedabase to verify the exact canonical verse or inclusive range page and
compare the transcript's announced reference and subsequent reading with
that page. The owner's example is SB 4.10.23 at
`https://vedabase.io/en/library/sb/4/10/23/`. Use the chapter index when
Vedabase groups several verses on one canonical page; never assume every
individual verse has its own URL. Reuse the repository's
`assets/verse-structure.md` range rules. Bound request time, retries and
response size; record source URL, retrieval time and match evidence. No web
lookup may send archive audio or full transcript text. If Vedabase is down,
the reference is missing, multiple pages could match, or Sanskrit ASR is too
uncertain to corroborate, keep a candidate for review and **do not fabricate
a verified URL**. A successful page lookup alone is not evidence that this
recording read that verse.

The owner confirms the live Baserow Media table has a lowercase
`description` column, even though the repository's current fake schema does
not list it. The Builder must update Tool 4's schema fixture/field handling,
but must discover and validate the **live** field name/type before any write.
Tool 7 emits the verified canonical URL and its provenance; **Tool 4 alone**
adds it to `description`. Preserve any existing human description, add the
link idempotently, and revalidate its precondition before committing. An
already populated conflicting Vedabase link or incompatible/missing live
field requires review. Do not write to `Youtube descr`, `Notes`, or an
unverified field, and do not create a new Baserow field. Tool 4 may also
synchronize other trustworthy category, scripture tag/title, and transcript
status metadata under its existing field-specific safety rules.

## 7. Review, CLI, dry-run, and integration

Expose a typed Tool 7 service plus a focused CLI command for one registered
file/tracking ID, and integrate it into Main Script `all` and `processing`
after Tool 6 when available. `renamer` remains Tools 1–4. Dry-run must show
planned transcription, existing cache validity, candidate category/verse,
Vedabase verification state, proposed Tool 1 filename and Tool 4 field diff
without changing media, scratch, registry, transcript, Baserow, or review
decisions. If no transcript exists, do not claim audio-derived results in
dry-run. Live mode needs no per-file prompt when evidence is sufficient;
uncertainty goes to the portal.

The portal should show transcript status, timed marker excerpts, candidate
category and verse with confidence, the verified Vedabase link, any conflict
with existing filename/Baserow values, and an auditable approve/correct/defer
path. Do not expose arbitrary filesystem paths or the entire private
transcript to an external network listener. The main script records the Tool
7 result separately and shows a short communication trace: Tool 7 evidence
→ Tool 1 proposed/committed rename if warranted → Tool 4 row/field result.
Failures in Tool 4 do not roll back a valid local transcript or rename; they
remain durable pending/review work.

## 8. Verification and acceptance

Use fake Whisper, FFmpeg, Vedabase, Baserow/Tool 4, and registry boundaries in
CI. Cover at least:

1. Known-WHAT standalone class still receives full transcription; a vague
   filename such as `AF2002 Lekce 2008.mp3` can become a validated SB/BG/CC
   category and verse from the audio.
2. Tool 6 class child is transcribed **after** cutting; the singing child and
   kirtan-only source skip full Tool 7 transcription; no pre-cut transcript
   is required or misused for the child.
3. Initiation, Vyasa-puja, event, and home-program inputs are fully
   transcribed without inventing a two-part cut.
4. Introduction/recitation/synonyms/translation/purport and end-marker
   variations, ASR misspellings, missing steps, unrelated scripture mentions,
   and conflicting filename/Baserow evidence.
5. Exact Vedabase verse and grouped-range links; nonexistent, ambiguous,
   unavailable, and weakly corroborated pages do not generate an automatic
   `description` write.
6. Existing `description` text is preserved, the same link is not duplicated,
   a competing link is flagged, and missing/wrong-type live field blocks its
   update. Tool 7 has zero Baserow access.
7. Metal success, auto CPU fallback, explicit-metal failure, WMA decode,
   missing model, changed input, cache validity, interruption/retry, and
   progress reporting without terminal spam.
8. Transcript sidecar is complete, timestamped, atomically stored, bound to
   the current child identity, ignored by Git, and available to Tools 8–10
   and eventual Tool 11 move.
9. Dry-run creates no media/registry/scratch/Baserow mutation; Tool 1/4 are
   re-invoked on trustworthy metadata even when the filename is unchanged;
   unresolved cases remain in the review portal.

Run focused and full pytest suites, package build, helper syntax checks, and
required GitHub CI. A practical local smoke test may use a bounded **copy of
one sample file** and a local model; do not edit/archive-cut an owner's
original or write live Baserow during Builder verification. Record concise
timing, transcript completeness, evidence, and projected field diffs in the
Tool 7 status file. Commit and push every implementation/status change, open
a Tool 7 PR, and hand off `READY_FOR_REVIEW` only when the pushed branch and
CI evidence are available. The planner independently reviews/tests before
acceptance and merge.
