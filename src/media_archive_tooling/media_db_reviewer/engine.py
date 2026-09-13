"""Media Database Reconciliation Engine for Tool 2 (Build Plan Sections 10-15)."""
from datetime import datetime
import re
from typing import Any, Dict, List, Optional, Tuple

from rapidfuzz import fuzz

from ..common.ascii_latin import to_ascii_latin
from ..renamer.models import ParserResult, ResolutionState
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


def _norm_token(text: Optional[str]) -> str:
    """Normalize text token for comparison."""
    if not text:
        return ""
    ascii_val = to_ascii_latin(str(text))
    return re.sub(r"[^a-zA-Z0-9]+", "", ascii_val).lower()


def _compare_dates(local_date: Optional[str], db_date: Optional[str]) -> Tuple[FieldComparisonState, Optional[str]]:
    """Compare local recording date with Baserow date."""
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

    # Check for exact YYYY-MM-DD vs YYYY-MM
    if len(l_clean) == 10 and len(d_clean) == 10:
        if l_clean != d_clean:
            return FieldComparisonState.CONFLICT, f"Local date '{l_clean}' conflicts with database date '{d_clean}'"

    if l_clean.startswith(d_clean) or d_clean.startswith(l_clean):
        return FieldComparisonState.AGREES, "Partial date match"

    return FieldComparisonState.CONFLICT, f"Local date '{l_clean}' conflicts with database date '{d_clean}'"


def _compare_places(
    local_place: Optional[str],
    local_country: Optional[str],
    db_place: Optional[str],
    db_country: Optional[str],
) -> Tuple[FieldComparisonState, Optional[str]]:
    """Compare local place/country with Baserow place/country."""
    if not local_place and not db_place:
        return FieldComparisonState.NOT_COMPARABLE, "Both locations missing"
    if local_place and not db_place:
        return FieldComparisonState.DATABASE_MISSING, "Database location is blank"
    if not local_place and db_place:
        return FieldComparisonState.LOCAL_MISSING, "Local location is blank"

    norm_lp = _norm_token(local_place)
    norm_dp = _norm_token(db_place)

    if norm_lp == norm_dp or norm_lp in norm_dp or norm_dp in norm_lp:
        return FieldComparisonState.AGREES, None

    # Fuzzy check for minor spelling variations
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
    """Compare local WHAT with Baserow what/title."""
    if not local_what and not db_what and not db_title:
        return FieldComparisonState.NOT_COMPARABLE, "Both WHAT and title missing"
    if local_what and not db_what and not db_title:
        return FieldComparisonState.DATABASE_MISSING, "Database what/title is blank"
    if not local_what and (db_what or db_title):
        return FieldComparisonState.LOCAL_MISSING, "Local what is blank"

    norm_lw = _norm_token(local_what)
    norm_dw = _norm_token(db_what)
    norm_dt = _norm_token(db_title)

    # If scripture reference matches
    if norm_dw and (norm_lw == norm_dw or norm_lw in norm_dw or norm_dw in norm_lw):
        return FieldComparisonState.AGREES, None

    # If title matches local what
    if norm_dt and (norm_lw in norm_dt or norm_dt in norm_lw):
        return FieldComparisonState.AGREES, "Local what matches database title"

    # If local scripture and db scripture exist and differ
    if norm_lw and norm_dw and norm_lw != norm_dw:
        return FieldComparisonState.CONFLICT, f"Local WHAT '{local_what}' conflicts with database WHAT '{db_what}'"

    # If db_what is blank but db_title exists and doesn't match local_what
    if norm_lw and not norm_dw:
        return FieldComparisonState.DATABASE_MISSING, "Database WHAT/scripture is blank"

    if db_title and not local_what:
        return FieldComparisonState.LOCAL_MISSING, None

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

            # F. Supporting travel schedule context
            travel_context = []
            if local_date or row["date"]:
                eval_date = local_date or row["date"]
                for tr in norm_travel_rows:
                    if tr["start_date"] == eval_date or (tr["start_date"] and eval_date.startswith(tr["start_date"][:7])):
                        if tr["place"]:
                            travel_context.append(f"Travel schedule records '{tr['place']}' on {tr['start_date']}")
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

        # Sort candidates descending by score
        candidates.sort(key=lambda c: c.score, reverse=True)

        # 2. Decision Logic
        has_direct_identity = False
        best_candidate: Optional[MediaCandidate] = None

        if candidates:
            best_candidate = candidates[0]
            has_direct_identity = bool(best_candidate.identity_evidence)

        # Check for material conflicts on the leading candidate
        has_material_conflict = bool(best_candidate and best_candidate.conflicts)

        # Check for competing equally plausible candidates
        multiple_plausible = False
        if len(candidates) >= 2:
            top_score = candidates[0].score
            second_score = candidates[1].score
            if abs(top_score - second_score) < 10.0 and top_score >= 50.0:
                multiple_plausible = True

        decision = ReviewDecision.INSUFFICIENT_EVIDENCE
        decision_state = ""
        selected_row_id: Optional[int] = None
        renamer_enr = RenamerEnrichment()
        tool4_action = Tool4Action.NO_WRITE
        routing = []
        review_reasons = []
        conflicts_out = []
        diag_notes = []

        if has_material_conflict:
            decision = ReviewDecision.CONFLICT_WITH_EXISTING
            decision_state = f"Candidate Media row {best_candidate.media_row_id} contradicts incoming evidence"
            conflicts_out.extend(best_candidate.conflicts)
            review_reasons.extend(best_candidate.conflicts)
            diag_notes.append(f"Conflict with Baserow Media row {best_candidate.media_row_id}: {'; '.join(best_candidate.conflicts)}")
            tool4_action = Tool4Action.NEEDS_REVIEW
            # Material contradiction stays unconfirmed and routes onward / human review
            routing.append("tool_3_travel_schedule_review")

        elif multiple_plausible:
            decision = ReviewDecision.MULTIPLE_CANDIDATES
            decision_state = f"Multiple plausible Media rows found ({candidates[0].media_row_id}, {candidates[1].media_row_id})"
            diag_notes.append(f"Multiple plausible Baserow rows found ({len(candidates)} candidates)")
            tool4_action = Tool4Action.NEEDS_REVIEW
            routing.append("tool_3_travel_schedule_review")

        elif best_candidate and (has_direct_identity or best_candidate.score >= 70.0):
            # Confirmed match!
            decision = ReviewDecision.EXISTING_MEDIA_MATCH
            selected_row_id = best_candidate.media_row_id
            decision_state = f"Confirmed association with Baserow Media row {selected_row_id}"
            diag_notes.append(f"Confirmed match with Baserow Media row {selected_row_id}")

            # Determine proposed Tool 4 action: link existing (if format differs) or enrich existing
            row_format = (best_candidate.normalized_row.get("format") or "").lower()
            if ext in (".mp4", ".mkv", ".avi", ".mov") and "video" not in row_format:
                tool4_action = Tool4Action.LINK_EXISTING
            else:
                tool4_action = Tool4Action.ENRICH_EXISTING

            # Prepare confirmed Renamer enrichment
            db_row = best_candidate.normalized_row
            title_full = db_row.get("title") or ""
            what_val = None
            if title_full and local_what:
                what_val = f"{local_what}-{title_full}"
            elif title_full:
                what_val = title_full
            elif local_what:
                what_val = local_what

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
                evidence=[f"baserow_media_row:{selected_row_id}"] + best_candidate.retrieval_reasons,
            )

        elif best_candidate and best_candidate.score >= 30.0:
            # Probable match (unconfirmed)
            decision = ReviewDecision.PROBABLE_EXISTING_MEDIA
            decision_state = f"Probable association with Baserow Media row {best_candidate.media_row_id} (score {best_candidate.score:.1f})"
            diag_notes.append(f"Probable association with Baserow Media row {best_candidate.media_row_id}; unconfirmed")
            tool4_action = Tool4Action.NO_WRITE
            routing.append("tool_3_travel_schedule_review")

        else:
            # No candidate found
            # Check if input was discriminating
            is_discriminating = bool(local_date and (local_what or local_place))
            if snapshot.state == "LIVE_COMPLETE" and is_discriminating:
                decision = ReviewDecision.NEW_MEDIA_CANDIDATE
                decision_state = "No matching Media row found; input is discriminating"
                diag_notes.append("Complete live database check: no matching Media row found (new media candidate)")
                tool4_action = Tool4Action.CREATE_NEW
            elif snapshot.state == "CACHED_STALE":
                # Stale cache must NOT produce complete no-match!
                decision = ReviewDecision.DATABASE_UNAVAILABLE
                decision_state = "Database snapshot is stale; cannot confirm new media candidate"
                diag_notes.append("Stale cache cannot confirm no-match / new media candidate")
                tool4_action = Tool4Action.NO_WRITE
            else:
                decision = ReviewDecision.INSUFFICIENT_EVIDENCE
                decision_state = "Input metadata is too sparse to evaluate media match"
                diag_notes.append("Insufficient evidence to determine media database association")
                tool4_action = Tool4Action.NO_WRITE

        # Baserow check complete is true ONLY when a complete usable database review occurred
        baserow_check_complete = (
            snapshot.state == "LIVE_COMPLETE"
            and decision in (ReviewDecision.EXISTING_MEDIA_MATCH, ReviewDecision.NEW_MEDIA_CANDIDATE)
        )

        return MediaDatabaseReviewResult(
            tracking_id=tracking_id,
            database_state=snapshot.state,
            database_snapshot_at=snapshot.snapshot_at,
            snapshot_complete=snapshot.complete,
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
            review_required=bool(review_reasons),
            review_reasons=review_reasons,
            conflicts=conflicts_out,
            diagnostic_notes=diag_notes,
            evidence=[f"snapshot_state:{snapshot.state}"] + (best_candidate.retrieval_reasons if best_candidate else []),
        )
