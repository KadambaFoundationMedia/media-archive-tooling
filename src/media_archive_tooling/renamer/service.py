"""Application service layer for Tool 1 Renamer operations and human review."""
import json
import logging
import re
from pathlib import Path
from typing import Optional, Dict, Any, List

from .models import ParserResult, ResolutionState, Evidence, RenameMode, EnrichmentEvidence
from .registry.registry import LocalRegistry
from .planner.planner import RenamePlanner
from .validator import validate_calendar_date, validate_iso2_country, validate_canonical_filename
from .parser.engine import has_class_evidence
from ..common.ascii_latin import to_ascii_latin, sanitize_filename_token

logger = logging.getLogger(__name__)

ALLOWED_REVIEW_ACTIONS = {"approve", "edit", "defer"}


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

    def apply_enrichment(self, evidence: EnrichmentEvidence) -> Dict[str, Any]:
        """Apply structured later evidence (Tools 2-4, 7, or Baserow) to update file resolution."""
        record = self.registry.get_file(evidence.tracking_id)
        if not record:
            raise ValueError(f"File with tracking_id '{evidence.tracking_id}' not found in registry")

        previous_values = {
            "when_val": record.get("when_val"),
            "what_val": record.get("what_val"),
            "where_val": record.get("where_val"),
            "proposed_filename": record.get("proposed_filename"),
            "status": record.get("status"),
        }

        parser_dict = record["parser_result"]
        parser_res = ParserResult.model_validate(parser_dict)
        changes = {}

        source = evidence.source_tool or "enrichment"

        if evidence.when_val is not None:
            ok, msg = validate_calendar_date(evidence.when_val)
            if not ok:
                raise ValueError(msg or "Invalid date in enrichment")
            parser_res.when.selected_value = evidence.when_val
            parser_res.when.state = ResolutionState.STRONG if "DD" in evidence.when_val or "MM" in evidence.when_val else ResolutionState.EXACT
            parser_res.when.evidence.append(Evidence(source=source, raw_value=evidence.when_val, details=evidence.details or "enriched"))
            changes["when_val"] = evidence.when_val

        if evidence.what_val is not None:
            what_clean = sanitize_filename_token(to_ascii_latin(evidence.what_val))
            parser_res.what.selected_value = what_clean
            parser_res.what.state = ResolutionState.EXACT
            parser_res.what.evidence.append(Evidence(source=source, raw_value=evidence.what_val, details=evidence.details or "enriched"))
            changes["what_val"] = what_clean

        if evidence.what_category is not None:
            parser_res.what.category = evidence.what_category
            changes["what_category"] = evidence.what_category

        if evidence.where_val is not None:
            where_clean = evidence.where_val.strip()
            if "-" in where_clean:
                parts = where_clean.rsplit("-", 1)
                place, iso = parts[0], parts[1]
                ok_iso, _ = validate_iso2_country(iso)
                if ok_iso:
                    parser_res.where.place_location = sanitize_filename_token(to_ascii_latin(place))
                    parser_res.where.country_iso2 = iso.lower()
                else:
                    from ..media_db_reviewer.engine import _norm_country
                    norm_c = _norm_country(iso)
                    if norm_c and len(norm_c) == 2:
                        parser_res.where.place_location = sanitize_filename_token(to_ascii_latin(place))
                        parser_res.where.country_iso2 = norm_c.lower()
                    else:
                        parser_res.where.place_location = sanitize_filename_token(to_ascii_latin(where_clean))
            else:
                parser_res.where.place_location = sanitize_filename_token(to_ascii_latin(where_clean))
            parser_res.where.state = ResolutionState.EXACT
            parser_res.where.evidence.append(Evidence(source=source, raw_value=where_clean, details=evidence.details or "enriched"))
            changes["where_val"] = where_clean

        if evidence.who_val is not None:
            parser_res.who = sanitize_filename_token(to_ascii_latin(evidence.who_val))
            changes["who"] = parser_res.who

        if evidence.baserow_check_complete is not None:
            parser_res.file_metadata.baserow_check_complete = evidence.baserow_check_complete
            changes["baserow_check_complete"] = evidence.baserow_check_complete

        if evidence.possible_combination is not None:
            parser_res.file_metadata.possible_combination = evidence.possible_combination
            changes["possible_combination"] = evidence.possible_combination

        # Regenerate proposal through planner in ENRICH mode
        enrich_planner = RenamePlanner(mode=RenameMode.ENRICH)
        proposal = enrich_planner.plan_rename(parser_res)

        # Update diagnostic notes & downstream routing based on new state
        diag_notes = []
        routing = []
        if parser_res.when.state == ResolutionState.UNRESOLVED:
            diag_notes.append("WHEN is unresolved")
            routing.append("tool_2_3_media_enrichment")
        elif parser_res.when.state == ResolutionState.PROVISIONAL:
            diag_notes.append(f"WHEN resolution is provisional ({parser_res.when.selected_value})")

        if parser_res.what.state == ResolutionState.UNRESOLVED:
            parent_folder = (
                parser_res.context.parent_folder
                if (parser_res.context and parser_res.context.parent_folder)
                else (Path(proposal.original_path).parent.name if proposal.original_path else "")
            )
            ancestor_folders = (
                parser_res.context.ancestor_folders
                if (parser_res.context and parser_res.context.ancestor_folders)
                else (
                    [p.name for p in Path(proposal.original_path).parents if p.name and p != Path(proposal.original_path).parent]
                    if proposal.original_path
                    else []
                )
            )
            prior_class_routed = (
                "tool_7_class_classification" in parser_res.downstream_routing
                and (not evidence.what_category or evidence.what_category in ("Class", "Lecture"))
            )
            is_class = (
                prior_class_routed
                or has_class_evidence(
                    parser_res.what,
                    proposal.current_filename,
                    parent_folder,
                    ancestor_folders,
                )
                or (
                    parser_res.identity
                    and has_class_evidence(
                        parser_res.what,
                        parser_res.identity.original_filename,
                        parent_folder,
                        ancestor_folders,
                    )
                )
            )
            if is_class:
                diag_notes.append("Class WHAT is unresolved")
                routing.append("tool_7_class_classification")
            else:
                diag_notes.append("WHAT is unresolved")
                routing.append("tool_2_media_database_review")
                routing.append("tool_5_content_discovery")
        elif parser_res.what.selected_value in ("Class", "Lecture"):
            diag_notes.append("Unidentified class WHAT")
            routing.append("tool_7_class_classification")

        if parser_res.where.state == ResolutionState.UNRESOLVED:
            diag_notes.append("WHERE is unresolved")
            routing.append("tool_2_3_media_enrichment")
        elif parser_res.where.state == ResolutionState.PROVISIONAL:
            diag_notes.append(f"WHERE resolution is provisional ({parser_res.where.place_location}-{parser_res.where.country_iso2 or ''})")
            routing.append("tool_2_3_media_enrichment")

        if parser_res.file_metadata.possible_combination:
            diag_notes.append("File has combination clue; retaining source stem + ID for splitting")
            routing.append("tool_5_6_split_combination")

        parser_res.diagnostic_notes = list(dict.fromkeys(diag_notes))
        parser_res.downstream_routing = list(dict.fromkeys(routing))

        # Re-evaluate remaining review reasons: keep only those still active
        remaining_reasons = []
        for r in parser_res.review_reasons:
            if "WHEN" in r and evidence.when_val is not None:
                continue
            if "WHAT" in r and evidence.what_val is not None:
                continue
            if "WHERE" in r and evidence.where_val is not None:
                continue
            if "combination" in r.lower() and evidence.possible_combination is False:
                continue
            remaining_reasons.append(r)
        parser_res.review_reasons = remaining_reasons

        self.registry.update_file_review(
            tracking_id=evidence.tracking_id,
            when_val=parser_res.when.selected_value,
            what_val=parser_res.what.selected_value or "",
            where_val=f"{parser_res.where.place_location or ''}-{parser_res.where.country_iso2 or ''}".strip("-"),
            proposed_filename=proposal.proposed_filename,
            status="enriched",
            needs_review=bool(remaining_reasons),
            review_reasons=remaining_reasons,
            parser_result_json=parser_res.model_dump_json(),
        )

        self.registry.record_review_action(
            tracking_id=evidence.tracking_id,
            action="enrich",
            reviewer=source,
            changes=changes,
            previous_values=previous_values,
        )

        return self.registry.get_file(evidence.tracking_id)

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
        if action not in ALLOWED_REVIEW_ACTIONS:
            raise ValueError(f"Invalid review action '{action}'. Must be one of {sorted(ALLOWED_REVIEW_ACTIONS)}")

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
            ok, msg = validate_calendar_date(w_clean)
            if not ok:
                raise ValueError(msg or f"Invalid date '{w_clean}'")
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
                ok_iso, msg_iso = validate_iso2_country(iso)
                if not ok_iso:
                    raise ValueError(msg_iso or f"Invalid country ISO2 code '{iso}'")
                parser_res.where.place_location = sanitize_filename_token(to_ascii_latin(place))
                parser_res.where.country_iso2 = iso.lower()
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
            # Strict shared validator check
            ok_cpf, errors = validate_canonical_filename(cpf, mode=self.planner.mode, tracking_id=tracking_id)
            if not ok_cpf:
                raise ValueError("; ".join(errors))
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
            what_val=parser_res.what.selected_value or "",
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

