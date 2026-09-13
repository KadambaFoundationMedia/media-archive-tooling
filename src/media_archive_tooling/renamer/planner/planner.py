"""Rename planner: formats proposed filenames and resolves collisions."""
import re
from pathlib import Path
from typing import List, Dict, Optional
from ..models import ParserResult, RenameProposal, RenameMode, ResolutionState
from ...common.ascii_latin import sanitize_filename_token, to_ascii_latin


class RenamePlanner:
    def __init__(self, mode: RenameMode = RenameMode.INITIAL):
        self.mode = mode

    def plan_rename(self, result: ParserResult) -> RenameProposal:
        """Construct a RenameProposal based on parsed fields and operating mode."""
        orig_path = Path(result.identity.original_path)
        ext = result.identity.extension.lower()
        tracking_id = result.identity.tracking_id

        has_meaningful_what = (
            result.what.selected_value is not None
            and result.what.state != ResolutionState.UNRESOLVED
        )
        has_meaningful_when = (
            result.when.selected_value != "YYYY-MM-DD"
            and result.when.state != ResolutionState.UNRESOLVED
        )

        if not has_meaningful_what and not has_meaningful_when:
            # Cannot yet meaningfully interpret -> retain useful original wording + tracking ID
            stem = Path(result.identity.original_filename).stem
            clean_stem = re.sub(r"_ID-[0-9a-fA-F]{8}$", "", stem)
            
            if self.mode == RenameMode.FINALIZE:
                proposed_name = f"{clean_stem}_ID-{tracking_id}{ext}"
            else:
                proposed_name = f"{clean_stem}_ID-{tracking_id}{ext}"
        else:
            parts = []
            
            # WHEN
            when_val = result.when.selected_value or "YYYY-MM-DD"
            parts.append(when_val)
            
            # WHO
            parts.append(result.who)
            
            # WHAT
            if result.what.selected_value:
                what_clean = sanitize_filename_token(to_ascii_latin(result.what.selected_value))
                parts.append(what_clean)
            elif result.what.category:
                cat_clean = sanitize_filename_token(to_ascii_latin(result.what.category))
                parts.append(cat_clean)
            else:
                if result.unclassified_text:
                    parts.append(sanitize_filename_token("-".join(result.unclassified_text[:2])))
                else:
                    parts.append("Recording")
                    
            # WHERE
            if result.where.place_location:
                place_clean = sanitize_filename_token(to_ascii_latin(result.where.place_location))
                if result.where.country_iso2:
                    parts.append(f"{place_clean}-{result.where.country_iso2.lower()}")
                else:
                    parts.append(place_clean)
            elif result.where.country_iso2:
                parts.append(result.where.country_iso2.lower())
                
            base_canonical = "_".join(parts)
            
            if self.mode == RenameMode.FINALIZE:
                proposed_name = f"{base_canonical}{ext}"
            else:
                proposed_name = f"{base_canonical}_ID-{tracking_id}{ext}"

        proposed_path = str(orig_path.parent / proposed_name)
        changes = (proposed_name != result.identity.current_filename)

        return RenameProposal(
            tracking_id=tracking_id,
            original_path=str(orig_path),
            current_filename=result.identity.current_filename,
            proposed_filename=proposed_name,
            proposed_path=proposed_path,
            mode=self.mode,
            is_collision=False,
            needs_review=bool(result.review_reasons),
            review_reasons=result.review_reasons,
            changes_detected=changes,
            parser_result=result
        )

    def resolve_batch_collisions(self, proposals: List[RenameProposal]) -> List[RenameProposal]:
        """Detect and resolve potential filename collisions within a batch and against existing files."""
        name_counts: Dict[str, List[RenameProposal]] = {}
        for p in proposals:
            name_counts.setdefault(p.proposed_filename.lower(), []).append(p)

        resolved_proposals = []
        for lower_name, group in name_counts.items():
            if len(group) == 1:
                resolved_proposals.append(group[0])
            else:
                for idx, item in enumerate(group):
                    item.is_collision = True
                    if self.mode == RenameMode.FINALIZE:
                        if item.parser_result.what.state != ResolutionState.UNRESOLVED:
                            if idx > 0:
                                p_path = Path(item.proposed_filename)
                                stem = p_path.stem
                                ext = p_path.suffix
                                item.proposed_filename = f"{stem}-{idx + 1:02d}{ext}"
                                item.proposed_path = str(Path(item.proposed_path).parent / item.proposed_filename)
                        else:
                            p_path = Path(item.proposed_filename)
                            item.proposed_filename = f"{p_path.stem}_ID-{item.tracking_id}{p_path.suffix}"
                            item.proposed_path = str(Path(item.proposed_path).parent / item.proposed_filename)
                    else:
                        pass
                    resolved_proposals.append(item)

        return resolved_proposals
