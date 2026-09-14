"""Media Database Reconciliation Engine for Tool 2 (Build Plan Sections 10-15)."""
from datetime import datetime
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

from rapidfuzz import fuzz

from ..common.ascii_latin import to_ascii_latin
from ..renamer.models import ParserResult, ResolutionState
from ..renamer.parser.what import SB_REGEX, BG_REGEX, CC_REGEX
from .models import (
    BaserowSnapshot,
    FieldComparison,
    FieldComparisonState,
    MediaCandidate,
    MediaDatabaseReviewResult,
    RenamerEnrichment,
    ReviewDecision,
    Tool4Action,
)
from .baserow_provider import (
    normalize_category_title_row,
    normalize_media_row,
    normalize_travel_schedule_row,
)


GENERIC_WHAT_TOKENS = {
    "class", "classes", "lecture", "lectures", "lekce", "prednaska",
    "bhajan", "bhajans", "bhajana", "bhajane",
    "kirtan", "kirtans", "kirtana", "kirtany",
    "chanting", "harinama", "maha mantra", "mahamantra", "japa",
    "seminar", "seminars", "workshop", "retreat",
    "talk", "talks", "speech", "general", "misc", "unknown",
    "program", "programme", "event", "darshan", "meeting", "conversation",
    "srimadbhagavatam", "bhagavadgita", "chaitanyacaritamrta", "chaitanyacharitamrita",
    "sundayfeast", "sunday feast", "festival", "initiation", "vyasapuja", "vyasa puja",
}


def is_specific_what(val: Optional[str]) -> bool:
    """Determine whether a WHAT string is sufficiently specific to participate in auto-association."""
    if not val or not str(val).strip():
        return False
    clean = str(val).strip()
    if parse_scripture_reference(clean) is not None:
        return True
    norm = _norm_token(clean)
    if not norm or len(norm) < 4:
        return False
    if norm in GENERIC_WHAT_TOKENS:
        return False
    # Check if all constituent words are generic tokens or generic qualifiers
    words = re.findall(r"[a-zA-Z0-9]+", clean.lower())
    generic_qualifiers = {
        "morning", "evening", "afternoon", "daily", "special", "intro", "introduction",
        "part", "pt", "disc", "cd", "tape", "audio", "video", "track", "session",
    }
    if words and all(w in GENERIC_WHAT_TOKENS or w in generic_qualifiers or _norm_token(w) in GENERIC_WHAT_TOKENS for w in words):
        return False
    return True


def _norm_token(text: Optional[str]) -> str:
    """Normalize text token for comparison."""
    if not text:
        return ""
    ascii_val = to_ascii_latin(str(text))
    return re.sub(r"[^a-zA-Z0-9]+", "", ascii_val).lower()


def _norm_country(country: Optional[str]) -> Optional[str]:
    """Normalize country string to uppercase ISO2 code if recognized."""
    if not country:
        return None
    c_clean = country.strip()
    if len(c_clean) == 2 and c_clean.isalpha():
        return c_clean.upper()

    c_lower = c_clean.lower()
    from ..adapters.baserow import COUNTRIES_PATH
    import json
    try:
        if COUNTRIES_PATH.exists():
            with open(COUNTRIES_PATH, "r", encoding="utf-8") as f:
                c_map = json.load(f)
                if c_lower in c_map:
                    return c_map[c_lower].upper()
    except Exception:
        pass

    common = {
        "india": "IN", "germany": "DE", "deutschland": "DE", "usa": "US",
        "united states": "US", "united kingdom": "GB", "uk": "GB",
        "great britain": "GB", "france": "FR", "italy": "IT", "russia": "RU",
        "switzerland": "CH", "sweden": "SE", "australia": "AU", "brazil": "BR",
        "czech republic": "CZ", "czechia": "CZ", "netherlands": "NL",
    }
    return common.get(c_lower, c_clean.upper())


def parse_scripture_reference(val: Optional[str]) -> Optional[Dict[str, Any]]:
    """Parse canonical scripture reference (BG, SB, CC, etc.) into structured components.

    Enforces Tool 1 canonical grammar:
    - BG: chapter.verse or chapter.verse-end (e.g. BG 1.1 or BG 1.1-3, BG-01-18)
    - SB: canto.chapter.verse or canto.chapter.verse-end (e.g. SB 1.1.2 or SB 1.1.2-4, SB-01-01-02)
    - CC: lila.chapter.verse or lila.chapter.verse-end (e.g. CC Adi 1.1 or CC Adi 1.1-3)

    Rejects dotted extra numeric components (e.g. BG 13.8.12) and descending ranges (e.g. BG 1.12-8).
    Returns dict with book, canto, chapter, v_start, v_end, or None if not recognized.
    """
    if not val:
        return None
    clean = val.strip()

    # SB: SB-01-01-01, SB 1.1.1, SB-01-01-01-02
    sb_m = SB_REGEX.search(clean)
    if sb_m:
        canto, chapter, v1, v2 = sb_m.groups()
        iv1 = int(v1)
        iv2 = int(v2) if v2 else iv1
        if v2 and int(v2) < int(v1):
            return None  # Descending range is invalid
        return {"book": "SB", "canto": int(canto), "chapter": int(chapter), "v_start": iv1, "v_end": iv2}

    # BG: BG-01-01, BG 1.1, BG-1-1-2
    bg_m = BG_REGEX.search(clean)
    if bg_m:
        chapter, v1, v2 = bg_m.groups()
        iv1 = int(v1)
        iv2 = int(v2) if v2 else iv1
        if v2 and int(v2) < int(v1):
            return None  # Descending range is invalid
        return {"book": "BG", "canto": None, "chapter": int(chapter), "v_start": iv1, "v_end": iv2}

    # CC: CC-Adi-01-01, CC-01-01-01
    cc_m = CC_REGEX.search(clean)
    if cc_m:
        lila, chapter, v1, v2 = cc_m.groups()
        iv1 = int(v1)
        iv2 = int(v2) if v2 else iv1
        if v2 and int(v2) < int(v1):
            return None  # Descending range is invalid
        return {"book": "CC", "canto": lila.capitalize() if lila else None, "chapter": int(chapter), "v_start": iv1, "v_end": iv2}

    return None


def compose_what_val(local_what: Optional[str], title_full: Optional[str]) -> Optional[str]:
    """Combine local WHAT evidence and database title into an enriched WHAT token idempotently."""
    if not title_full or not str(title_full).strip():
        return local_what
    if not local_what or not str(local_what).strip():
        return str(title_full).strip()

    clean_title = str(title_full).strip()
    clean_local = str(local_what).strip()

    norm_title = _norm_token(clean_title)
    norm_local = _norm_token(clean_local)

    if not norm_title:
        return clean_local
    if not norm_local:
        return clean_title

    # 1. Exact or near match: already identical
    if norm_local == norm_title:
        return clean_local

    # 2. If the full title is already contained in local_what (e.g. from previous enrichment pass)
    if norm_title in norm_local:
        return clean_local

    # 3. Check if local_what already contains the title or a compacted version of it alongside scripture
    for regex in (SB_REGEX, BG_REGEX, CC_REGEX):
        m = regex.search(clean_local)
        if m:
            prefix = clean_local[:m.end()].rstrip(" -_")
            remainder = clean_local[m.end():].lstrip(" -_")
            norm_rem = _norm_token(remainder)
            if norm_rem and len(norm_rem) >= 4:
                if norm_title.startswith(norm_rem) or norm_rem.startswith(norm_title) or norm_rem in norm_title:
                    return clean_local
            # If local_what is just the scripture prefix, check if title also has the scripture prefix
            m_title = regex.search(clean_title)
            if m_title:
                rem_title = clean_title[m_title.end():].lstrip(" -_.:")
                if rem_title:
                    return f"{prefix}-{rem_title}"
            break

    # 4. If local_what is generic or not specific (e.g. "Lecture", "Class"), title_full supersedes it
    if not is_specific_what(clean_local):
        return clean_title

    # 5. If local_what is already contained in title_full
    if norm_local in norm_title:
        return clean_title

    # 6. Standard combination: preserve local_what and append title_full
    return f"{clean_local}-{clean_title}"



def _compare_dates(local_date: Optional[str], db_date: Optional[str]) -> Tuple[FieldComparisonState, Optional[str]]:
    """Compare local recording date with Baserow date supporting partial precision."""
    if not local_date and not db_date:
        return FieldComparisonState.NOT_COMPARABLE, "Both dates missing"
    if local_date and not db_date:
        return FieldComparisonState.DATABASE_MISSING, "Database date is blank"
    if not local_date and db_date:
        return FieldComparisonState.LOCAL_MISSING, "Local date is blank"

    # Normalize dates
    l_clean = (local_date or "").replace("/", "-").strip()
    d_clean = (db_date or "").replace("/", "-").strip()

    if l_clean == d_clean:
        return FieldComparisonState.AGREES, None

    l_parts = l_clean.split("-")
    d_parts = d_clean.split("-")

    l_year = l_parts[0] if len(l_parts) > 0 else ""
    d_year = d_parts[0] if len(d_parts) > 0 else ""

    if l_year != d_year:
        return FieldComparisonState.CONFLICT, f"Local year '{l_year}' conflicts with database year '{d_year}'"

    l_month = l_parts[1] if len(l_parts) > 1 else ""
    d_month = d_parts[1] if len(d_parts) > 1 else ""

    if not l_month or l_month.upper() in ("MM", "??", "00"):
        return FieldComparisonState.AGREES, "Partial date match (year compatible)"

    if l_month != d_month:
        return FieldComparisonState.CONFLICT, f"Local month '{l_month}' conflicts with database month '{d_month}'"

    l_day = l_parts[2] if len(l_parts) > 2 else ""
    d_day = d_parts[2] if len(d_parts) > 2 else ""

    if not l_day or l_day.upper() in ("DD", "??", "00"):
        return FieldComparisonState.AGREES, "Partial date match (year and month compatible)"

    if l_day != d_day:
        return FieldComparisonState.CONFLICT, f"Local day '{l_day}' conflicts with database day '{d_day}'"

    return FieldComparisonState.AGREES, None


def _compare_places(
    local_place: Optional[str],
    local_country: Optional[str],
    db_place: Optional[str],
    db_country: Optional[str],
) -> Tuple[FieldComparisonState, Optional[str]]:
    """Compare local place/country with Baserow place/country with country awareness."""
    if not local_place and not db_place:
        return FieldComparisonState.NOT_COMPARABLE, "Both locations missing"
    if local_place and not db_place:
        return FieldComparisonState.DATABASE_MISSING, "Database location is blank"
    if not local_place and db_place:
        return FieldComparisonState.LOCAL_MISSING, "Local location is blank"

    # Normalize and compare countries when both provided
    nc_local = _norm_country(local_country)
    nc_db = _norm_country(db_country)

    if nc_local and nc_db and nc_local != nc_db:
        return (
            FieldComparisonState.CONFLICT,
            f"Country conflict: local '{local_country}' ({nc_local}) contradicts database '{db_country}' ({nc_db})",
        )

    norm_lp = _norm_token(local_place)
    norm_dp = _norm_token(db_place)

    if norm_lp == norm_dp:
        return FieldComparisonState.AGREES, None

    if norm_lp in norm_dp or norm_dp in norm_lp:
        if len(norm_lp) >= 4 and len(norm_dp) >= 4:
            return FieldComparisonState.AGREES, None

    ratio = fuzz.ratio(norm_lp, norm_dp)
    if ratio >= 85:
        return FieldComparisonState.AGREES, f"Fuzzy location match ({ratio:.1f}%)"

    return FieldComparisonState.CONFLICT, f"Local place '{local_place}' conflicts with database place '{db_place}'"


def _compare_what(
    local_what: Optional[str],
    local_category: Optional[str],
    db_what: Optional[str],
    db_title: Optional[str],
    db_category: Optional[str],
) -> Tuple[FieldComparisonState, Optional[str]]:
    """Compare local WHAT with Baserow what/title using structural scripture matching."""
    if not local_what and not db_what and not db_title:
        return FieldComparisonState.NOT_COMPARABLE, "Both WHAT and title missing"
    if local_what and not db_what and not db_title:
        return FieldComparisonState.DATABASE_MISSING, "Database what/title is blank"
    if not local_what and (db_what or db_title):
        return FieldComparisonState.LOCAL_MISSING, "Local what is blank"

    local_scrip = parse_scripture_reference(local_what)
    db_scrip = parse_scripture_reference(db_what) or parse_scripture_reference(db_title)

    if local_scrip and db_scrip:
        if local_scrip["book"] != db_scrip["book"]:
            return FieldComparisonState.CONFLICT, f"Scripture book mismatch: '{local_what}' vs '{db_what or db_title}'"
        if local_scrip["canto"] != db_scrip["canto"]:
            return FieldComparisonState.CONFLICT, f"Scripture canto mismatch: '{local_what}' vs '{db_what or db_title}'"
        if local_scrip["chapter"] != db_scrip["chapter"]:
            return FieldComparisonState.CONFLICT, f"Scripture chapter mismatch: '{local_what}' vs '{db_what or db_title}'"

        lv1, lv2 = local_scrip["v_start"], local_scrip["v_end"]
        dv1, dv2 = db_scrip["v_start"], db_scrip["v_end"]
        if lv1 == dv1 and lv2 == dv2:
            return FieldComparisonState.AGREES, None
        if max(lv1, dv1) <= min(lv2, dv2):
            return (
                FieldComparisonState.CONFLICT,
                f"Scripture range mismatch: partial verse overlap ({lv1}-{lv2} vs {dv1}-{dv2}) does not establish exact identity",
            )
        return FieldComparisonState.CONFLICT, f"Scripture verse conflict: '{local_what}' vs '{db_what or db_title}'"

    if local_scrip and not db_scrip:
        if db_what:
            return FieldComparisonState.CONFLICT, f"Local scripture '{local_what}' conflicts with database WHAT '{db_what}'"
        norm_lw = _norm_token(local_what)
        norm_dt = _norm_token(db_title)
        if norm_lw and norm_lw in norm_dt:
            return FieldComparisonState.AGREES, "Local scripture mentioned in database title"
        return FieldComparisonState.DATABASE_MISSING, "Database scripture reference is blank"

    if not local_scrip and db_scrip:
        if local_what:
            return FieldComparisonState.CONFLICT, f"Local non-scripture WHAT '{local_what}' conflicts with database scripture '{db_what or db_title}'"

    norm_lw = _norm_token(local_what)
    norm_dw = _norm_token(db_what)
    norm_dt = _norm_token(db_title)

    if norm_dw and norm_lw == norm_dw:
        return FieldComparisonState.AGREES, None
    if norm_dt and (norm_lw in norm_dt or norm_dt in norm_lw):
        return FieldComparisonState.AGREES, "Local what matches database title"
    if db_category and _norm_token(db_category) == norm_lw:
        return FieldComparisonState.AGREES, "Local what matches database category"

    if norm_dw and fuzz.ratio(norm_lw, norm_dw) >= 85:
        return FieldComparisonState.AGREES, "Fuzzy WHAT match"
    if norm_dt and fuzz.ratio(norm_lw, norm_dt) >= 85:
        return FieldComparisonState.AGREES, "Fuzzy title match"

    if norm_dw and norm_lw != norm_dw:
        return FieldComparisonState.CONFLICT, f"Local WHAT '{local_what}' conflicts with database WHAT '{db_what}'"

    if not norm_dw:
        return FieldComparisonState.DATABASE_MISSING, "Database WHAT is blank"

    return FieldComparisonState.CONFLICT, f"Local WHAT '{local_what}' does not match database WHAT '{db_what or db_title}'"


class MediaDatabaseReconciliationEngine:
    """Core candidate search and reconciliation engine (Tool 2)."""

    def reconcile(
        self,
        parser_res: ParserResult,
        snapshot: BaserowSnapshot,
    ) -> MediaDatabaseReviewResult:
        """Evaluate incoming ParserResult against Baserow tables and produce MediaDatabaseReviewResult."""
        tracking_id = parser_res.identity.tracking_id
        orig_filename = parser_res.identity.original_filename
        ext = parser_res.identity.extension.lower()

        local_date = parser_res.when.selected_value if parser_res.when.state != ResolutionState.UNRESOLVED else None
        local_what = parser_res.what.selected_value if parser_res.what.state != ResolutionState.UNRESOLVED else None
        local_what_category = parser_res.what.category
        local_place = parser_res.where.place_location if parser_res.where.state != ResolutionState.UNRESOLVED else None
        local_country = parser_res.where.country_iso2 if parser_res.where.state != ResolutionState.UNRESOLVED else None

        # Extract source identifiers from filename / technical flags
        source_ids: List[str] = []
        if parser_res.file_metadata.source_sequence_id:
            source_ids.append(parser_res.file_metadata.source_sequence_id)
        if parser_res.identity.tracking_id:
            source_ids.append(parser_res.identity.tracking_id)

        # Pre-normalize snapshot rows
        norm_media_rows = [normalize_media_row(r) for r in snapshot.media_rows]
        norm_cat_rows = [normalize_category_title_row(r) for r in snapshot.category_title_rows]
        norm_travel_rows = [normalize_travel_schedule_row(r) for r in snapshot.travel_schedule_rows]

        # Check database availability
        if snapshot.state == "UNAVAILABLE":
            return MediaDatabaseReviewResult(
                tracking_id=tracking_id,
                database_state="UNAVAILABLE",
                database_snapshot_at=snapshot.snapshot_at,
                snapshot_complete=False,
                baserow_check_complete=False,
                decision=ReviewDecision.DATABASE_UNAVAILABLE,
                decision_state="Database snapshot unavailable",
                review_required=False,
                diagnostic_notes=["Baserow database unavailable; cannot perform media check"],
                downstream_routing=["tool_2_media_database_review"],
            )

        # 1. Candidate Retrieval & Comparison
        candidates: List[MediaCandidate] = []

        for row in norm_media_rows:
            reasons = []
            identity_evidence = []
            score = 0.0

            # A. Direct identity checks
            # Source ID match
            for sid in source_ids:
                if sid in row["source_ids"]:
                    reasons.append(f"Source ID match '{sid}'")
                    identity_evidence.append(f"source_id:{sid}")
                    score += 100.0

            # Stored filename match
            if row["filename"]:
                clean_rf = _norm_token(row["filename"])
                clean_orig = _norm_token(orig_filename)
                if clean_rf == clean_orig:
                    reasons.append(f"Exact stored filename match '{row['filename']}'")
                    identity_evidence.append(f"filename:{row['filename']}")
                    score += 90.0
                elif clean_rf in clean_orig or clean_orig in clean_rf:
                    reasons.append(f"Partial filename match '{row['filename']}'")
                    score += 40.0

            # Attachment file match
            for att in row["attachments"]:
                clean_att = _norm_token(att)
                clean_orig = _norm_token(orig_filename)
                if clean_att == clean_orig:
                    reasons.append(f"Attachment name match '{att}'")
                    identity_evidence.append(f"attachment:{att}")
                    score += 90.0

            # B. Semantic date match
            date_match = False
            if local_date and row["date"]:
                date_state, date_detail = _compare_dates(local_date, row["date"])
                if date_state == FieldComparisonState.AGREES:
                    reasons.append(f"Date match '{local_date}'")
                    score += 30.0
                    date_match = True

            # C. Specific WHAT match
            what_match = False
            if local_what and (row["what"] or row["title"]):
                what_state, what_detail = _compare_what(
                    local_what, local_what_category, row["what"], row["title"], row["category"]
                )
                if what_state == FieldComparisonState.AGREES:
                    reasons.append(f"WHAT match '{local_what}'")
                    score += 35.0
                    what_match = True
                elif what_detail and "partial verse overlap" in what_detail:
                    reasons.append(what_detail)
                    score += 15.0

            # D. Location match
            loc_match = False
            if local_place and row["place"]:
                place_state, place_detail = _compare_places(
                    local_place, local_country, row["place"], row["country"]
                )
                if place_state == FieldComparisonState.AGREES:
                    reasons.append(f"Place match '{row['place']}'")
                    score += 20.0
                    loc_match = True

            # E. High-specificity combo bonus
            if date_match and what_match and loc_match:
                score += 30.0
                reasons.append("Exact full date + WHAT + WHERE high-specificity signature")
            elif date_match and what_match and (row["title"] or row["category"]):
                score += 20.0
                reasons.append("Exact date + WHAT + corroborating title/category signature")

            # F. Supporting travel schedule context (R-006: exact date or bounded range only)
            travel_context = []
            eval_date = local_date or row["date"]
            if eval_date and len(eval_date) == 10 and not eval_date.endswith("DD"):
                for tr in norm_travel_rows:
                    start = tr.get("start_date")
                    end = tr.get("end_date")
                    in_range = False
                    if start and end and len(start) == 10 and len(end) == 10:
                        in_range = (start <= eval_date <= end)
                    elif start and start == eval_date:
                        in_range = True

                    if in_range and tr.get("place"):
                        date_desc = f"{start}..{end}" if end and end != start else f"{start}"
                        travel_context.append(f"Travel schedule records '{tr['place']}' on {date_desc}")
                        if local_place and _norm_token(local_place) == _norm_token(tr["place"]):
                            reasons.append(f"Travel schedule corroborates place '{tr['place']}'")
                            score += 10.0

            # G. Category title matching support
            category_context = []
            for cr in norm_cat_rows:
                for term in cr["title_matching_terms"]:
                    if term and _norm_token(term) in _norm_token(orig_filename):
                        category_context.append(f"Matched category title term '{term}' -> {cr['category']}")
                        if not local_what_category:
                            reasons.append(f"Category term corroboration '{term}'")
                            score += 5.0

            # If this row has plausible reasons to be considered
            if score >= 20.0 or reasons:
                # Perform field-by-field comparisons
                f_comps: Dict[str, FieldComparison] = {}
                conflicts: List[str] = []

                # Date comparison
                d_st, d_det = _compare_dates(local_date, row["date"])
                f_comps["date"] = FieldComparison(
                    field_name="date", state=d_st, local_value=local_date, database_value=row["date"], details=d_det
                )
                if d_st == FieldComparisonState.CONFLICT:
                    conflicts.append(d_det or "Date conflict")

                # WHAT comparison
                w_st, w_det = _compare_what(
                    local_what, local_what_category, row["what"], row["title"], row["category"]
                )
                f_comps["what"] = FieldComparison(
                    field_name="what", state=w_st, local_value=local_what, database_value=row["what"] or row["title"], details=w_det
                )
                if w_st == FieldComparisonState.CONFLICT:
                    conflicts.append(w_det or "WHAT conflict")

                # Place comparison
                p_st, p_det = _compare_places(local_place, local_country, row["place"], row["country"])
                f_comps["place"] = FieldComparison(
                    field_name="place", state=p_st, local_value=local_place, database_value=row["place"], details=p_det
                )
                if p_st == FieldComparisonState.CONFLICT:
                    conflicts.append(p_det or "Place conflict")

                # Build candidate possible enrichments
                possible_enr = {}
                if row["title"]:
                    possible_enr["title_full"] = row["title"]
                if row["place"] and not local_place:
                    possible_enr["where_val"] = f"{row['place']}-{row['country'] or ''}".strip("-")
                if row["date"] and not local_date:
                    possible_enr["when_val"] = row["date"]
                if row["category"] and not local_what_category:
                    possible_enr["category"] = row["category"]

                candidate = MediaCandidate(
                    media_row_id=row["id"],
                    raw_row=row,
                    normalized_row=row,
                    retrieval_reasons=reasons,
                    identity_evidence=identity_evidence,
                    field_comparisons=f_comps,
                    conflicts=conflicts,
                    possible_enrichments=possible_enr,
                    category_title_context=category_context,
                    travel_schedule_context=travel_context,
                    score=score,
                )
                candidates.append(candidate)

        # Sort candidates descending by score for secondary ranking/display
        candidates.sort(key=lambda c: c.score, reverse=True)

        # 2. Decision Logic via Explicit Predicates (R-002)
        def _check_predicates(cand: MediaCandidate) -> Tuple[bool, str]:
            """Evaluate explicit rules for confirmed association. Score alone never authorizes match."""
            if cand.conflicts:
                return False, ""

            # Rule 1: Direct Identity Match (highest confidence)
            if cand.identity_evidence:
                return True, "rule1_direct_identity"

            # Check semantic fields
            d_comp = cand.field_comparisons.get("date")
            w_comp = cand.field_comparisons.get("what")
            p_comp = cand.field_comparisons.get("place")

            # Exact full date (R-002): requires both dates present, 10 chars, no DD wildcard, equal, and AGREES
            db_date = cand.normalized_row.get("date")
            is_exact_full_date = (
                bool(local_date)
                and bool(db_date)
                and len(local_date) == 10
                and len(db_date) == 10
                and not local_date.endswith("DD")
                and not db_date.endswith("DD")
                and local_date == db_date
                and d_comp is not None
                and d_comp.state == FieldComparisonState.AGREES
                and (not d_comp.details or "Partial" not in d_comp.details)
            )

            # Specific WHAT and exact WHAT match (R-002)
            is_exact_what = False
            if is_specific_what(local_what) and w_comp is not None and w_comp.state == FieldComparisonState.AGREES:
                # Must not be a fuzzy match or category match
                if not (w_comp.details and ("Fuzzy" in w_comp.details or "category" in w_comp.details)):
                    local_scrip = parse_scripture_reference(local_what)
                    if local_scrip:
                        db_what = cand.normalized_row.get("what")
                        db_title = cand.normalized_row.get("title")
                        db_scrip = parse_scripture_reference(db_what) or parse_scripture_reference(db_title)
                        if db_scrip:
                            is_exact_what = (
                                local_scrip["book"] == db_scrip["book"]
                                and local_scrip["canto"] == db_scrip["canto"]
                                and local_scrip["chapter"] == db_scrip["chapter"]
                                and local_scrip["v_start"] == db_scrip["v_start"]
                                and local_scrip["v_end"] == db_scrip["v_end"]
                            )
                        elif db_title:
                            is_exact_what = _norm_token(local_what) in _norm_token(db_title)
                    else:
                        norm_lw = _norm_token(local_what)
                        norm_dw = _norm_token(cand.normalized_row.get("what"))
                        norm_dt = _norm_token(cand.normalized_row.get("title"))
                        is_exact_what = (norm_lw == norm_dw) or (norm_lw == norm_dt)

            # Exact normalized place and country (R-002)
            is_exact_place = False
            db_place = cand.normalized_row.get("place")
            if local_place and db_place and p_comp is not None and p_comp.state == FieldComparisonState.AGREES:
                # Must not be a fuzzy match
                if not (p_comp.details and "Fuzzy" in p_comp.details):
                    norm_lp = _norm_token(local_place)
                    norm_dp = _norm_token(db_place)
                    if norm_lp == norm_dp:
                        nc_l = _norm_country(local_country)
                        nc_d = _norm_country(cand.normalized_row.get("country"))
                        if not (nc_l and nc_d and nc_l != nc_d):
                            is_exact_place = True

            # Rule 2: Unique High-Specificity Semantic Match (Exact Full Date + Specific WHAT + Exact Normalized WHERE)
            if is_exact_full_date and is_exact_what and is_exact_place:
                return True, "rule2_date_what_where"

            # Rule 3: High-Specificity Semantic Match with Genuine Independent Corroboration (R-002)
            corroboration = False
            if is_exact_full_date and is_exact_what:
                db_title = cand.normalized_row.get("title") or ""
                db_cat = cand.normalized_row.get("category") or ""
                norm_title = _norm_token(db_title)
                norm_what = _norm_token(local_what)
                norm_base_fn = _norm_token(Path(orig_filename).stem) if orig_filename else ""

                # 1. Title corroboration: title genuinely matches local WHAT, scripture, or filename topic (not mere presence)
                title_matches = False
                if norm_title:
                    if norm_what and (norm_what in norm_title or norm_title in norm_what):
                        title_matches = True
                    elif norm_base_fn and len(norm_base_fn) >= 5 and norm_base_fn in norm_title:
                        title_matches = True
                    elif w_comp.details == "Local what matches database title":
                        title_matches = True
                    elif local_scrip:
                        b = local_scrip.get("book")
                        book_terms = []
                        if b == "SB":
                            book_terms = ["sb", "srimadbhagavatam", "bhagavatam"]
                        elif b == "BG":
                            book_terms = ["bg", "bhagavadgita", "gita"]
                        elif b == "CC":
                            book_terms = ["cc", "caitanyacaritamrta", "chaitanyacharitamrita", "charitamrita"]
                        for bt in book_terms:
                            if bt in norm_title:
                                title_matches = True
                                break

                # 2. Category corroboration: DB category genuinely matches local category or scripture
                category_matches = False
                if db_cat:
                    norm_cat = _norm_token(db_cat)
                    if local_what_category and norm_cat == _norm_token(local_what_category):
                        category_matches = True
                    elif local_scrip:
                        b = local_scrip.get("book")
                        if b == "SB" and "bhagavatam" in norm_cat:
                            category_matches = True
                        elif b == "BG" and "gita" in norm_cat:
                            category_matches = True
                        elif b == "CC" and ("caitanya" in norm_cat or "charitamrita" in norm_cat):
                            category_matches = True

                # 3. Category title context match
                cat_context_matches = bool(cand.category_title_context)

                # 4. Travel schedule corroboration: travel schedule explicitly corroborates place
                travel_corroborates = any("corroborates place" in r for r in cand.retrieval_reasons)

                if title_matches or category_matches or cat_context_matches or travel_corroborates:
                    corroboration = True

            if is_exact_full_date and is_exact_what and corroboration:
                return True, "rule3_date_what_corroboration"

            return False, ""

        confirmed_candidates = []
        for c in candidates:
            is_match, rule_name = _check_predicates(c)
            if is_match:
                confirmed_candidates.append((c, rule_name))

        has_material_conflict = False
        leading_candidate_with_conflict: Optional[MediaCandidate] = None
        for c in candidates:
            if c.conflicts:
                has_material_conflict = True
                leading_candidate_with_conflict = c
                break

        # Check for competing equally plausible candidates
        multiple_plausible = False
        if len(candidates) >= 2:
            top_score = candidates[0].score
            second_score = candidates[1].score
            if abs(top_score - second_score) < 15.0 and top_score >= 35.0:
                multiple_plausible = True

        best_candidate: Optional[MediaCandidate] = candidates[0] if candidates else None

        decision = ReviewDecision.INSUFFICIENT_EVIDENCE
        decision_state = ""
        selected_row_id: Optional[int] = None
        renamer_enr = RenamerEnrichment()
        tool4_action = Tool4Action.NO_WRITE
        routing = []
        review_reasons = []
        conflicts_out = []
        diag_notes = []
        review_required_now = False

        is_live = snapshot.state in ("LIVE_CURRENT", "LIVE_COMPLETE")

        if len(confirmed_candidates) == 1 and not has_material_conflict:
            # Exactly one candidate satisfies the explicit confirmed predicates with no conflicts!
            matched_cand, rule_name = confirmed_candidates[0]
            decision = ReviewDecision.EXISTING_MEDIA_MATCH
            selected_row_id = matched_cand.media_row_id
            decision_state = f"Confirmed association with Baserow Media row {selected_row_id} via {rule_name}"
            diag_notes.append(f"Confirmed match with Baserow Media row {selected_row_id} ({rule_name})")

            # Determine proposed Tool 4 action: link existing (if format differs) or enrich existing
            row_format = (matched_cand.normalized_row.get("format") or "").lower()
            if ext in (".mp4", ".mkv", ".avi", ".mov") and "video" not in row_format:
                tool4_action = Tool4Action.LINK_EXISTING
            else:
                tool4_action = Tool4Action.ENRICH_EXISTING

            # Prepare confirmed Renamer enrichment
            db_row = matched_cand.normalized_row
            title_full = db_row.get("title") or ""
            what_val = compose_what_val(local_what, title_full)

            where_val = None
            if db_row.get("place"):
                p = db_row.get("place")
                c = db_row.get("country") or ""
                where_val = f"{p}-{c}".strip("-")
            elif local_place:
                where_val = f"{local_place}-{local_country or ''}".strip("-")

            renamer_enr = RenamerEnrichment(
                confirmed=True,
                media_row_id=selected_row_id,
                when_val=db_row.get("date") or local_date,
                what_val=what_val,
                title_full=title_full,
                where_val=where_val,
                category=db_row.get("category") or local_what_category,
                source_identifiers=db_row.get("source_ids") or [],
                evidence=[f"baserow_media_row:{selected_row_id}", f"predicate:{rule_name}"] + matched_cand.retrieval_reasons,
                baserow_read_at=snapshot.snapshot_at,
                live_read_complete=is_live,
            )

        elif len(confirmed_candidates) > 1 or (not confirmed_candidates and multiple_plausible):
            # Multiple candidates satisfy confirmed criteria or are equally plausible (R-002)
            c1_id = confirmed_candidates[0][0].media_row_id if confirmed_candidates else candidates[0].media_row_id
            c2_id = confirmed_candidates[1][0].media_row_id if len(confirmed_candidates) > 1 else candidates[1].media_row_id
            decision = ReviewDecision.MULTIPLE_CANDIDATES
            decision_state = f"Multiple plausible Media rows found ({c1_id}, {c2_id})"
            diag_notes.append(f"Multiple plausible Baserow rows found ({len(candidates)} candidates)")
            tool4_action = Tool4Action.NEEDS_REVIEW
            routing.append("tool_3_travel_schedule_review")
            review_required_now = False

        elif has_material_conflict:
            # Conflict with existing candidate (R-007)
            cand_c = leading_candidate_with_conflict or candidates[0]
            decision = ReviewDecision.CONFLICT_WITH_EXISTING
            decision_state = f"Candidate Media row {cand_c.media_row_id} contradicts incoming evidence"
            conflicts_out.extend(cand_c.conflicts)
            diag_notes.append(f"Conflict with Baserow Media row {cand_c.media_row_id}: {'; '.join(cand_c.conflicts)}")
            tool4_action = Tool4Action.NEEDS_REVIEW

            # Check if this is an immediate human-review contradiction or resolvable by Tool 3
            is_direct_identity_contradiction = bool(cand_c.identity_evidence)
            conflict_fields = [
                fld for fld, comp in cand_c.field_comparisons.items() if comp.state == FieldComparisonState.CONFLICT
            ]
            only_place_conflict = (conflict_fields == ["place"])

            if is_direct_identity_contradiction or not only_place_conflict:
                # Direct-identity contradiction or multi-field irreducible conflict: requires human review now
                review_required_now = True
                review_reasons.extend(cand_c.conflicts)
            else:
                # Location-only conflict on semantic candidate: route to Tool 3 without immediate human review
                review_required_now = False
                routing.append("tool_3_travel_schedule_review")
                diag_notes.append("Location conflict routed to Tool 3 travel schedule review")

        elif best_candidate and (
            best_candidate.field_comparisons.get("date", FieldComparison(field_name="d", state=FieldComparisonState.NOT_COMPARABLE)).state == FieldComparisonState.AGREES
            or best_candidate.field_comparisons.get("what", FieldComparison(field_name="w", state=FieldComparisonState.NOT_COMPARABLE)).state == FieldComparisonState.AGREES
        ):
            # Partial semantic match, unconfirmed
            decision = ReviewDecision.PROBABLE_EXISTING_MEDIA
            decision_state = f"Probable association with Baserow Media row {best_candidate.media_row_id}; unconfirmed"
            diag_notes.append(f"Probable association with Baserow Media row {best_candidate.media_row_id}; unconfirmed")
            tool4_action = Tool4Action.NO_WRITE
            routing.append("tool_3_travel_schedule_review")
            review_required_now = False

        else:
            # No candidate found
            is_partial_date = bool(local_date and (local_date.endswith("DD") or len(local_date) < 10))
            if is_partial_date:
                # Partial date without a specific WHAT is NOT discriminating enough for NEW_MEDIA_CANDIDATE (R-012)
                is_discriminating = bool(is_specific_what(local_what))
            else:
                # Full date: discriminating if specific WHAT or place
                is_discriminating = bool(local_date and (is_specific_what(local_what) or local_place))

            if is_live and is_discriminating:
                decision = ReviewDecision.NEW_MEDIA_CANDIDATE
                decision_state = "No matching Media row found; input is discriminating"
                diag_notes.append("Complete live database check: no matching Media row found (new media candidate)")
                tool4_action = Tool4Action.CREATE_NEW
            elif not is_live:
                decision = ReviewDecision.DATABASE_UNAVAILABLE
                decision_state = "Database is unavailable; cannot confirm media database association"
                diag_notes.append("Baserow database unavailable; live read required")
                tool4_action = Tool4Action.NO_WRITE
                routing.append("tool_2_media_database_review")
            else:
                decision = ReviewDecision.INSUFFICIENT_EVIDENCE
                decision_state = "Input metadata is too sparse to evaluate media match"
                diag_notes.append("Insufficient evidence to determine media database association")
                tool4_action = Tool4Action.NO_WRITE

        # Baserow check complete is true ONLY when a live check confirmed or ruled out
        baserow_check_complete = (
            is_live
            and decision in (ReviewDecision.EXISTING_MEDIA_MATCH, ReviewDecision.NEW_MEDIA_CANDIDATE)
        )

        # Determine review_required (non-automated/review needed overall) vs review_required_now (immediate human blocker)
        review_required = (
            decision in (
                ReviewDecision.MULTIPLE_CANDIDATES,
                ReviewDecision.CONFLICT_WITH_EXISTING,
                ReviewDecision.PROBABLE_EXISTING_MEDIA,
                ReviewDecision.DATABASE_UNAVAILABLE,
            )
            or review_required_now
        )

        return MediaDatabaseReviewResult(
            tracking_id=tracking_id,
            database_state=snapshot.state,
            database_snapshot_at=snapshot.snapshot_at,
            baserow_read_at=snapshot.snapshot_at,
            snapshot_complete=snapshot.complete,
            live_read_complete=is_live,
            baserow_check_complete=baserow_check_complete,
            decision=decision,
            decision_state=decision_state,
            selected_media_row_id=selected_row_id,
            candidates=candidates,
            selected_field_evidence={
                "local_date": local_date,
                "local_what": local_what,
                "local_place": local_place,
                "local_country": local_country,
            },
            renamer_enrichment=renamer_enr,
            proposed_tool4_action=tool4_action,
            downstream_routing=routing,
            review_required=review_required,
            review_required_now=review_required_now,
            review_reasons=review_reasons,
            conflicts=conflicts_out,
            diagnostic_notes=diag_notes,
            evidence=[f"snapshot_state:{snapshot.state}"] + (best_candidate.retrieval_reasons if best_candidate else []),
        )
