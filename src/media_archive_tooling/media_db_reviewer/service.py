import inspect
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..common.ascii_latin import to_ascii_latin
from ..renamer.models import EnrichmentEvidence, ParserResult
from ..renamer.registry.registry import LocalRegistry
from .baserow_provider import (
    BaserowSnapshotProvider,
    BaserowUnavailableError,
    normalize_category_title_row,
)
from .engine import (
    MediaDatabaseReconciliationEngine,
    compose_what_val,
    _compare_dates,
    _compare_places,
    _compare_what,
    _norm_country,
)
from .models import (
    BaserowSnapshot,
    CategoryTitleResolution,
    CategoryTitleResolutionStatus,
    FieldComparisonState,
    MediaDatabaseReviewResult,
    RenamerEnrichment,
    ReviewDecision,
    Tool4Action,
)

logger = logging.getLogger(__name__)


def _load_snapshot_for_parser_res(provider: Any, parser_res: ParserResult) -> Any:
    """Helper to query per-decision live state if provider supports parser_result parameter."""
    try:
        return provider.load_snapshot(parser_result=parser_res)
    except TypeError:
        return provider.load_snapshot()


class MediaDatabaseReviewService:
    """Service providing media lookup, candidate reconciliation, and Renamer handoff."""

    def __init__(
        self,
        registry: LocalRegistry,
        provider: BaserowSnapshotProvider,
        engine: Optional[MediaDatabaseReconciliationEngine] = None,
    ):
        self.registry = registry
        self.provider = provider
        self.engine = engine or MediaDatabaseReconciliationEngine()

    def review_file(
        self,
        tracking_id: str,
        force_refresh: bool = False,
        auto_enrich: bool = True,
    ) -> MediaDatabaseReviewResult:
        """Execute Media database review on one registered file."""
        record = self.registry.get_file(tracking_id)
        if not record:
            raise ValueError(f"Tracking ID '{tracking_id}' not found in registry")

        parser_res = ParserResult.model_validate(record["parser_result"])
        snapshot = _load_snapshot_for_parser_res(self.provider, parser_res)

        # Check if this file already has a confirmed association in the registry
        stored = self.registry.get_media_db_review(tracking_id)
        confirmed_row_id = None
        has_confirmed_row = False
        is_confirmed_new = False
        if stored:
            s_dec = stored.get("decision")
            s_row = stored.get("selected_media_row_id")
            s_res = stored.get("result") or {}
            if isinstance(s_res, str):
                try:
                    s_res = json.loads(s_res)
                except Exception:
                    s_res = {}
            renamer_enr = s_res.get("renamer_enrichment") or {}
            if s_row is not None or s_dec == ReviewDecision.EXISTING_MEDIA_MATCH.value or renamer_enr.get("confirmed"):
                has_confirmed_row = True
                confirmed_row_id = s_row or renamer_enr.get("media_row_id")
            elif s_dec in (ReviewDecision.NEW_MEDIA_CANDIDATE.value, "CONFIRMED_NEW"):
                is_confirmed_new = True

        if not has_confirmed_row:
            actions = self.registry.get_review_actions(tracking_id)
            for act in reversed(actions):
                a_name = act.get("action")
                changes = act.get("changes") or {}
                if isinstance(changes, str):
                    try:
                        changes = json.loads(changes)
                    except Exception:
                        changes = {}
                if a_name == "media_db_confirm_existing" and changes.get("selected_media_row_id"):
                    has_confirmed_row = True
                    confirmed_row_id = changes["selected_media_row_id"]
                    break
                elif a_name in ("media_db_confirm_new", "media_db_confirm_new_force"):
                    is_confirmed_new = True
                    break

        if has_confirmed_row and confirmed_row_id:
            try:
                live_row = None
                if hasattr(self.provider, "fetch_media_row_live"):
                    try:
                        res_row = self.provider.fetch_media_row_live(confirmed_row_id)
                        if isinstance(res_row, dict):
                            live_row = res_row
                    except Exception as e:
                        logger.warning(f"Live fetch for confirmed row {confirmed_row_id} failed: {e}")
                if not live_row and hasattr(self.provider, "load_snapshot"):
                    snapshot = self.provider.load_snapshot()
                    for r in getattr(snapshot, "media_rows", []):
                        if isinstance(r, dict) and r.get("id") == confirmed_row_id:
                            from .baserow_provider import normalize_media_row
                            live_row = normalize_media_row(r)
                            break
                if live_row:
                    now_str = datetime.now(timezone.utc).isoformat()
                    local_date = parser_res.when.selected_value if parser_res.when else None
                    local_what = parser_res.what.selected_value if parser_res.what else None
                    local_place = parser_res.where.place_location if parser_res.where else None
                    local_country = parser_res.where.country_iso2 if parser_res.where else None
                    d_st, d_det = _compare_dates(local_date, live_row.get("date"))
                    p_st, p_det = _compare_places(local_place, local_country, live_row.get("place"), live_row.get("country"))
                    
                    has_fatal_conflict = (d_st == FieldComparisonState.CONFLICT or p_st == FieldComparisonState.CONFLICT)
                    if not has_fatal_conflict:
                        title_full = live_row.get("title") or ""
                        what_val = compose_what_val(local_what, title_full)
                        where_val = None
                        if live_row.get("place"):
                            where_val = f"{live_row['place']}-{live_row.get('country') or ''}".strip("-")
                        elif local_place:
                            where_val = f"{local_place}-{local_country or ''}".strip("-")

                        result = MediaDatabaseReviewResult(
                            tracking_id=tracking_id,
                            decision=ReviewDecision.EXISTING_MEDIA_MATCH,
                            selected_media_row_id=confirmed_row_id,
                            decision_state=f"Human confirmed association with Media row {confirmed_row_id} (revalidated live)",
                            review_required=False,
                            review_required_now=False,
                            review_reasons=[],
                            database_state="LIVE_CURRENT",
                            baserow_read_at=now_str,
                            database_snapshot_at=now_str,
                            snapshot_complete=True,
                            live_read_complete=True,
                            baserow_check_complete=True,
                            proposed_tool4_action=Tool4Action.ENRICH_EXISTING,
                            candidates=s_res.get("candidates", []),
                            selected_field_evidence=s_res.get("selected_field_evidence", {}),
                            renamer_enrichment=RenamerEnrichment(
                                confirmed=True,
                                media_row_id=confirmed_row_id,
                                when_val=live_row.get("date") or local_date,
                                what_val=what_val,
                                title_full=title_full,
                                where_val=where_val,
                                category=live_row.get("category"),
                                source_identifiers=live_row.get("source_ids") or [],
                                evidence=[f"human_confirmed_media_row:{confirmed_row_id}", f"live_revalidated:{now_str}"],
                                baserow_read_at=now_str,
                                live_read_complete=True,
                            ),
                        )
                        self.registry.save_media_db_review(
                            tracking_id=tracking_id,
                            decision=result.decision.value,
                            database_state=result.database_state,
                            snapshot_timestamp=result.database_snapshot_at,
                            result_json=result.model_dump_json(),
                            selected_media_row_id=result.selected_media_row_id,
                            review_required=result.review_required,
                        )
                        return result
            except Exception as e:
                logger.warning(f"Error revalidating confirmed row {confirmed_row_id}: {e}")

        result = self.engine.reconcile(parser_res, snapshot)

        # Persist to local registry
        self.registry.save_media_db_review(
            tracking_id=tracking_id,
            decision=result.decision.value,
            database_state=result.database_state,
            snapshot_timestamp=result.database_snapshot_at,
            result_json=result.model_dump_json(),
            selected_media_row_id=result.selected_media_row_id,
            review_required=result.review_required,
        )

        # Automatically hand safe completed Tool 2 evidence to Renamer Enrich (R-013)
        if auto_enrich and (result.renamer_enrichment.confirmed or result.baserow_check_complete):
            try:
                self.apply_enrichment_to_renamer(tracking_id)
            except Exception as e:
                logger.error(f"Error applying enrichment to renamer for '{tracking_id}': {e}")

        return result

    def review_batch(
        self,
        tracking_ids: Optional[List[str]] = None,
        force_refresh: bool = False,
        auto_enrich: bool = True,
    ) -> List[MediaDatabaseReviewResult]:
        """Execute review across multiple files. Batch continues even if individual items fail."""
        if tracking_ids is None:
            all_files = self.registry.list_files()
            target_ids = [f["tracking_id"] for f in all_files]
        else:
            target_ids = tracking_ids

        results: List[MediaDatabaseReviewResult] = []
        for tid in target_ids:
            try:
                record = self.registry.get_file(tid)
                if not record:
                    continue
                parser_res = ParserResult.model_validate(record["parser_result"])
                # Per-decision live query - no batch-wide snapshot reuse (R-001)
                snapshot = _load_snapshot_for_parser_res(self.provider, parser_res)
                res = self.engine.reconcile(parser_res, snapshot)
                self.registry.save_media_db_review(
                    tracking_id=tid,
                    decision=res.decision.value,
                    database_state=res.database_state,
                    snapshot_timestamp=res.database_snapshot_at,
                    result_json=res.model_dump_json(),
                    selected_media_row_id=res.selected_media_row_id,
                    review_required=res.review_required,
                )
                # Automatically hand safe completed Tool 2 evidence to Renamer Enrich (R-013)
                if auto_enrich and (res.renamer_enrichment.confirmed or res.baserow_check_complete):
                    try:
                        self.apply_enrichment_to_renamer(tid)
                    except Exception as e:
                        logger.error(f"Error applying enrichment to renamer for '{tid}': {e}")
                results.append(res)
            except Exception as e:
                logger.error(f"Error reviewing tracking ID '{tid}': {e}")

        return results

    def apply_human_decision(
        self,
        tracking_id: str,
        action: str,
        media_row_id: Optional[int] = None,
        notes: str = "",
        reviewer: str = "human",
        auto_enrich: bool = True,
        force: bool = False,
    ) -> MediaDatabaseReviewResult:
        """Apply a human confirmation decision from review portal or CLI."""
        stored = self.registry.get_media_db_review(tracking_id)
        if not stored:
            # Run initial review first if not yet performed
            result = self.review_file(tracking_id)
        else:
            result = MediaDatabaseReviewResult.model_validate(stored["result"])

        previous_values = {
            "decision": result.decision.value,
            "selected_media_row_id": result.selected_media_row_id,
            "review_required": result.review_required,
        }

        changes: Dict[str, Any] = {"action": action, "notes": notes}

        if action in ("confirm_existing", "choose_candidate"):
            chosen_id = media_row_id or result.selected_media_row_id
            if not chosen_id:
                raise ValueError("media_row_id is required to confirm existing candidate")

            # Revalidate live state: fetch chosen row live directly from provider
            live_row = None
            if hasattr(self.provider, "fetch_media_row_live"):
                try:
                    res_row = self.provider.fetch_media_row_live(chosen_id)
                    if isinstance(res_row, dict):
                        live_row = res_row
                    elif res_row is None:
                        # Explicitly not found (HTTP 404) in live Baserow -> row was deleted
                        raise RuntimeError(f"Media row ID {chosen_id} no longer exists in Baserow")
                except BaserowUnavailableError as e:
                    raise RuntimeError(f"Cannot confirm Media row {chosen_id}: live database is unavailable: {e}") from e
                except RuntimeError:
                    raise
                except Exception as e:
                    raise RuntimeError(f"Cannot confirm Media row {chosen_id}: live database revalidation failed: {e}") from e

            if not isinstance(live_row, dict) and hasattr(self.provider, "load_snapshot"):
                snapshot = self.provider.load_snapshot()
                if getattr(snapshot, "state", None) in ("UNAVAILABLE", "DATABASE_UNAVAILABLE"):
                    raise RuntimeError(f"Cannot confirm Media row {chosen_id}: live database is unavailable")
                for r in getattr(snapshot, "media_rows", []):
                    if isinstance(r, dict) and r.get("id") == chosen_id:
                        from .baserow_provider import normalize_media_row
                        live_row = normalize_media_row(r)
                        break

            if not live_row:
                raise RuntimeError(f"Media row ID {chosen_id} no longer exists in Baserow")

            # Recompute comparisons against current live row to detect collaborator races
            local_date = result.selected_field_evidence.get("local_date")
            local_what = result.selected_field_evidence.get("local_what")
            local_place = result.selected_field_evidence.get("local_place")
            local_country = result.selected_field_evidence.get("local_country")

            d_st, d_det = _compare_dates(local_date, live_row.get("date"))
            w_st, w_det = _compare_what(local_what, None, live_row.get("what"), live_row.get("title"), live_row.get("category"))
            p_st, p_det = _compare_places(local_place, local_country, live_row.get("place"), live_row.get("country"))

            live_conflicts = []
            if d_st == FieldComparisonState.CONFLICT:
                live_conflicts.append(d_det or "Date conflict with updated live row")
            if w_st == FieldComparisonState.CONFLICT:
                live_conflicts.append(w_det or "WHAT conflict with updated live row")
            if p_st == FieldComparisonState.CONFLICT:
                live_conflicts.append(p_det or "Place conflict with updated live row")

            if live_conflicts:
                raise ValueError(
                    f"Cannot confirm Media row {chosen_id}: live row changed materially and now contradicts evidence: {'; '.join(live_conflicts)}"
                )

            now_str = datetime.now(timezone.utc).isoformat()
            result.decision = ReviewDecision.EXISTING_MEDIA_MATCH
            result.selected_media_row_id = chosen_id
            result.decision_state = f"Human confirmed association with Media row {chosen_id}"
            result.review_required = False
            result.review_required_now = False
            result.review_reasons = []
            result.database_state = "LIVE_CURRENT"
            result.baserow_read_at = now_str
            result.database_snapshot_at = now_str
            result.live_read_complete = True

            title_full = live_row.get("title") or ""
            what_val = compose_what_val(local_what, title_full)

            where_val = None
            if live_row.get("place"):
                where_val = f"{live_row['place']}-{live_row.get('country') or ''}".strip("-")
            elif local_place:
                where_val = f"{local_place}-{local_country or ''}".strip("-")

            result.renamer_enrichment = RenamerEnrichment(
                confirmed=True,
                media_row_id=chosen_id,
                when_val=live_row.get("date") or local_date,
                what_val=what_val,
                title_full=title_full,
                where_val=where_val,
                category=live_row.get("category"),
                source_identifiers=live_row.get("source_ids") or [],
                evidence=[f"human_confirmed_media_row:{chosen_id}", f"reviewer:{reviewer}", f"live_revalidated:{now_str}"],
                baserow_read_at=now_str,
                live_read_complete=True,
            )
            result.proposed_tool4_action = Tool4Action.ENRICH_EXISTING
            result.baserow_check_complete = True
            changes["selected_media_row_id"] = chosen_id

        elif action in ("confirm_new", "confirm_new_force"):
            is_force = force or (action == "confirm_new_force")

            # Revalidate live state before finalizing new media candidate (R-012)
            parser_res = None
            record = self.registry.get_file(tracking_id)
            if record and record.get("parser_result"):
                parser_res = ParserResult.model_validate(record["parser_result"])
            else:
                prop = self.registry.get_proposal(tracking_id)
                if prop and prop.parser_result:
                    parser_res = prop.parser_result

            if not parser_res:
                raise RuntimeError(f"Cannot confirm new media candidate: no parser result found for tracking ID '{tracking_id}'")

            # Check provider live query support
            if not hasattr(self.provider, "load_snapshot") and not hasattr(self.provider, "search_media_candidates_live"):
                if not is_force:
                    raise RuntimeError("Cannot confirm new media candidate: provider does not support live search")

            # 1. Full live candidate retrieval and reconciliation via provider snapshot
            fresh_snapshot = None
            if hasattr(self.provider, "load_snapshot"):
                try:
                    fresh_snapshot = _load_snapshot_for_parser_res(self.provider, parser_res)
                except BaserowUnavailableError as e:
                    if not is_force:
                        raise RuntimeError(f"Cannot confirm new media candidate: live database search is unavailable: {e}") from e
                except Exception as e:
                    if not is_force:
                        raise RuntimeError(f"Cannot confirm new media candidate: live database search failed: {e}") from e

            if isinstance(fresh_snapshot, BaserowSnapshot):
                if fresh_snapshot.state in ("UNAVAILABLE", "DATABASE_UNAVAILABLE") or not fresh_snapshot.complete:
                    if not is_force:
                        raise RuntimeError("Cannot confirm new media candidate: live database search is unavailable")
                else:
                    fresh_result = self.engine.reconcile(parser_res, fresh_snapshot)
                    if fresh_result.decision != ReviewDecision.NEW_MEDIA_CANDIDATE:
                        if fresh_result.candidates:
                            cand_id = fresh_result.candidates[0].media_row_id
                            if not is_force:
                                raise RuntimeError(
                                    f"Cannot confirm new media candidate: live search discovered matching row {cand_id} ({fresh_result.decision.value})"
                                )
                        else:
                            if not is_force:
                                raise RuntimeError(
                                    f"Cannot confirm new media candidate: fresh review evaluated to {fresh_result.decision.value}"
                                )

            # 2. Fallback check for test doubles mocking search_media_candidates_live directly
            elif hasattr(self.provider, "search_media_candidates_live") and not is_force:
                local_date = result.selected_field_evidence.get("local_date") or (parser_res.when.selected_value if parser_res.when else None)
                local_what = result.selected_field_evidence.get("local_what") or (parser_res.what.selected_value if parser_res.what else None)
                try:
                    fresh_candidates = self.provider.search_media_candidates_live(query_text=local_what or local_date or "")
                except BaserowUnavailableError as e:
                    raise RuntimeError(f"Cannot confirm new media candidate: live database search is unavailable: {e}") from e
                except Exception as e:
                    raise RuntimeError(f"Cannot confirm new media candidate: live database search failed: {e}") from e

                if isinstance(fresh_candidates, list):
                    for cand in fresh_candidates:
                        if isinstance(cand, dict):
                            c_date = cand.get("date") or cand.get("Date")
                            c_what = cand.get("what") or cand.get("What")
                            c_title = cand.get("title") or cand.get("Title")
                            if (local_date and c_date == local_date) and (
                                (local_what and c_what == local_what)
                                or (local_what and c_title and local_what in c_title)
                            ):
                                raise RuntimeError(
                                    f"Cannot confirm new media candidate: live search discovered matching row {cand.get('id')}"
                                )

            now_str = datetime.now(timezone.utc).isoformat()
            result.decision = ReviewDecision.NEW_MEDIA_CANDIDATE
            result.selected_media_row_id = None
            if is_force:
                result.decision_state = f"Human forced new media candidate (confirmed by {reviewer}, overrides live check)"
            else:
                result.decision_state = f"Human confirmed new media candidate (confirmed by {reviewer})"
            result.review_required = False
            result.review_required_now = False
            result.review_reasons = []
            result.database_state = "LIVE_CURRENT" if not is_force else result.database_state
            result.baserow_read_at = now_str
            result.database_snapshot_at = now_str
            result.live_read_complete = not is_force
            evidence_tag = f"human_forced_new_media:{tracking_id}" if is_force else f"human_confirmed_new_media:{tracking_id}"
            result.renamer_enrichment = RenamerEnrichment(
                confirmed=False,
                evidence=[evidence_tag, f"reviewer:{reviewer}", f"live_revalidated:{now_str}"],
                baserow_read_at=now_str,
                live_read_complete=not is_force,
            )
            result.proposed_tool4_action = Tool4Action.CREATE_NEW
            result.baserow_check_complete = True

        elif action == "defer":
            if (
                result.decision == ReviewDecision.EXISTING_MEDIA_MATCH
                or (result.renamer_enrichment and result.renamer_enrichment.confirmed)
                or (previous_values.get("decision") == ReviewDecision.EXISTING_MEDIA_MATCH.value)
            ):
                raise ValueError(
                    f"Cannot defer tracking ID '{tracking_id}': media association is already confirmed. "
                    "Contradictory deferral of a confirmed enrichment is not permitted."
                )

            result.decision = ReviewDecision.INSUFFICIENT_EVIDENCE
            result.selected_media_row_id = None
            result.decision_state = f"Deferred by {reviewer}"
            result.review_required = True
            result.review_required_now = True
            result.review_reasons = ["Review deferred by human operator"]
            result.proposed_tool4_action = Tool4Action.NEEDS_REVIEW
            result.baserow_check_complete = False
            result.renamer_enrichment = RenamerEnrichment(
                confirmed=False,
                evidence=[f"deferred_by_{reviewer}:{tracking_id}"],
                baserow_read_at=result.baserow_read_at,
                live_read_complete=result.live_read_complete,
            )

        else:
            raise ValueError(f"Unknown human decision action '{action}'")

        # Update registry
        self.registry.save_media_db_review(
            tracking_id=tracking_id,
            decision=result.decision.value,
            database_state=result.database_state,
            snapshot_timestamp=result.database_snapshot_at,
            result_json=result.model_dump_json(),
            selected_media_row_id=result.selected_media_row_id,
            review_required=result.review_required,
        )

        self.registry.record_review_action(
            tracking_id=tracking_id,
            action=f"media_db_{action}",
            reviewer=reviewer,
            changes=changes,
            previous_values=previous_values,
        )

        # Automatically hand safe completed Tool 2 evidence to Renamer Enrich (R-013)
        if auto_enrich and (result.renamer_enrichment.confirmed or result.baserow_check_complete):
            try:
                self.apply_enrichment_to_renamer(tracking_id)
            except Exception as e:
                logger.error(f"Error applying enrichment to renamer for '{tracking_id}': {e}")

        return result

    def apply_enrichment_to_renamer(
        self,
        tracking_id: str,
        renamer_service: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
        """Feed confirmed Tool 2 enrichment evidence into Renamer Enrich pass."""
        stored = self.registry.get_media_db_review(tracking_id)
        if not stored:
            return None

        review_res = MediaDatabaseReviewResult.model_validate(stored["result"])

        # Never apply enrichment if decision is deferred / insufficient evidence / unavailable (R-014 defense-in-depth)
        if review_res.decision in (ReviewDecision.INSUFFICIENT_EVIDENCE, ReviewDecision.DATABASE_UNAVAILABLE):
            return None

        evidence = None
        if review_res.renamer_enrichment.confirmed:
            enr = review_res.renamer_enrichment
            read_at_info = f" (live read at {enr.baserow_read_at})" if enr.baserow_read_at else ""
            evidence = EnrichmentEvidence(
                tracking_id=tracking_id,
                when_val=enr.when_val,
                what_val=enr.what_val,
                where_val=enr.where_val,
                what_category=enr.category,
                baserow_check_complete=True,
                source_tool="tool_2_media_database_review",
                details=f"Confirmed Baserow Media row {enr.media_row_id}{read_at_info}",
            )
        elif review_res.baserow_check_complete:
            # Complete no-match still marks baserow_check_complete=True
            evidence = EnrichmentEvidence(
                tracking_id=tracking_id,
                baserow_check_complete=True,
                source_tool="tool_2_media_database_review",
                details=f"Complete Baserow review: {review_res.decision.value}",
            )

        if evidence is not None:
            from ..renamer.service import RenamerApplicationService
            service = renamer_service or RenamerApplicationService(registry=self.registry)
            return service.apply_enrichment(evidence)

        return None

    def confirm_existing(
        self,
        tracking_id: str,
        media_row_id: Optional[int] = None,
        notes: str = "",
        reviewer: str = "human",
        auto_enrich: bool = True,
    ) -> MediaDatabaseReviewResult:
        """Convenience method to confirm association with existing media row."""
        return self.apply_human_decision(
            tracking_id=tracking_id,
            action="confirm_existing",
            media_row_id=media_row_id,
            notes=notes,
            reviewer=reviewer,
            auto_enrich=auto_enrich,
        )

    def confirm_new(
        self,
        tracking_id: str,
        notes: str = "",
        reviewer: str = "human",
        auto_enrich: bool = True,
    ) -> MediaDatabaseReviewResult:
        """Convenience method to confirm item as new media candidate."""
        return self.apply_human_decision(
            tracking_id=tracking_id,
            action="confirm_new",
            notes=notes,
            reviewer=reviewer,
            auto_enrich=auto_enrich,
        )

    def resolve_category_title(
        self,
        term_or_title: str,
        snapshot: Optional[BaserowSnapshot] = None,
    ) -> CategoryTitleResolution:
        """Resolve an uncertain title or WHAT term against live Baserow category_title table.

        Performs conservative whole term/phrase matching after punctuation,
        case, and Latin transliteration normalization.
        Prefers a unique, more specific term.
        Does NOT match substrings inside unrelated words (e.g. 'access' must not match 'CC').
        Returns typed CategoryTitleResolution.
        """
        now_str = datetime.now(timezone.utc).isoformat()
        clean_query = to_ascii_latin(str(term_or_title or "")).strip()
        if not clean_query:
            return CategoryTitleResolution(
                status=CategoryTitleResolutionStatus.NO_MATCH,
                read_at=now_str,
                reason="Empty query term",
            )

        # 1. Obtain rows from snapshot or live provider
        table_id = None
        raw_rows = []
        read_at = now_str
        if snapshot is not None:
            raw_rows = snapshot.category_title_rows
            read_at = snapshot.snapshot_at
            table_id = getattr(self.provider, "category_table_id", None)
            if not raw_rows and snapshot.state == "DATABASE_UNAVAILABLE":
                return CategoryTitleResolution(
                    status=CategoryTitleResolutionStatus.DATABASE_UNAVAILABLE,
                    read_at=read_at,
                    table_id=table_id,
                    reason="Baserow database snapshot state is DATABASE_UNAVAILABLE",
                )
        else:
            try:
                raw_rows, read_at, table_id = self.provider.fetch_category_title_rows_live()
            except BaserowUnavailableError as e:
                return CategoryTitleResolution(
                    status=CategoryTitleResolutionStatus.DATABASE_UNAVAILABLE,
                    read_at=now_str,
                    table_id=getattr(self.provider, "category_table_id", None),
                    reason=str(e),
                )
            except Exception as e:
                return CategoryTitleResolution(
                    status=CategoryTitleResolutionStatus.DATABASE_UNAVAILABLE,
                    read_at=now_str,
                    table_id=getattr(self.provider, "category_table_id", None),
                    reason=f"Unexpected error fetching category_title rows: {e}",
                )

        if not raw_rows:
            return CategoryTitleResolution(
                status=CategoryTitleResolutionStatus.DATABASE_UNAVAILABLE,
                read_at=read_at,
                table_id=table_id,
                reason="Live category_title table returned 0 rows or is unpopulated",
            )

        # 2. Normalize rows
        norm_rows = [normalize_category_title_row(r) for r in raw_rows]

        # 3. Match terms
        query_lower = clean_query.lower()
        matches = []
        for row in norm_rows:
            cat_name = row.get("category")
            if not cat_name:
                continue
            for raw_term in row.get("title_matching_terms", []):
                norm_term = to_ascii_latin(raw_term).strip().lower()
                if not norm_term:
                    continue

                words = [re.escape(w) for w in re.split(r"[\s_.\-]+", norm_term) if w]
                if not words:
                    continue
                term_pattern_str = r"[\s_.\-]+".join(words)
                pattern = rf"(?:^|[\s_.\-,/()\[\]]){term_pattern_str}(?=[_.\s\-,/()\[\]]|$)"
                m = re.search(pattern, query_lower)
                if m:
                    matches.append({
                        "row_id": row["id"],
                        "category": cat_name,
                        "matched_term": raw_term,
                        "term_len": len(norm_term),
                        "span": m.span(),
                    })

        if not matches:
            return CategoryTitleResolution(
                status=CategoryTitleResolutionStatus.NO_MATCH,
                read_at=read_at,
                table_id=table_id,
                reason=f"No category_title terms matched query '{term_or_title}'",
            )

        # 4. Group by category and find maximum specificity (term length)
        max_len = max(m["term_len"] for m in matches)
        top_matches = [m for m in matches if m["term_len"] == max_len]

        unique_cats = list(dict.fromkeys(m["category"] for m in top_matches))
        if len(unique_cats) > 1:
            return CategoryTitleResolution(
                status=CategoryTitleResolutionStatus.AMBIGUOUS,
                read_at=read_at,
                table_id=table_id,
                reason=f"Ambiguous category_title matches for '{term_or_title}': multiple categories matched with equal specificity ({unique_cats})",
            )

        best = top_matches[0]
        return CategoryTitleResolution(
            status=CategoryTitleResolutionStatus.MATCHED,
            matched_row_id=best["row_id"],
            matched_term=best["matched_term"],
            category=best["category"],
            read_at=read_at,
            table_id=table_id,
        )


