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
        media_table_id=config.baserow_media_table_id
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
    host = args.host or "127.0.0.1"
    port = args.port or 8000
    print(f"Starting review portal on http://{host}:{port}")
    uvicorn.run("media_archive_tooling.review_portal.app:app", host=host, port=port, reload=False)


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
    else:
        all_files = registry.list_files()
        print(f"Registry: {reg_path}")
        print(f"Total files: {len(all_files)}")
        print(f"Needs review: {sum(1 for f in all_files if f['needs_review'])}")
        print(f"Committed: {sum(1 for f in all_files if f['status'] == 'committed')}")


def main():
    parser = argparse.ArgumentParser(prog="media-archive", description="Media Archive Tooling CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Renamer command
    renamer_parser = subparsers.add_parser("renamer", help="Run Tool 1: Renamer")
    renamer_parser.add_argument("target", help="Target directory containing media files")
    renamer_parser.add_argument("--mode", choices=["initial", "enrich", "finalize"], default="initial")
    renamer_parser.add_argument("--dry-run", dest="commit", action="store_false", default=True, help="Perform dry-run (default)")
    renamer_parser.add_argument("--commit", dest="commit", action="store_true", help="Apply renames to filesystem")
    renamer_parser.add_argument("--registry-path", help="Custom SQLite registry path")
    renamer_parser.add_argument("--log-dir", help="Custom log output directory")
    renamer_parser.set_defaults(func=run_renamer)

    # Scan command alias
    scan_parser = subparsers.add_parser("scan", help="Alias for renamer")
    scan_parser.add_argument("target", help="Target directory containing media files")
    scan_parser.add_argument("--mode", choices=["initial", "enrich", "finalize"], default="initial")
    scan_parser.add_argument("--dry-run", dest="commit", action="store_false", default=True)
    scan_parser.add_argument("--commit", dest="commit", action="store_true")
    scan_parser.add_argument("--registry-path", help="Custom SQLite registry path")
    scan_parser.add_argument("--log-dir", help="Custom log output directory")
    scan_parser.set_defaults(func=run_renamer)

    # Review portal command
    review_parser = subparsers.add_parser("review", help="Start local review portal web server")
    review_parser.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    review_parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
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
