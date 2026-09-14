"""Unified CLI entry point for media-archive-tooling."""
import argparse
import sys
from pathlib import Path

from .config import load_config
from .renamer.models import RenameMode
from .renamer.registry.registry import LocalRegistry
from .renamer.logging.logger import RenamerLogger
from .renamer.planner.executor import BatchExecutor
from .adapters.baserow import BaserowReferenceProvider


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
    provider = BaserowReferenceProvider(
        api_url=config.baserow_api_url,
        api_token=config.baserow_api_token,
        media_table_id=config.baserow_media_table_id,
        category_table_id=config.baserow_category_table_id,
    )

    executor = BatchExecutor(registry=registry, logger=logger, provider=provider, mode=mode)

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
        failed_count = sum(1 for p in committed_proposals if p.status == "failed")
        print(f"Successfully renamed: {success_count}")
        print(f"Unchanged/skipped: {skipped_count}")
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

    if getattr(args, "tracking_id", None):
        result = service.review_file(args.tracking_id, force_refresh=force_refresh)
        results = [result]
    else:
        results = service.review_batch(force_refresh=force_refresh)

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
    else:
        print(f"Sample evaluation across {len(results)} files completed.")

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


def main():
    parser = argparse.ArgumentParser(prog="media-archive", description="Media Archive Tooling CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Tool 2: Media Database Reviewer command
    media_db_parser = subparsers.add_parser("media-db-review", help="Run Tool 2: Media Database Reviewer")
    media_db_parser.add_argument("tracking_id", nargs="?", help="Optional tracking ID to review")
    media_db_parser.add_argument("--registry-path", help="Custom SQLite registry path")
    media_db_parser.add_argument("--snapshot-path", help="Custom Baserow snapshot JSON path")
    media_db_parser.add_argument("--refresh-snapshot", action="store_true", default=False, help="Force refresh live snapshot from Baserow")
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
