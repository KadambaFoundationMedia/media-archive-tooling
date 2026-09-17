"""Rename planner: formats proposed filenames and resolves collisions."""
import re
from pathlib import Path
from typing import List, Dict, Optional
from ..models import ParserResult, RenameProposal, RenameMode, ResolutionState
from ...common.ascii_latin import sanitize_filename_token, to_ascii_latin
from ...media_db_reviewer.title_compaction import compact_title_for_filename


SAFE_ABBREVIATIONS = [
    ("Srimad-Bhagavatam", "SB"),
    ("Bhagavad-Gita", "BG"),
    ("Chaitanya-Charitamrita", "CC"),
    ("Jaya-Radha-Madhava", "JRM"),
    ("Sundayfeast", "Sunday-Feast"),
]


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
        edited_suffix = "_edited" if (result.file_metadata.edited and not result.file_metadata.baserow_check_complete) else ""

        if not has_meaningful_what:
            # Cannot yet form a semantic canonical filename. Retain a safe,
            # punctuation-free rendering of the useful original wording.
            orig_name = result.identity.original_filename
            stem = orig_name.rsplit(".", 1)[0] if "." in orig_name else orig_name
            clean_stem = re.sub(r"_ID-[0-9a-fA-F]{8}$", "", stem, flags=re.IGNORECASE)
            clean_stem = re.sub(r"_edited$", "", clean_stem, flags=re.IGNORECASE)
            clean_stem = to_ascii_latin(clean_stem)
            clean_stem = re.sub(r"[^A-Za-z0-9_-]+", "-", clean_stem)
            clean_stem = re.sub(r"-+", "-", clean_stem).strip("-_") or "Unresolved"
            if self.mode == RenameMode.FINALIZE and not edited_suffix:
                proposed_name = f"{clean_stem}{ext}"
            else:
                proposed_name = f"{clean_stem}{edited_suffix}_ID-{tracking_id}{ext}"
        else:
            parts = []

            # WHEN
            when_val = result.when.selected_value or "YYYY-MM-DD"
            parts.append(when_val)

            # WHO
            parts.append(result.who)

            # WHAT
            what_clean = sanitize_filename_token(to_ascii_latin(result.what.selected_value))
            parts.append(what_clean)

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

            if self.mode == RenameMode.FINALIZE and not edited_suffix:
                proposed_name = f"{base_canonical}{ext}"
            else:
                proposed_name = f"{base_canonical}{edited_suffix}_ID-{tracking_id}{ext}"

        review_reasons = list(result.review_reasons)
        needs_review = bool(review_reasons)

        # Enforce hard 128-character limit
        # 1. First shorten only the title component at whole-word boundaries if filename exceeds 128 during ENRICH/FINALIZE
        if len(proposed_name) > 128 and has_meaningful_what and self.mode in (RenameMode.ENRICH, RenameMode.FINALIZE):
            non_what_len = len(proposed_name) - len(what_clean)
            max_what_budget = max(10, 128 - non_what_len)
            if len(what_clean) > max_what_budget:
                scripture_match = re.match(r"^([A-Za-z0-9\-]+?)-(.*)$", what_clean)
                if scripture_match and re.match(r"^(?:BG|SB|CC|NOD|BS|ISO)-[0-9]", scripture_match.group(1), re.IGNORECASE):
                    prefix = scripture_match.group(1)
                    title_part = scripture_match.group(2)
                    avail_title = max_what_budget - len(prefix) - 1
                    if avail_title > 0:
                        compacted_title, was_c, reason = compact_title_for_filename(title_part, avail_title)
                        if was_c:
                            what_clean = f"{prefix}-{compacted_title}"
                            result.diagnostic_notes.append(f"Title shortened at whole-word boundary for length budget: {reason}")
                else:
                    compacted_what, was_c, reason = compact_title_for_filename(what_clean, max_what_budget)
                    if was_c:
                        what_clean = compacted_what
                        result.diagnostic_notes.append(f"Title shortened at whole-word boundary for length budget: {reason}")

                # Rebuild canonical name with compacted title
                parts[2] = what_clean
                base_canonical = "_".join(parts)
                if self.mode == RenameMode.FINALIZE and not edited_suffix:
                    proposed_name = f"{base_canonical}{ext}"
                else:
                    proposed_name = f"{base_canonical}{edited_suffix}_ID-{tracking_id}{ext}"

        # 2. Check safe abbreviations if still over 128
        if len(proposed_name) > 128:
            for full, abbr in SAFE_ABBREVIATIONS:
                if full in proposed_name:
                    candidate = proposed_name.replace(full, abbr)
                    if len(candidate) <= 128:
                        proposed_name = candidate
                        break
            if len(proposed_name) > 128:
                review_reasons.append(f"Filename exceeds maximum length of 128 characters (current: {len(proposed_name)})")
                needs_review = True

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
            needs_review=needs_review,
            review_reasons=review_reasons,
            diagnostic_notes=result.diagnostic_notes,
            downstream_routing=result.downstream_routing,
            changes_detected=changes,
            parser_result=result
        )

    def resolve_batch_collisions(self, proposals: List[RenameProposal]) -> List[RenameProposal]:
        """Detect and resolve potential filename collisions within a batch and against existing files on disk."""
        name_counts: Dict[str, List[RenameProposal]] = {}
        for p in proposals:
            name_counts.setdefault(p.proposed_filename.lower(), []).append(p)

        resolved_proposals = []
        for lower_name, group in name_counts.items():
            if len(group) == 1:
                item = group[0]
                # Check if file with same destination name already exists on disk for a distinct file
                dest_path = Path(item.proposed_path)
                orig_path = Path(item.original_path)
                if dest_path.exists() and dest_path.resolve() != orig_path.resolve():
                    item.is_collision = True
                    if self.mode == RenameMode.FINALIZE:
                        parent_dir = dest_path.parent
                        stem = dest_path.stem
                        ext = dest_path.suffix
                        counter = 2
                        while (parent_dir / f"{stem}-{counter:02d}{ext}").exists():
                            counter += 1
                        item.proposed_filename = f"{stem}-{counter:02d}{ext}"
                        item.proposed_path = str(parent_dir / item.proposed_filename)
                    else:
                        item.needs_review = True
                        item.review_reasons.append("Collision: target destination filename already exists on disk")
                resolved_proposals.append(item)
            else:
                for idx, item in enumerate(group):
                    item.is_collision = True
                    if self.mode == RenameMode.FINALIZE:
                        if (
                            item.parser_result.what.state != ResolutionState.UNRESOLVED
                            and not item.parser_result.file_metadata.possible_combination
                        ):
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
                        item.needs_review = True
                        item.review_reasons.append("Collision: duplicate proposed filename in batch")
                    resolved_proposals.append(item)

        return resolved_proposals
