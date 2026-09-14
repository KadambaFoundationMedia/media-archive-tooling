import inspect
from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional

from ..renamer.models import EnrichmentEvidence, ParserResult
from ..renamer.registry.registry import LocalRegistry
from .baserow_provider import BaserowSnapshotProvider, BaserowUnavailableError
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

        elif action == "confirm_new":
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
                raise RuntimeError("Cannot confirm new media candidate: provider does not support live search")

            # 1. Full live candidate retrieval and reconciliation via provider snapshot
            fresh_snapshot = None
            if hasattr(self.provider, "load_snapshot"):
                try:
                    fresh_snapshot = _load_snapshot_for_parser_res(self.provider, parser_res)
                except BaserowUnavailableError as e:
                    raise RuntimeError(f"Cannot confirm new media candidate: live database search is unavailable: {e}") from e
                except Exception as e:
                    raise RuntimeError(f"Cannot confirm new media candidate: live database search failed: {e}") from e

            if isinstance(fresh_snapshot, BaserowSnapshot):
                if fresh_snapshot.state in ("UNAVAILABLE", "DATABASE_UNAVAILABLE") or not fresh_snapshot.complete:
                    raise RuntimeError("Cannot confirm new media candidate: live database search is unavailable")

                fresh_result = self.engine.reconcile(parser_res, fresh_snapshot)
                if fresh_result.decision != ReviewDecision.NEW_MEDIA_CANDIDATE:
                    if fresh_result.candidates:
                        cand_id = fresh_result.candidates[0].media_row_id
                        raise RuntimeError(
                            f"Cannot confirm new media candidate: live search discovered matching row {cand_id} ({fresh_result.decision.value})"
                        )
                    else:
                        raise RuntimeError(
                            f"Cannot confirm new media candidate: fresh review evaluated to {fresh_result.decision.value}"
                        )

            # 2. Fallback check for test doubles mocking search_media_candidates_live directly
            elif hasattr(self.provider, "search_media_candidates_live"):
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
            result.decision_state = f"Human confirmed new media candidate (confirmed by {reviewer})"
            result.review_required = False
            result.review_required_now = False
            result.review_reasons = []
            result.database_state = "LIVE_CURRENT"
            result.baserow_read_at = now_str
            result.database_snapshot_at = now_str
            result.live_read_complete = True
            result.renamer_enrichment = RenamerEnrichment(
                confirmed=False,
                evidence=[f"human_confirmed_new_media:{tracking_id}", f"reviewer:{reviewer}", f"live_revalidated:{now_str}"],
                baserow_read_at=now_str,
                live_read_complete=True,
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

