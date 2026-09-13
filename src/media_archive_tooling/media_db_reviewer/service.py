"""Application service coordinating Tool 2 review, persistence, and Renamer integration."""
import json
import logging
from typing import Any, Dict, List, Optional

from ..renamer.models import EnrichmentEvidence, ParserResult
from ..renamer.registry.registry import LocalRegistry
from .baserow_provider import BaserowSnapshotProvider
from .engine import MediaDatabaseReconciliationEngine
from .models import (
    MediaDatabaseReviewResult,
    RenamerEnrichment,
    ReviewDecision,
    Tool4Action,
)

logger = logging.getLogger(__name__)


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

    def review_file(self, tracking_id: str, force_refresh: bool = False) -> MediaDatabaseReviewResult:
        """Execute Media database review on one registered file."""
        record = self.registry.get_file(tracking_id)
        if not record:
            raise ValueError(f"Tracking ID '{tracking_id}' not found in registry")

        parser_res = ParserResult.model_validate(record["parser_result"])
        snapshot = self.provider.load_snapshot(force_refresh=force_refresh)

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

        return result

    def review_batch(
        self,
        tracking_ids: Optional[List[str]] = None,
        force_refresh: bool = False,
    ) -> List[MediaDatabaseReviewResult]:
        """Execute review across multiple files. Batch continues even if individual items fail."""
        if tracking_ids is None:
            all_files = self.registry.list_files()
            target_ids = [f["tracking_id"] for f in all_files]
        else:
            target_ids = tracking_ids

        # Ensure snapshot loaded once for the whole batch
        snapshot = self.provider.load_snapshot(force_refresh=force_refresh)

        results: List[MediaDatabaseReviewResult] = []
        for tid in target_ids:
            try:
                record = self.registry.get_file(tid)
                if not record:
                    continue
                parser_res = ParserResult.model_validate(record["parser_result"])
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

            # Find chosen candidate in result.candidates or snapshot
            candidate_row: Optional[Dict[str, Any]] = None
            for c in result.candidates:
                if c.media_row_id == chosen_id:
                    candidate_row = c.normalized_row
                    break

            if not candidate_row:
                snapshot = self.provider.load_snapshot()
                for r in snapshot.media_rows:
                    if r.get("id") == chosen_id:
                        from .baserow_provider import normalize_media_row
                        candidate_row = normalize_media_row(r)
                        break

            if not candidate_row:
                raise ValueError(f"Media row ID {chosen_id} not found in candidates or snapshot")

            result.decision = ReviewDecision.EXISTING_MEDIA_MATCH
            result.selected_media_row_id = chosen_id
            result.decision_state = f"Human confirmed association with Media row {chosen_id}"
            result.review_required = False
            result.review_reasons = []

            title_full = candidate_row.get("title") or ""
            local_what = result.selected_field_evidence.get("local_what")
            what_val = None
            if title_full and local_what:
                what_val = f"{local_what}-{title_full}"
            elif title_full:
                what_val = title_full
            elif local_what:
                what_val = local_what

            where_val = None
            if candidate_row.get("place"):
                where_val = f"{candidate_row['place']}-{candidate_row.get('country') or ''}".strip("-")

            result.renamer_enrichment = RenamerEnrichment(
                confirmed=True,
                media_row_id=chosen_id,
                when_val=candidate_row.get("date") or result.selected_field_evidence.get("local_date"),
                what_val=what_val,
                title_full=title_full,
                where_val=where_val,
                category=candidate_row.get("category"),
                source_identifiers=candidate_row.get("source_ids") or [],
                evidence=[f"human_confirmed_media_row:{chosen_id}", f"reviewer:{reviewer}"],
            )
            result.proposed_tool4_action = Tool4Action.ENRICH_EXISTING
            result.baserow_check_complete = True
            changes["selected_media_row_id"] = chosen_id

        elif action == "confirm_new":
            result.decision = ReviewDecision.NEW_MEDIA_CANDIDATE
            result.selected_media_row_id = None
            result.decision_state = f"Human confirmed new media candidate (confirmed by {reviewer})"
            result.review_required = False
            result.review_reasons = []
            result.renamer_enrichment = RenamerEnrichment(
                confirmed=False,
                evidence=[f"human_confirmed_new_media:{tracking_id}", f"reviewer:{reviewer}"],
            )
            result.proposed_tool4_action = Tool4Action.CREATE_NEW
            result.baserow_check_complete = True

        elif action == "defer":
            result.decision = ReviewDecision.INSUFFICIENT_EVIDENCE
            result.decision_state = f"Deferred by {reviewer}"
            result.review_required = True
            result.review_reasons = ["Review deferred by human operator"]
            result.proposed_tool4_action = Tool4Action.NEEDS_REVIEW
            result.baserow_check_complete = False

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

        evidence = None
        if review_res.renamer_enrichment.confirmed:
            enr = review_res.renamer_enrichment
            evidence = EnrichmentEvidence(
                tracking_id=tracking_id,
                when_val=enr.when_val,
                what_val=enr.what_val,
                where_val=enr.where_val,
                what_category=enr.category,
                baserow_check_complete=True,
                source_tool="tool_2_media_database_review",
                details=f"Confirmed Baserow Media row {enr.media_row_id}",
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
