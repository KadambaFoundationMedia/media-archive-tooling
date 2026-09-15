"""Travel Schedule Reviewer matching engine and in-memory index."""
from datetime import date, datetime, timedelta
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from rapidfuzz import fuzz

from ..adapters.baserow import DEFAULT_LOCATIONS_PATH as LOCATIONS_PATH
from ..common.ascii_latin import to_ascii_latin
from ..media_db_reviewer.engine import _norm_country
from ..media_db_reviewer.models import MediaDatabaseReviewResult, ReviewDecision
from ..renamer.models import ParserResult, ResolutionState
from .models import (
    FieldComparisonState,
    NormalizedTravelRow,
    TravelCandidate,
    TravelRenamerEnrichment,
    TravelReviewDecision,
    TravelReviewResult,
    TravelScheduleManifest,
)

logger = logging.getLogger(__name__)


def _norm_place_token(place: Optional[str]) -> str:
    """Normalize place string for robust equality matching."""
    if not place:
        return ""
    ascii_val = to_ascii_latin(place).lower()
    return re.sub(r"[^a-z0-9]+", "", ascii_val)


def parse_iso_date(d_str: Optional[str]) -> Optional[date]:
    """Parse ISO YYYY-MM-DD date string safely."""
    if not d_str:
        return None
    clean = d_str.strip().replace("/", "-")
    # Must match YYYY-MM-DD
    match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", clean)
    if not match:
        return None
    try:
        y, m, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
        return date(y, m, d)
    except ValueError:
        return None


def parse_partial_when(when_val: Optional[str]) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """Parse Tool 1 when_val into (year, month, day). Returns None for wildcards/missing."""
    if not when_val or when_val == "YYYY-MM-DD":
        return None, None, None
    clean = when_val.strip().replace("/", "-")
    parts = clean.split("-")
    if len(parts) != 3:
        return None, None, None

    y = int(parts[0]) if parts[0].isdigit() else None
    m = int(parts[1]) if parts[1].isdigit() else None
    d = int(parts[2]) if parts[2].isdigit() else None
    return y, m, d


def parse_structured_where(val: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Parse structured WHERE string into (place, country_iso2).

    Robustly preserves hyphenated place names (e.g. 'Villa-Vrindavan', 'New-York',
    'Serbia-summer-camp', 'Krsna-Dvur', 'Farma-KD') while extracting trailing ISO
    country code suffixes (e.g. '-IT', '-US', '-RS', '-CZ', '-DE') only if recognized (R-013).
    """
    if not val:
        return None, None
    clean = val.strip()
    if not clean:
        return None, None
    if "-" in clean:
        parts = clean.rsplit("-", 1)
        place_part, cand_country = parts[0].strip(), parts[1].strip()
        from ..renamer.validator import _get_valid_iso2_codes
        valid_codes = _get_valid_iso2_codes()
        cand_lower = cand_country.lower()
        if len(cand_lower) == 2 and cand_lower in valid_codes:
            return place_part, cand_lower
        from ..adapters.baserow import COUNTRIES_PATH
        import json
        try:
            if COUNTRIES_PATH.exists():
                with open(COUNTRIES_PATH, "r", encoding="utf-8") as f:
                    c_map = json.load(f)
                    if cand_lower in c_map:
                        return place_part, c_map[cand_lower].lower()
        except Exception:
            pass
    return clean, None


class TravelScheduleIndex:
    """In-memory multi-key index for fast, bounded travel schedule queries."""

    def __init__(self, manifest: TravelScheduleManifest):
        self.manifest = manifest
        self.rows_by_id: Dict[int, NormalizedTravelRow] = {}
        self.date_to_row_ids: Dict[str, List[int]] = {}
        self.month_to_row_ids: Dict[str, List[int]] = {}
        self.year_to_row_ids: Dict[str, List[int]] = {}
        self.place_to_row_ids: Dict[str, List[int]] = {}
        self.alias_to_canonical: Dict[str, str] = {}
        self.invalid_rows: List[NormalizedTravelRow] = []
        self.long_ranges: List[Tuple[date, date, int]] = []

        self._load_location_aliases()
        self._build_index()

    def _load_location_aliases(self):
        """Load canonical location aliases from Tool 1 assets if available."""
        try:
            if LOCATIONS_PATH.exists():
                import json
                with open(LOCATIONS_PATH, "r", encoding="utf-8") as f:
                    locs = json.load(f)
                    for loc in locs:
                        canonical = _norm_place_token(loc.get("canonical_place", ""))
                        if not canonical:
                            continue
                        self.alias_to_canonical[canonical] = canonical
                        for alias in loc.get("aliases", []):
                            norm_a = _norm_place_token(alias)
                            if norm_a:
                                self.alias_to_canonical[norm_a] = canonical
        except Exception as e:
            logger.debug(f"Could not load location aliases: {e}")

    def canonical_place(self, place: str) -> str:
        """Resolve normalized place to its canonical alias if known."""
        token = _norm_place_token(place)
        return self.alias_to_canonical.get(token, token)

    def _build_index(self):
        for row in self.manifest.normalized_rows:
            self.rows_by_id[row.id] = row

            d_start = parse_iso_date(row.start_date)
            if not d_start:
                # Missing or unparseable start date
                self.invalid_rows.append(row)
                continue

            if row.end_date and row.end_date.strip():
                d_end = parse_iso_date(row.end_date)
                if d_end is None:
                    # Present but malformed end date is invalid schedule data
                    self.invalid_rows.append(row)
                    continue
                if d_end < d_start:
                    # End before start is invalid schedule data
                    self.invalid_rows.append(row)
                    continue
            else:
                d_end = d_start

            # Index dates
            effective_end = d_end
            span_days = (effective_end - d_start).days
            if 0 <= span_days <= 366:
                curr = d_start
                while curr <= effective_end:
                    ds = curr.isoformat()
                    self.date_to_row_ids.setdefault(ds, []).append(row.id)
                    ms = f"{curr.year:04d}-{curr.month:02d}"
                    if row.id not in self.month_to_row_ids.setdefault(ms, []):
                        self.month_to_row_ids[ms].append(row.id)
                    ys = f"{curr.year:04d}"
                    if row.id not in self.year_to_row_ids.setdefault(ys, []):
                        self.year_to_row_ids[ys].append(row.id)
                    curr += timedelta(days=1)
            else:
                # Valid long explicit range (> 366 days)
                self.long_ranges.append((d_start, effective_end, row.id))

            # Index place
            if row.place:
                norm_p = self.canonical_place(row.place)
                self.place_to_row_ids.setdefault(norm_p, []).append(row.id)

    def get_rows_by_date(self, d_str: str) -> List[NormalizedTravelRow]:
        """Return all rows whose interval contains d_str (YYYY-MM-DD)."""
        r_ids = list(self.date_to_row_ids.get(d_str, []))
        q_date = parse_iso_date(d_str)
        if q_date:
            for s, e, rid in self.long_ranges:
                if s <= q_date <= e and rid not in r_ids:
                    r_ids.append(rid)
        return [self.rows_by_id[rid] for rid in r_ids if rid in self.rows_by_id]

    def get_rows_by_month(self, ym_str: str) -> List[NormalizedTravelRow]:
        """Return all rows whose interval overlaps ym_str (YYYY-MM)."""
        r_ids = list(self.month_to_row_ids.get(ym_str, []))
        try:
            parts = ym_str.split("-")
            y, m = int(parts[0]), int(parts[1])
            m_start = date(y, m, 1)
            if m == 12:
                m_end = date(y, 12, 31)
            else:
                m_end = date(y, m + 1, 1) - timedelta(days=1)
            for s, e, rid in self.long_ranges:
                if not (e < m_start or s > m_end) and rid not in r_ids:
                    r_ids.append(rid)
        except Exception:
            pass
        return [self.rows_by_id[rid] for rid in r_ids if rid in self.rows_by_id]

    def get_rows_by_year(self, y_str: str) -> List[NormalizedTravelRow]:
        """Return all rows whose interval overlaps y_str (YYYY)."""
        r_ids = list(self.year_to_row_ids.get(y_str, []))
        try:
            y = int(y_str)
            y_start = date(y, 1, 1)
            y_end = date(y, 12, 31)
            for s, e, rid in self.long_ranges:
                if not (e < y_start or s > y_end) and rid not in r_ids:
                    r_ids.append(rid)
        except Exception:
            pass
        return [self.rows_by_id[rid] for rid in r_ids if rid in self.rows_by_id]

    def get_rows_by_place(self, place: str) -> List[NormalizedTravelRow]:
        """Return all rows matching place by exact normalized token or canonical alias."""
        canon = self.canonical_place(place)
        r_ids = self.place_to_row_ids.get(canon, [])
        return [self.rows_by_id[rid] for rid in r_ids if rid in self.rows_by_id]


def group_candidates_semantically(
    rows: List[NormalizedTravelRow],
    index: Optional[TravelScheduleIndex] = None,
) -> List[TravelCandidate]:
    """Group rows that represent the exact same semantic visit interval and canonical place.

    Preserves every source row ID and original text in provenance, with deterministic
    ordering independent of input iteration order.
    """
    grouped: Dict[Tuple[str, str, str, Optional[str]], List[NormalizedTravelRow]] = {}
    for r in rows:
        eff_end = r.end_date if (r.end_date and r.end_date.strip()) else r.start_date
        canon_p = index.canonical_place(r.place) if index else _norm_place_token(r.place)
        iso = r.country_iso2.lower() if r.country_iso2 else None
        key = (r.start_date, eff_end, canon_p, iso)
        grouped.setdefault(key, []).append(r)

    candidates = []
    for key, row_group in grouped.items():
        # Deterministically choose representative row by lowest row ID
        rep = min(row_group, key=lambda r: r.id)
        row_ids = sorted(list(set(r.id for r in row_group)))
        all_texts = sorted(list(set(r.schedule_text for r in row_group if r.schedule_text)))
        combined_text = "; ".join(all_texts)

        cand = TravelCandidate(
            schedule_row_ids=row_ids,
            start_date=rep.start_date,
            end_date=key[1],
            place=rep.place,
            country=rep.country,
            country_iso2=rep.country_iso2,
            schedule_text=combined_text,
        )
        candidates.append(cand)

    # Sort candidates deterministically
    candidates.sort(
        key=lambda c: (
            c.start_date,
            c.end_date,
            (index.canonical_place(c.place) if index else _norm_place_token(c.place)),
            c.country_iso2 or "",
            c.schedule_row_ids[0] if c.schedule_row_ids else 0,
        )
    )
    return candidates


class TravelScheduleEngine:
    """Core evaluation engine matching Tool 1 ParserResult against immutable travel schedule."""

    def __init__(self, manifest: TravelScheduleManifest):
        self.manifest = manifest
        self.index = TravelScheduleIndex(manifest)

    def evaluate(
        self,
        parser_result: ParserResult,
        tool2_context: Optional[Any] = None,
    ) -> TravelReviewResult:
        """Evaluate a media item's WHEN and WHERE against the travel schedule."""
        tracking_id = parser_result.identity.tracking_id
        res = TravelReviewResult(
            tracking_id=tracking_id,
            reference_checksum=self.manifest.canonical_sha256,
            reference_row_count=self.manifest.row_count,
            input_when_val=parser_result.when.selected_value,
            input_when_state=parser_result.when.state.value if parser_result.when else None,
            input_where_val=f"{parser_result.where.place_location or ''}-{parser_result.where.country_iso2 or ''}".strip("-") if parser_result.where else None,
            input_where_state=parser_result.where.state.value if parser_result.where else None,
        )

        # Inspect Tool 2 context
        t2_res = None
        if tool2_context:
            if isinstance(tool2_context, MediaDatabaseReviewResult):
                t2_res = tool2_context
            elif isinstance(tool2_context, dict):
                try:
                    t2_res = MediaDatabaseReviewResult.model_validate(tool2_context)
                except Exception:
                    pass

        if t2_res:
            res.tool2_decision = t2_res.decision.value if hasattr(t2_res.decision, "value") else str(t2_res.decision)
            res.tool2_context_state = t2_res.database_state
            res.selected_media_row_id = t2_res.selected_media_row_id
        else:
            res.tool2_context_state = "UNAVAILABLE"
            res.diagnostic_notes.append("Media context unavailable; proceeding as schedule-only provisional review")

        # Extract confirmed Tool 2 Media context
        is_confirmed_media = (
            t2_res is not None
            and t2_res.decision == ReviewDecision.EXISTING_MEDIA_MATCH
            and t2_res.selected_media_row_id is not None
        )
        confirmed_media_when: Optional[str] = None
        confirmed_media_where: Optional[str] = None
        confirmed_media_country_iso: Optional[str] = None

        if is_confirmed_media and t2_res:
            if t2_res.renamer_enrichment and t2_res.renamer_enrichment.confirmed:
                confirmed_media_when = t2_res.renamer_enrichment.when_val
                confirmed_media_where = t2_res.renamer_enrichment.where_val
            cand = next((c for c in t2_res.candidates if c.media_row_id == t2_res.selected_media_row_id), None)
            if cand:
                if not confirmed_media_when and cand.normalized_row.get("date"):
                    confirmed_media_when = str(cand.normalized_row.get("date")).strip()
                if cand.normalized_row.get("country"):
                    c_iso = _norm_country(str(cand.normalized_row.get("country")))
                    if c_iso:
                        confirmed_media_country_iso = c_iso.lower()
                if not confirmed_media_where and cand.normalized_row.get("place"):
                    p = str(cand.normalized_row.get("place")).strip()
                    confirmed_media_where = f"{p}-{confirmed_media_country_iso}" if confirmed_media_country_iso else p

            if confirmed_media_where and not confirmed_media_country_iso:
                _, parsed_cm_iso = parse_structured_where(confirmed_media_where)
                if parsed_cm_iso:
                    confirmed_media_country_iso = parsed_cm_iso

            if t2_res.conflicts:
                res.conflicts.extend(t2_res.conflicts)
                res.diagnostic_notes.append("Preserving high-authority conflict between local and confirmed Media row")

        # Determine what is known in input
        y, m, d = parse_partial_when(parser_result.when.selected_value)
        has_full_date = (y is not None and m is not None and d is not None)
        has_partial_date = (y is not None) and not has_full_date
        has_any_date = (y is not None)

        local_place = parser_result.where.place_location if parser_result.where else None
        local_iso = parser_result.where.country_iso2.lower() if (parser_result.where and parser_result.where.country_iso2) else None
        if not local_iso and parser_result.where and parser_result.where.country:
            c_code = _norm_country(parser_result.where.country)
            if c_code:
                local_iso = c_code.lower()
        has_place = bool(local_place and local_place.strip())
        has_country_only = bool(local_iso and not has_place)

        # Incorporate confirmed Media authority for missing local dimensions
        cm_p, cm_p_iso = parse_structured_where(confirmed_media_where) if confirmed_media_where else (None, None)
        cm_country = confirmed_media_country_iso or cm_p_iso

        # 1. Authoritative date from confirmed Media when local lacks full date
        if not has_full_date and confirmed_media_when:
            cm_y, cm_m, cm_d = parse_partial_when(confirmed_media_when)
            if cm_y and cm_m and cm_d:
                auth_date = f"{cm_y:04d}-{cm_m:02d}-{cm_d:02d}"
                eff_place = local_place or cm_p
                eff_iso = local_iso or cm_country
                if eff_place:
                    eval_res = self._evaluate_case_a(
                        parser_result=parser_result,
                        res=res,
                        exact_date=auth_date,
                        place=eff_place,
                        country_iso=eff_iso,
                        is_confirmed_media=is_confirmed_media,
                        t2_res=t2_res,
                    )
                    return self._apply_media_authority_guard(eval_res, confirmed_media_when, confirmed_media_where)
                else:
                    eval_res = self._evaluate_case_c(
                        parser_result=parser_result,
                        res=res,
                        exact_date=auth_date,
                        country_iso=eff_iso,
                        is_confirmed_media=is_confirmed_media,
                        t2_res=t2_res,
                    )
                    return self._apply_media_authority_guard(eval_res, confirmed_media_when, confirmed_media_where)

        # 2. Authoritative place from confirmed Media when local lacks place but has full date
        if has_full_date and not has_place and cm_p:
            eff_place = cm_p
            eff_iso = local_iso or cm_country
            eval_res = self._evaluate_case_a(
                parser_result=parser_result,
                res=res,
                exact_date=f"{y:04d}-{m:02d}-{d:02d}",
                place=eff_place,
                country_iso=eff_iso,
                is_confirmed_media=is_confirmed_media,
                t2_res=t2_res,
            )
            return self._apply_media_authority_guard(eval_res, confirmed_media_when, confirmed_media_where)

        # 3. Authoritative place from confirmed Media when local lacks date and place (R-008 boundary case)
        if not has_full_date and not has_place and cm_p and not confirmed_media_when:
            eff_iso = local_iso or cm_country
            eval_res = self._evaluate_case_b(
                parser_result=parser_result,
                res=res,
                place=cm_p,
                country_iso=eff_iso,
                year=y,
                month=m,
                is_confirmed_media=is_confirmed_media,
                t2_res=t2_res,
            )
            # Crucial: confirmed Media WHERE was used as anchor; do not emit it as provisional Tool 3 evidence
            if eval_res.provisional_enrichment:
                eval_res.provisional_enrichment.where_val = None
                if not eval_res.provisional_enrichment.when_val:
                    eval_res.provisional_enrichment = None
                    if eval_res.decision == TravelReviewDecision.PROVISIONAL_ENRICHMENT:
                        eval_res.decision = TravelReviewDecision.CORROBORATED
            return self._apply_media_authority_guard(eval_res, confirmed_media_when, confirmed_media_where)

        # Case D: neither date nor location usable
        if not has_any_date and not has_place and not has_country_only:
            res.decision = TravelReviewDecision.INSUFFICIENT_EVIDENCE
            res.diagnostic_notes.append("Neither WHEN nor WHERE provides enough evidence for schedule lookup")
            res.downstream_routing.append("tool_5_content_discovery")
            return res

        # Case A: Both WHEN and WHERE known
        if has_full_date and has_place:
            eff_iso = local_iso or cm_country
            eval_res = self._evaluate_case_a(
                parser_result=parser_result,
                res=res,
                exact_date=f"{y:04d}-{m:02d}-{d:02d}",
                place=local_place,
                country_iso=eff_iso,
                is_confirmed_media=is_confirmed_media,
                t2_res=t2_res,
            )
            return self._apply_media_authority_guard(eval_res, confirmed_media_when, confirmed_media_where)

        # Case B: Location known, date missing or partial
        if has_place and (not has_any_date or has_partial_date):
            eff_iso = local_iso or cm_country
            eval_res = self._evaluate_case_b(
                parser_result=parser_result,
                res=res,
                place=local_place,
                country_iso=eff_iso,
                year=y,
                month=m,
                is_confirmed_media=is_confirmed_media,
                t2_res=t2_res,
            )
            return self._apply_media_authority_guard(eval_res, confirmed_media_when, confirmed_media_where)

        # Case C: Date known, location missing or partial
        if has_full_date and (not has_place):
            eff_iso = local_iso or cm_country
            eval_res = self._evaluate_case_c(
                parser_result=parser_result,
                res=res,
                exact_date=f"{y:04d}-{m:02d}-{d:02d}",
                country_iso=eff_iso,
                is_confirmed_media=is_confirmed_media,
                t2_res=t2_res,
            )
            return self._apply_media_authority_guard(eval_res, confirmed_media_when, confirmed_media_where)

        # Sub-case: Partial date with no place, or country only
        if has_partial_date and not has_place:
            res.decision = TravelReviewDecision.INSUFFICIENT_EVIDENCE
            res.diagnostic_notes.append("Partial date without location is insufficient to constrain schedule")
            res.downstream_routing.append("tool_5_content_discovery")
            return res

        # Fallback
        res.decision = TravelReviewDecision.INSUFFICIENT_EVIDENCE
        return res

    def _apply_media_authority_guard(
        self,
        res: TravelReviewResult,
        confirmed_media_when: Optional[str],
        confirmed_media_where: Optional[str],
    ) -> TravelReviewResult:
        """Ensure confirmed Tool 2 Media values are never contradicted, downgraded, or overwritten (R-008, R-013)."""
        if not res.provisional_enrichment:
            return res

        if confirmed_media_when and res.provisional_enrichment.when_val:
            if res.provisional_enrichment.when_val != confirmed_media_when:
                res.conflicts.append(
                    f"Schedule date {res.provisional_enrichment.when_val} contradicts confirmed Media date {confirmed_media_when}"
                )
                res.provisional_enrichment.when_val = None
                res.diagnostic_notes.append("Suppressed provisional WHEN enrichment: contradicts confirmed Media date")
            else:
                # Schedule agrees with confirmed Media date; record corroboration without redundant provisional enrichment (R-013)
                res.provisional_enrichment.when_val = None
                res.diagnostic_notes.append("Schedule agrees with confirmed Media date; recording corroboration without redundant provisional enrichment")

        if confirmed_media_where and res.provisional_enrichment.where_val:
            cand_p, cand_iso = parse_structured_where(res.provisional_enrichment.where_val)
            cm_p, cm_iso = parse_structured_where(confirmed_media_where)
            place_diff = (
                cand_p is not None
                and cm_p is not None
                and self.index.canonical_place(cand_p) != self.index.canonical_place(cm_p)
            )
            country_diff = (
                cand_iso is not None
                and cm_iso is not None
                and cand_iso.lower() != cm_iso.lower()
            )
            if place_diff or country_diff:
                conflict_details = []
                if place_diff:
                    conflict_details.append(f"place {cand_p} vs confirmed Media {cm_p}")
                if country_diff:
                    conflict_details.append(f"country {cand_iso} vs confirmed Media {cm_iso}")
                res.conflicts.append(
                    f"Schedule location {res.provisional_enrichment.where_val} contradicts confirmed Media WHERE {confirmed_media_where} ({', '.join(conflict_details)})"
                )
                res.provisional_enrichment.where_val = None
                res.diagnostic_notes.append(
                    f"Suppressed provisional WHERE enrichment: contradicts confirmed Media location ({', '.join(conflict_details)})"
                )
            else:
                # Schedule agrees with confirmed Media location; record corroboration without redundant provisional enrichment (R-013)
                res.provisional_enrichment.where_val = None
                res.diagnostic_notes.append("Schedule agrees with confirmed Media location; recording corroboration without redundant provisional enrichment")

        if not res.provisional_enrichment.when_val and not res.provisional_enrichment.where_val:
            res.provisional_enrichment = None
            if res.decision == TravelReviewDecision.PROVISIONAL_ENRICHMENT:
                res.decision = TravelReviewDecision.SCHEDULE_CONFLICT if res.conflicts else TravelReviewDecision.CORROBORATED

        return res

    def _evaluate_case_a(
        self,
        parser_result: ParserResult,
        res: TravelReviewResult,
        exact_date: str,
        place: str,
        country_iso: Optional[str],
        is_confirmed_media: bool,
        t2_res: Optional[MediaDatabaseReviewResult],
    ) -> TravelReviewResult:
        """Case A: Both date and location known. Corroborate or conflict."""
        matching_rows = self.index.get_rows_by_date(exact_date)
        if not matching_rows:
            res.decision = TravelReviewDecision.NO_SCHEDULE_SUPPORT
            res.diagnostic_notes.append(f"No schedule entry found for date {exact_date}")
            return res

        candidates = group_candidates_semantically(matching_rows, index=self.index)
        res.candidates = candidates

        norm_local_place = self.index.canonical_place(place)
        corroborating_candidates = []
        conflicting_candidates = []

        for cand in candidates:
            cand_canon_place = self.index.canonical_place(cand.place)
            place_agrees = (norm_local_place == cand_canon_place)
            
            cand_iso = cand.country_iso2.lower() if cand.country_iso2 else None

            cand.date_comparison = FieldComparisonState.AGREES.value
            
            # Place comparison computed independently (R-013)
            if place_agrees:
                cand.place_comparison = FieldComparisonState.AGREES.value
            else:
                cand.place_comparison = FieldComparisonState.CONFLICT.value

            # Country comparison computed independently (R-013)
            if country_iso and cand_iso:
                if country_iso.lower() == cand_iso.lower():
                    cand.country_comparison = FieldComparisonState.AGREES.value
                    country_compatible = True
                else:
                    cand.country_comparison = FieldComparisonState.CONFLICT.value
                    country_compatible = False
            elif country_iso or cand_iso:
                cand.country_comparison = FieldComparisonState.NOT_COMPARABLE.value
                country_compatible = True
            else:
                cand.country_comparison = FieldComparisonState.NOT_COMPARABLE.value
                country_compatible = True

            if place_agrees and country_compatible:
                cand.match_reasons.append(f"Schedule corroborates {place} on {exact_date}")
                corroborating_candidates.append(cand)
            else:
                conflict_details = []
                if not place_agrees:
                    conflict_details.append(f"place {cand.place} vs {place}")
                if not country_compatible:
                    conflict_details.append(f"country {cand_iso or '??'} vs {country_iso or '??'}")
                cand.match_reasons.append(
                    f"Schedule places speaker in {cand.place} ({cand_iso or '??'}) on {exact_date}, conflicting with {place} ({', '.join(conflict_details)})"
                )
                conflicting_candidates.append(cand)

        if corroborating_candidates:
            # If local lacked country and schedule uniquely supplies it, provisionally enrich country
            if not country_iso:
                cand_countries = list(dict.fromkeys(c.country_iso2.lower() for c in corroborating_candidates if c.country_iso2))
                if len(cand_countries) == 1:
                    supplied_iso = cand_countries[0]
                    res.decision = TravelReviewDecision.PROVISIONAL_ENRICHMENT
                    res.selected_schedule_row_ids = [rid for c in corroborating_candidates for rid in c.schedule_row_ids]
                    selected_where = f"{place}-{supplied_iso}"
                    res.provisional_enrichment = TravelRenamerEnrichment(
                        confirmed=False,
                        source_tool="tool_3_travel_schedule_review",
                        where_val=selected_where,
                        where_state=ResolutionState.PROVISIONAL,
                        schedule_row_ids=res.selected_schedule_row_ids,
                        reference_checksum=self.manifest.canonical_sha256,
                        evidence=[f"Unique schedule match provisionally supplies country {supplied_iso.upper()} for known place {place}"],
                    )
                    res.diagnostic_notes.append(
                        f"Unique schedule match provisionally supplies country {supplied_iso.upper()} for known place {place}"
                    )
                    return res

            res.decision = TravelReviewDecision.CORROBORATED
            res.selected_schedule_row_ids = [rid for c in corroborating_candidates for rid in c.schedule_row_ids]
            res.diagnostic_notes.append(
                f"Schedule corroborates recording date {exact_date} and location {place}"
            )
            # Never overwrite or change fields on CORROBORATED
            return res

        # If matching rows exist on that date but all are elsewhere: SCHEDULE_CONFLICT
        res.decision = TravelReviewDecision.SCHEDULE_CONFLICT
        conflict_places = [f"{c.place}-{c.country_iso2 or ''}".strip("-") for c in conflicting_candidates]
        target_loc = f"{place}-{country_iso}" if country_iso else place
        res.conflicts.append(
            f"Schedule on {exact_date} records speaker in {', '.join(conflict_places)}, not {target_loc}"
        )
        res.diagnostic_notes.append(
            f"Schedule conflict on {exact_date}: planned {', '.join(conflict_places)} vs local {target_loc}"
        )
        return res

    def _evaluate_case_b(
        self,
        parser_result: ParserResult,
        res: TravelReviewResult,
        place: str,
        country_iso: Optional[str],
        year: Optional[int],
        month: Optional[int],
        is_confirmed_media: bool,
        t2_res: Optional[MediaDatabaseReviewResult],
    ) -> TravelReviewResult:
        """Case B: Location known, date missing or partial. Search by place and constrain by date."""
        matching_rows = self.index.get_rows_by_place(place)
        if not matching_rows:
            res.decision = TravelReviewDecision.NO_SCHEDULE_SUPPORT
            res.diagnostic_notes.append(f"No schedule entry found for place {place}")
            return res

        # Filter by country compatibility if country is known
        if country_iso:
            compatible = []
            for r in matching_rows:
                r_iso = r.country_iso2.lower() if r.country_iso2 else None
                if r_iso is None or r_iso == country_iso:
                    compatible.append(r)
            matching_rows = compatible

        if not matching_rows:
            res.decision = TravelReviewDecision.NO_SCHEDULE_SUPPORT
            res.diagnostic_notes.append(f"No schedule entry for place {place} in country {country_iso}")
            return res

        # Filter by partial date constraints (year and month)
        if year is not None:
            filtered = []
            for r in matching_rows:
                d_s = parse_iso_date(r.start_date)
                d_e = parse_iso_date(r.end_date) if r.end_date else d_s
                if not d_s:
                    continue
                eff_e = d_e or d_s
                # Overlaps year?
                if d_s.year <= year <= eff_e.year:
                    if month is not None:
                        # Overlaps year-month?
                        s_ym = (d_s.year, d_s.month)
                        e_ym = (eff_e.year, eff_e.month)
                        t_ym = (year, month)
                        if s_ym <= t_ym <= e_ym:
                            filtered.append(r)
                    else:
                        filtered.append(r)
            matching_rows = filtered

        if not matching_rows:
            res.decision = TravelReviewDecision.NO_SCHEDULE_SUPPORT
            date_ctx = f"{year:04d}-{month:02d}" if (year and month) else (f"{year}" if year else "")
            res.diagnostic_notes.append(f"No schedule entry for {place} compatible with partial date {date_ctx}")
            return res

        # Group semantically
        candidates = group_candidates_semantically(matching_rows, index=self.index)
        res.candidates = candidates

        # Populate candidate comparison states & match reasons (R-011)
        for c in candidates:
            c.place_comparison = FieldComparisonState.AGREES.value
            c_iso = c.country_iso2.lower() if c.country_iso2 else None
            if country_iso and c_iso:
                c.country_comparison = FieldComparisonState.AGREES.value if country_iso == c_iso else FieldComparisonState.CONFLICT.value
            elif not country_iso and c_iso:
                c.country_comparison = FieldComparisonState.LOCAL_MISSING.value
            elif country_iso and not c_iso:
                c.country_comparison = FieldComparisonState.SCHEDULE_MISSING.value
            else:
                c.country_comparison = FieldComparisonState.NOT_COMPARABLE.value

            if year is None:
                c.date_comparison = FieldComparisonState.LOCAL_MISSING.value
            else:
                c.date_comparison = FieldComparisonState.AGREES.value

            c.match_reasons.append(
                f"Schedule visit to {c.place} ({c.start_date}..{c.end_date}) matches location constraint"
            )

        # Check distinct visit intervals
        distinct_intervals = set((c.start_date, c.end_date) for c in candidates)

        if len(distinct_intervals) > 1:
            res.decision = TravelReviewDecision.MULTIPLE_SCHEDULE_CANDIDATES
            res.diagnostic_notes.append(
                f"Multiple distinct schedule visits ({len(distinct_intervals)}) found for {place}"
            )
            for c in candidates:
                c.possible_when = f"{c.start_date} to {c.end_date}" if c.start_date != c.end_date else c.start_date
            return res

        # Exactly 1 distinct visit interval
        # Preserve the union of all contributing row IDs across matching candidates (R-009)
        union_row_ids = sorted(list(set(rid for c in candidates for rid in c.schedule_row_ids)))
        all_cand_texts = [c.schedule_text for c in candidates if c.schedule_text]
        combined_cand_text = "; ".join(dict.fromkeys(all_cand_texts))

        cand = candidates[0]
        cand.schedule_row_ids = union_row_ids
        if combined_cand_text:
            cand.schedule_text = combined_cand_text
        res.selected_schedule_row_ids = union_row_ids

        d_s = parse_iso_date(cand.start_date)
        d_e = parse_iso_date(cand.end_date) if cand.end_date else d_s

        if not d_s:
            res.decision = TravelReviewDecision.NO_SCHEDULE_SUPPORT
            return res

        eff_e = d_e or d_s

        # Single-day visit
        if d_s == eff_e:
            selected_when = d_s.isoformat()
            res.decision = TravelReviewDecision.PROVISIONAL_ENRICHMENT
            cand.possible_when = selected_when
            res.provisional_enrichment = TravelRenamerEnrichment(
                confirmed=False,
                source_tool="tool_3_travel_schedule_review",
                when_val=selected_when,
                when_state=ResolutionState.PROVISIONAL,
                schedule_row_ids=union_row_ids,
                reference_checksum=self.manifest.canonical_sha256,
                evidence=[f"Single-day travel schedule visit to {place} on {selected_when}"],
            )
            res.diagnostic_notes.append(f"Unique single-day visit to {place} authorizes provisional date {selected_when}")
            return res

        # Multi-day range: determine safe precision
        if d_s.year == eff_e.year and d_s.month == eff_e.month:
            # Within single month -> YYYY-MM-DD
            month_precision = f"{d_s.year:04d}-{d_s.month:02d}-DD"
            # If local already had this exact month precision, it is corroborated
            if parser_result.when and parser_result.when.selected_value == month_precision:
                res.decision = TravelReviewDecision.CORROBORATED
                res.diagnostic_notes.append(f"Schedule range corroborates existing partial date {month_precision}")
                return res

            res.decision = TravelReviewDecision.PROVISIONAL_ENRICHMENT
            cand.possible_when = month_precision
            res.provisional_enrichment = TravelRenamerEnrichment(
                confirmed=False,
                source_tool="tool_3_travel_schedule_review",
                when_val=month_precision,
                when_state=ResolutionState.PROVISIONAL,
                schedule_row_ids=union_row_ids,
                reference_checksum=self.manifest.canonical_sha256,
                evidence=[f"Multi-day visit to {place} ({cand.start_date}..{cand.end_date}) authorizes month precision {month_precision}"],
            )
            res.diagnostic_notes.append(f"Multi-day visit within one month authorizes partial date {month_precision}")
            return res

        if d_s.year == eff_e.year and d_s.month != eff_e.month:
            # Spans months within same year -> YYYY-MM-DD
            year_precision = f"{d_s.year:04d}-MM-DD"
            if parser_result.when and parser_result.when.selected_value == year_precision:
                res.decision = TravelReviewDecision.CORROBORATED
                res.diagnostic_notes.append(f"Schedule range corroborates existing partial date {year_precision}")
                return res

            res.decision = TravelReviewDecision.PROVISIONAL_ENRICHMENT
            cand.possible_when = year_precision
            res.provisional_enrichment = TravelRenamerEnrichment(
                confirmed=False,
                source_tool="tool_3_travel_schedule_review",
                when_val=year_precision,
                when_state=ResolutionState.PROVISIONAL,
                schedule_row_ids=union_row_ids,
                reference_checksum=self.manifest.canonical_sha256,
                evidence=[f"Visit to {place} spans months ({cand.start_date}..{cand.end_date}); authorizes year precision {year_precision}"],
            )
            res.diagnostic_notes.append(f"Multi-month visit within year authorizes partial date {year_precision}")
            return res

        # Spans multiple years -> no invented selected date!
        res.decision = TravelReviewDecision.MULTIPLE_SCHEDULE_CANDIDATES
        cand.possible_when = f"{cand.start_date} to {cand.end_date}"
        res.diagnostic_notes.append(
            f"Schedule visit to {place} spans years ({cand.start_date}..{cand.end_date}); no single date precision selected"
        )
        return res

    def _evaluate_case_c(
        self,
        parser_result: ParserResult,
        res: TravelReviewResult,
        exact_date: str,
        country_iso: Optional[str],
        is_confirmed_media: bool,
        t2_res: Optional[MediaDatabaseReviewResult],
    ) -> TravelReviewResult:
        """Case C: Date known, location missing or partial. Search by date and constrain by country."""
        matching_rows = self.index.get_rows_by_date(exact_date)
        if not matching_rows:
            res.decision = TravelReviewDecision.NO_SCHEDULE_SUPPORT
            res.diagnostic_notes.append(f"No schedule entry found for date {exact_date}")
            return res

        candidates = group_candidates_semantically(matching_rows, index=self.index)
        res.candidates = candidates

        # Populate candidate comparison states & match reasons (R-011)
        for c in candidates:
            c.date_comparison = FieldComparisonState.AGREES.value
            local_p = parser_result.where.place_location if parser_result.where else ""
            if local_p:
                if self.index.canonical_place(local_p) == self.index.canonical_place(c.place):
                    c.place_comparison = FieldComparisonState.AGREES.value
                else:
                    c.place_comparison = FieldComparisonState.CONFLICT.value
            else:
                c.place_comparison = FieldComparisonState.LOCAL_MISSING.value

            c_iso = c.country_iso2.lower() if c.country_iso2 else None
            if country_iso and c_iso:
                c.country_comparison = FieldComparisonState.AGREES.value if country_iso == c_iso else FieldComparisonState.CONFLICT.value
            elif not country_iso and c_iso:
                c.country_comparison = FieldComparisonState.LOCAL_MISSING.value
            elif country_iso and not c_iso:
                c.country_comparison = FieldComparisonState.SCHEDULE_MISSING.value
            else:
                c.country_comparison = FieldComparisonState.NOT_COMPARABLE.value

            c.match_reasons.append(
                f"Schedule places speaker in {c.place} ({c.country or 'country unknown'}) on {exact_date}"
            )

        # Check for country contradiction if country is known locally
        if country_iso:
            compatible = []
            contradicting = []
            for c in candidates:
                c_iso = c.country_iso2.lower() if c.country_iso2 else None
                if c_iso and c_iso != country_iso:
                    contradicting.append(c)
                else:
                    compatible.append(c)

            if contradicting and not compatible:
                res.decision = TravelReviewDecision.SCHEDULE_CONFLICT
                c_countries = [c.country for c in contradicting]
                res.conflicts.append(
                    f"Schedule country ({', '.join(c_countries)}) contradicts local country code {country_iso.upper()}"
                )
                res.diagnostic_notes.append("Country contradiction prevents location selection")
                return res

            candidates = compatible

        if not candidates:
            res.decision = TravelReviewDecision.NO_SCHEDULE_SUPPORT
            return res

        # Check distinct structured locations (canonical_place, country_iso) (R-009)
        distinct_places = set(self.index.canonical_place(c.place) for c in candidates if c.place and c.place.strip())

        # Check for multiple distinct canonical places or same place in different countries
        distinct_countries_by_place: Dict[str, Set[str]] = {}
        for c in candidates:
            if c.place and c.place.strip():
                cp = self.index.canonical_place(c.place)
                c_iso = c.country_iso2.lower() if c.country_iso2 else ""
                distinct_countries_by_place.setdefault(cp, set()).add(c_iso)

        has_multi_country_conflict = any(
            len([iso for iso in isos if iso]) > 1
            for isos in distinct_countries_by_place.values()
        )

        if len(distinct_places) > 1 or has_multi_country_conflict:
            res.decision = TravelReviewDecision.MULTIPLE_SCHEDULE_CANDIDATES
            res.diagnostic_notes.append(
                f"Multiple distinct schedule locations found for date {exact_date}"
            )
            for c in candidates:
                c_iso = c.country_iso2.lower() if c.country_iso2 else ""
                c.possible_where = f"{c.place}-{c_iso}".strip("-")
            return res

        if len(distinct_places) == 0:
            # Schedule has only country or empty place; do not fabricate a city
            res.decision = TravelReviewDecision.NO_SCHEDULE_SUPPORT
            res.diagnostic_notes.append(f"Schedule for {exact_date} contains no usable place name")
            return res

        # Exactly 1 unique structured location
        # Preserve the union of all contributing row IDs across matching candidates (R-009)
        union_row_ids = sorted(list(set(rid for c in candidates for rid in c.schedule_row_ids)))
        all_cand_texts = [c.schedule_text for c in candidates if c.schedule_text]
        combined_cand_text = "; ".join(dict.fromkeys(all_cand_texts))

        cand = candidates[0]
        cand.schedule_row_ids = union_row_ids
        if combined_cand_text:
            cand.schedule_text = combined_cand_text

        # Determine country: pick known candidate country or local country
        cand_with_iso = next((c for c in candidates if c.country_iso2), None)
        best_iso = cand_with_iso.country_iso2.lower() if cand_with_iso else (country_iso or "")

        clean_place = cand.place.strip().replace(" ", "-")
        selected_where = f"{clean_place}-{best_iso}".strip("-")

        # Check if local already had this exact place (missing country only)
        local_p = parser_result.where.place_location if parser_result.where else ""
        if local_p and self.index.canonical_place(local_p) == self.index.canonical_place(cand.place):
            # Known place missing country: provisionally fill country without replacing place
            selected_where = f"{local_p}-{best_iso}".strip("-")
            evidence_str = f"Unique schedule match provisionally supplies country {best_iso.upper()} for known place {local_p}"
        else:
            evidence_str = f"Unique schedule entry on {exact_date} provisionally enriches location to {selected_where}"

        res.decision = TravelReviewDecision.PROVISIONAL_ENRICHMENT
        res.selected_schedule_row_ids = union_row_ids
        cand.possible_where = selected_where
        res.provisional_enrichment = TravelRenamerEnrichment(
            confirmed=False,
            source_tool="tool_3_travel_schedule_review",
            where_val=selected_where,
            where_state=ResolutionState.PROVISIONAL,
            schedule_row_ids=union_row_ids,
            reference_checksum=self.manifest.canonical_sha256,
            evidence=[evidence_str],
        )
        res.diagnostic_notes.append(f"Unique location {selected_where} on {exact_date} authorizes provisional WHERE")
        return res
