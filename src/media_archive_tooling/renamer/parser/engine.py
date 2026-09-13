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


class RenamerParser:
    def __init__(
        self,
        categories_ref: Optional[List[Dict[str, Any]]] = None,
        locations_ref: Optional[List[Dict[str, Any]]] = None,
        countries_ref: Optional[Dict[str, str]] = None,
        vedabase_validator: Optional[Any] = None,
        location_lookup_provider: Optional[Any] = None,
    ):
        self.categories_ref = categories_ref
        self.vedabase_validator = vedabase_validator
        self.location_lookup_provider = location_lookup_provider
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

        # 1. Identity & Tracking ID
        tracking_id, working_name, _ = extract_or_generate_tracking_id(filename)
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

        # 4. Resolve WHEN
        when_res, remaining_after_when = parse_when(
            working_stem,
            parent_folder=parent_folder,
            ancestors=ancestor_folders,
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

        # 8. Check conflicts & review reasons
        conflicts = []
        review_reasons = []

        if what_conflict:
            conflicts.append(what_conflict)
            review_reasons.append(what_conflict)

        if when_res.state in (ResolutionState.AMBIGUOUS, ResolutionState.PROVISIONAL):
            review_reasons.append(f"WHEN resolution is {when_res.state.value} ({when_res.selected_value})")
        elif when_res.state == ResolutionState.UNRESOLVED:
            review_reasons.append("WHEN is unresolved")

        if what_res.state == ResolutionState.UNRESOLVED:
            review_reasons.append("WHAT is unresolved")

        if where_res.state == ResolutionState.UNRESOLVED:
            review_reasons.append("WHERE is unresolved")

        if file_metadata.corrupted:
            review_reasons.append("File marked as CORRUPTED")

        if file_metadata.possible_combination:
            review_reasons.append("File has combination clue (possible multiple recordings)")

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
            review_reasons=review_reasons
        )
