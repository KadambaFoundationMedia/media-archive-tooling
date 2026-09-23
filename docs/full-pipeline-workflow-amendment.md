# Full Pipeline and Archive-Scale Workflow Amendment

Status: **USER-CONFIRMED WORKFLOW DECISIONS — authoritative for future integration**

This amendment records the owner's 2026-09-23 clarification. It supersedes any
older downstream ordering that places Tool 11 before the final Tool 1 rename or
retains an extra full-length working copy after Tool 6 successfully splits a
combination. It does not authorize implementing Tools 6–11 without their own
finalized build plans.

## End-to-end order

1. Tool 1 initially interprets and renames in place when evidence permits. It
   uses Tool 2's read-only Media review and Tool 3's travel-schedule review.
2. Tool 4 creates or updates the matching Media item after a committed Phase 1
   final filename, subject to its existing review, duplicate, and write gates.
   Files that cannot yet be safely renamed or synchronized may still proceed
   in place to audio discovery.
3. Tool 1 may enrich a committed filename when new approved evidence appears;
   Tool 4 updates the **same** matched item when committed metadata changes.
4. Tool 5 transcribes and classifies the file as class, singing, combination,
   or another supported content type. Uncertain results remain in place for
   review; they must not trigger a destructive downstream step.
5. For a confirmed singing-and-class combination, Tool 6 cuts the working
   input into two resulting files: a singing part and a class part. On
   successful completion, the full-length working input **no longer exists**.
   The archive is backed up separately; the pipeline must not keep or create
   a permanent third copy of the original. Temporary staging while safely
   producing and validating both outputs is allowed. If cutting fails or
   cannot be verified, retain the input and route it for review rather than
   deleting it or presenting a partial split as complete.
6. The previously created class Media row remains the class row and follows
   the class output; do not create a second class row. Tool 4 checks for an
   existing matching singing item through Tool 2 and creates the singing row
   when it is genuinely new. This Tool 4 step occurs after the cut. Both row
   identities remain distinct and durable through later renames and moves.
   If the Phase 1 class row was blocked or never created, do not fabricate an
   existing row ID; Tool 4 must resolve the class item under its normal safe
   create/update/review rules when enough evidence becomes available.
7. Tool 7 discovers class type. Tools 8 (class trimming), 9 (class gain), and
   10 (questions gain) act only on the applicable class part/evidence. The
   singing part does not pass through class-only processing merely because it
   originated in the same recording. Exact applicability and processing rules
   belong to each later tool's build plan.
8. Tool 1 performs final enrichment and canonical naming for each resulting
   file. Tool 11 then moves each processed file and its applicable transcript
   to the destination determined by final WHAT/category metadata. Tool 4
   finally updates the existing class row and the singing row with their
   committed final metadata and paths. Tool 4 alone writes Baserow and must
   preserve confirmed existing fields and unrelated online links.

Tool 4 may be invoked more than once for one logical item as trustworthy
metadata and committed paths change; those are updates to its existing row,
not duplicate creates. A failed or uncertain Baserow operation remains in a
durable pending/review state and does not undo a successful local file change.

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
for the confirmed later sequence. The Tool 6 builder must receive a separate
finalized plan for exact cut boundaries, output formats, collision handling,
transcript segment/child-file association, and review approval. Do not invent
those choices from this amendment. Tools 7–11 likewise retain their own
future build plans.
