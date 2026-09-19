"""WHEN (date) parsing and resolution for media archive files."""
import json
import re
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any
from ..models import WhenResult, ResolutionState, Evidence

MIN_ARCHIVE_YEAR = 1993
MAX_ARCHIVE_YEAR = 2023

ASSETS_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / "assets"
MONTH_ALIASES_PATH = ASSETS_DIR / "month_aliases.json"

_MONTH_MAP: Dict[str, str] = {}


def _get_month_map() -> Dict[str, str]:
    global _MONTH_MAP
    if not _MONTH_MAP:
        if MONTH_ALIASES_PATH.exists():
            with open(MONTH_ALIASES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                for lang, mapping in data.items():
                    for alias, month_num in mapping.items():
                        _MONTH_MAP[alias.lower()] = month_num
        fallbacks = {
            "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
            "jul": "07", "aug": "08", "sep": "09", "sept": "09", "oct": "10", "okt": "10",
            "nov": "11", "dec": "12", "dez": "12", "duben": "04", "rijen": "10", "říjen": "10"
        }
        for k, v in fallbacks.items():
            _MONTH_MAP.setdefault(k, v)
    return _MONTH_MAP


def expand_two_digit_year(yy: int) -> Optional[int]:
    if 93 <= yy <= 99:
        return 1900 + yy
    elif 0 <= yy <= 23:
        return 2000 + yy
    return None


def is_valid_archive_year(year: int) -> bool:
    return MIN_ARCHIVE_YEAR <= year <= MAX_ARCHIVE_YEAR


def is_valid_date(year: int, month: int, day: int) -> bool:
    if not is_valid_archive_year(year):
        return False
    if not (1 <= month <= 12):
        return False
    days_in_month = [31, 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28,
                     31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return 1 <= day <= days_in_month[month - 1]


def _folder_numeric_context_evidence(
    parent_folder: str,
    ancestors: List[str],
    selected_date: str,
) -> Optional[Evidence]:
    """Return non-authoritative folder support for a selected filename date.

    A folder token such as ``8.9.11`` is ambiguous as a day/month date, but it
    can still corroborate that a file belongs to an August/September 2011
    collection. It must never override the explicit date in the filename.
    """
    try:
        selected_year, selected_month, _ = (int(part) for part in selected_date.split("-"))
    except (TypeError, ValueError):
        return None

    for folder in [parent_folder] + ancestors:
        for match in re.finditer(r"(?<!\d)(\d{1,2})[-._/](\d{1,2})[-._/](\d{2})(?!\d)", folder):
            first, second, short_year = (int(group) for group in match.groups())
            folder_year = expand_two_digit_year(short_year)
            if folder_year == selected_year and selected_month in {first, second}:
                return Evidence(
                    source="folder_numeric_context",
                    raw_value=folder,
                    details=(
                        f"supports year {selected_year} and includes month "
                        f"{selected_month:02d}; folder date order remains ambiguous"
                    ),
                )
    return None


def parse_when(
    filename: str,
    parent_folder: str = "",
    ancestors: Optional[List[str]] = None,
    us_context: bool = False,
    collection_grammar: Optional[Any] = None,
) -> Tuple[WhenResult, str]:
    """Parse WHEN date information from filename and folder context."""
    month_map = _get_month_map()
    ancestors = ancestors or []
    folder_context = " ".join([parent_folder] + ancestors).lower()
    
    # 1. Canonical ISO YYYY-MM-DD or explicit partials
    iso_match = re.search(
        r"(?:^|[\s_.-])((?:199[3-9]|20[0-2]\d|YYYY))[-._]((?:0[1-9]|1[0-2]|MM))[-._]((?:0[1-9]|[12]\d|3[01]|DD))(?=[_.\s-]|$)",
        filename
    )
    if iso_match:
        y, m, d = iso_match.group(1), iso_match.group(2), iso_match.group(3)
        val = f"{y}-{m}-{d}"
        span = iso_match.span()
        cleaned = filename[:span[0]] + " " + filename[span[1]:]
        res = WhenResult(
            selected_value=val,
            precision="day" if y != "YYYY" and m != "MM" and d != "DD" else "partial",
            state=ResolutionState.EXACT if (y != "YYYY" and m != "MM" and d != "DD") else ResolutionState.STRONG,
            evidence=[Evidence(source="filename_iso", raw_value=iso_match.group(0).strip(" _.-"))]
        )
        return res, cleaned.strip()

    # 2. Scripture guard
    masked_filename = re.sub(
        r"(?<![A-Za-z0-9])(?:S\s*\.?\s*B\.?|B\s*\.?\s*G\.?|C\s*\.?\s*C\.?)"
        r"[- .:]+\d+[- .:]+\d+(?:[- .:]+\d+)?(?=$|[^A-Za-z0-9])",
        lambda match: "_" * len(match.group(0)),
        filename,
        flags=re.IGNORECASE
    )

    # 3. Check for date with word month (e.g. "sep 2019", "feb-2015", "6-10-jul-2015", "27_8_15")
    for word, month_num in sorted(month_map.items(), key=lambda x: len(x[0]), reverse=True):
        pattern = rf"(?:^|[\s_.-])(?:(\d{{1,2}})[-._\s]+)?{re.escape(word)}(?:[-._\s]+(\d{{2,4}}))?(?=[_.\s-]|$)"
        m = re.search(pattern, masked_filename, re.IGNORECASE)
        if m:
            day_str, year_str = m.group(1), m.group(2)
            year_val = None
            if year_str:
                y_int = int(year_str)
                year_val = y_int if len(year_str) == 4 else expand_two_digit_year(y_int)
            elif folder_context:
                fy_match = re.search(r"\b(199[3-9]|20[01]\d|202[0-3])\b", folder_context)
                if fy_match:
                    year_val = int(fy_match.group(1))
                    
            if year_val and is_valid_archive_year(year_val):
                day_val = f"{int(day_str):02d}" if day_str and 1 <= int(day_str) <= 31 else "DD"
                val = f"{year_val}-{month_num}-{day_val}"
                span = m.span()
                cleaned = filename[:span[0]] + " " + filename[span[1]:]
                res = WhenResult(
                    selected_value=val,
                    precision="day" if day_val != "DD" else "month",
                    state=ResolutionState.STRONG,
                    evidence=[Evidence(source="filename_word_month", raw_value=m.group(0).strip(" _.-"), details=f"matched '{word}' -> {month_num}")]
                )
                return res, cleaned.strip()

    # 4. 3-part numeric date: e.g. "10-9-10", "03-10-23", "27_8_15", "24/5/11", "24-5-11"
    numeric_pattern = re.compile(
        r"(?:^|[\s_.-])(\d{1,4})[-._/](\d{1,2})[-._/](\d{1,4})(?=[_.\s-]|$)"
    )
    for num_match in numeric_pattern.finditer(masked_filename):
        p1, p2, p3 = num_match.group(1), num_match.group(2), num_match.group(3)
        span = num_match.span()
        
        candidates = []
        y1 = int(p1) if len(p1) == 4 else expand_two_digit_year(int(p1))
        m1, d1 = int(p2), int(p3)
        if y1 and is_valid_date(y1, m1, d1):
            candidates.append((f"{y1}-{m1:02d}-{d1:02d}", "YY-MM-DD"))
            
        y3 = int(p3) if len(p3) == 4 else expand_two_digit_year(int(p3))
        if y3:
            if is_valid_date(y3, int(p2), int(p1)):
                candidates.append((f"{y3}-{int(p2):02d}-{int(p1):02d}", "D-M-Y"))
            if is_valid_date(y3, int(p1), int(p2)):
                candidates.append((f"{y3}-{int(p1):02d}-{int(p2):02d}", "M-D-Y"))
                
        if candidates:
            cleaned = filename[:span[0]] + " " + filename[span[1]:]
            if len(candidates) == 1:
                cand, fmt = candidates[0]
                evidence = [Evidence(source="numeric_date", raw_value=num_match.group(0).strip(" _.-"), details=fmt)]
                folder_evidence = _folder_numeric_context_evidence(parent_folder, ancestors, cand)
                if folder_evidence:
                    evidence.append(folder_evidence)
                return WhenResult(
                    selected_value=cand,
                    precision="day",
                    state=ResolutionState.STRONG,
                    evidence=evidence,
                ), cleaned.strip()
            else:
                selected = None
                alternatives = []

                # If collection grammar has a repeated date format (e.g. YY-MM-DD), prioritize it
                if collection_grammar and getattr(collection_grammar, "repeated_date_format", None):
                    target_fmt = collection_grammar.repeated_date_format
                    for cand, fmt in candidates:
                        if fmt == target_fmt:
                            selected = cand
                            break

                if not selected:
                    for cand, fmt in candidates:
                        cyear, cmonth, cday = cand.split("-")
                        if cyear in folder_context:
                            selected = cand
                        else:
                            alternatives.append(cand)
                            
                if not selected:
                    if us_context:
                        selected = next((c for c, f in candidates if f == "M-D-Y"), candidates[0][0])
                    else:
                        selected = next((c for c, f in candidates if f == "D-M-Y"), candidates[0][0])
                    alternatives = [c for c, _ in candidates if c != selected]
                    
                evidence = [Evidence(source="numeric_ambiguous", raw_value=num_match.group(0).strip(" _.-"), details=f"grammar:{getattr(collection_grammar, 'repeated_date_format', None)}" if collection_grammar else None)]
                folder_evidence = _folder_numeric_context_evidence(parent_folder, ancestors, selected)
                if folder_evidence:
                    evidence.append(folder_evidence)
                return WhenResult(
                    selected_value=selected,
                    precision="day",
                    state=ResolutionState.PROVISIONAL if not (collection_grammar and getattr(collection_grammar, "repeated_date_format", None)) else ResolutionState.STRONG,
                    alternatives=alternatives,
                    evidence=evidence,
                ), cleaned.strip()

    # 5. Check folder context if filename has no date
    folder_year = re.search(r"\b(199[3-9]|20[01]\d|202[0-3])\b", folder_context)
    if folder_year:
        yr = int(folder_year.group(1))
        found_mo = None
        for word, mo in sorted(month_map.items(), key=lambda x: len(x[0]), reverse=True):
            if re.search(rf"(?:^|[\s_.-]){re.escape(word)}(?=[_.\s-]|$)", folder_context, re.IGNORECASE):
                found_mo = mo
                break
                
        is_sequence = bool(
            (collection_grammar and getattr(collection_grammar, "has_sequence_prefix", False))
            or re.match(r"^(\d{1,3})[\s_\-]+(?:KKS|SB|BG|CC)\b", filename.strip(), re.IGNORECASE)
        )
        lead_num = re.match(r"^(\d{1,2})\b", filename.strip())
        day_str = "DD"
        if not is_sequence and lead_num and 1 <= int(lead_num.group(1)) <= 31:
            day_str = f"{int(lead_num.group(1)):02d}"
            
        val = f"{yr}-{found_mo or 'MM'}-{day_str}"
        return WhenResult(
            selected_value=val,
            precision="day" if day_str != "DD" and found_mo else ("month" if found_mo else "year"),
            state=ResolutionState.PROVISIONAL if (day_str != "DD" and found_mo) else ResolutionState.STRONG,
            evidence=[Evidence(source="folder_context", raw_value=parent_folder)]
        ), filename

    return WhenResult(
        selected_value="YYYY-MM-DD",
        precision="none",
        state=ResolutionState.UNRESOLVED,
        evidence=[Evidence(source="default", raw_value="none")]
    ), filename
