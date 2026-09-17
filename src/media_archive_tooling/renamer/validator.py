"""Shared filename and proposal validator for automatic planning, review edits, and finalization."""
import json
import re
from pathlib import Path
from typing import Tuple, List, Optional, Dict
from .models import RenameMode

ASSETS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "assets"
COUNTRIES_PATH = ASSETS_DIR / "country_codes.json"

_VALID_ISO2_CODES: Optional[set] = None

def _get_valid_iso2_codes() -> set:
    global _VALID_ISO2_CODES
    if _VALID_ISO2_CODES is None:
        if COUNTRIES_PATH.exists():
            with open(COUNTRIES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                _VALID_ISO2_CODES = {v.lower() for v in data.values()}
        else:
            _VALID_ISO2_CODES = {"in", "it", "cz", "nl", "be", "au", "se", "no", "si", "rs", "ch", "es", "za", "gb", "us", "de", "fr", "ru", "sk"}
    return _VALID_ISO2_CODES

def is_leap_year(year: int) -> bool:
    return (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0))

def get_days_in_month(year: int, month: int) -> int:
    if month in (1, 3, 5, 7, 8, 10, 12):
        return 31
    elif month in (4, 6, 9, 11):
        return 30
    elif month == 2:
        return 29 if is_leap_year(year) else 28
    return 0

def validate_calendar_date(date_str: str) -> Tuple[bool, Optional[str]]:
    """Validate date string format and calendar correctness (1993-2023, valid day/month)."""
    if not date_str or not isinstance(date_str, str):
        return False, "Date string is empty"
    
    parts = date_str.split("-")
    if len(parts) != 3:
        return False, f"Date {date_str} must have 3 parts separated by hyphens (YYYY-MM-DD)"
        
    y_str, m_str, d_str = parts[0], parts[1], parts[2]
    
    # Year validation
    if y_str != "YYYY":
        if not (y_str.isdigit() and len(y_str) == 4):
            return False, f"Invalid date format: Invalid year {y_str}"
        y = int(y_str)
        if not (1993 <= y <= 2023):
            return False, f"Invalid date: Year {y} outside archive bounds (1993-2023)"
    else:
        y = None
        
    # Month validation
    if m_str != "MM":
        if not (m_str.isdigit() and len(m_str) == 2):
            return False, f"Invalid date format: Invalid month {m_str}"
        m = int(m_str)
        if not (1 <= m <= 12):
            return False, f"Invalid date: Month {m} must be between 01 and 12"
    else:
        m = None
        
    # Day validation
    if d_str != "DD":
        if not (d_str.isdigit() and len(d_str) == 2):
            return False, f"Invalid date format: Invalid day {d_str}"
        d = int(d_str)
        if not (1 <= d <= 31):
            return False, f"Invalid date: Day {d} must be between 01 and 31"
        if y is not None and m is not None:
            max_days = get_days_in_month(y, m)
            if d > max_days:
                return False, f"Invalid date: Day {d} exceeds max {max_days} days for month {m:02d} in year {y}"
                
    return True, None

def validate_iso2_country(code: str) -> Tuple[bool, Optional[str]]:
    """Validate lowercase ISO 3166-1 alpha-2 code."""
    if not code or len(code) != 2:
        return False, f"Country code {code} must be exactly 2 characters"
    c_lower = code.lower()
    valid_codes = _get_valid_iso2_codes()
    if c_lower not in valid_codes:
        return False, f"{code} is not a recognized ISO 3166-1 alpha-2 country code"
    return True, None

def validate_canonical_filename(
    filename: str,
    mode: RenameMode = RenameMode.INITIAL,
    tracking_id: Optional[str] = None
) -> Tuple[bool, List[str]]:
    """Comprehensive validation of canonical or custom proposed filenames."""
    errors: List[str] = []
    
    if not filename:
        return False, ["Filename cannot be empty"]
        
    # 1. Length check (hard 128 characters)
    if len(filename) > 128:
        errors.append(f"Filename exceeds maximum length of 128 characters (current: {len(filename)})")
        
    # 2. One-dot rule
    dot_count = filename.count(".")
    if dot_count == 0:
        errors.append("Filename must have an extension")
    elif dot_count > 1:
        errors.append(f"Filename violates the one-dot rule (has {dot_count} dots)")
        
    # 3. ASCII and illegal character check
    try:
        filename.encode("ascii")
    except UnicodeEncodeError:
        errors.append("Filename contains non-ASCII characters")
        
    # The archive convention is intentionally stricter than filesystem rules:
    # only ASCII letters/digits, underscore and hyphen are allowed before the
    # single extension dot. Parentheses and all other punctuation are rejected.
    illegal_chars = re.findall(r"[^A-Za-z0-9_.-]", filename)
    if illegal_chars:
        chars_joined = ", ".join(set(illegal_chars))
        errors.append(f"Filename contains illegal characters: {chars_joined}")
        
    if " " in filename:
        errors.append("Filename cannot contain spaces (use underscores between parts and hyphens within parts)")
        
    # 4. Tracking ID placement
    if mode != RenameMode.FINALIZE:
        if tracking_id:
            expected_tag = f"_ID-{tracking_id.lower()}"
            if expected_tag.lower() not in filename.lower():
                errors.append(f"Filename must contain tracking ID tag {expected_tag} before extension in {mode.value} mode")
        else:
            if not re.search(r"_ID-[0-9a-fA-F]{8}(?=\.[^.]+$)", filename):
                errors.append(f"Filename must end with tracking ID tag _ID-xxxxxxxx before extension in {mode.value} mode")
                
    # 5. Field checks if canonical WWWW structure (at least 3 underscore-delimited parts)
    stem = Path(filename).stem
    # strip _ID-xxxxxxxx and _edited
    stem_no_id = re.sub(r"_ID-[0-9a-fA-F]{8}$", "", stem)
    stem_no_id = re.sub(r"_edited$", "", stem_no_id)
    parts = stem_no_id.split("_")
    
    if len(parts) >= 3 and re.match(r"^(?:199\d|20[0-2]\d|YYYY)", parts[0]):
        # Validate WHEN part
        ok, msg = validate_calendar_date(parts[0])
        if not ok:
            errors.append(msg or "Invalid WHEN in filename")
            
        # If WHERE part is present (parts >= 4)
        if len(parts) >= 4:
            where_part = parts[3]
            if "-" in where_part:
                iso2 = where_part.rsplit("-", 1)[1]
                if len(iso2) == 2:
                    ok_iso, msg_iso = validate_iso2_country(iso2)
                    if not ok_iso:
                        errors.append(msg_iso or "Invalid ISO2 in WHERE part")
                        
    return (len(errors) == 0), errors
