"""Domain and data models for Tool 1 — Renamer."""
from enum import Enum
from pathlib import Path
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class ResolutionState(str, Enum):
    EXACT = "exact"
    STRONG = "strong"
    PROVISIONAL = "provisional"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


class RenameMode(str, Enum):
    INITIAL = "initial"
    ENRICH = "enrich"
    FINALIZE = "finalize"


class Evidence(BaseModel):
    source: str  # e.g., "filename", "parent_folder", "sibling_grammar", "baserow", "cldr_month"
    raw_value: str
    details: Optional[str] = None


class WhenResult(BaseModel):
    selected_value: str = "YYYY-MM-DD"  # e.g. "2010-09-10", "2019-09-DD", "2018-MM-DD"
    precision: str = "unknown"  # "day", "month", "year", "none"
    state: ResolutionState = ResolutionState.UNRESOLVED
    alternatives: List[str] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)


class WhatResult(BaseModel):
    selected_value: Optional[str] = None  # e.g. "SB-1-4-5", "Kirtan", "Jaya-Radha-Madhava"
    category: Optional[str] = None  # Broad category e.g. "Srimad Bhagavatam", "Kirtan"
    state: ResolutionState = ResolutionState.UNRESOLVED
    candidates: List[str] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)


class WhereResult(BaseModel):
    place_location: Optional[str] = None  # Human-readable place e.g. "Vrindavan", "Villa-Vrindavan"
    country: Optional[str] = None  # e.g. "India", "Italy"
    country_iso2: Optional[str] = None  # Lowercase ISO 3166-1 alpha-2 e.g. "in", "it"
    state: ResolutionState = ResolutionState.UNRESOLVED
    alternatives: List[str] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)


class FileMetadata(BaseModel):
    edited: bool = False
    baserow_check_complete: bool = False
    corrupted: bool = False
    source_sequence_id: Optional[str] = None  # e.g. "A019", "R09_0004"
    part_or_track_number: Optional[str] = None  # e.g. "01", "07", "part-1"
    possible_combination: bool = False
    other_annotations: List[str] = Field(default_factory=list)


class EnrichmentEvidence(BaseModel):
    tracking_id: str
    when_val: Optional[str] = None
    when_state: Optional[ResolutionState] = None
    what_val: Optional[str] = None
    what_category: Optional[str] = None
    where_val: Optional[str] = None
    where_state: Optional[ResolutionState] = None
    who_val: Optional[str] = None
    baserow_check_complete: Optional[bool] = None
    possible_combination: Optional[bool] = None
    source_tool: Optional[str] = None
    confidence: Optional[str] = None
    details: Optional[str] = None


class Identity(BaseModel):
    tracking_id: str  # 8 hex characters, e.g. "a7c92e4b"
    original_filename: str
    original_path: str
    current_filename: str
    extension: str  # Lowercase extension including dot, e.g. ".mp3"


class Context(BaseModel):
    parent_folder: str = ""
    ancestor_folders: List[str] = Field(default_factory=list)
    sibling_pattern_context: Optional[str] = None
    filesystem_timestamps: Dict[str, Any] = Field(default_factory=dict)


class ParserResult(BaseModel):
    identity: Identity
    context: Context
    when: WhenResult = Field(default_factory=WhenResult)
    who: str = "KKS"
    what: WhatResult = Field(default_factory=WhatResult)
    where: WhereResult = Field(default_factory=WhereResult)
    file_metadata: FileMetadata = Field(default_factory=FileMetadata)
    unclassified_text: List[str] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)
    review_reasons: List[str] = Field(default_factory=list)
    diagnostic_notes: List[str] = Field(default_factory=list)
    downstream_routing: List[str] = Field(default_factory=list)


class RenameProposal(BaseModel):
    tracking_id: str
    original_path: str
    current_filename: str
    proposed_filename: str
    proposed_path: str
    mode: RenameMode
    is_collision: bool = False
    needs_review: bool = False
    review_reasons: List[str] = Field(default_factory=list)
    diagnostic_notes: List[str] = Field(default_factory=list)
    downstream_routing: List[str] = Field(default_factory=list)
    changes_detected: bool = False
    parser_result: ParserResult
    status: str = "pending"  # "pending", "approved", "committed", "failed", "deferred"
    error: Optional[str] = None
