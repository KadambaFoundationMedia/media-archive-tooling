"""Unified CLI entry point for media-archive-tooling."""
import argparse
import json
import sys
from typing import Optional, Any
from pathlib import Path

from .config import load_config
from .renamer.models import RenameMode, ParserResult
from .renamer.registry.registry import LocalRegistry
from .renamer.logging.logger import RenamerLogger
from .renamer.planner.executor import BatchExecutor
from .renamer.service import RenamerApplicationService
from .adapters.baserow import BaserowReferenceProvider
from .media_db_reviewer.baserow_provider import BaserowSnapshotProvider
from .media_db_reviewer.service import MediaDatabaseReviewService
from .travel_reviewer.models import TravelReviewDecision
from .travel_reviewer.reference_store import TravelReferenceStore
from .travel_reviewer.service import TravelScheduleReviewService, validate_tool3_review_result
from .media_db_updater import MediaDatabaseUpdaterService, BaserowWriteAdapter
from .orchestrator.models import WorkflowType
from .orchestrator.reporter import TerminalReporter
from .orchestrator.service import create_main_tooling_service, MainToolingScriptService
from .content_discoverer.service import ContentDiscovererService


def create_media_db_updater_service(
    registry: LocalRegistry,
    config: Optional[Any] = None,
    write_adapter: Optional[BaserowWriteAdapter] = None,
    tool2_service: Optional[MediaDatabaseReviewService] = None,
) -> MediaDatabaseUpdaterService:
    """Construct one correctly configured Tool 2 + Tool 4 service composition."""
    if config is None:
        config = load_config()
    if write_adapter is None:
        write_adapter = BaserowWriteAdapter(
            api_url=config.baserow_api_url,
            api_token=config.baserow_api_token,
            media_table_id=config.baserow_media_table_id,
        )
    if tool2_service is None:
        tool2_provider = BaserowSnapshotProvider(
            api_url=config.baserow_api_url,
            api_token=config.baserow_api_token,
            media_table_id=config.baserow_media_table_id,
            category_table_id=config.baserow_category_table_id,
            travel_schedule_table_id=config.baserow_travel_schedule_table_id,
        )
        tool2_service = MediaDatabaseReviewService(registry=registry, provider=tool2_provider)
    return MediaDatabaseUpdaterService(
        registry=registry,
        write_adapter=write_adapter,
        tool2_service=tool2_service,
    )


def run_renamer(args):
    config = load_config()
    target_path = Path(args.target).resolve()
    if not target_path.exists():
        print(f"Error: Target path does not exist: {target_path}", file=sys.stderr)
        sys.exit(1)

    mode = RenameMode(args.mode)
    commit = args.commit

    reg_path = Path(args.registry_path) if args.registry_path else config.registry_path
    log_path = Path(args.log_dir) if args.log_dir else config.log_dir

    registry = LocalRegistry(reg_path)
    logger = RenamerLogger(log_path)
    # Tool 1 receives no Baserow credentials/access (amendment section 1 & 5)
    provider = BaserowReferenceProvider()

    updater_service = getattr(args, "updater_service", None)
    if updater_service is None:
        updater_service = create_media_db_updater_service(
            registry=registry,
            config=config,
        )

    if mode == RenameMode.FINALIZE:
        # Full authoritative pipeline (amendment section 2):
        # 1. Tool 1 Initial Scan
        # 2. Tool 2 Live Read-Only Query / Candidate Check & Enrichment
        # 3. Tool 3 Offline Travel Schedule Corroboration
        # 4. Tool 1 Final Proposal Generation
        # 5. Commit (if requested) -> Tool 4 Sync
        print(f"=== Step 1: Initial Scan & Parse: {target_path} ===")
        init_executor = BatchExecutor(
            registry=registry,
            logger=logger,
            provider=provider,
            mode=RenameMode.INITIAL,
            media_db_updater_service=updater_service,
        )
        proposals = init_executor.scan_directory(target_path)
        print(f"Discovered and analyzed {len(proposals)} media files.")

        print("=== Step 2: Tool 2 Media Database Candidate Review & Enrichment ===")
        t2_results = []
        if updater_service.tool2_service is None:
            err_msg = "Tool 2 media database review service is not configured; finalization blocked"
            print(f"Error: {err_msg}", file=sys.stderr)
            for p in proposals:
                p.needs_review = True
                p.status = "blocked"
                p.review_reasons.append(err_msg)
        else:
            try:
                t2_results = updater_service.tool2_service.review_batch(force_refresh=False, auto_enrich=True)
                print(f"Reviewed {len(t2_results)} files through Tool 2.")
            except Exception as e:
                err_msg = f"Tool 2 media database review encountered error: {e}"
                print(f"Error: {err_msg}", file=sys.stderr)
                for p in proposals:
                    p.needs_review = True
                    p.status = "blocked"
                    p.review_reasons.append(err_msg)

        t2_by_id = {r.tracking_id: r for r in t2_results}
        for p in proposals:
            t2_r = t2_by_id.get(p.tracking_id)
            if t2_r is None and not (p.needs_review and p.status == "blocked"):
                p.needs_review = True
                p.status = "blocked"
                p.review_reasons.append("Tool 2 media database review missing for file")
            elif t2_r is not None:
                dec_val = t2_r.decision.value if hasattr(t2_r.decision, "value") else str(t2_r.decision)
                if dec_val == "DATABASE_UNAVAILABLE":
                    p.needs_review = True
                    p.status = "blocked"
                    p.review_reasons.append("Tool 2 reported DATABASE_UNAVAILABLE")

        print("=== Step 3: Tool 3 Travel Schedule Corroboration ===")
        ref_path = getattr(args, "travel_schedule_path", None)
        if ref_path is None:
            ref_path = Path(".renamer/reference/travel_schedule.json")
        else:
            ref_path = Path(ref_path)
        ref_store = TravelReferenceStore(reference_path=ref_path, provider=None)
        if not ref_store.reference_path.exists():
            err_msg = "Tool 3 verified travel schedule reference does not exist; finalization blocked"
            print(f"Error: {err_msg}", file=sys.stderr)
            for p in proposals:
                p.needs_review = True
                p.status = "blocked"
                if err_msg not in p.review_reasons:
                    p.review_reasons.append(err_msg)
        else:
            t3_renamer = RenamerApplicationService(registry=registry)
            t3_service = TravelScheduleReviewService(
                registry=registry,
                reference_store=ref_store,
                renamer_service=t3_renamer,
            )
            for p in proposals:
                if p.needs_review and p.status == "blocked":
                    continue
                t2_ctx = t2_by_id.get(p.tracking_id)
                try:
                    t3_raw = t3_service.review_file(p.tracking_id, tool2_context=t2_ctx, auto_enrich=True)
                    t3_res, val_err = validate_tool3_review_result(t3_raw, p.tracking_id)
                    if val_err or t3_res is None:
                        p.needs_review = True
                        p.status = "blocked"
                        p.review_reasons.append(f"Tool 3 travel schedule review contract invalid: {val_err}")
                    elif t3_res.decision in (
                        TravelReviewDecision.REFERENCE_UNAVAILABLE,
                        TravelReviewDecision.PROCESSING_ERROR,
                    ):
                        p.needs_review = True
                        p.status = "blocked"
                        p.review_reasons.append(f"Tool 3 travel schedule review failed: {t3_res.decision.value}")
                except Exception as e:
                    logger.warning(f"Tool 3 review failed for {p.tracking_id}: {e}")
                    p.needs_review = True
                    p.status = "blocked"
                    p.review_reasons.append(f"Tool 3 travel schedule review failed: {e}")
            print("Completed Tool 3 schedule corroboration.")

        print("=== Step 4: Final Proposal Generation ===")
        final_executor = BatchExecutor(
            registry=registry,
            logger=logger,
            provider=provider,
            mode=RenameMode.FINALIZE,
            media_db_updater_service=updater_service,
        )
        final_proposals = []
        for p in proposals:
            if p.needs_review and p.status == "blocked":
                rec = registry.get_file(p.tracking_id)
                if rec:
                    registry.update_file_review(
                        tracking_id=p.tracking_id,
                        status="blocked",
                        needs_review=True,
                        review_reasons=p.review_reasons,
                        proposed_filename=p.proposed_filename,
                        when_val=rec.get("when_val") or "",
                        what_val=rec.get("what_val") or "",
                        where_val=rec.get("where_val") or "",
                        parser_result_json=rec.get("parser_result_json") or "",
                    )
                final_proposals.append(p)
                continue

            rec = registry.get_file(p.tracking_id)
            if rec and rec.get("parser_result"):
                pr = ParserResult.model_validate(rec["parser_result"])
                prop = final_executor.planner.plan_rename(pr)
                registry.save_proposal(prop)
                final_proposals.append(prop)
            else:
                final_proposals.append(p)
        proposals = final_proposals
        executor = final_executor
    else:
        executor = BatchExecutor(
            registry=registry,
            logger=logger,
            provider=provider,
            mode=mode,
            media_db_updater_service=updater_service,
        )
        print(f"=== Scanning Directory: {target_path} (Mode: {mode.value}) ===")
        proposals = executor.scan_directory(target_path)
        print(f"Discovered and analyzed {len(proposals)} media files.")

    review_needed = sum(1 for p in proposals if p.needs_review)
    collisions = sum(1 for p in proposals if p.is_collision)
    print(f"Files requiring human review: {review_needed}")
    print(f"Collisions detected: {collisions}")

    # Output preview of proposals
    for p in proposals[:10]:
        print(f"  [{p.tracking_id}] {p.current_filename}")
        print(f"   -> {p.proposed_filename}")
        if p.needs_review:
            print(f"      Reasons: {', '.join(p.review_reasons)}")
    if len(proposals) > 10:
        print(f"  ... and {len(proposals) - 10} more.")

    print(f"Log written to: {logger.jsonl_path}")
    print(f"Summary CSV: {logger.csv_path}")

    if commit:
        print("\n=== Committing Renames to Filesystem ===")
        committed_proposals = executor.commit_proposals(proposals)
        success_count = sum(1 for p in committed_proposals if p.status == "committed")
        skipped_count = sum(1 for p in committed_proposals if p.status == "skipped_unchanged")
        blocked_count = sum(1 for p in committed_proposals if p.status == "blocked" or p.needs_review)
        failed_count = sum(1 for p in committed_proposals if p.status == "failed")
        print(f"Successfully renamed: {success_count}")
        print(f"Unchanged/skipped: {skipped_count}")
        print(f"Blocked/needs review: {blocked_count}")
        print(f"Failed: {failed_count}")
    else:
        print("\nDry-run complete. No files were modified on disk. Use --commit to apply renames.")


def run_review(args):
    import uvicorn
    from .review_portal.app import app, configure_review_context

    host = args.host or "127.0.0.1"
    if host not in ("127.0.0.1", "localhost"):
        print(f"Error: Non-loopback host '{host}' is forbidden. Review portal must bind to loopback (127.0.0.1 or localhost).", file=sys.stderr)
        sys.exit(1)

    port = args.port or 8000
    registry_path = Path(args.registry_path) if getattr(args, "registry_path", None) else None
    review_root = Path(args.review_root).resolve() if getattr(args, "review_root", None) else None
    configure_review_context(registry_path=registry_path, review_root=review_root)

    print(f"Starting review portal on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, reload=False)


def run_status(args):
    config = load_config()
    reg_path = Path(args.registry_path) if args.registry_path else config.registry_path
    registry = LocalRegistry(reg_path)
    if args.tracking_id:
        record = registry.get_file(args.tracking_id)
        if not record:
            print(f"Tracking ID {args.tracking_id} not found in registry.", file=sys.stderr)
            sys.exit(1)
        print(f"Tracking ID: {record['tracking_id']}")
        print(f"Current: {record['current_filename']}")
        print(f"Proposed: {record['proposed_filename']}")
        print(f"Status: {record['status']}")
        print(f"Needs Review: {bool(record['needs_review'])}")
        if record["review_reasons"]:
            print("Review reasons:")
            for reason in record["review_reasons"]:
                print(f"  - {reason}")
    else:
        all_files = registry.list_files()
        review_files = [f for f in all_files if f["needs_review"]]
        print(f"Registry: {reg_path}")
        print(f"Total files: {len(all_files)}")
        print(f"Needs review: {len(review_files)}")
        print(f"Committed: {sum(1 for f in all_files if f['status'] == 'committed')}")
        if review_files:
            print("Review items:")
            for record in review_files:
                print(f"  - {record['original_filename']}")
                if record["review_reasons"]:
                    for reason in record["review_reasons"]:
                        print(f"      {reason}")
                else:
                    print("      Review required (no reason recorded)")


def run_media_db_review(args):
    from .media_db_reviewer.baserow_provider import BaserowSnapshotProvider
    from .media_db_reviewer.service import MediaDatabaseReviewService

    config = load_config()
    reg_path = Path(args.registry_path) if args.registry_path else config.registry_path
    registry = LocalRegistry(reg_path)
    snapshot_path = Path(args.snapshot_path) if getattr(args, "snapshot_path", None) else config.baserow_snapshot_path

    provider = BaserowSnapshotProvider(
        api_url=config.baserow_api_url,
        api_token=config.baserow_api_token,
        media_table_id=config.baserow_media_table_id,
        category_table_id=config.baserow_category_table_id,
        travel_schedule_table_id=config.baserow_travel_schedule_table_id,
        snapshot_path=snapshot_path,
    )
    service = MediaDatabaseReviewService(registry=registry, provider=provider)

    force_refresh = getattr(args, "refresh_snapshot", False)
    auto_enrich = getattr(args, "auto_enrich", True)

    if getattr(args, "tracking_id", None):
        result = service.review_file(args.tracking_id, force_refresh=force_refresh, auto_enrich=auto_enrich)
        results = [result]
    else:
        results = service.review_batch(force_refresh=force_refresh, auto_enrich=auto_enrich)

    if getattr(args, "json", False):
        import json
        print(json.dumps([r.model_dump() for r in results], indent=2))
        return

    print(f"=== Tool 2: Media Database Review ({len(results)} files evaluated) ===")
    if len(results) <= 20:
        for r in results:
            print(f"[{r.tracking_id}] Decision: {r.decision.value} (Database: {r.database_state})")
            if r.selected_media_row_id:
                print(f"  Selected Media Row ID: {r.selected_media_row_id}")
            if r.candidates:
                print(f"  Candidates found: {len(r.candidates)}")
                for c in r.candidates[:3]:
                    print(f"    - Row {c.media_row_id}: {c.normalized_row.get('title') or ''} (Score: {c.score:.1f})")
            if r.conflicts:
                print(f"  Conflicts: {', '.join(r.conflicts)}")
            if r.renamer_enrichment.confirmed:
                print(f"  Confirmed Title: {r.renamer_enrichment.title_full}")
            file_rec = registry.get_file(r.tracking_id)
            if file_rec and (r.renamer_enrichment.confirmed or r.baserow_check_complete):
                print(f"  Proposed Filename: {file_rec.get('proposed_filename')}")
    else:
        print(f"Sample evaluation across {len(results)} files completed.")
        confirmed_matches = [r for r in results if r.decision.value == 'EXISTING_MEDIA_MATCH']
        if confirmed_matches:
            print("\n--- Confirmed Existing Matches ---")
            for cm in confirmed_matches:
                file_rec = registry.get_file(cm.tracking_id)
                print(f"[{cm.tracking_id}] Row ID {cm.selected_media_row_id}: {cm.renamer_enrichment.title_full}")
                if file_rec:
                    print(f"  Proposed Filename: {file_rec.get('proposed_filename')}")

    print("\n--- Evaluation Summary ---")
    print(f"total files: {len(results)}")
    print(f"confirmed existing matches: {sum(1 for r in results if r.decision.value == 'EXISTING_MEDIA_MATCH')}")
    print(f"probable existing matches: {sum(1 for r in results if r.decision.value == 'PROBABLE_EXISTING_MEDIA')}")
    print(f"multiple candidates: {sum(1 for r in results if r.decision.value == 'MULTIPLE_CANDIDATES')}")
    print(f"new-media candidates: {sum(1 for r in results if r.decision.value == 'NEW_MEDIA_CANDIDATE')}")
    print(f"insufficient evidence: {sum(1 for r in results if r.decision.value == 'INSUFFICIENT_EVIDENCE')}")
    print(f"conflicts: {sum(1 for r in results if r.decision.value == 'CONFLICT_WITH_EXISTING')}")
    print(f"database failures: {sum(1 for r in results if r.decision.value == 'DATABASE_UNAVAILABLE')}")
    print(f"human-review-required-now: {sum(1 for r in results if r.review_required_now)}")
    print(f"review-required-overall: {sum(1 for r in results if r.review_required)}")
    print(f"downstream-to-Tool-3 count: {sum(1 for r in results if 'tool_3_travel_schedule_review' in r.downstream_routing)}")
    print(f"confirmed title/metadata enrichments: {sum(1 for r in results if r.renamer_enrichment.confirmed)}")


def run_travel_review(args):
    from .media_db_reviewer.baserow_provider import BaserowSnapshotProvider
    from .travel_reviewer.reference_store import TravelReferenceStore
    from .travel_reviewer.service import TravelScheduleReviewService

    config = load_config()
    reg_path = Path(args.registry_path) if getattr(args, "registry_path", None) else config.registry_path
    registry = LocalRegistry(reg_path)
    ref_path = Path(args.reference_path) if getattr(args, "reference_path", None) else None

    # Tool 3 operates offline from local verified reference without Baserow access (amendment section 1 & 5)
    store = TravelReferenceStore(reference_path=ref_path)
    service = TravelScheduleReviewService(registry=registry, reference_store=store)

    auto_enrich = getattr(args, "auto_enrich", True)

    if getattr(args, "tracking_id", None):
        result = service.review_file(args.tracking_id, auto_enrich=auto_enrich)
        results = [result]
    else:
        results = service.review_batch(auto_enrich=auto_enrich)

    if getattr(args, "json", False):
        import json
        print(json.dumps([r.model_dump() for r in results], indent=2))
        return

    print(f"=== Tool 3: Travel Schedule Review ({len(results)} files evaluated) ===")
    for r in results[:15]:
        print(f"[{r.tracking_id}] Decision: {r.decision.value}")
        if r.provisional_enrichment:
            if r.provisional_enrichment.when_val:
                print(f"  Provisional WHEN: {r.provisional_enrichment.when_val}")
            if r.provisional_enrichment.where_val:
                print(f"  Provisional WHERE: {r.provisional_enrichment.where_val}")
        if r.conflicts:
            print(f"  Conflicts: {', '.join(r.conflicts)}")
        if r.diagnostic_notes:
            print(f"  Notes: {'; '.join(r.diagnostic_notes)}")
    if len(results) > 15:
        print(f"  ... and {len(results) - 15} more.")

    print("\n--- Evaluation Summary ---")
    print(f"total files: {len(results)}")
    print(f"corroborated: {sum(1 for r in results if r.decision.value == 'CORROBORATED')}")
    print(f"provisional enrichments: {sum(1 for r in results if r.decision.value == 'PROVISIONAL_ENRICHMENT')}")
    when_enrich = sum(1 for r in results if r.provisional_enrichment and r.provisional_enrichment.when_val)
    where_enrich = sum(1 for r in results if r.provisional_enrichment and r.provisional_enrichment.where_val)
    print(f"  when enrichments: {when_enrich}")
    print(f"  where enrichments: {where_enrich}")
    print(f"multiple candidates: {sum(1 for r in results if r.decision.value == 'MULTIPLE_SCHEDULE_CANDIDATES')}")
    print(f"schedule conflicts: {sum(1 for r in results if r.decision.value == 'SCHEDULE_CONFLICT')}")
    print(f"no schedule support: {sum(1 for r in results if r.decision.value == 'NO_SCHEDULE_SUPPORT')}")
    print(f"insufficient evidence: {sum(1 for r in results if r.decision.value == 'INSUFFICIENT_EVIDENCE')}")
    print(f"reference unavailable: {sum(1 for r in results if r.decision.value == 'REFERENCE_UNAVAILABLE')}")
    print(f"processing errors: {sum(1 for r in results if r.decision.value == 'PROCESSING_ERROR')}")


def run_travel_reference(args):
    import json
    from .media_db_reviewer.baserow_provider import BaserowSnapshotProvider
    from .travel_reviewer.reference_store import TravelReferenceStore

    config = load_config()
    ref_path = Path(args.reference_path) if getattr(args, "reference_path", None) else None
    provider = BaserowSnapshotProvider(
        api_url=config.baserow_api_url,
        api_token=config.baserow_api_token,
        media_table_id=config.baserow_media_table_id,
        category_table_id=config.baserow_category_table_id,
        travel_schedule_table_id=config.baserow_travel_schedule_table_id,
    )
    store = TravelReferenceStore(reference_path=ref_path, provider=provider)

    action = args.action

    if action == "init":
        existing = store.load_reference()
        if existing:
            if getattr(args, "json", False):
                print(json.dumps({
                    "status": "already_exists",
                    "path": str(store.reference_path),
                    "canonical_sha256": existing.canonical_sha256,
                    "row_count": existing.row_count,
                    "message": "Verified reference already exists; refusing to overwrite. Use 'verify' to inspect remote differences."
                }, indent=2))
            else:
                print(f"Verified travel schedule reference already exists at: {store.reference_path}")
                print(f"Source Table ID: {existing.source_table_id}")
                print(f"Rows: {existing.row_count} | Canonical SHA-256: {existing.canonical_sha256}")
                print("Refusing to silently overwrite verified reference. Use 'media-archive travel-reference verify' to check remote state.")
            return

        manifest = store.ensure_reference()
        if getattr(args, "json", False):
            print(json.dumps({
                "status": "initialized",
                "source_table_id": manifest.source_table_id,
                "row_count": manifest.row_count,
                "canonical_sha256": manifest.canonical_sha256,
                "retrieved_at": manifest.retrieved_at,
                "path": str(store.reference_path),
            }, indent=2))
        else:
            print(f"Travel schedule reference initialized at: {store.reference_path}")
            print(f"Source Table ID: {manifest.source_table_id}")
            print(f"Rows: {manifest.row_count}")
            print(f"Canonical SHA-256: {manifest.canonical_sha256}")
            print(f"Retrieved At: {manifest.retrieved_at}")

    elif action == "status":
        manifest = store.load_reference()
        if not manifest:
            if getattr(args, "json", False):
                print(json.dumps({"status": "unavailable", "path": str(store.reference_path)}, indent=2))
            else:
                print(f"Reference at {store.reference_path} is unavailable or failed integrity check.", file=sys.stderr)
            sys.exit(1)
        if getattr(args, "json", False):
            print(json.dumps({
                "status": "ok",
                "source_table_id": manifest.source_table_id,
                "row_count": manifest.row_count,
                "canonical_sha256": manifest.canonical_sha256,
                "retrieved_at": manifest.retrieved_at,
                "path": str(store.reference_path),
            }, indent=2))
        else:
            print(f"Reference Status: OK")
            print(f"Path: {store.reference_path}")
            print(f"Source Table ID: {manifest.source_table_id}")
            print(f"Rows: {manifest.row_count}")
            print(f"Canonical SHA-256: {manifest.canonical_sha256}")
            print(f"Retrieved At: {manifest.retrieved_at}")

    elif action == "verify":
        try:
            res = store.verify_remote_reference()
            if getattr(args, "json", False):
                print(json.dumps(res, indent=2))
            else:
                print(f"Remote verification completed:")
                print(f"Matches: {'YES' if res['matches'] else 'NO'}")
                print(f"Local SHA-256:  {res['local_sha256']}")
                print(f"Remote SHA-256: {res['remote_sha256']}")
                print(f"Local Rows: {res['local_row_count']} | Remote Rows: {res['remote_row_count']}")
                if res['unexpected_change']:
                    print("WARNING: Remote table differs from static reference! Unexpected change detected.")
        except Exception as e:
            if getattr(args, "json", False):
                print(json.dumps({"error": str(e)}, indent=2))
            else:
                print(f"Verification failed: {e}", file=sys.stderr)
            sys.exit(1)


def run_media_db_update(args):
    config = load_config()
    reg_path = Path(args.registry_path) if getattr(args, "registry_path", None) else config.registry_path
    registry = LocalRegistry(reg_path)

    updater_service = create_media_db_updater_service(
        registry=registry,
        config=config,
    )

    commit = getattr(args, "commit", False)
    is_json = getattr(args, "json", False)

    if getattr(args, "purge_test_rows", False):
        dry_run = not commit
        summary = updater_service.purge_test_rows(dry_run=dry_run)
        if is_json:
            print(summary.model_dump_json(indent=2))
        else:
            mode_str = "DRY-RUN" if dry_run else "COMMIT"
            print(f"=== Purge Test Rows ({mode_str}) ===")
            print(f"Total processed: {summary.total_processed}")
            print(f"Deleted: {summary.deleted_count}, Already absent: {summary.already_absent_count}, Blocked: {summary.blocked_count}")
            for res in summary.results:
                reason_str = f" - {res.reason}" if res.reason else ""
                print(f"  Row {res.row_id} (Table {res.table_id}): {res.action}{reason_str}")
        return

    if getattr(args, "retry_pending", False):
        results = updater_service.retry_pending()
        if is_json:
            print(json.dumps([r.model_dump() for r in results], indent=2))
        else:
            print(f"Retried {len(results)} pending synchronization requests.")
            for r in results:
                print(f"  [{r.tracking_id}] Status: {r.status.value} | Operation: {r.operation.value} | Row ID: {r.media_row_id}")
        return

    tracking_ids = [args.tracking_id] if getattr(args, "tracking_id", None) else [f["tracking_id"] for f in registry.list_files()]
    if not tracking_ids:
        if is_json:
            print("[]")
        else:
            print("No files found in registry to synchronize.")
        return

    results = []
    for tid in tracking_ids:
        res = updater_service.synchronize(tid, commit=commit)
        results.append(res)

    if is_json:
        print(json.dumps([r.model_dump() for r in results], indent=2))
    else:
        mode_str = "COMMIT" if commit else "DRY-RUN / PREVIEW"
        print(f"=== Media Database Update ({mode_str}) ===")
        print(f"Processed {len(results)} file(s).")
        for r in results:
            print(f"  [{r.tracking_id}] Status: {r.status.value} | Op: {r.operation.value} | Row: {r.media_row_id}")
            if r.field_diffs:
                for d in r.field_diffs:
                    if d.action.value == "SET":
                        print(f"    - {d.field_name}: '{d.old_value}' -> '{d.new_value}' (SET)")
            if r.conflicts:
                print(f"    Conflicts: {', '.join(r.conflicts)}")


def run_main_script(args):
    """Main Tooling Script entry point (media-archive run)."""
    config = load_config()
    targets = getattr(args, "targets", [])
    purge = getattr(args, "purge", False)
    dry_run = getattr(args, "dry_run", False)
    verbose = getattr(args, "verbose", False)
    production = getattr(args, "production", False)
    workflow_str = getattr(args, "workflow", "all")
    reg_path = getattr(args, "registry_path", None)
    log_file = getattr(args, "log_file", None)
    review_portal = getattr(args, "review_portal", False)
    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 8000)

    try:
        workflow = WorkflowType(workflow_str)
    except ValueError:
        print(f"Error: Invalid workflow '{workflow_str}'. Choose from: all, renamer, processing", file=sys.stderr)
        sys.exit(1)

    service = getattr(args, "orchestrator_service", None)
    if service is None:
        service = create_main_tooling_service(
            config=config,
            registry_path=reg_path,
            log_file=log_file,
            workflow=workflow,
            dry_run=dry_run,
            verbose=verbose,
            production=production,
            tool2_service=getattr(args, "tool2_service", None),
            travel_service=getattr(args, "travel_service", None),
            tool4_service=getattr(args, "tool4_service", None),
            tool5_service=getattr(args, "tool5_service", None),
            tool6_service=getattr(args, "tool6_service", None),
        )

    if purge:
        if targets:
            print("Error: Normal file targets cannot be specified with --purge; cleanup is a standalone operation.", file=sys.stderr)
            sys.exit(2)
        exit_code, _ = service.purge(dry_run=dry_run)
        if exit_code != 0:
            sys.exit(exit_code)
        return

    if not targets:
        print("Error: No target paths specified. Provide media file(s) or folder(s), or use --purge.", file=sys.stderr)
        sys.exit(2)

    summary = service.run(targets)

    if review_portal:
        import uvicorn
        from .review_portal.app import app as portal_app, configure_review_context
        configure_review_context(
            registry=service.registry,
            media_db_service=service.tool2_service,
            media_db_updater_service=service.tool4_service,
        )
        print(f"\nStarting review portal on http://{host}:{port}/ ...")
        uvicorn.run(portal_app, host=host, port=port, log_level="info")

    if summary.exit_code != 0:
        sys.exit(summary.exit_code)


def run_discover_content(args):
    """Execute Tool 5: Content Discoverer standalone CLI command."""
    config = load_config()
    reg_path = Path(args.registry_path) if args.registry_path else config.registry_path
    registry = LocalRegistry(reg_path)
    service = getattr(args, "content_discoverer_service", None)
    if service is None:
        service = ContentDiscovererService(registry=registry)

    model_path = Path(args.model_path) if getattr(args, "model_path", None) else None
    progress_callback = None if getattr(args, "json", False) else TerminalReporter().report_tool5_progress

    try:
        result = service.discover_content(
            target=args.target,
            dry_run=args.dry_run,
            device=args.device,
            model_path=model_path,
            force_retranscribe=args.force_retranscribe,
            progress_callback=progress_callback,
        )
    except Exception as e:
        print(f"Error during content discovery: {e}", file=sys.stderr)
        sys.exit(1)

    if getattr(args, "json", False):
        print(json.dumps(result.model_dump(), indent=2))
        return

    type_display_map = {
        "CLASS": "Class",
        "KIRTAN_AND_CLASS": "Kirtan and Class",
        "KIRTAN": "Kirtan",
        "INITIATION": "Initiation",
        "EVENT_OR_FESTIVAL_ADDRESS": "Event / Festival Address",
        "HOME_PROGRAM": "Home Program",
        "UNKNOWN_REVIEW": "Unknown (Review Required)",
    }
    type_str = type_display_map.get(result.classification.value, result.classification.value.replace("_", " ").title())
    print("Tool 5 - Content Discoverer")
    print(f"Type: {type_str} ({result.confidence.value})")
    print(f"Mantra: {result.mantra_type.value}")
    if result.cutter_proposal:
        print(f"Boundary: {result.cutter_proposal.suggested_cut_points}")
    print(f"Transcript: {result.transcript_path}")
    route_str = "process_by_tool_6" if result.process_by_tool_6 else "none"
    print(f"Route: {route_str}")
    if result.review_required:
        print(f"Review Required: {result.review_reason or 'Yes'}")


def run_cut(args):
    """Run Tool 6 File Cutter CLI command."""
    config = load_config()
    reg_path = Path(args.registry_path) if args.registry_path else config.registry_path
    registry = LocalRegistry(reg_path)
    from .file_cutter.service import FileCutterService

    updater_service = getattr(args, "updater_service", None)
    cutter_service = FileCutterService(registry=registry, media_db_service=updater_service)

    result = cutter_service.cut_file(
        tracking_id_or_path=args.target,
        dry_run=args.dry_run,
        cut_point_override=args.cut_point,
        force=getattr(args, "force", False),
    )

    if args.json:
        print(json.dumps(result.model_dump(), indent=2))
        return

    print("Tool 6 — File Cutter")
    if result.dry_run:
        print("Mode: DRY-RUN (no files or database modified)")
    if result.success:
        print(f"Status: SUCCESS (cut at {result.cut_point_seconds:.2f}s)")
        print(f"Singing Part: {result.singing_output_path} ({result.singing_duration_seconds:.2f}s, trim: {result.singing_leading_silence_seconds:.2f}s)")
        print(f"Class Part:   {result.class_output_path} ({result.class_duration_seconds:.2f}s, trim: {result.class_leading_silence_seconds:.2f}s)")
        if result.singing_pending_tool_11_move:
            print("Location: Pending Tool 11 category move (files remain beside source)")
    else:
        print("Status: FAILED / REVIEW REQUIRED")
        if result.review_reason:
            print(f"Reason: {result.review_reason}")
        if result.error_message:
            print(f"Error: {result.error_message}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(prog="media-archive", description="Media Archive Tooling CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Tool 6: File Cutter command
    cut_parser = subparsers.add_parser("cut", help="Run Tool 6: File Cutter")
    cut_parser.add_argument("target", help="Target media file path or tracking ID to split")
    cut_parser.add_argument("--dry-run", action="store_true", default=False, help="Perform dry-run cut preview without modifying files or database")
    cut_parser.add_argument("--force", action="store_true", default=False, help="Force re-cutting even if a previous split exists in registry")
    cut_parser.add_argument("--cut-point", type=float, default=None, help="Explicit cut point in seconds (overrides automatic Tool 5 proposal)")
    cut_parser.add_argument("--registry-path", help="Custom SQLite registry path")
    cut_parser.add_argument("--json", action="store_true", default=False, help="Output machine-readable JSON")
    cut_parser.set_defaults(func=run_cut)

    # Tool 5: Content Discoverer command
    discover_parser = subparsers.add_parser("discover-content", help="Run Tool 5: Content Discoverer")
    discover_parser.add_argument("target", help="Target media file path or tracking ID")
    discover_parser.add_argument("--dry-run", action="store_true", default=False, help="Perform discovery preview without disk or registry mutations")
    discover_parser.add_argument("--device", choices=["auto", "metal", "cpu"], default="auto", help="Compute device for transcription (default: auto)")
    discover_parser.add_argument("--model-path", help="Path to whisper model file")
    discover_parser.add_argument("--force-retranscribe", action="store_true", default=False, help="Force re-transcription ignoring existing sidecar cache")
    discover_parser.add_argument("--registry-path", help="Custom SQLite registry path")
    discover_parser.add_argument("--json", action="store_true", default=False, help="Output machine-readable JSON")
    discover_parser.set_defaults(func=run_discover_content)

    # Main Tooling Script orchestrator command
    run_parser = subparsers.add_parser("run", help="Run Main Tooling Script orchestrator (Phase A: Tools 1–4)")
    run_parser.add_argument("targets", nargs="*", default=[], help="Target media file(s) and/or folder(s)")
    run_parser.add_argument("--purge", action="store_true", default=False, help="Purge alpha/beta test data from Baserow and reset local review registry")
    run_parser.add_argument("--production", action="store_true", default=False, help="Run in production archive mode (gated pending confirmed production retention policy)")
    run_parser.add_argument("--dry-run", dest="dry_run", action="store_true", default=False, help="Perform dry-run preview without modifying filesystem or database")
    run_parser.add_argument("--verbose", action="store_true", default=False, help="Show detailed output in terminal")
    run_parser.add_argument("--workflow", choices=["all", "renamer", "processing"], default="all", help="Workflow selection: all (default), renamer, processing")
    run_parser.add_argument("--registry-path", help="Custom SQLite registry path")
    run_parser.add_argument("--log-file", help="Custom persistent log file path (default: .renamer/media-archive-tooling.log)")
    run_parser.add_argument("--review-portal", action="store_true", default=False, help="Launch review portal web server after run")
    run_parser.add_argument("--host", choices=["127.0.0.1", "localhost"], default="127.0.0.1", help="Loopback host for review portal (default: 127.0.0.1)")
    run_parser.add_argument("--port", type=int, default=8000, help="Port for review portal (default: 8000)")
    run_parser.set_defaults(func=run_main_script)

    # Tool 4: Media Database Updater command
    media_update_parser = subparsers.add_parser("media-db-update", help="Run Tool 4: Media Database Updater")
    media_update_parser.add_argument("tracking_id", nargs="?", help="Optional tracking ID to synchronize")
    media_update_parser.add_argument("--commit", dest="commit", action="store_true", default=False, help="Apply mutations to Baserow")
    media_update_parser.add_argument("--dry-run", dest="commit", action="store_false", help="Perform dry-run preview without mutating database (default)")
    media_update_parser.add_argument("--retry-pending", action="store_true", default=False, help="Retry all pending or retryable sync records")
    media_update_parser.add_argument("--purge-test-rows", action="store_true", default=False, help="Purge alpha/beta test rows recorded in ledger")
    media_update_parser.add_argument("--registry-path", help="Custom SQLite registry path")
    media_update_parser.add_argument("--json", action="store_true", default=False, help="Output machine-readable JSON")
    media_update_parser.set_defaults(func=run_media_db_update)


    # Tool 3: Travel Schedule Reviewer commands
    travel_review_parser = subparsers.add_parser("travel-review", help="Run Tool 3: Travel Schedule Reviewer")
    travel_review_parser.add_argument("tracking_id", nargs="?", help="Optional tracking ID to review")
    travel_review_parser.add_argument("--registry-path", help="Custom SQLite registry path")
    travel_review_parser.add_argument("--reference-path", help="Custom travel schedule reference JSON path")
    travel_review_parser.add_argument("--no-enrich", dest="auto_enrich", action="store_false", default=True, help="Do not automatically apply provisional enrichment to Renamer proposals")
    travel_review_parser.add_argument("--json", action="store_true", default=False, help="Output machine-readable JSON")
    travel_review_parser.set_defaults(func=run_travel_review)

    travel_ref_parser = subparsers.add_parser("travel-reference", help="Manage Tool 3 immutable travel schedule reference")
    travel_ref_parser.add_argument("action", choices=["init", "status", "verify"], help="Action: init (bootstrap local reference), status (inspect local reference), verify (compare local vs remote)")
    travel_ref_parser.add_argument("--reference-path", help="Custom travel schedule reference JSON path")
    travel_ref_parser.add_argument("--json", action="store_true", default=False, help="Output machine-readable JSON")
    travel_ref_parser.set_defaults(func=run_travel_reference)

    # Tool 2: Media Database Reviewer command
    media_db_parser = subparsers.add_parser("media-db-review", help="Run Tool 2: Media Database Reviewer")
    media_db_parser.add_argument("tracking_id", nargs="?", help="Optional tracking ID to review")
    media_db_parser.add_argument("--registry-path", help="Custom SQLite registry path")
    media_db_parser.add_argument("--snapshot-path", help="Custom Baserow snapshot JSON path")
    media_db_parser.add_argument("--refresh-snapshot", action="store_true", default=False, help="Force refresh live snapshot from Baserow")
    media_db_parser.add_argument("--no-enrich", dest="auto_enrich", action="store_false", default=True, help="Do not automatically apply confirmed enrichment to Renamer proposals")
    media_db_parser.add_argument("--json", action="store_true", default=False, help="Output machine-readable JSON")
    media_db_parser.set_defaults(func=run_media_db_review)

    # Renamer command
    renamer_parser = subparsers.add_parser("renamer", help="Run Tool 1: Renamer")
    renamer_parser.add_argument("target", help="Target directory containing media files")
    renamer_parser.add_argument("--mode", choices=["initial", "enrich", "finalize"], default="initial")
    renamer_parser.add_argument("--commit", dest="commit", action="store_true", default=False, help="Apply renames to filesystem")
    renamer_parser.add_argument("--dry-run", dest="commit", action="store_false", help="Perform dry-run without modifying filesystem (default)")
    renamer_parser.add_argument("--registry-path", help="Custom SQLite registry path")
    renamer_parser.add_argument("--log-dir", help="Custom log output directory")
    renamer_parser.set_defaults(func=run_renamer)

    # Scan command alias
    scan_parser = subparsers.add_parser("scan", help="Alias for renamer")
    scan_parser.add_argument("target", help="Target directory containing media files")
    scan_parser.add_argument("--mode", choices=["initial", "enrich", "finalize"], default="initial")
    scan_parser.add_argument("--commit", dest="commit", action="store_true", default=False, help="Apply renames to filesystem")
    scan_parser.add_argument("--dry-run", dest="commit", action="store_false", help="Perform dry-run without modifying filesystem (default)")
    scan_parser.add_argument("--registry-path", help="Custom SQLite registry path")
    scan_parser.add_argument("--log-dir", help="Custom log output directory")
    scan_parser.set_defaults(func=run_renamer)

    # Review portal command
    review_parser = subparsers.add_parser("review", help="Start local review portal web server")
    review_parser.add_argument("--host", choices=["127.0.0.1", "localhost"], default="127.0.0.1", help="Loopback host only (default: 127.0.0.1)")
    review_parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    review_parser.add_argument("--registry-path", help="SQLite registry to display in the portal")
    review_parser.add_argument("--review-root", help="Root directory used to display relative source paths")
    review_parser.set_defaults(func=run_review)

    # Status command
    status_parser = subparsers.add_parser("status", help="Inspect processing registry status")
    status_parser.add_argument("tracking_id", nargs="?", help="Optional tracking ID to inspect")
    status_parser.add_argument("--registry-path", help="Custom SQLite registry path")
    status_parser.set_defaults(func=run_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
