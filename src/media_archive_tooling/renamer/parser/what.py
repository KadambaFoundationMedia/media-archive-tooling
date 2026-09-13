"""WHAT resolution: topic, scripture reference, and category matching."""
import json
import re
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any
from ..models import WhatResult, ResolutionState, Evidence
from ...common.ascii_latin import to_ascii_latin, sanitize_filename_token

ASSETS_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / "assets"
DEFAULT_CATEGORIES_PATH = ASSETS_DIR / "default_categories.json"

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

KNOWN_SPECIFIC_TERMS = {
    "jrm": "Jaya-Radha-Madhava",
    "jaya radha madhava": "Jaya-Radha-Madhava",
    "jaya-radha-madhava": "Jaya-Radha-Madhava",
    "bhajans": "Kirtan",
    "bhajan": "Kirtan",
    "kirtan": "Kirtan",
    "kirtany": "Kirtan",
    "kirtana": "Kirtan",
    "sunday feast": "Sundayfeast",
    "sundayfeast": "Sundayfeast",
    "home program": "Home-program",
    "home-program": "Home-program",
    "vyasa puja": "Vyasa-puja",
    "vyasapuja": "Vyasa-puja",
    "janmastami": "Janmastami",
    "janmashtami": "Janmastami",
    "gaura purnima": "Gaura-purnima",
    "radhastami": "Radhastami",
    "ratha yatra": "Ratha-yatra",
}


def load_category_definitions() -> List[Dict[str, Any]]:
    if DEFAULT_CATEGORIES_PATH.exists():
        with open(DEFAULT_CATEGORIES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def parse_what(
    filename: str,
    parent_folder: str = "",
    categories_ref: Optional[List[Dict[str, Any]]] = None,
    vedabase_validator: Optional[Any] = None,
) -> Tuple[WhatResult, str, Optional[str]]:
    """Resolve WHAT topic and category from filename and folder context."""
    categories = categories_ref or load_category_definitions()
    working = filename
    folder_context = parent_folder.lower()
    conflict: Optional[str] = None
    
    # 1. Check Scripture Verses
    sb_match = SB_REGEX.search(working)
    if sb_match:
        canto, chapter, v1, v2 = sb_match.groups()
        verse_str = f"{v1}-{v2}" if v2 else v1
        what_val = f"SB-{canto}-{chapter}-{verse_str}"
        span = sb_match.span()
        cleaned = working[:span[0]] + " " + working[span[1]:]
        
        if "kirtan" in folder_context:
            conflict = f"Filename WHAT '{what_val}' contradicts parent folder category 'Kirtan'"
            
        ev_details = "parsed"
        state = ResolutionState.EXACT
        if vedabase_validator:
            is_valid, v_status = vedabase_validator.validate_scripture_reference(what_val)
            ev_details = f"vedabase:{v_status}"
            if not is_valid and v_status == "not_found":
                state = ResolutionState.AMBIGUOUS
                conflict = f"Scripture reference '{what_val}' not found in Vedabase"
            
        res = WhatResult(
            selected_value=what_val,
            category="Srimad Bhagavatam",
            state=state,
            evidence=[Evidence(source="filename_scripture_sb", raw_value=sb_match.group(0).strip(" _.-"), details=ev_details)]
        )
        return res, cleaned.strip(), conflict

    bg_match = BG_REGEX.search(working)
    if bg_match:
        chapter, v1, v2 = bg_match.groups()
        verse_str = f"{v1}-{v2}" if v2 else v1
        what_val = f"BG-{chapter}-{verse_str}"
        span = bg_match.span()
        cleaned = working[:span[0]] + " " + working[span[1]:]
        
        if "kirtan" in folder_context:
            conflict = f"Filename WHAT '{what_val}' contradicts parent folder category 'Kirtan'"
            
        ev_details = "parsed"
        state = ResolutionState.EXACT
        if vedabase_validator:
            is_valid, v_status = vedabase_validator.validate_scripture_reference(what_val)
            ev_details = f"vedabase:{v_status}"
            if not is_valid and v_status == "not_found":
                state = ResolutionState.AMBIGUOUS
                conflict = f"Scripture reference '{what_val}' not found in Vedabase"
            
        res = WhatResult(
            selected_value=what_val,
            category="Bhagavad Gita",
            state=state,
            evidence=[Evidence(source="filename_scripture_bg", raw_value=bg_match.group(0).strip(" _.-"), details=ev_details)]
        )
        return res, cleaned.strip(), conflict

    cc_match = CC_REGEX.search(working)
    if cc_match:
        section, chapter, verse = cc_match.groups()
        sec_prefix = f"-{section.capitalize()}" if section else ""
        what_val = f"CC{sec_prefix}-{chapter}-{verse}"
        span = cc_match.span()
        cleaned = working[:span[0]] + " " + working[span[1]:]
        
        ev_details = "parsed"
        state = ResolutionState.EXACT
        if vedabase_validator:
            is_valid, v_status = vedabase_validator.validate_scripture_reference(what_val)
            ev_details = f"vedabase:{v_status}"
            if not is_valid and v_status == "not_found":
                state = ResolutionState.AMBIGUOUS
                conflict = f"Scripture reference '{what_val}' not found in Vedabase"
            
        res = WhatResult(
            selected_value=what_val,
            category="Chaitanya Charitamrita",
            state=state,
            evidence=[Evidence(source="filename_scripture_cc", raw_value=cc_match.group(0).strip(" _.-"), details=ev_details)]
        )
        return res, cleaned.strip(), conflict

    # 2. Check Specific Known Terms
    working_lower = working.lower()
    for term, canonical in sorted(KNOWN_SPECIFIC_TERMS.items(), key=lambda x: len(x[0]), reverse=True):
        pattern = rf"(?:^|[\s_.-]){re.escape(term)}(?=[_.\s-]|$)"
        m = re.search(pattern, working_lower)
        if m:
            span = m.span()
            cleaned = working[:span[0]] + " " + working[span[1]:]
            res = WhatResult(
                selected_value=canonical,
                category="Kirtan" if "Radha" in canonical or canonical == "Kirtan" else None,
                state=ResolutionState.STRONG,
                evidence=[Evidence(source="known_specific_term", raw_value=m.group(0).strip(" _.-"), details=f"mapped to {canonical}")]
            )
            return res, cleaned.strip(), conflict

    # 3. Check Category Title matching terms
    best_match = None
    best_len = 0
    best_category = None
    
    for cat in categories:
        cat_name = cat.get("category", "")
        terms = [t.strip().lower() for t in cat.get("title_matching_terms", "").split(",") if t.strip()]
        for term in terms:
            pattern = rf"(?:^|[\s_.-]){re.escape(term)}(?=[_.\s-]|$)"
            m = re.search(pattern, working_lower)
            if m and len(term) > best_len:
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
                    evidence=[Evidence(source="folder_category_context", raw_value=parent_folder, details=f"matched '{cat_name}'")]
                )
                return res, working, conflict

    return WhatResult(
        selected_value=None,
        category=None,
        state=ResolutionState.UNRESOLVED,
        evidence=[]
    ), working, conflict
