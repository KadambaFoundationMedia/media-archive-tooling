"""Structured JSONL logging and human-readable CSV summary generator."""
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

from ..models import RenameProposal, ResolutionState


class RenamerLogger:
    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.jsonl_path = self.log_dir / f"renamer_{self.session_id}.jsonl"
        self.csv_path = self.log_dir / f"renamer_{self.session_id}_summary.csv"

    def log_proposals(self, proposals: List[RenameProposal], duration_secs: float = 0.0):
        """Write detailed JSONL records and CSV summary."""
        # 1. Detailed JSONL
        with open(self.jsonl_path, "w", encoding="utf-8") as f:
            for p in proposals:
                pr = p.parser_result
                record = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "tracking_id": p.tracking_id,
                    "original_filename": pr.identity.original_filename,
                    "current_filename": p.current_filename,
                    "proposed_filename": p.proposed_filename,
                    "original_path": p.original_path,
                    "proposed_path": p.proposed_path,
                    "mode": p.mode.value,
                    "is_collision": p.is_collision,
                    "changes_detected": p.changes_detected,
                    "status": p.status,
                    "error": p.error,
                    "needs_review": p.needs_review,
                    "review_reasons": p.review_reasons,
                    "diagnostic_notes": p.diagnostic_notes,
                    "downstream_routing": p.downstream_routing,
                    "evaluation_category": self._categorize_proposal(p),
                    "parser_result": pr.model_dump(),
                    "folder_grammar": pr.context.sibling_pattern_context,
                    "conflicts": pr.conflicts,
                    "duration_secs": duration_secs,
                    "tool_version": "0.1.0"
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        # 2. Human-Readable CSV Summary
        with open(self.csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Tracking ID",
                "Original Filename",
                "Proposed Filename",
                "WHEN",
                "WHEN State",
                "WHAT",
                "WHAT State",
                "WHERE",
                "WHERE State",
                "Needs Review",
                "Review Reasons",
                "Diagnostic Notes",
                "Downstream Routing",
                "Status",
                "Evaluation Category"
            ])
            for p in proposals:
                pr = p.parser_result
                where_str = f"{pr.where.place_location or ''}-{pr.where.country_iso2 or ''}".strip("-")
                writer.writerow([
                    p.tracking_id,
                    pr.identity.original_filename,
                    p.proposed_filename,
                    pr.when.selected_value,
                    pr.when.state.value,
                    pr.what.selected_value or pr.what.category or "",
                    pr.what.state.value,
                    where_str,
                    pr.where.state.value,
                    "YES" if p.needs_review else "NO",
                    "; ".join(p.review_reasons),
                    "; ".join(p.diagnostic_notes),
                    "; ".join(p.downstream_routing),
                    p.status,
                    self._categorize_proposal(p)
                ])

    def _categorize_proposal(self, p: RenameProposal) -> str:
        if p.status == "error" or p.error:
            return "blocked_error"
        if p.needs_review:
            return "human_review_required"
        if p.parser_result.file_metadata.possible_combination:
            return "downstream_split"
        pr = p.parser_result
        has_unresolved_or_provisional = (
            pr.when.state in (ResolutionState.UNRESOLVED, ResolutionState.PROVISIONAL)
            or pr.what.state in (ResolutionState.UNRESOLVED, ResolutionState.PROVISIONAL)
            or pr.where.state in (ResolutionState.UNRESOLVED, ResolutionState.PROVISIONAL)
            or bool(p.downstream_routing)
        )
        if has_unresolved_or_provisional:
            return "downstream_enrichment"
        return "safe_automatic"
