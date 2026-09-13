"""Core Renamer parser engine integrating all normalization and extraction components."""
import re
from pathlib import Path
from typing import Optional, List, Dict, Any

from ..models import (
    ParserResult, Identity, Context, FileMetadata,
    WhenResult, WhatResult, WhereResult, ResolutionState, RenameMode
)
from .technical import extract_or_generate_tracking_id, extract_technical_metadata
from .when import parse_when
from .what import parse_what
from .where import WhereResolver
from .collection import CollectionGrammar
from ...common.ascii_latin import to_ascii_latin, sanitize_filename_token


CLASS_KEYWORDS_REGEX = re.compile(
    r"\b(class|classes|lecture|lectures|prednaska|prednasky|lekce)\b",
    re.IGNORECASE
)


def has_class_evidence(
    what_res: WhatResult,
    filename: str,
    parent_folder: str = "",
    ancestor_folders: Optional[List[str]] = None,
) -> bool:
    """Determine whether direct evidence already establishes that the item is a class."""
    if what_res.category in ("Class", "Lecture"):
        return True
    if what_res.selected_value in ("Class", "Lecture"):
        return True
    if CLASS_KEYWORDS_REGEX.search(filename):
        return True
    if parent_folder and CLASS_KEYWORDS_REGEX.search(parent_folder):
        return True
    if ancestor_folders:
        for folder in ancestor_folders:
            if CLASS_KEYWORDS_REGEX.search(folder):
                return True
    return False


class RenamerParser:
    def __init__(
        self,
        categories_ref: Optional[List[Dict[str, Any]]] = None,
        locations_ref: Optional[List[Dict[str, Any]]] = None,
        countries_ref: Optional[Dict[str, str]] = None,
        vedabase_validator: Optional[Any] = None,
        location_lookup_provider: Optional[Any] = None,
        registry: Optional[Any] = None,
    ):
        self.categories_ref = categories_ref
        self.vedabase_validator = vedabase_validator
        self.location_lookup_provider = location_lookup_provider
        self.registry = registry
        self.where_resolver = WhereResolver(
            locations_data=locations_ref,
            countries_data=countries_ref,
            location_lookup_provider=location_lookup_provider
        )

    def parse_file(
        self,
        file_path: Path,
        mode: RenameMode = RenameMode.INITIAL,
        collection_grammar: Optional[CollectionGrammar] = None,
        timestamps: Optional[Dict[str, Any]] = None,
        raw_filename: Optional[str] = None,
    ) -> ParserResult:
        """Parse an archive file into a structured ParserResult."""
        filename = raw_filename or file_path.name
        ext = Path(filename).suffix.lower() if "." in filename else file_path.suffix.lower()
        parent_folder = file_path.parent.name
        ancestor_folders = [p.name for p in file_path.parents if p.name and p != file_path.parent]

        # 1. Identity & Tracking ID with registry collision check
        tracking_id, working_name, _ = extract_or_generate_tracking_id(filename, registry=self.registry)
        # Remove extension from working name
        if working_name.lower().endswith(ext):
            stem = working_name[:-len(ext)] if ext else working_name
        else:
            stem = Path(working_name).stem

        # 2. Extract technical flags & metadata
        working_stem, file_metadata = extract_technical_metadata(stem)

        # 3. Collection grammar clues & sequence prefix extraction
        pattern_desc = collection_grammar.describe() if collection_grammar else None
        if collection_grammar and collection_grammar.has_sequence_prefix and not file_metadata.source_sequence_id:
            seq_match = re.match(r"^(\d{1,3})\b", working_stem.strip())
            if seq_match:
                file_metadata.source_sequence_id = seq_match.group(1)
                working_stem = working_stem.strip()[seq_match.end():].lstrip(" _-")

        # Derive lightweight location context before ambiguous-date selection
        prelim_where, _ = self.where_resolver.resolve(
            working_stem,
            parent_folder=parent_folder,
            ancestor_folders=ancestor_folders
        )
        us_context = (prelim_where.country_iso2 == "us")

        # 4. Resolve WHEN
        when_res, remaining_after_when = parse_when(
            working_stem,
            parent_folder=parent_folder,
            ancestors=ancestor_folders,
            us_context=us_context,
            collection_grammar=collection_grammar,
        )

        # 5. Resolve WHAT
        what_res, remaining_after_what, what_conflict = parse_what(
            remaining_after_when,
            parent_folder=parent_folder,
            categories_ref=self.categories_ref,
            vedabase_validator=self.vedabase_validator,
        )

        # 6. Resolve WHERE
        where_res, residual_text = self.where_resolver.resolve(
            remaining_after_what,
            parent_folder=parent_folder,
            ancestor_folders=ancestor_folders
        )

        # 7. Unclassified text & residual tokens
        noise_tokens = {"kks", "mp3", "wma", "avi", "mp4", "wav", "m4a", "-", "_"}
        residual_tokens = [
            t for t in re.split(r"[\s_\-]+", residual_text)
            if t.strip() and t.lower() not in noise_tokens
        ]

        # 8. Check conflicts, downstream routing, and review reasons
        conflicts = []
        review_reasons = []
        diagnostic_notes = []
        downstream_routing = []

        if what_conflict:
            conflicts.append(what_conflict)
            review_reasons.append(what_conflict)

        # Check filename-vs-folder date conflict
        folder_text = " ".join([parent_folder] + ancestor_folders).lower()
        folder_yr_match = re.search(r"\b(199[3-9]|20[01]\d|202[0-3])\b", folder_text)
        if folder_yr_match and when_res.selected_value != "YYYY-MM-DD":
            f_yr = folder_yr_match.group(1)
            if not when_res.selected_value.startswith(f_yr) and "YYYY" not in when_res.selected_value:
                date_conflict = f"Filename date '{when_res.selected_value}' conflicts with folder year '{f_yr}'"
                when_res.conflicts.append(date_conflict)
                conflicts.append(date_conflict)
                review_reasons.append(date_conflict)

        # Genuine ambiguities / competing interpretations require human decision
        if when_res.state == ResolutionState.AMBIGUOUS:
            review_reasons.append(f"WHEN resolution is ambiguous between {when_res.selected_value} and alternatives {when_res.alternatives}")
        elif when_res.state == ResolutionState.PROVISIONAL:
            diagnostic_notes.append(f"WHEN resolution is provisional ({when_res.selected_value})")
        elif when_res.state == ResolutionState.UNRESOLVED:
            diagnostic_notes.append("WHEN is unresolved")
            downstream_routing.append("tool_2_3_media_enrichment")

        if what_res.state == ResolutionState.AMBIGUOUS:
            review_reasons.append(f"WHAT resolution is ambiguous for '{what_res.selected_value}'")
        elif what_res.state == ResolutionState.UNRESOLVED:
            if has_class_evidence(what_res, filename, parent_folder, ancestor_folders):
                diagnostic_notes.append("Class WHAT is unresolved")
                downstream_routing.append("tool_7_class_classification")
            else:
                diagnostic_notes.append("WHAT is unresolved")
                downstream_routing.append("tool_2_media_database_review")
                downstream_routing.append("tool_5_content_discovery")
        elif what_res.selected_value in ("Class", "Lecture"):
            diagnostic_notes.append("Unidentified class WHAT")
            downstream_routing.append("tool_7_class_classification")

        if where_res.state == ResolutionState.AMBIGUOUS:
            review_reasons.append(f"WHERE resolution is ambiguous near-tie for '{where_res.place_location}' (alternatives: {where_res.alternatives})")
        elif where_res.state == ResolutionState.PROVISIONAL:
            diagnostic_notes.append(f"WHERE resolution is provisional ({where_res.place_location}-{where_res.country_iso2 or ''})")
            downstream_routing.append("tool_2_3_media_enrichment")
        elif where_res.state == ResolutionState.UNRESOLVED:
            diagnostic_notes.append("WHERE is unresolved")
            downstream_routing.append("tool_2_3_media_enrichment")

        if file_metadata.corrupted:
            review_reasons.append("File marked as CORRUPTED")

        if file_metadata.possible_combination:
            # Combination is a downstream split task (Tools 5/6), not an immediate human review error
            diagnostic_notes.append("File has combination clue; retaining source stem + ID for splitting")
            downstream_routing.append("tool_5_6_split_combination")

        diagnostic_notes = list(dict.fromkeys(diagnostic_notes))
        downstream_routing = list(dict.fromkeys(downstream_routing))

        identity = Identity(
            tracking_id=tracking_id,
            original_filename=filename,
            original_path=str(file_path),
            current_filename=filename,
            extension=ext
        )

        context = Context(
            parent_folder=parent_folder,
            ancestor_folders=ancestor_folders[:3],
            sibling_pattern_context=pattern_desc,
            filesystem_timestamps=timestamps or {}
        )

        return ParserResult(
            identity=identity,
            context=context,
            when=when_res,
            who="KKS",
            what=what_res,
            where=where_res,
            file_metadata=file_metadata,
            unclassified_text=residual_tokens,
            conflicts=conflicts,
            review_reasons=review_reasons,
            diagnostic_notes=diagnostic_notes,
            downstream_routing=downstream_routing
        )
