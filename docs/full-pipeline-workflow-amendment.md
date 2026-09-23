# Full Pipeline and Archive-Scale Workflow Amendment

Status: **USER-CONFIRMED WORKFLOW DECISIONS — authoritative for future integration**

This amendment records the owner's 2026-09-23 clarification. It supersedes any
older downstream ordering that places Tool 11 before the final Tool 1 rename or
retains an extra full-length working copy after Tool 6 successfully splits a
combination. It does not authorize implementing Tools 6–11 without their own
finalized build plans.

## Dependency sequence, not a fixed call schedule

The list below is the owner's **suggested workflow** and identifies the
dependencies between operations. It is not a rule that Tool 1 or Tool 4 may
run only at the positions shown. Whenever any tool or approved human review
discovers trustworthy new metadata, the orchestrator must persist its evidence,
ask Tool 1 to re-evaluate the canonical filename (and commit a change only
when warranted), and ask Tool 4 to synchronize any Baserow-relevant change
under its existing review/write gates. Tool 4 may update the matched row even
when the filename is unchanged. Both actions must be idempotent and must
preserve row identity; uncertain evidence goes to review rather than becoming
an automatic rename or database write.

1. Tool 1 initially interprets and renames in place when evidence permits. It
   uses Tool 2's read-only Media review and Tool 3's travel-schedule review.
2. Tool 4 creates or updates the matching Media item after a committed Phase 1
   final filename, subject to its existing review, duplicate, and write gates.
   Files that cannot yet be safely renamed or synchronized may still proceed
   in place to audio discovery.
3. Tool 1 and Tool 4 recur whenever later accepted evidence changes a
   filename-relevant or database-relevant field, respectively. A Tool 1
   rename is not a precondition for every Tool 4 metadata-only update.
4. Tool 5 transcribes and classifies the file as class, singing, combination,
   or another supported content type. Uncertain results remain in place for
   review; they must not trigger a destructive downstream step.
5. For a confirmed singing-and-class combination, Tool 6 cuts the working
   audio input into two resulting files: a singing part and a class part. On
   successful completion, the full-length working **audio** input no longer
   exists. The archive is backed up separately; the pipeline must not keep
   a permanent third audio copy. For video, retain the original video and
   split Tool 5's extracted MP3; remove only that owned full-length MP3 after
   both MP3 outputs are verified. Temporary staging while safely producing
   and validating both outputs is allowed. If cutting fails, retain the
   working input and route it for review rather than presenting a partial
   split as complete.
6. The previously created class Media row remains the class row and follows
   the class output; do not create a second class row. Tool 4 checks for an
   existing matching singing item through Tool 2 and creates the singing row
   when it is genuinely new. This Tool 4 step occurs after the cut. Both row
   identities remain distinct and durable through later renames and moves.
   If the Phase 1 class row was blocked or never created, do not fabricate an
   existing row ID; Tool 4 must resolve the class item under its normal safe
   create/update/review rules when enough evidence becomes available. For a
   retained video, preserve its filename/archive path and use
   `audio_file_path` for the class MP3's full local path; do not overwrite the
   video path or write a local path into the `Audio link` URL field.
7. Tool 7 discovers class type. Tools 8 (class trimming), 9 (class gain), and
   10 (questions gain) act only on the applicable class part/evidence. The
   singing part does not pass through class-only processing merely because it
   originated in the same recording. Exact applicability and processing rules
   belong to each later tool's build plan.
8. Tool 1 performs the latest canonical naming for each resulting file before
   Tool 11 chooses a destination from the latest WHAT/category metadata. Tool
   11 moves each processed file and its applicable transcript. Tool 4 then
   updates the class and singing rows with committed paths/metadata. If new
   trustworthy metadata arrives even after this point, the appropriate Tool
   1, Tool 11, and/or Tool 4 actions can recur. Tool 4 alone writes Baserow
   and must preserve confirmed existing fields and unrelated online links.

The initial Tool 6 build handles two-part kirtan/class combinations. Tool 5
provides a recording-specific, high-confidence exact end-of-singing timestamp
for automatic cutting; uncertain cases remain intact for waveform/audio
review and manual adjustment. Tool 6 trims only actual leading silence and
does not remove meaningful speech. Both outputs stay in place with a durable
pending Tool 11 move until Tool 11 has its own accepted implementation.

The class and singing items can therefore receive many Tool 1/Tool 4 passes
over their lifetimes. A metadata-only update must not be omitted merely
because no filename changed, and a repeated synchronization must not create
a duplicate row. A failed or uncertain Baserow operation remains in a durable
pending/review state and does not undo a successful local file change.

## Archive-scale operating constraints

- Normal processing must operate on selected files/folders in place and must
  never make a full copy of the selected folder or the 15+ TB archive.
- Evaluation helpers must not implicitly copy the whole `sample-files` tree;
  `scripts/run_tool_4_evaluation.py` currently does so with `copytree` and
  needs an explicit bounded fixture/subset strategy before archive-scale use.
- Keep scratch data bounded to the active file(s), verify sufficient scratch
  space before a conversion/cut, and clean up owned scratch artifacts after
  success or failure. A crash/restart must identify and handle only its own
  abandoned scratch artifacts, never arbitrary archive files.
- Persist per-file and per-stage identity, results, and pending work so a run
  can resume without reprocessing completed stages or losing Baserow sync
  work. A failed/review file must not indefinitely block independent files.
- Folder discovery and progress logging must not require loading or logging
  the entire media payload or an archive-wide path list in one event.
- Preserve the user's current alpha/beta test-row purge policy during testing.
  Before real archive production, a separate explicit production-mode decision
  must prevent automatic test-state purges from erasing durable work.

## Work allocation and unresolved tool details

The Main Tooling Script builder should address the currently actionable
archive-scale runner/evaluation-helper safeguards and keep extension points
for the confirmed later sequence. The Tool 6 builder must use its separate
finalized plan for cut evidence, formats, collision handling, transcript
association, and portal review. Tools 7–11 retain their own future plans.
