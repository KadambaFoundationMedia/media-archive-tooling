"""Representative 260-file acceptance evaluation for Tool 4 (Build Plan Section 23).

Runs safe write-preview synchronization over sample-files across Tool 1 -> Tool 2 -> Tool 3 -> Tool 4
without bulk-writing production Baserow.
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from media_archive_tooling.adapters.baserow import BaserowReferenceProvider
from media_archive_tooling.config import load_config
from media_archive_tooling.media_db_reviewer.baserow_provider import BaserowSnapshotProvider
from media_archive_tooling.media_db_reviewer.service import MediaDatabaseReviewService
from media_archive_tooling.media_db_updater.models import (
    FieldAction,
    SyncOperation,
    SyncStatus,
)
from media_archive_tooling.media_db_updater.service import MediaDatabaseUpdaterService
from media_archive_tooling.media_db_updater.write_adapter import BaserowWriteAdapter
from media_archive_tooling.renamer.logging.logger import RenamerLogger
from media_archive_tooling.renamer.models import RenameMode
from media_archive_tooling.renamer.planner.executor import BatchExecutor
from media_archive_tooling.renamer.registry.registry import LocalRegistry
from media_archive_tooling.renamer.service import RenamerApplicationService
from media_archive_tooling.travel_reviewer.reference_store import TravelReferenceStore
from media_archive_tooling.travel_reviewer.service import TravelScheduleReviewService

logger = logging.getLogger(__name__)


def run_evaluation():
    config = load_config()
    sample_dir = Path("sample-files").resolve()
    eval_reg_path = Path(".renamer/eval_tool4_registry.db")
    if eval_reg_path.exists():
        eval_reg_path.unlink()

    eval_reg = LocalRegistry(eval_reg_path)
    renamer_logger = RenamerLogger(Path(".renamer/eval_tool4_logs"))
    ref_provider = BaserowReferenceProvider()

    print("=== Step 1: Tool 1 Fresh Structured Population ===")
    executor = BatchExecutor(
        registry=eval_reg,
        logger=renamer_logger,
        provider=ref_provider,
        mode=RenameMode.INITIAL,
    )
    proposals = executor.scan_directory(sample_dir)
    print(f"Scanned {len(proposals)} files from {sample_dir}")

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

    print("\n=== Step 3: Tool 3 Travel Schedule Review Context ===")
    ref_store = TravelReferenceStore(Path(".renamer/reference/travel_schedule.json"), provider=t2_provider)
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
    for p in proposals:
        tid = p.tracking_id
        t2_res = t2_results_by_id.get(tid)
        t3_res = t3_service.review_file(
            target=tid,
            tool2_context=t2_res,
            auto_enrich=True,
        )
        t3_results.append(t3_res)
    print(f"Evaluated {len(t3_results)} files through Tool 3")

    print("\n=== Step 4: Tool 4 Media Database Synchronization Preview ===")
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

    for p in proposals:
        tid = p.tracking_id
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

        # Representative diffs collection (R-011)
        if res.operation == SyncOperation.UPDATE and len(representative_diffs["matched_updates"]) < 3:
            representative_diffs["matched_updates"].append({
                "tracking_id": tid,
                "filename": p.current_filename,
                "media_row_id": res.media_row_id,
                "field_diffs": [d.model_dump() for d in res.field_diffs],
            })
        if res.operation == SyncOperation.CREATE and len(representative_diffs["candidate_creates"]) < 3:
            representative_diffs["candidate_creates"].append({
                "tracking_id": tid,
                "filename": p.current_filename,
                "field_diffs": [d.model_dump() for d in res.field_diffs],
            })
        if (has_partial_date or has_partial_date_note) and len(representative_diffs["partial_date_notes"]) < 3:
            representative_diffs["partial_date_notes"].append({
                "tracking_id": tid,
                "filename": p.current_filename,
                "field_diffs": [d.model_dump() for d in res.field_diffs],
            })
        if res.status == SyncStatus.REVIEW_REQUIRED and len(representative_diffs["conflict_blocked"]) < 3:
            representative_diffs["conflict_blocked"].append({
                "tracking_id": tid,
                "filename": p.current_filename,
                "conflicts": res.conflicts,
                "field_diffs": [d.model_dump() for d in res.field_diffs],
            })

        detailed_results.append({
            "tracking_id": tid,
            "filename": p.current_filename,
            "tool2_decision": t2_dec,
            "status": res.status.value,
            "operation": res.operation.value,
            "media_row_id": res.media_row_id,
            "fields_modified": res.fields_modified,
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

    eval_summary = {
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
        "representative_diffs": representative_diffs,
        "sample_detailed_results": detailed_results[:20],
    }

    out_path = Path("docs/eval_summary_tool4.json")
    out_path.write_text(json.dumps(eval_summary, indent=2), encoding="utf-8")
    print(f"\nSaved evaluation evidence to {out_path}")


if __name__ == "__main__":
    run_evaluation()
