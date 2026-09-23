"""Representative 260-file acceptance evaluation for Tool 4 (Build Plan Section 23).

Runs safe write-preview synchronization over sample-files across Tool 1 -> Tool 2 -> Tool 3 -> Tool 4
without bulk-writing production Baserow.
"""
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
from typing import Any, Dict, List, Optional

from media_archive_tooling.adapters.baserow import BaserowReferenceProvider
from media_archive_tooling.config import load_config
from media_archive_tooling.media_db_reviewer.baserow_provider import BaserowSnapshotProvider
from media_archive_tooling.media_db_reviewer.models import (
    MediaDatabaseReviewResult,
    ReviewDecision,
)
from media_archive_tooling.media_db_reviewer.service import MediaDatabaseReviewService
from media_archive_tooling.media_db_updater.models import (
    FieldAction,
    SyncOperation,
    SyncStatus,
)
from media_archive_tooling.media_db_updater.service import MediaDatabaseUpdaterService
from media_archive_tooling.media_db_updater.write_adapter import BaserowWriteAdapter
from media_archive_tooling.renamer.logging.logger import RenamerLogger
from media_archive_tooling.renamer.models import ParserResult, RenameMode
from media_archive_tooling.renamer.planner.executor import BatchExecutor
from media_archive_tooling.renamer.registry.registry import LocalRegistry
from media_archive_tooling.renamer.service import RenamerApplicationService
from media_archive_tooling.travel_reviewer.models import TravelReviewDecision
from media_archive_tooling.travel_reviewer.reference_store import TravelReferenceStore
from media_archive_tooling.travel_reviewer.service import (
    TravelScheduleReviewService,
    validate_tool3_review_result,
)

logger = logging.getLogger(__name__)


def make_portable(data: Any) -> Any:
    """Sanitize machine-local paths into repository-relative portable paths (R-034)."""
    if isinstance(data, str):
        s = data.replace(str(Path.cwd()), ".")
        s = re.sub(r"/Users/[^/]+/dev/media-archive-tooling/?", "./", s)
        s = re.sub(r"/Users/[^/]+/dev/Media-renaming/?", "Media-renaming/", s)
        s = re.sub(r"/Users/[^/]+/", "~/", s)
        return s
    elif isinstance(data, dict):
        return {k: make_portable(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [make_portable(v) for v in data]
    return data


DEFAULT_MAX_EVAL_FILES = 30
DEFAULT_MAX_EVAL_BYTES = 500 * 1024 * 1024  # 500 MB preflight budget


def select_and_copy_bounded_evaluation_media(
    sample_dir: Path,
    eval_media_dir: Path,
    max_files: int = DEFAULT_MAX_EVAL_FILES,
    max_bytes: int = DEFAULT_MAX_EVAL_BYTES,
) -> List[Path]:
    """Select a diverse bounded subset of sample media files and copy within budget (R-052).

    Never copies the full archive or sample-files directory wholesale. Preserves source files.
    """
    eval_media_dir.mkdir(parents=True, exist_ok=True)

    # 1. Preflight disk space verification
    try:
        usage = shutil.disk_usage(eval_media_dir.parent)
        required_free = max_bytes * 2
        if usage.free < required_free:
            raise RuntimeError(
                f"Insufficient disk space for evaluation: {usage.free} bytes free, "
                f"{required_free} bytes required (budget {max_bytes} bytes)."
            )
    except OSError as e:
        logger.warning(f"Could not check disk usage: {e}")

    # 2. Discover media files in sample_dir
    from media_archive_tooling.orchestrator.discovery import is_supported_media_file
    from media_archive_tooling.renamer.planner.executor import is_ignored_file

    candidates: List[Path] = []
    for root, dirs, files in os.walk(sample_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fname in files:
            p = Path(root) / fname
            if not is_ignored_file(p) and is_supported_media_file(p):
                candidates.append(p)

    candidates.sort(key=lambda p: str(p))

    # 3. Select a bounded, representative subset
    selected: List[Path] = []
    total_bytes = 0

    for cand in candidates:
        if len(selected) >= max_files:
            break
        sz = cand.stat().st_size
        if total_bytes + sz > max_bytes and len(selected) > 0:
            continue
        selected.append(cand)
        total_bytes += sz

    # 4. Copy selected subset into eval_media_dir preserving relative structure
    copied: List[Path] = []
    for src in selected:
        rel = src.relative_to(sample_dir)
        dst = eval_media_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(dst)

    print(f"Bounded evaluation copy: copied {len(copied)} files ({total_bytes / (1024 * 1024):.1f} MB) into {eval_media_dir}")
    return copied


def run_evaluation(
    sample_dir: Optional[Path] = None,
    eval_workspace: Optional[Path] = None,
    max_files: int = DEFAULT_MAX_EVAL_FILES,
    max_bytes: int = DEFAULT_MAX_EVAL_BYTES,
    skip_git_check: bool = False,
    output_summary_path: Optional[Path] = None,
):
    import subprocess
    if not skip_git_check:
        dirty = subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()
        if dirty:
            raise RuntimeError(
                "Evaluation refused: working tree is dirty. R-027 requires evaluation to run from a clean tree at an exact committed implementation head.\n"
                f"{dirty}"
            )
        commit_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    else:
        commit_sha = "test_eval_head"

    config = load_config()
    sample_path = (sample_dir or Path("sample-files")).resolve()
    workspace_path = (eval_workspace or Path(".renamer/eval_workspace")).resolve()
    if workspace_path.exists():
        shutil.rmtree(workspace_path)
    eval_media_dir = workspace_path / "media"

    select_and_copy_bounded_evaluation_media(
        sample_dir=sample_path,
        eval_media_dir=eval_media_dir,
        max_files=max_files,
        max_bytes=max_bytes,
    )

    eval_reg_path = workspace_path / "eval_tool4_registry.db"
    eval_reg = LocalRegistry(eval_reg_path)
    renamer_logger = RenamerLogger(workspace_path / "logs")
    ref_provider = BaserowReferenceProvider()

    print("=== Step 1: Tool 1 Fresh Structured Population ===")
    executor = BatchExecutor(
        registry=eval_reg,
        logger=renamer_logger,
        provider=ref_provider,
        mode=RenameMode.INITIAL,
    )
    initial_proposals = executor.scan_directory(eval_media_dir)
    initial_proposals_by_id = {p.tracking_id: p for p in initial_proposals}
    print(f"Scanned {len(initial_proposals)} files from {eval_media_dir}")

    print("\n=== Step 2: Tool 2 Media Database Review Context ===")
    t2_provider = BaserowSnapshotProvider(
        api_url=config.baserow_api_url,
        api_token=config.baserow_api_token,
        media_table_id=config.baserow_media_table_id,
        category_table_id=config.baserow_category_table_id,
        travel_schedule_table_id=config.baserow_travel_schedule_table_id,
        snapshot_path=config.baserow_snapshot_path,
    )
    t2_service = MediaDatabaseReviewService(registry=eval_reg, provider=t2_provider)
    t2_results = t2_service.review_batch(force_refresh=False, auto_enrich=True)
    t2_results_by_id = {r.tracking_id: r for r in t2_results}

    t2_counts = {}
    for r in t2_results:
        dec = r.decision.value
        t2_counts[dec] = t2_counts.get(dec, 0) + 1
    print(f"Evaluated {len(t2_results)} files through Tool 2:")
    for dec, c in sorted(t2_counts.items()):
        print(f"  {dec}: {c}")

    if not t2_results:
        raise RuntimeError("Evaluation failed: Tool 2 returned 0 review results")

    t2_states = set()
    t2_timestamps = set()
    for r in t2_results:
        if not isinstance(r, MediaDatabaseReviewResult):
            raise RuntimeError(f"Tool 2 result for {r.tracking_id} is not a MediaDatabaseReviewResult: {type(r)}")
        if r.live_read_complete is not True:
            raise RuntimeError(f"Tool 2 result for {r.tracking_id} has live_read_complete != True")
        if r.snapshot_complete is not True:
            raise RuntimeError(f"Tool 2 result for {r.tracking_id} has snapshot_complete != True")
        if r.decision in (ReviewDecision.EXISTING_MEDIA_MATCH, ReviewDecision.NEW_MEDIA_CANDIDATE):
            if r.baserow_check_complete is not True:
                raise RuntimeError(f"Tool 2 result for {r.tracking_id} with decision {r.decision} has baserow_check_complete != True")
        if r.database_state not in ("LIVE_CURRENT", "LIVE_COMPLETE"):
            raise RuntimeError(f"Tool 2 result for {r.tracking_id} has non-live database_state: {r.database_state}")
        read_ts = (r.baserow_read_at or r.database_snapshot_at or "").strip()
        if not read_ts:
            raise RuntimeError(f"Tool 2 result for {r.tracking_id} has missing live read timestamp")
        if r.decision == ReviewDecision.DATABASE_UNAVAILABLE:
            raise RuntimeError(f"Tool 2 result for {r.tracking_id} reported DATABASE_UNAVAILABLE")
        t2_states.add(r.database_state)
        t2_timestamps.add(read_ts)

    if len(t2_states) != 1:
        raise RuntimeError(f"Tool 2 database_state is inconsistent across batch: {t2_states}")
    t2_db_state = list(t2_states)[0]
    t2_read_timestamp = sorted(t2_timestamps)[-1]

    print("\n=== Step 3: Tool 3 Travel Schedule Review Context ===")
    # Tool 3 receives only local verified schedule artifact without Baserow provider (amendment section 1 & 5)
    ref_store = TravelReferenceStore(Path(".renamer/reference/travel_schedule.json"))
    manifest = ref_store.load_reference()
    if manifest:
        print(f"Using verified travel schedule reference ({manifest.row_count} rows)")
    t3_renamer = RenamerApplicationService(registry=eval_reg)
    t3_service = TravelScheduleReviewService(
        registry=eval_reg,
        reference_store=ref_store,
        renamer_service=t3_renamer,
    )

    t3_results = []
    for p in initial_proposals:
        tid = p.tracking_id
        t2_res = t2_results_by_id.get(tid)
        t3_res = t3_service.review_file(
            target=tid,
            tool2_context=t2_res,
            auto_enrich=True,
        )
        valid_t3, val_err = validate_tool3_review_result(t3_res, tid)
        if val_err or valid_t3 is None:
            raise RuntimeError(f"Tool 3 result for {tid} failed contract validation: {val_err}")
        if valid_t3.decision in (TravelReviewDecision.REFERENCE_UNAVAILABLE, TravelReviewDecision.PROCESSING_ERROR):
            raise RuntimeError(f"Tool 3 result for {tid} failed with {valid_t3.decision.value}")
        t3_results.append(valid_t3)
    print(f"Evaluated {len(t3_results)} files through Tool 3")

    print("\n=== Step 4: Final Proposal Generation (RenameMode.FINALIZE) ===")
    final_executor = BatchExecutor(
        registry=eval_reg,
        logger=renamer_logger,
        provider=ref_provider,
        mode=RenameMode.FINALIZE,
    )
    proposals_by_dir = {}
    for p in initial_proposals:
        rec = eval_reg.get_file(p.tracking_id)
        if rec and rec.get("parser_result"):
            pr = ParserResult.model_validate(rec["parser_result"])
            prop = final_executor.planner.plan_rename(pr)
            p_dir = Path(prop.original_path).parent
            proposals_by_dir.setdefault(p_dir, []).append(prop)

    final_proposals = []
    for p_dir, p_list in proposals_by_dir.items():
        resolved = final_executor.planner.resolve_batch_collisions(p_list)
        for r in resolved:
            # Simulate human review approval so all proposals commit in isolated evaluation workspace
            if r.needs_review:
                r.needs_review = False
                r.status = "approved"
            eval_reg.save_proposal(r)
            final_proposals.append(r)
    print(f"Generated final proposals for {len(final_proposals)} files")

    print("\n=== Step 4b: Committing Final Renames in Isolated Workspace ===")
    committed_proposals = final_executor.commit_proposals(final_proposals)
    committed_count = sum(1 for p in committed_proposals if p.status == "committed")
    unchanged_count = sum(1 for p in committed_proposals if p.status == "skipped_unchanged")
    print(f"Committed {committed_count} renames to disk ({unchanged_count} already canonical).")
    proposals = final_proposals

    print("\n=== Step 5: Tool 4 Media Database Synchronization Preview ===")
    write_adapter = BaserowWriteAdapter(
        api_url=config.baserow_api_url,
        api_token=config.baserow_api_token,
        media_table_id=config.baserow_media_table_id,
    )
    updater_service = MediaDatabaseUpdaterService(
        registry=eval_reg,
        write_adapter=write_adapter,
        tool2_service=t2_service,
    )

    # Metrics required by Section 23
    total_files = len(proposals)
    would_update = 0
    would_create = 0
    noop_count = 0
    review_required_conflicts = 0
    duplicate_multiple_candidate_blocked = 0
    insufficient_evidence_blocked = 0
    database_unavailable = 0
    partial_date_notes_cases = 0
    country_location_options_proposed = 0
    archive_path_conflicts = 0

    results_by_tid: Dict[str, Any] = {}
    detailed_results: List[Dict[str, Any]] = []
    representative_diffs: Dict[str, List[Any]] = {
        "matched_updates": [],
        "candidate_creates": [],
        "partial_date_notes": [],
        "conflict_blocked": [],
    }

    # Cache fields schema once for option check
    try:
        fields = write_adapter.fetch_table_fields()
        fields_by_name = {f["name"].lower(): f for f in fields}
        country_fld = fields_by_name.get("country", {})
        existing_countries = {
            opt["value"].strip().lower() for opt in country_fld.get("select_options", [])
        }
        loc_fld = fields_by_name.get("place, location", {})
        existing_locations = {
            opt["value"].strip().lower() for opt in loc_fld.get("select_options", [])
        }
    except Exception as e:
        logger.warning(f"Could not pre-fetch fields for schema option inspection: {e}")
        existing_countries = set()
        existing_locations = set()

    representative_identities: List[Dict[str, Any]] = []

    for p in proposals:
        tid = p.tracking_id
        req = updater_service.build_sync_request(tid)

        # R-038 assertion: Tool 4 request filename/path matches finalized Tool 1 output
        if req.current_filename != p.proposed_filename:
            raise AssertionError(
                f"R-038 assertion failure for {tid}: Tool 4 request current_filename '{req.current_filename}' "
                f"does not match finalized Tool 1 proposed_filename '{p.proposed_filename}'"
            )
        if req.current_path != p.proposed_path:
            raise AssertionError(
                f"R-038 assertion failure for {tid}: Tool 4 request current_path '{req.current_path}' "
                f"does not match finalized Tool 1 proposed_path '{p.proposed_path}'"
            )
        if not Path(req.current_path).exists():
            raise AssertionError(
                f"R-038 assertion failure for {tid}: committed file does not exist on disk at '{req.current_path}'"
            )

        # Safe write-preview mode (commit=False)
        res = updater_service.preview(tid)
        results_by_tid[tid] = res

        # Check operations
        if res.operation == SyncOperation.UPDATE:
            would_update += 1
        elif res.operation == SyncOperation.CREATE:
            would_create += 1
        elif res.operation == SyncOperation.NOOP:
            noop_count += 1

        # Check status and conflicts
        if res.status == SyncStatus.REVIEW_REQUIRED:
            review_required_conflicts += 1

        t2_res = t2_results_by_id.get(tid)
        t2_dec = t2_res.decision.value if t2_res else ""

        if t2_dec == "MULTIPLE_CANDIDATES" or (res.status == SyncStatus.FAILED_BLOCKED and "MULTIPLE_CANDIDATES" in str(res.conflicts)):
            duplicate_multiple_candidate_blocked += 1
        elif t2_dec == "INSUFFICIENT_EVIDENCE":
            insufficient_evidence_blocked += 1

        if res.status == SyncStatus.FAILED_RETRYABLE or t2_dec == "DATABASE_UNAVAILABLE":
            database_unavailable += 1

        # Partial-date Notes cases
        has_partial_date = any("Incomplete date stored in Notes" in str(d.details or "") for d in res.field_diffs)
        has_partial_date_note = any("Incomplete recording date" in str(d.new_value or "") for d in res.field_diffs if d.field_name == "Notes")
        if has_partial_date or has_partial_date_note:
            partial_date_notes_cases += 1

        # Country / location option additions proposed
        proposed_new_opt = False
        for diff in res.field_diffs:
            if diff.field_name == "Country" and diff.action == FieldAction.SET and diff.new_value:
                val = str(diff.new_value).strip().lower()
                if val and val not in existing_countries:
                    proposed_new_opt = True
            elif diff.field_name == "Place, location" and diff.action == FieldAction.SET and diff.new_value:
                val = str(diff.new_value).strip().lower()
                if val and val not in existing_locations:
                    proposed_new_opt = True
        if proposed_new_opt:
            country_location_options_proposed += 1

        # Archive path conflicts
        has_path_conflict = any("media_archive_path" in c for c in res.conflicts)
        if has_path_conflict:
            archive_path_conflicts += 1

        # Representative diffs collection (R-011, R-021)
        if res.operation == SyncOperation.UPDATE and len(representative_diffs["matched_updates"]) < 3:
            representative_diffs["matched_updates"].append({
                "tracking_id": tid,
                "filename": p.current_filename,
                "media_row_id": res.media_row_id,
                "fields_preserved": res.fields_preserved,
                "field_diffs": [d.model_dump() for d in res.field_diffs],
            })
        if res.operation == SyncOperation.CREATE and len(representative_diffs["candidate_creates"]) < 3:
            representative_diffs["candidate_creates"].append({
                "tracking_id": tid,
                "filename": p.current_filename,
                "fields_preserved": res.fields_preserved,
                "field_diffs": [d.model_dump() for d in res.field_diffs],
            })
        if (has_partial_date or has_partial_date_note) and len(representative_diffs["partial_date_notes"]) < 3:
            representative_diffs["partial_date_notes"].append({
                "tracking_id": tid,
                "filename": p.current_filename,
                "fields_preserved": res.fields_preserved,
                "field_diffs": [d.model_dump() for d in res.field_diffs],
            })
        if res.status == SyncStatus.REVIEW_REQUIRED and len(representative_diffs["conflict_blocked"]) < 3:
            representative_diffs["conflict_blocked"].append({
                "tracking_id": tid,
                "filename": p.current_filename,
                "conflicts": res.conflicts,
                "fields_preserved": res.fields_preserved,
                "field_diffs": [d.model_dump() for d in res.field_diffs],
            })

        # Representative identities (R-038)
        init_p = initial_proposals_by_id[tid]
        before_fn = Path(init_p.original_path).name
        if res.operation == SyncOperation.UPDATE and not any(i["category"] == "matched_update" for i in representative_identities):
            representative_identities.append({
                "category": "matched_update",
                "tracking_id": tid,
                "before_filename": before_fn,
                "initial_proposed_filename": init_p.proposed_filename,
                "final_proposed_filename": p.proposed_filename,
                "committed_filename_on_disk": Path(req.current_path).name,
                "tool4_request_current_filename": req.current_filename,
                "tool4_request_original_filename": req.original_filename,
                "tool4_operation": res.operation.value,
                "tool4_status": res.status.value,
                "tool2_decision": t2_dec,
            })
        elif res.operation == SyncOperation.CREATE and not any(i["category"] == "candidate_create" for i in representative_identities):
            representative_identities.append({
                "category": "candidate_create",
                "tracking_id": tid,
                "before_filename": before_fn,
                "initial_proposed_filename": init_p.proposed_filename,
                "final_proposed_filename": p.proposed_filename,
                "committed_filename_on_disk": Path(req.current_path).name,
                "tool4_request_current_filename": req.current_filename,
                "tool4_request_original_filename": req.original_filename,
                "tool4_operation": res.operation.value,
                "tool4_status": res.status.value,
                "tool2_decision": t2_dec,
            })
        elif (has_partial_date or has_partial_date_note) and not any(i["category"] == "partial_date_notes" for i in representative_identities):
            representative_identities.append({
                "category": "partial_date_notes",
                "tracking_id": tid,
                "before_filename": before_fn,
                "initial_proposed_filename": init_p.proposed_filename,
                "final_proposed_filename": p.proposed_filename,
                "committed_filename_on_disk": Path(req.current_path).name,
                "tool4_request_current_filename": req.current_filename,
                "tool4_request_original_filename": req.original_filename,
                "tool4_operation": res.operation.value,
                "tool4_status": res.status.value,
                "tool2_decision": t2_dec,
            })
        elif res.status == SyncStatus.REVIEW_REQUIRED and not any(i["category"] == "conflict_blocked" for i in representative_identities):
            representative_identities.append({
                "category": "conflict_blocked",
                "tracking_id": tid,
                "before_filename": before_fn,
                "initial_proposed_filename": init_p.proposed_filename,
                "final_proposed_filename": p.proposed_filename,
                "committed_filename_on_disk": Path(req.current_path).name,
                "tool4_request_current_filename": req.current_filename,
                "tool4_request_original_filename": req.original_filename,
                "tool4_operation": res.operation.value,
                "tool4_status": res.status.value,
                "tool2_decision": t2_dec,
            })
        elif before_fn == p.proposed_filename and not any(i["category"] == "unchanged_already_canonical" for i in representative_identities):
            representative_identities.append({
                "category": "unchanged_already_canonical",
                "tracking_id": tid,
                "before_filename": before_fn,
                "initial_proposed_filename": init_p.proposed_filename,
                "final_proposed_filename": p.proposed_filename,
                "committed_filename_on_disk": Path(req.current_path).name,
                "tool4_request_current_filename": req.current_filename,
                "tool4_request_original_filename": req.original_filename,
                "tool4_operation": res.operation.value,
                "tool4_status": res.status.value,
                "tool2_decision": t2_dec,
            })

        detailed_results.append({
            "tracking_id": tid,
            "filename": p.current_filename,
            "tool2_decision": t2_dec,
            "status": res.status.value,
            "operation": res.operation.value,
            "media_row_id": res.media_row_id,
            "fields_modified": res.fields_modified,
            "fields_preserved": res.fields_preserved,
            "conflicts": res.conflicts,
            "diagnostic_notes": res.diagnostic_notes,
        })

    # Output Section 23 formatted report
    print("\n==========================================")
    print("TOOL 4 REPRESENTATIVE EVALUATION REPORT")
    print("==========================================")
    print(f"total files: {total_files}")
    print(f"would-update existing rows: {would_update}")
    print(f"would-create new rows: {would_create}")
    print(f"no-op/already synchronized: {noop_count}")
    print(f"review-required conflicts: {review_required_conflicts}")
    print(f"duplicate/multiple-candidate blocked: {duplicate_multiple_candidate_blocked}")
    print(f"insufficient-evidence blocked: {insufficient_evidence_blocked}")
    print(f"database-unavailable: {database_unavailable}")
    print(f"partial-date Notes cases: {partial_date_notes_cases}")
    print(f"country/location option additions proposed: {country_location_options_proposed}")
    print(f"archive-path representation conflicts: {archive_path_conflicts}")

    import subprocess
    from datetime import datetime, timezone
    ref_checksums = {}
    if manifest:
        ref_checksums["travel_schedule_sha256"] = manifest.canonical_sha256
        ref_checksums["travel_schedule_row_count"] = manifest.row_count
    if config.baserow_snapshot_path and Path(config.baserow_snapshot_path).exists():
        snap_content = Path(config.baserow_snapshot_path).read_bytes()
        ref_checksums["snapshot_sha256"] = hashlib.sha256(snap_content).hexdigest()

    eval_summary = {
        "evaluated_commit": commit_sha,
        "clean_worktree_confirmed": True,
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "tool2_live_database_state": t2_db_state,
        "tool2_read_timestamp": t2_read_timestamp,
        "reference_checksums": ref_checksums,
        "live_reference_info": {
            "media_table_id": config.baserow_media_table_id,
            "category_table_id": config.baserow_category_table_id,
            "travel_schedule_table_id": config.baserow_travel_schedule_table_id,
            "snapshot_path": str(config.baserow_snapshot_path) if config.baserow_snapshot_path else None,
        },
        "total_files": total_files,
        "would_update_existing_rows": would_update,
        "would_create_new_rows": would_create,
        "noop_already_synchronized": noop_count,
        "review_required_conflicts": review_required_conflicts,
        "duplicate_multiple_candidate_blocked": duplicate_multiple_candidate_blocked,
        "insufficient_evidence_blocked": insufficient_evidence_blocked,
        "database_unavailable": database_unavailable,
        "partial_date_notes_cases": partial_date_notes_cases,
        "country_location_option_additions_proposed": country_location_options_proposed,
        "archive_path_representation_conflicts": archive_path_conflicts,
        "tool2_counts": t2_counts,
        "representative_identities": representative_identities,
        "representative_diffs": representative_diffs,
        "sample_detailed_results": detailed_results[:20],
    }

    out_path = Path(output_summary_path or "docs/eval_summary_tool4.json")
    portable_summary = make_portable(eval_summary)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(portable_summary, indent=2), encoding="utf-8")
    print(f"\nSaved evaluation evidence to {out_path}")
    return eval_summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run Tool 4 representative evaluation with bounded copy.")
    parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_EVAL_FILES, help="Maximum number of files to copy and evaluate")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_EVAL_BYTES, help="Maximum total bytes budget for copied files")
    parser.add_argument("--sample-dir", type=Path, default=None, help="Source sample-files directory")
    parser.add_argument("--workspace", type=Path, default=None, help="Evaluation workspace directory")
    parser.add_argument("--skip-git-check", action="store_true", help="Skip clean git worktree check")
    parser.add_argument("--output-summary", type=Path, default=None, help="Output summary JSON path")
    args = parser.parse_args()

    run_evaluation(
        sample_dir=args.sample_dir,
        eval_workspace=args.workspace,
        max_files=args.max_files,
        max_bytes=args.max_bytes,
        skip_git_check=args.skip_git_check,
        output_summary_path=args.output_summary,
    )
