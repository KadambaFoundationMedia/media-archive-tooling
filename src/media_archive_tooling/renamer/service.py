"""Application service layer for Tool 1 Renamer operations and human review."""
import json
import logging
import re
from pathlib import Path
from typing import Optional, Dict, Any, List

from .models import ParserResult, ResolutionState, Evidence, RenameMode
from .registry.registry import LocalRegistry
from .planner.planner import RenamePlanner
from ..common.ascii_latin import to_ascii_latin, sanitize_filename_token

logger = logging.getLogger(__name__)


class RenamerApplicationService:
    """Coordinates review operations, validation, evidence tracking, and proposal generation."""

    def __init__(
        self,
        registry: LocalRegistry,
        planner: Optional[RenamePlanner] = None,
    ):
        self.registry = registry
        self.planner = planner or RenamePlanner(mode=RenameMode.INITIAL)

    def get_file(self, tracking_id: str) -> Optional[Dict[str, Any]]:
        return self.registry.get_file(tracking_id)

    def list_files(self, filter_mode: str = "all") -> Dict[str, Any]:
        all_files = self.registry.list_files()
        total_count = len(all_files)
        review_count = sum(1 for f in all_files if f["needs_review"])
        committed_count = sum(1 for f in all_files if f["status"] == "committed")

        if filter_mode == "review":
            display_files = [f for f in all_files if f["needs_review"]]
        elif filter_mode == "committed":
            display_files = [f for f in all_files if f["status"] == "committed"]
        else:
            display_files = all_files

        return {
            "files": display_files,
            "total_count": total_count,
            "review_count": review_count,
            "committed_count": committed_count,
            "current_filter": filter_mode,
        }

    def apply_review_action(
        self,
        tracking_id: str,
        action: str,
        when_val: Optional[str] = None,
        what_val: Optional[str] = None,
        where_val: Optional[str] = None,
        custom_proposed_filename: Optional[str] = None,
        reviewer: str = "human",
    ) -> Dict[str, Any]:
        """Validate review corrections, record audit history, regenerate proposal, and update registry."""
        record = self.registry.get_file(tracking_id)
        if not record:
            raise ValueError(f"File with tracking_id '{tracking_id}' not found in registry")

        # Previous values snapshot for audit
        previous_values = {
            "when_val": record.get("when_val"),
            "what_val": record.get("what_val"),
            "where_val": record.get("where_val"),
            "proposed_filename": record.get("proposed_filename"),
            "status": record.get("status"),
            "needs_review": record.get("needs_review"),
            "review_reasons": record.get("review_reasons"),
        }

        # Parse stored ParserResult
        parser_dict = record["parser_result"]
        parser_res = ParserResult.model_validate(parser_dict)

        changes = {}

        # 1. Validate & update WHEN
        if when_val is not None and when_val.strip() and when_val.strip() != record.get("when_val"):
            w_clean = when_val.strip()
            # Validate format: YYYY-MM-DD or partials
            if not re.match(r"^(?:199[3-9]|20[0-2]\d|YYYY)-(?:0[1-9]|1[0-2]|MM)-(?:0[1-9]|[12]\d|3[01]|DD)$", w_clean):
                raise ValueError(f"Invalid date format '{w_clean}'. Must be YYYY-MM-DD or explicit partials.")
            parser_res.when.selected_value = w_clean
            parser_res.when.state = ResolutionState.EXACT if "DD" not in w_clean and "MM" not in w_clean else ResolutionState.STRONG
            parser_res.when.evidence.append(Evidence(source="human_review", raw_value=w_clean, details=f"corrected by {reviewer}"))
            changes["when_val"] = w_clean

        # 2. Validate & update WHAT
        if what_val is not None and what_val.strip() and what_val.strip() != record.get("what_val"):
            what_clean = sanitize_filename_token(to_ascii_latin(what_val.strip()))
            parser_res.what.selected_value = what_clean
            parser_res.what.state = ResolutionState.EXACT
            parser_res.what.evidence.append(Evidence(source="human_review", raw_value=what_val.strip(), details=f"corrected by {reviewer}"))
            changes["what_val"] = what_clean

        # 3. Validate & update WHERE
        if where_val is not None and where_val.strip() and where_val.strip() != record.get("where_val"):
            where_clean = where_val.strip()
            if "-" in where_clean:
                parts = where_clean.rsplit("-", 1)
                place, iso = parts[0], parts[1]
                if len(iso) == 2 and iso.isalpha():
                    parser_res.where.place_location = sanitize_filename_token(to_ascii_latin(place))
                    parser_res.where.country_iso2 = iso.lower()
                else:
                    parser_res.where.place_location = sanitize_filename_token(to_ascii_latin(where_clean))
            else:
                parser_res.where.place_location = sanitize_filename_token(to_ascii_latin(where_clean))

            parser_res.where.state = ResolutionState.EXACT
            parser_res.where.evidence.append(Evidence(source="human_review", raw_value=where_clean, details=f"corrected by {reviewer}"))
            changes["where_val"] = where_clean

        # 4. Regenerate proposal through naming planner to guarantee filename standard adherence
        proposal = self.planner.plan_rename(parser_res)
        final_proposed = proposal.proposed_filename

        # If human gave a custom proposed filename, validate it
        if custom_proposed_filename and custom_proposed_filename.strip():
            cpf = custom_proposed_filename.strip()
            # Ensure valid extension and tracking ID
            ext = parser_res.identity.extension.lower()
            if not cpf.lower().endswith(ext):
                cpf = f"{cpf}{ext}"
            if f"_ID-{tracking_id}" not in cpf and self.planner.mode != RenameMode.FINALIZE:
                # Retain tracking ID
                p_stem = Path(cpf).stem
                cpf = f"{p_stem}_ID-{tracking_id}{ext}"
            final_proposed = cpf
            changes["custom_proposed_filename"] = cpf

        changes["action"] = action
        changes["final_proposed_filename"] = final_proposed

        # 5. Determine new status and review reasons
        new_review_reasons = list(parser_res.review_reasons)
        if action == "approve":
            new_status = "approved"
            needs_review = False
            new_review_reasons = []  # Human approved, clear blocker review reasons
        elif action == "defer":
            new_status = "deferred"
            needs_review = True
        else:
            new_status = "pending"
            needs_review = bool(new_review_reasons)

        parser_res.review_reasons = new_review_reasons

        # 6. Update registry
        self.registry.update_file_review(
            tracking_id=tracking_id,
            when_val=parser_res.when.selected_value,
            what_val=parser_res.what.selected_value,
            where_val=f"{parser_res.where.place_location or ''}-{parser_res.where.country_iso2 or ''}".strip("-"),
            proposed_filename=final_proposed,
            status=new_status,
            needs_review=needs_review,
            review_reasons=new_review_reasons,
            parser_result_json=parser_res.model_dump_json(),
        )

        # 7. Record audit action
        self.registry.record_review_action(
            tracking_id=tracking_id,
            action=action,
            reviewer=reviewer,
            changes=changes,
            previous_values=previous_values,
        )

        return self.registry.get_file(tracking_id)

