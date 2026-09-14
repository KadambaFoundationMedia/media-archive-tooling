"""Representative 260-file acceptance evaluation for Tool 3 (Build Plan Section 36)."""
import json
from pathlib import Path

from media_archive_tooling.adapters.baserow import BaserowReferenceProvider
from media_archive_tooling.config import load_config
from media_archive_tooling.media_db_reviewer.baserow_provider import BaserowSnapshotProvider
from media_archive_tooling.media_db_reviewer.service import MediaDatabaseReviewService
from media_archive_tooling.renamer.logging.logger import RenamerLogger
from media_archive_tooling.renamer.models import RenameMode, ResolutionState
from media_archive_tooling.renamer.planner.executor import BatchExecutor
from media_archive_tooling.renamer.registry.registry import LocalRegistry
from media_archive_tooling.renamer.service import RenamerApplicationService
from media_archive_tooling.travel_reviewer.models import TravelReviewDecision
from media_archive_tooling.travel_reviewer.reference_store import TravelReferenceStore
from media_archive_tooling.travel_reviewer.service import TravelScheduleReviewService


def run_evaluation():
    config = load_config()
    sample_dir = Path("sample-files").resolve()
    eval_reg_path = Path(".renamer/eval_tool3_registry.db")
    if eval_reg_path.exists():
        eval_reg_path.unlink()

    eval_reg = LocalRegistry(eval_reg_path)
    logger = RenamerLogger(Path(".renamer/eval_logs"))
    ref_provider = BaserowReferenceProvider()

    print("=== Step 1: Tool 1 Fresh Structured Population ===")
    executor = BatchExecutor(
        registry=eval_reg,
        logger=logger,
        provider=ref_provider,
        mode=RenameMode.INITIAL,
    )
    proposals = executor.scan_directory(sample_dir)
    print(f"Scanned {len(proposals)} files from {sample_dir}")

    # Capture initial proposed filenames
    initial_proposals = {p.tracking_id: p.proposed_filename for p in proposals}
    initial_parser_results = {p.tracking_id: p.parser_result for p in proposals}

    print("\n=== Step 2: Tool 2 Media Database Review Context ===")
    provider = BaserowSnapshotProvider(
        api_url=config.baserow_api_url,
        api_token=config.baserow_api_token,
        media_table_id=config.baserow_media_table_id,
        category_table_id=config.baserow_category_table_id,
        travel_schedule_table_id=config.baserow_travel_schedule_table_id,
        snapshot_path=config.baserow_snapshot_path,
    )
    t2_service = MediaDatabaseReviewService(registry=eval_reg, provider=provider)
    t2_results = t2_service.review_batch(force_refresh=False, auto_enrich=True)
    t2_results_by_id = {r.tracking_id: r for r in t2_results}
    t2_counts = {}
    for r in t2_results:
        dec = r.decision.value
        t2_counts[dec] = t2_counts.get(dec, 0) + 1
    print(f"Evaluated {len(t2_results)} files through Tool 2:")
    for dec, c in sorted(t2_counts.items()):
        print(f"  {dec}: {c}")

    print("\n=== Step 3: Tool 3 Travel Schedule Review ===")
    ref_store = TravelReferenceStore(Path(".renamer/reference/travel_schedule.json"), provider=provider)
    manifest = ref_store.load_reference()
    if not manifest:
        print("ERROR: Verified reference could not be loaded!")
        return

    print(f"Using verified static reference (SHA: {manifest.canonical_sha256[:16]}, {manifest.row_count} rows)")

    t3_renamer = RenamerApplicationService(registry=eval_reg)
    t3_service = TravelScheduleReviewService(
        registry=eval_reg,
        reference_store=ref_store,
        renamer_service=t3_renamer,
    )

    t3_results = []
    downstream_from_t2 = 0
    overwritten_high_authority = 0

    for p in proposals:
        tid = p.tracking_id
        t2_res = t2_results_by_id.get(tid)
        is_routed = t2_res and "tool_3_travel_schedule_review" in t2_res.downstream_routing
        if is_routed:
            downstream_from_t2 += 1

        t3_res = t3_service.review_file(
            target=tid,
            tool2_context=t2_res,
            auto_enrich=True,
        )
        t3_results.append(t3_res)

        # Verification: ensure no high-authority local/confirmed-Media value was overwritten
        updated_rec = eval_reg.get_file(tid)
        init_pr = initial_parser_results[tid]
        # High-authority WHEN: full exact date (YYYY-MM-DD)
        if init_pr.when.state == ResolutionState.EXACT and len(init_pr.when.selected_value) == 10 and "DD" not in init_pr.when.selected_value and "MM" not in init_pr.when.selected_value:
            if updated_rec["when_val"] != init_pr.when.selected_value:
                overwritten_high_authority += 1
        # High-authority WHERE: exact place and country
        if init_pr.where.state == ResolutionState.EXACT and init_pr.where.place_location and init_pr.where.country_iso2:
            init_where = f"{init_pr.where.place_location}-{init_pr.where.country_iso2}".lower()
            curr_where = updated_rec["where_val"].lower()
            if curr_where != init_where:
                overwritten_high_authority += 1

    # Count decisions
    counts = {
        TravelReviewDecision.CORROBORATED.value: 0,
        TravelReviewDecision.PROVISIONAL_ENRICHMENT.value: 0,
        TravelReviewDecision.MULTIPLE_SCHEDULE_CANDIDATES.value: 0,
        TravelReviewDecision.SCHEDULE_CONFLICT.value: 0,
        TravelReviewDecision.NO_SCHEDULE_SUPPORT.value: 0,
        TravelReviewDecision.INSUFFICIENT_EVIDENCE.value: 0,
        TravelReviewDecision.REFERENCE_UNAVAILABLE.value: 0,
    }
    when_enrichments = 0
    where_enrichments = 0
    enriched_examples = []
    category_examples = {
        "CORROBORATED": [],
        "SCHEDULE_CONFLICT": [],
        "MULTIPLE_SCHEDULE_CANDIDATES": [],
        "NO_SCHEDULE_SUPPORT": [],
        "PROVISIONAL_ENRICHMENT": [],
        "INSUFFICIENT_EVIDENCE": [],
    }

    for r in t3_results:
        counts[r.decision.value] = counts.get(r.decision.value, 0) + 1
        if r.provisional_enrichment:
            if r.provisional_enrichment.when_val:
                when_enrichments += 1
            if r.provisional_enrichment.where_val:
                where_enrichments += 1
            rec = eval_reg.get_file(r.tracking_id)
            enriched_examples.append({
                "tracking_id": r.tracking_id,
                "before": initial_proposals[r.tracking_id],
                "after": rec["proposed_filename"],
                "when_val": r.provisional_enrichment.when_val,
                "where_val": r.provisional_enrichment.where_val,
                "when_state": rec["parser_result"]["when"]["state"],
                "where_state": rec["parser_result"]["where"]["state"],
            })

        if len(category_examples.get(r.decision.value, [])) < 3:
            rec = eval_reg.get_file(r.tracking_id)
            category_examples[r.decision.value].append({
                "tracking_id": r.tracking_id,
                "filename": rec["original_filename"],
                "notes": r.diagnostic_notes,
                "conflicts": r.conflicts,
                "candidates_count": len(r.candidates),
            })

    # Summary report
    print("\n==========================================")
    print("TOOL 3 REPRESENTATIVE EVALUATION REPORT")
    print("==========================================")
    print(f"total files reviewed: {len(t3_results)}")
    print(f"files entering Tool 3 from Tool 2/downstream routing: {downstream_from_t2}")
    print(f"CORROBORATED count: {counts['CORROBORATED']}")
    print(f"PROVISIONAL_ENRICHMENT count: {counts['PROVISIONAL_ENRICHMENT']}")
    print(f"  WHEN enrichments: {when_enrichments}")
    print(f"  WHERE enrichments: {where_enrichments}")
    print(f"MULTIPLE_SCHEDULE_CANDIDATES count: {counts['MULTIPLE_SCHEDULE_CANDIDATES']}")
    print(f"SCHEDULE_CONFLICT count: {counts['SCHEDULE_CONFLICT']}")
    print(f"NO_SCHEDULE_SUPPORT count: {counts['NO_SCHEDULE_SUPPORT']}")
    print(f"INSUFFICIENT_EVIDENCE count: {counts['INSUFFICIENT_EVIDENCE']}")
    print(f"REFERENCE_UNAVAILABLE count: {counts['REFERENCE_UNAVAILABLE']}")
    print(f"Media-context-unavailable count: {sum(1 for r in t3_results if r.tool2_context_state in ('UNAVAILABLE', 'DATABASE_UNAVAILABLE'))}")
    print(f"number of schedule enrichments applied to Tool 1: {len(enriched_examples)}")
    print(f"number of high-priority local/confirmed-Media values overwritten: {overwritten_high_authority}")

    # Output JSON summary for walkthrough documentation
    summary_data = {
        "total_reviewed": len(t3_results),
        "downstream_from_t2": downstream_from_t2,
        "tool2_counts": t2_counts,
        "counts": counts,
        "when_enrichments": when_enrichments,
        "where_enrichments": where_enrichments,
        "overwritten_high_authority": overwritten_high_authority,
        "enriched_examples": enriched_examples[:10],
        "category_examples": category_examples,
    }
    Path("docs/eval_summary_tool3.json").write_text(json.dumps(summary_data, indent=2), encoding="utf-8")
    print("\nSaved evaluation summary to docs/eval_summary_tool3.json")


if __name__ == "__main__":
    run_evaluation()
