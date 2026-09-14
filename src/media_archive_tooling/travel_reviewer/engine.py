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
            d_end = parse_iso_date(row.end_date) if row.end_date else d_start

            if d_start and d_end and d_end < d_start:
                # End before start is invalid schedule data
                self.invalid_rows.append(row)
                continue

            # Index dates
            if d_start:
                effective_end = d_end or d_start
                # Cap span to 366 days to avoid runaway index
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

            # Index place
            if row.place:
                norm_p = self.canonical_place(row.place)
                self.place_to_row_ids.setdefault(norm_p, []).append(row.id)

    def get_rows_by_date(self, d_str: str) -> List[NormalizedTravelRow]:
        """Return all rows whose interval contains d_str (YYYY-MM-DD)."""
        r_ids = self.date_to_row_ids.get(d_str, [])
        return [self.rows_by_id[rid] for rid in r_ids if rid in self.rows_by_id]

    def get_rows_by_month(self, ym_str: str) -> List[NormalizedTravelRow]:
        """Return all rows whose interval overlaps ym_str (YYYY-MM)."""
        r_ids = self.month_to_row_ids.get(ym_str, [])
        return [self.rows_by_id[rid] for rid in r_ids if rid in self.rows_by_id]

    def get_rows_by_year(self, y_str: str) -> List[NormalizedTravelRow]:
        """Return all rows whose interval overlaps y_str (YYYY)."""
        r_ids = self.year_to_row_ids.get(y_str, [])
        return [self.rows_by_id[rid] for rid in r_ids if rid in self.rows_by_id]

    def get_rows_by_place(self, place: str) -> List[NormalizedTravelRow]:
        """Return all rows matching place by exact normalized token or canonical alias."""
        canon = self.canonical_place(place)
        r_ids = self.place_to_row_ids.get(canon, [])
        return [self.rows_by_id[rid] for rid in r_ids if rid in self.rows_by_id]


def group_candidates_semantically(rows: List[NormalizedTravelRow]) -> List[TravelCandidate]:
    """Group rows that represent the exact same semantic visit interval and place.

    Preserves every source row ID and original text in provenance.
    """
    grouped: Dict[Tuple[str, str, str, Optional[str]], List[NormalizedTravelRow]] = {}
    for r in rows:
        norm_p = _norm_place_token(r.place)
        iso = r.country_iso2.lower() if r.country_iso2 else None
        key = (r.start_date, r.end_date, norm_p, iso)
        grouped.setdefault(key, []).append(r)

    candidates = []
    for key, row_group in grouped.items():
        first = row_group[0]
        row_ids = [r.id for r in row_group]
        all_texts = [r.schedule_text for r in row_group if r.schedule_text]
        combined_text = "; ".join(dict.fromkeys(all_texts))

        cand = TravelCandidate(
            schedule_row_ids=row_ids,
            start_date=first.start_date,
            end_date=first.end_date or first.start_date,
            place=first.place,
            country=first.country,
            country_iso2=first.country_iso2,
            schedule_text=combined_text,
        )
        candidates.append(cand)
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
            res.tool2_context_state = t2_res.database_state
            res.selected_media_row_id = t2_res.selected_media_row_id
        else:
            res.tool2_context_state = "UNAVAILABLE"

        # Check for high-authority contradiction between local and confirmed Media row
        is_confirmed_media = (
            t2_res is not None
            and t2_res.decision == ReviewDecision.EXISTING_MEDIA_MATCH
            and t2_res.selected_media_row_id is not None
        )
        if is_confirmed_media:
            # Check if local contradicts confirmed Media row
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

        # Case D: neither date nor location usable
        if not has_any_date and not has_place and not has_country_only:
            res.decision = TravelReviewDecision.INSUFFICIENT_EVIDENCE
            res.diagnostic_notes.append("Neither WHEN nor WHERE provides enough evidence for schedule lookup")
            res.downstream_routing.append("tool_5_content_discovery")
            return res

        # Case A: Both WHEN and WHERE known
        if has_full_date and has_place:
            return self._evaluate_case_a(
                parser_result=parser_result,
                res=res,
                exact_date=f"{y:04d}-{m:02d}-{d:02d}",
                place=local_place,
                country_iso=local_iso,
                is_confirmed_media=is_confirmed_media,
                t2_res=t2_res,
            )

        # Case B: Location known, date missing or partial
        if has_place and (not has_any_date or has_partial_date):
            return self._evaluate_case_b(
                parser_result=parser_result,
                res=res,
                place=local_place,
                country_iso=local_iso,
                year=y,
                month=m,
                is_confirmed_media=is_confirmed_media,
                t2_res=t2_res,
            )

        # Case C: Date known, location missing or partial
        if has_full_date and (not has_place):
            return self._evaluate_case_c(
                parser_result=parser_result,
                res=res,
                exact_date=f"{y:04d}-{m:02d}-{d:02d}",
                country_iso=local_iso,
                is_confirmed_media=is_confirmed_media,
                t2_res=t2_res,
            )

        # Sub-case: Partial date with no place, or country only
        if has_partial_date and not has_place:
            res.decision = TravelReviewDecision.INSUFFICIENT_EVIDENCE
            res.diagnostic_notes.append("Partial date without location is insufficient to constrain schedule")
            res.downstream_routing.append("tool_5_content_discovery")
            return res

        # Fallback
        res.decision = TravelReviewDecision.INSUFFICIENT_EVIDENCE
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

        candidates = group_candidates_semantically(matching_rows)
        res.candidates = candidates

        norm_local_place = self.index.canonical_place(place)
        corroborating_candidates = []
        conflicting_candidates = []

        for cand in candidates:
            cand_canon_place = self.index.canonical_place(cand.place)
            place_agrees = (norm_local_place == cand_canon_place)
            
            cand_iso = cand.country_iso2.lower() if cand.country_iso2 else None
            country_compatible = (
                country_iso is None
                or cand_iso is None
                or country_iso == cand_iso
            )

            cand.date_comparison = FieldComparisonState.AGREES.value
            if place_agrees and country_compatible:
                cand.place_comparison = FieldComparisonState.AGREES.value
                cand.country_comparison = FieldComparisonState.AGREES.value if (country_iso and cand_iso) else FieldComparisonState.NOT_COMPARABLE.value
                cand.match_reasons.append(f"Schedule corroborates {place} on {exact_date}")
                corroborating_candidates.append(cand)
            else:
                cand.place_comparison = FieldComparisonState.CONFLICT.value
                cand.match_reasons.append(
                    f"Schedule places speaker in {cand.place} ({cand_iso or '??'}) on {exact_date}, conflicting with {place}"
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
        res.conflicts.append(
            f"Schedule on {exact_date} records speaker in {', '.join(conflict_places)}, not {place}"
        )
        res.diagnostic_notes.append(
            f"Schedule conflict on {exact_date}: planned {', '.join(conflict_places)} vs local {place}"
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
        candidates = group_candidates_semantically(matching_rows)
        res.candidates = candidates

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
        cand = candidates[0]
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
            res.selected_schedule_row_ids = cand.schedule_row_ids
            cand.possible_when = selected_when
            res.provisional_enrichment = TravelRenamerEnrichment(
                confirmed=False,
                source_tool="tool_3_travel_schedule_review",
                when_val=selected_when,
                when_state=ResolutionState.PROVISIONAL,
                schedule_row_ids=cand.schedule_row_ids,
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
                res.selected_schedule_row_ids = cand.schedule_row_ids
                res.diagnostic_notes.append(f"Schedule range corroborates existing partial date {month_precision}")
                return res

            res.decision = TravelReviewDecision.PROVISIONAL_ENRICHMENT
            res.selected_schedule_row_ids = cand.schedule_row_ids
            cand.possible_when = month_precision
            res.provisional_enrichment = TravelRenamerEnrichment(
                confirmed=False,
                source_tool="tool_3_travel_schedule_review",
                when_val=month_precision,
                when_state=ResolutionState.PROVISIONAL,
                schedule_row_ids=cand.schedule_row_ids,
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
                res.selected_schedule_row_ids = cand.schedule_row_ids
                res.diagnostic_notes.append(f"Schedule range corroborates existing partial date {year_precision}")
                return res

            res.decision = TravelReviewDecision.PROVISIONAL_ENRICHMENT
            res.selected_schedule_row_ids = cand.schedule_row_ids
            cand.possible_when = year_precision
            res.provisional_enrichment = TravelRenamerEnrichment(
                confirmed=False,
                source_tool="tool_3_travel_schedule_review",
                when_val=year_precision,
                when_state=ResolutionState.PROVISIONAL,
                schedule_row_ids=cand.schedule_row_ids,
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

        candidates = group_candidates_semantically(matching_rows)
        res.candidates = candidates

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

        # Check distinct structured places
        distinct_places = set(self.index.canonical_place(c.place) for c in candidates if c.place and c.place.strip())

        if len(distinct_places) > 1:
            res.decision = TravelReviewDecision.MULTIPLE_SCHEDULE_CANDIDATES
            res.diagnostic_notes.append(
                f"Multiple distinct schedule locations ({len(distinct_places)}) found for date {exact_date}"
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

        # Exactly 1 unique place
        cand = candidates[0]
        clean_place = cand.place.strip().replace(" ", "-")
        iso_suffix = cand.country_iso2.lower() if cand.country_iso2 else (country_iso or "")
        selected_where = f"{clean_place}-{iso_suffix}".strip("-")

        # Check if local already had this exact place (missing country only)
        local_p = parser_result.where.place_location if parser_result.where else ""
        if local_p and self.index.canonical_place(local_p) == self.index.canonical_place(cand.place):
            # Known place missing country: provisionally fill country without replacing place
            selected_where = f"{local_p}-{iso_suffix}".strip("-")
            evidence_str = f"Unique schedule match provisionally supplies country {iso_suffix.upper()} for known place {local_p}"
        else:
            evidence_str = f"Unique schedule entry on {exact_date} provisionally enriches location to {selected_where}"

        res.decision = TravelReviewDecision.PROVISIONAL_ENRICHMENT
        res.selected_schedule_row_ids = cand.schedule_row_ids
        cand.possible_where = selected_where
        res.provisional_enrichment = TravelRenamerEnrichment(
            confirmed=False,
            source_tool="tool_3_travel_schedule_review",
            where_val=selected_where,
            where_state=ResolutionState.PROVISIONAL,
            schedule_row_ids=cand.schedule_row_ids,
            reference_checksum=self.manifest.canonical_sha256,
            evidence=[evidence_str],
        )
        res.diagnostic_notes.append(f"Unique location {selected_where} on {exact_date} authorizes provisional WHERE")
        return res
