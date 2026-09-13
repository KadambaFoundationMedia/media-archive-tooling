"""WHAT resolution: topic, scripture reference, and category matching."""
import json
import re
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any
from ..models import WhatResult, ResolutionState, Evidence
from ...common.ascii_latin import to_ascii_latin, sanitize_filename_token

ASSETS_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / "assets"
DEFAULT_CATEGORIES_PATH = ASSETS_DIR / "default_categories.json"
SPECIFIC_TITLES_PATH = ASSETS_DIR / "specific_titles.json"

# Scripture patterns
SB_REGEX = re.compile(
    r"(?:^|[\s_.-])(?:SB|Srimad[- ]?Bhagavatam)[- ]+(\d{1,2})[- .:]+(\d{1,2})[- .:]+(\d{1,3})(?:[- .:]+(\d{1,3}))?(?=[_.\s-]|$)",
    re.IGNORECASE
)
BG_REGEX = re.compile(
    r"(?:^|[\s_.-])(?:BG|Bhagavad[- ]?Gita)[- ]+(\d{1,2})[- .:]+(\d{1,3})(?:[- .:]+(\d{1,3}))?(?=[_.\s-]|$)",
    re.IGNORECASE
)
CC_REGEX = re.compile(
    r"(?:^|[\s_.-])(?:CC|Caitanya[- ]?Caritamrta|Chaitanya[- ]?Charitamrita)[- ]+(?:(Adi|Madhya|Antya)[- ]+)?(\d{1,2})[- .:]+(\d{1,3})(?=[_.\s-]|$)",
    re.IGNORECASE
)


def load_category_definitions() -> List[Dict[str, Any]]:
    if DEFAULT_CATEGORIES_PATH.exists():
        with open(DEFAULT_CATEGORIES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def load_specific_title_definitions() -> List[Dict[str, Any]]:
    if SPECIFIC_TITLES_PATH.exists():
        with open(SPECIFIC_TITLES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _check_scripture_descriptive_suffix(
    working: str,
    span_end: int,
    specific_titles: List[Dict[str, Any]]
) -> Tuple[Optional[str], int]:
    """Check if a meaningful descriptive suffix (e.g. -Sundayfeast) immediately follows a scripture match."""
    remainder = working[span_end:]
    if remainder.startswith(("-", "_")):
        m = re.match(r"^[-_]([A-Za-z0-9\-]+)", remainder)
        if m:
            raw_suffix = m.group(1).strip("-_")
            # Do not consume technical tokens, country codes, or tracking IDs
            if not re.match(r"^(?:ID-[0-9a-fA-F]{8}|cz|in|nl|it|us|se|no|rs|kks|\d+)$", raw_suffix, re.IGNORECASE):
                # Check if it matches a known specific title
                for item in specific_titles:
                    canon = item.get("canonical_what", "")
                    terms = [t.lower() for t in item.get("terms", [])] + [canon.lower()]
                    if raw_suffix.lower() in terms:
                        return canon, len(m.group(0))
                # Otherwise preserve as cleaned ASCII token if plausible word
                if len(raw_suffix) >= 3 and not raw_suffix.isdigit():
                    return sanitize_filename_token(to_ascii_latin(raw_suffix)), len(m.group(0))
    return None, 0


def parse_what(
    filename: str,
    parent_folder: str = "",
    categories_ref: Optional[List[Dict[str, Any]]] = None,
    vedabase_validator: Optional[Any] = None,
    specific_titles_ref: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[WhatResult, str, Optional[str]]:
    """Resolve WHAT topic and category from filename and folder context."""
    categories = categories_ref or load_category_definitions()
    specific_titles = specific_titles_ref or load_specific_title_definitions()
    working = filename
    folder_context = parent_folder.lower()
    conflict: Optional[str] = None
    candidates: List[str] = []

    # 1. Check Scripture Verses
    sb_match = SB_REGEX.search(working)
    if sb_match:
        canto, chapter, v1, v2 = sb_match.groups()
        verse_str = f"{v1}-{v2}" if v2 else v1
        base_ref = f"SB-{canto}-{chapter}-{verse_str}"
        what_val = base_ref
        span = sb_match.span()
        suffix_token, suffix_len = _check_scripture_descriptive_suffix(working, span[1], specific_titles)
        if suffix_token:
            what_val = f"{what_val}-{suffix_token}"
            cleaned = working[:span[0]] + " " + working[span[1] + suffix_len:]
        else:
            cleaned = working[:span[0]] + " " + working[span[1]:]

        if "kirtan" in folder_context:
            conflict = f"Filename WHAT '{what_val}' contradicts parent folder category 'Kirtan'"

        ev_details = "parsed"
        state = ResolutionState.EXACT
        if vedabase_validator:
            is_valid, v_status = vedabase_validator.validate_scripture_reference(base_ref)
            ev_details = f"vedabase:{v_status}"
            if not is_valid and v_status == "not_found":
                state = ResolutionState.AMBIGUOUS
                conflict = f"Scripture reference '{what_val}' not found in Vedabase"

        candidates.append(what_val)
        candidates.append("Srimad Bhagavatam")
        res = WhatResult(
            selected_value=what_val,
            category="Srimad Bhagavatam",
            state=state,
            candidates=candidates,
            evidence=[Evidence(source="filename_scripture_sb", raw_value=sb_match.group(0).strip(" _.-"), details=ev_details)]
        )
        return res, cleaned.strip(), conflict

    bg_match = BG_REGEX.search(working)
    if bg_match:
        chapter, v1, v2 = bg_match.groups()
        verse_str = f"{v1}-{v2}" if v2 else v1
        base_ref = f"BG-{chapter}-{verse_str}"
        what_val = base_ref
        span = bg_match.span()
        suffix_token, suffix_len = _check_scripture_descriptive_suffix(working, span[1], specific_titles)
        if suffix_token:
            what_val = f"{what_val}-{suffix_token}"
            cleaned = working[:span[0]] + " " + working[span[1] + suffix_len:]
        else:
            cleaned = working[:span[0]] + " " + working[span[1]:]

        if "kirtan" in folder_context:
            conflict = f"Filename WHAT '{what_val}' contradicts parent folder category 'Kirtan'"

        ev_details = "parsed"
        state = ResolutionState.EXACT
        if vedabase_validator:
            is_valid, v_status = vedabase_validator.validate_scripture_reference(base_ref)
            ev_details = f"vedabase:{v_status}"
            if not is_valid and v_status == "not_found":
                state = ResolutionState.AMBIGUOUS
                conflict = f"Scripture reference '{what_val}' not found in Vedabase"

        candidates.append(what_val)
        candidates.append("Bhagavad Gita")
        res = WhatResult(
            selected_value=what_val,
            category="Bhagavad Gita",
            state=state,
            candidates=candidates,
            evidence=[Evidence(source="filename_scripture_bg", raw_value=bg_match.group(0).strip(" _.-"), details=ev_details)]
        )
        return res, cleaned.strip(), conflict

    cc_match = CC_REGEX.search(working)
    if cc_match:
        section, chapter, verse = cc_match.groups()
        sec_prefix = f"-{section.capitalize()}" if section else ""
        base_ref = f"CC{sec_prefix}-{chapter}-{verse}"
        what_val = base_ref
        span = cc_match.span()
        suffix_token, suffix_len = _check_scripture_descriptive_suffix(working, span[1], specific_titles)
        if suffix_token:
            what_val = f"{what_val}-{suffix_token}"
            cleaned = working[:span[0]] + " " + working[span[1] + suffix_len:]
        else:
            cleaned = working[:span[0]] + " " + working[span[1]:]

        ev_details = "parsed"
        state = ResolutionState.EXACT
        if vedabase_validator:
            is_valid, v_status = vedabase_validator.validate_scripture_reference(base_ref)
            ev_details = f"vedabase:{v_status}"
            if not is_valid and v_status == "not_found":
                state = ResolutionState.AMBIGUOUS
                conflict = f"Scripture reference '{what_val}' not found in Vedabase"

        candidates.append(what_val)
        candidates.append("Chaitanya Charitamrita")
        res = WhatResult(
            selected_value=what_val,
            category="Chaitanya Charitamrita",
            state=state,
            candidates=candidates,
            evidence=[Evidence(source="filename_scripture_cc", raw_value=cc_match.group(0).strip(" _.-"), details=ev_details)]
        )
        return res, cleaned.strip(), conflict

    # 2. Check Specific Known Terms from project reference data
    working_lower = working.lower()
    matched_specific = None
    matched_specific_span = None

    for item in specific_titles:
        canon = item.get("canonical_what", "")
        cat = item.get("category")
        for term in sorted(item.get("terms", []), key=len, reverse=True):
            pattern = rf"(?:^|[\s_.-]){re.escape(term.lower())}(?=[_.\s-]|$)"
            m = re.search(pattern, working_lower)
            if m:
                candidates.append(canon)
                if not matched_specific:
                    matched_specific = (canon, cat, m.group(0).strip(" _.-"))
                    matched_specific_span = m.span()

    if matched_specific and matched_specific_span:
        canon, cat, raw_tok = matched_specific
        span = matched_specific_span
        cleaned = working[:span[0]] + " " + working[span[1]:]
        res = WhatResult(
            selected_value=canon,
            category=cat,
            state=ResolutionState.STRONG,
            candidates=list(dict.fromkeys(candidates)),
            evidence=[Evidence(source="specific_title_reference", raw_value=raw_tok, details=f"mapped to {canon}")]
        )
        return res, cleaned.strip(), conflict

    # 3. Check Category Title matching terms
    matched_cats = []
    best_match = None
    best_len = 0
    best_category = None

    for cat in categories:
        cat_name = cat.get("category", "")
        terms = [t.strip().lower() for t in cat.get("title_matching_terms", "").split(",") if t.strip()]
        for term in terms:
            pattern = rf"(?:^|[\s_.-]){re.escape(term)}(?=[_.\s-]|$)"
            m = re.search(pattern, working_lower)
            if m:
                matched_cats.append(cat_name)
                if len(term) > best_len:
                    best_len = len(term)
                    best_match = m
                    best_category = cat_name

    if best_match:
        span = best_match.span()
        raw_matched = working[span[0]:span[1]].strip(" _.-")
        cleaned = working[:span[0]] + " " + working[span[1]:]
        canon_what = sanitize_filename_token(to_ascii_latin(best_category))
        res = WhatResult(
            selected_value=canon_what,
            category=best_category,
            state=ResolutionState.STRONG,
            candidates=list(dict.fromkeys(matched_cats)),
            evidence=[Evidence(source="category_title_terms", raw_value=raw_matched, details=f"matched category '{best_category}'")]
        )
        return res, cleaned.strip(), conflict

    # 4. Folder Context fallback
    for cat in categories:
        cat_name = cat.get("category", "")
        terms = [t.strip().lower() for t in cat.get("title_matching_terms", "").split(",") if t.strip()]
        for term in terms:
            if re.search(rf"(?:^|[\s_.-]){re.escape(term)}(?=[_.\s-]|$)", folder_context):
                canon_what = sanitize_filename_token(to_ascii_latin(cat_name))
                res = WhatResult(
                    selected_value=canon_what,
                    category=cat_name,
                    state=ResolutionState.PROVISIONAL,
                    candidates=[cat_name],
                    evidence=[Evidence(source="folder_category_context", raw_value=parent_folder, details=f"matched '{cat_name}'")]
                )
                return res, working, conflict

    return WhatResult(
        selected_value=None,
        category=None,
        state=ResolutionState.UNRESOLVED,
        candidates=[],
        evidence=[]
    ), working, conflict
