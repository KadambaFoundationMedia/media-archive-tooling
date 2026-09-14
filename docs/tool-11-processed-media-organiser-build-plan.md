# Tool 11 — Processed Media Organiser Build Plan

Status: **DRAFT / DEFERRED — not implementation-ready**

This document records the requirements and safety architecture already agreed for Tool 11 so they are not lost while Tools 2–10 are completed. Detailed planning, archive-policy decisions, destination mapping, UI/CLI behavior, tests, and implementation acceptance criteria will be finalized only when the project reaches Tool 11.

Nothing in this draft authorizes Builder implementation yet.

## 1. Purpose

Tool 11 is the **Processed Media Organiser**.

Its job is to move fully processed media from its current processing location into the correct final archive location based on the resolved **media category / class or kirtan type** and the corresponding configured **media-category path**.

Tool 11 is a final-placement tool. It does not discover content type, classify a class, trim audio, change gain, update Baserow, or invent destination categories. It consumes conclusions produced by earlier tools.

Current sequence around Tool 11:

```text
Tool 5  Content Discoverer
Tool 6  File Cutter
Tool 7  Class Type Discoverer
Tool 8  Class Trimmer
Tool 9  Class Gain Booster
Tool 10 Questions Gain Booster
Tool 11 Processed Media Organiser
Tool 1  Renamer — final update/enrich pass
```

The exact ordering between final placement and the final Tool 1 filename pass must be reviewed when Tool 11 is finalized. The current project sequence places Tool 11 before the final Renamer pass.

## 2. Core archive-safety principle

A production archive file must never rely on an in-place media transformation as its only copy.

For any tool that changes media bytes, the safe model is:

```text
verified current file
        |
        +--> create new temporary output
                |
                +--> validate output
                        |
                 +------+------+
                 |             |
               fail           pass
                 |             |
        discard new output   promote new output
        keep verified input  to verified current version
```

Tool 11 itself primarily moves files rather than modifying media content, but it must preserve this provenance and must never treat an unverified processing output as ready for final archive placement.

This safety architecture also affects Tool 6, Tool 8, Tool 9, and Tool 10 and must be propagated into those plans when they are finalized.

## 3. Process files in situ; do not clone entire batches by default

The production workflow may process very large batches. Copying every incoming file into a second full-size temporary tree before work begins would create excessive disk-space requirements and unnecessary I/O.

The preferred model is **transactional per-file processing with a rolling checkpoint**, not a complete batch duplicate.

At a content-changing stage the system normally needs only:

```text
previous verified version
+
new unverified output for the file currently being processed
```

Once the new output has been validated and promoted to the verified current version, older intermediate derivatives may become eligible for cleanup according to the retention policy finalized later.

Batch concurrency must be bounded so temporary-space use remains predictable.

## 4. Managed temporary working area

Content-changing tools should write new outputs into a managed temporary working area rather than over the source file.

Conceptual location:

```text
.media-archive-work/
    <tracking-id>/
        <temporary-output>
        <other-stage-artifacts>
```

Requirements to carry forward:

- the working directory must be excluded from normal archive discovery/scanning;
- partial `.tmp` / work products must never be mistaken for new archive media;
- temporary files remain associated with the stable tracking ID;
- crash recovery must be able to determine whether an output is incomplete, created, verified, or promoted;
- where practical, the working area should be on the same filesystem/volume as the media being processed so final promotion can use atomic rename semantics;
- the temporary-root location and space quota should be configurable rather than hard-coded.

A complete archive-sized staging copy is not required by default.

## 5. Rolling processing state

The exact state model will be finalized later, but it should distinguish states conceptually equivalent to:

```text
VERIFIED_SOURCE
PROCESSING
OUTPUT_CREATED
OUTPUT_VERIFIED
PROMOTED
READY_FOR_FINAL_PLACEMENT
FINALIZED
FAILED_VALIDATION
```

A crash or restart must not require guessing which physical file is authoritative.

Only a verified/promoted file may advance to the next destructive/content-changing stage. Only `READY_FOR_FINAL_PLACEMENT` media may be handed to Tool 11.

## 6. Content hashes and lineage

The managed workflow should store a strong content hash for the current verified file, preferably SHA-256 unless a later implementation review selects an equally strong project-wide standard.

Conceptual provenance:

```text
tracking ID
  source path
  source hash
  transformation history
  current verified path
  current verified hash
```

A pure rename or same-filesystem move must preserve the content hash.

A content transformation intentionally produces a new hash and records lineage, for example:

```text
source hash
   -> Tool 8 trim
trimmed hash
   -> Tool 9 gain adjustment
gain-adjusted hash
```

The hash is not a substitute for backup, but it provides corruption detection, move verification, and an auditable relationship between source and processed derivatives.

## 7. Validation before promotion

A newly generated media output must be validated before it can replace the previous verified version in the workflow.

Validation should include, as appropriate for the format/transformation:

- file exists and is non-zero;
- media container can be opened/read;
- expected audio/video streams are present;
- duration is sane for the requested operation;
- output size is plausible;
- transformation-specific invariants pass;
- content hash is calculated and recorded after successful output creation;
- where a transformation expects multiple outputs, every required output validates before the source can be retired from that processing step.

Validation details belong primarily to the content-changing tool that creates the output, but Tool 11 must require evidence that validation was completed.

## 8. Special rule for Tool 6 — combination files

For a combination file, the original source must remain authoritative until **all required split outputs** have been created and validated.

Conceptually:

```text
original combination file
        |
        +--> part A temporary output
        +--> part B temporary output
        +--> ...
                 |
           validate every part
                 |
         +-------+-------+
         |               |
       failure          success
         |               |
 keep original      promote full set
```

A partially successful split must not cause the original to be discarded.

## 9. Space management

Large-batch processing must not assume enough free space exists for a duplicate of the complete batch.

The later implementation should support controls conceptually equivalent to:

- configurable maximum concurrent content-changing jobs;
- configurable minimum free-space reserve;
- configurable working-space quota;
- estimate required output space before starting a transformation where practical;
- pause/refuse a new destructive stage when the safety reserve cannot be maintained;
- cleanup only artifacts that are no longer required for rollback under the finalized retention policy.

Space pressure must cause a controlled blocked state, not an unsafe overwrite of the verified source.

## 10. Optional filesystem optimizations

Filesystem-specific facilities may be used as optimizations but must not be required for correctness.

On APFS, copy-on-write clones or snapshots may provide cheap additional protection or faster staging in some deployments. They are optional enhancements.

Hard links are **not** an acceptable substitute for the verified-source/new-output model because both directory entries refer to the same underlying file data and therefore do not protect against in-place modification.

The core safety guarantee must work independently of APFS-specific behavior.

## 11. Tool 11 input boundary

Tool 11 should operate only on media whose preceding workflow is complete enough to make final placement safe.

Expected input evidence will likely include:

```text
tracking_id
current verified path
current verified content hash
media category / class or kirtan type
resolved destination category/path key
processing-stage completion state
transformation lineage / audit history
final-placement readiness state
```

The exact typed contract will be designed when Tool 11 is planned.

Tool 11 must not infer a final category from an unresolved earlier-tool result simply to complete a move.

## 12. Destination mapping

Tool 11 places media according to the resolved **media category** and the matching **media-category path**.

The authoritative source of category-to-path mappings is intentionally not selected yet. It may be configuration, Baserow data, or another controlled project asset depending on the archive workflow at that time.

The future plan must define:

- canonical category identifiers;
- mapping from category/type to final path;
- handling of nested class/kirtan types;
- path normalization;
- unavailable or contradictory mappings;
- whether destination mappings are live shared data and therefore require the same live-current semantics as other Baserow-backed decisions.

Until this is finalized, Tool 11 must not be implemented.

## 13. Same-filesystem final placement

When source and destination are on the same filesystem and the destination is safe, Tool 11 should prefer an atomic filesystem rename/move rather than copying the media bytes.

Conceptually:

```text
verified processed file
        -> validate destination eligibility
        -> atomic move/rename
        -> verify resulting path and unchanged hash
        -> FINALIZED
```

The tool must never silently overwrite an existing destination file.

A failed move must leave a recoverable authoritative file and a clear registry state.

## 14. Cross-filesystem final placement

A move across filesystems cannot rely on a single atomic rename. The safe model is copy-verify-promote-delete:

```text
verified source
   -> copy to temporary destination name
   -> close/flush completed output
   -> validate destination file
   -> verify destination hash equals source hash
   -> atomically rename destination temporary file to final destination name
   -> only then remove source
   -> verify final state
```

If any pre-delete step fails, the source remains untouched and authoritative.

The future implementation should record enough state to recover safely after interruption at any point in this sequence.

## 15. Collision and overwrite safety

Tool 11 must never use blind overwrite behavior.

The final policy for a destination path that already exists is deferred. Potential cases that must be distinguished later include:

- exact same content already present;
- same logical media item but different processed derivative;
- genuinely different media with colliding filename/path;
- abandoned partial destination artifact;
- unexpected manually created file.

The tool should stop or route for review according to the eventual policy rather than choosing first-match-wins or overwriting silently.

## 16. Idempotency and recovery

Rerunning Tool 11 after a successful final placement must not create a duplicate or attempt to move a now-missing old source path.

A restart after interruption should use registry state plus filesystem/hash verification to determine whether:

- source remains authoritative;
- a destination temporary copy can be safely resumed or discarded;
- final destination has already been promoted;
- source removal is the only remaining step;
- the state is contradictory and requires review.

No recovery path may infer success merely because the old source path is absent.

## 17. Relationship to final Renamer pass

The project currently places the final Tool 1 Renamer update/enrich pass after Tool 11.

When Tool 11 is finalized we must explicitly confirm whether:

- Tool 11 moves a file using its already-current processed filename and Tool 1 then performs the last rename in the final directory;
- Tool 1 should instead finalize the filename before Tool 11 computes the final destination path;
- destination mapping depends only on category/type and therefore is independent of filename.

Whichever sequence is selected must avoid unnecessary cross-filesystem copies or ambiguous registry paths.

## 18. Production backup preflight

Transactional processing protects against tool crashes and corrupt transformation outputs, but it is not a substitute for an independent backup against disk, filesystem, hardware, theft, or catastrophic operator failure.

For production archive batches, the workflow should support a preflight or operational rule confirming that the source set is covered by an external backup before destructive/content-changing processing proceeds.

The exact enforcement level (warning, explicit operator acknowledgement, or machine-verifiable backup state) is deferred.

## 19. Audit requirements

Tool 11 should eventually record at least:

```text
tracking_id
source path
destination path
source hash
destination hash
media category / destination mapping used
same-filesystem vs cross-filesystem strategy
operation timestamps
validation result
move/copy/promote/delete steps
operator/reviewer action where relevant
failure/recovery history
```

This audit trail must make it possible to explain where a processed file went and why.

## 20. Deferred archive-policy decisions

These questions are deliberately postponed until Tool 11 planning begins:

- authoritative category-to-path mapping source;
- exact definition of `READY_FOR_FINAL_PLACEMENT`;
- whether Tool 1 final rename happens before or after physical placement;
- retention period for previous verified processing generations;
- cleanup policy for working artifacts after finalization;
- destination collision/duplicate policy;
- whether original unprocessed media remains permanently retained elsewhere;
- default temporary-work root and free-space thresholds;
- concurrency defaults for large batches;
- cross-volume/network-volume behavior;
- permissions/ownership/metadata preservation requirements;
- whether final placement may cross storage devices or archive tiers;
- backup-preflight enforcement level;
- portal/CLI review actions and batch controls.

These are planning questions, not Builder decisions.

## 21. Acceptance direction for later planning

When Tool 11 is eventually implemented, acceptance should include destructive-failure simulations rather than only happy-path moves. At minimum, later tests should cover:

- same-volume atomic placement with hash unchanged;
- cross-volume copy where source is retained until destination hash validation succeeds;
- interrupted copy;
- validation failure;
- insufficient temporary disk space;
- destination collision;
- rerun after successful finalization;
- restart after each transactional stage;
- batch isolation where one failure does not lose unrelated media;
- assurance that temporary work files are excluded from archive discovery;
- proof that no content-changing stage requires modifying the only verified copy in place.

Detailed fixtures, commands, performance targets, and user-facing workflows are deferred until the full Tool 11 planning session.
