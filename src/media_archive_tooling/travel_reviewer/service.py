"""Application service for Tool 3 — Travel Schedule Reviewer."""
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from ..config import AppConfig, load_config
from ..media_db_reviewer.baserow_provider import (
    BaserowSnapshotProvider,
    BaserowUnavailableError,
)
from ..media_db_reviewer.models import MediaDatabaseReviewResult
from ..renamer.models import EnrichmentEvidence, ParserResult, ResolutionState
from ..renamer.registry.registry import LocalRegistry
from ..renamer.service import RenamerApplicationService
from .engine import TravelScheduleEngine
from .models import (
    TravelCandidate,
    TravelRenamerEnrichment,
    TravelReviewDecision,
    TravelReviewResult,
    TravelScheduleManifest,
)
from .reference_store import TravelReferenceStore

logger = logging.getLogger(__name__)


def validate_tool3_review_result(
    rev: Any, tracking_id: str
) -> Tuple[Optional[TravelReviewResult], Optional[str]]:
    """Strictly validate that Tool 3 returned a typed, valid TravelReviewResult contract.

    Enforces R-040:
    - Object must be an instance of TravelReviewResult (or validatable via model).
    - tracking_id must match.
    - decision must be present and a recognized TravelReviewDecision member.
    - Returns (result_model, error_message). If invalid contract, returns (None, err_msg).
    """
    if rev is None:
        return None, "Tool 3 returned no review result (None)"

    result_model: Optional[TravelReviewResult] = None
    if isinstance(rev, TravelReviewResult):
        result_model = rev
    elif isinstance(rev, dict):
        try:
            result_model = TravelReviewResult.model_validate(rev)
        except Exception as e:
            return None, f"Tool 3 review dictionary failed TravelReviewResult contract validation: {e}"
    else:
        if hasattr(rev, "model_dump"):
            try:
                result_model = TravelReviewResult.model_validate(rev.model_dump())
            except Exception as e:
                return None, f"Tool 3 result object failed TravelReviewResult contract: {e}"
        else:
            return None, f"Tool 3 result is of type {type(rev).__name__}, not TravelReviewResult"

    if result_model.tracking_id != tracking_id:
        return None, f"Tool 3 result tracking_id '{result_model.tracking_id}' does not match expected '{tracking_id}'"

    dec = getattr(result_model, "decision", None)
    if dec is None:
        return None, "Tool 3 review decision is missing or None"

    if isinstance(dec, TravelReviewDecision):
        dec_enum = dec
    else:
        try:
            dec_enum = TravelReviewDecision(str(dec).strip())
        except ValueError:
            return None, f"Tool 3 decision '{dec}' is not a recognized TravelReviewDecision"

    result_model.decision = dec_enum
    return result_model, None


class TravelScheduleReviewService:
    """Service coordinating travel schedule reference loading, review evaluation, and Renamer handoff."""

    def __init__(
        self,
        registry: Optional[LocalRegistry] = None,
        reference_store: Optional[TravelReferenceStore] = None,
        renamer_service: Optional[RenamerApplicationService] = None,
        media_db_service: Optional[Any] = None,
        config: Optional[AppConfig] = None,
    ):
        self.config = config or load_config()
        self.registry = registry or LocalRegistry(self.config.registry_path)

        if reference_store:
            self.reference_store = reference_store
        else:
            provider = BaserowSnapshotProvider(
                api_url=self.config.baserow_api_url,
                api_token=self.config.baserow_api_token,
                media_table_id=self.config.baserow_media_table_id,
                category_table_id=self.config.baserow_category_table_id,
                travel_schedule_table_id=self.config.baserow_travel_schedule_table_id,
            )
            self.reference_store = TravelReferenceStore(
                provider=provider,
            )

        self.renamer_service = renamer_service or RenamerApplicationService(
            registry=self.registry,
        )
        self.media_db_service = media_db_service
        if not self.media_db_service and hasattr(self.reference_store, "provider") and self.reference_store.provider:
            p = self.reference_store.provider
            if getattr(p, "media_table_id", None):
                try:
                    from ..media_db_reviewer.service import MediaDatabaseReviewService
                    self.media_db_service = MediaDatabaseReviewService(
                        registry=self.registry,
                        provider=p,
                    )
                except Exception:
                    self.media_db_service = None

        self._engine: Optional[TravelScheduleEngine] = None
        self._cached_checksum: Optional[str] = None

    def ensure_reference(self) -> TravelScheduleManifest:
        """Ensure verified local travel schedule reference exists and return it."""
        return self.reference_store.ensure_reference()

    def verify_reference(self) -> Dict[str, Any]:
        """Admin check: compare current remote Baserow table with local reference."""
        return self.reference_store.verify_remote_reference()

    def get_engine(self) -> Optional[TravelScheduleEngine]:
        """Get or create cached TravelScheduleEngine backed by verified reference."""
        try:
            manifest = self.ensure_reference()
            if self._engine is None or self._cached_checksum != manifest.canonical_sha256:
                self._engine = TravelScheduleEngine(manifest)
                self._cached_checksum = manifest.canonical_sha256
            return self._engine
        except Exception as e:
            logger.warning(f"Could not load travel schedule reference engine: {e}")
            return None

    def review_file(
        self,
        target: Union[str, ParserResult],
        tool2_context: Optional[Any] = None,
        auto_enrich: bool = True,
    ) -> TravelReviewResult:
        """Review a single media file against travel schedule reference.

        Args:
            target: tracking_id string or ParserResult instance.
            tool2_context: MediaDatabaseReviewResult or dict if available from Tool 2.
            auto_enrich: whether to automatically apply provisional enrichment to Tool 1 Renamer.
        """
        tracking_id = target.identity.tracking_id if isinstance(target, ParserResult) else target
        parser_res = target if isinstance(target, ParserResult) else None

        if parser_res is None:
            record = self.registry.get_file(tracking_id)
            if not record:
                raise ValueError(f"Tracking ID '{tracking_id}' not found in registry")
            parser_res = ParserResult.model_validate(record["parser_result"])

        # If tool2_context is not explicitly passed, attempt to obtain current live Media context
        if tool2_context is None and self.media_db_service:
            try:
                tool2_context = self.media_db_service.review_file(
                    tracking_id=tracking_id,
                    auto_enrich=False,
                )
            except Exception as e:
                logger.debug(f"Could not obtain current Tool 2 Media context for {tracking_id}: {e}")
                tool2_context = None

        # Attempt to get engine
        engine = self.get_engine()
        if not engine:
            res = TravelReviewResult(
                tracking_id=tracking_id,
                decision=TravelReviewDecision.REFERENCE_UNAVAILABLE,
                input_when_val=parser_res.when.selected_value if parser_res.when else None,
                input_when_state=parser_res.when.state.value if parser_res.when else None,
                input_where_val=f"{parser_res.where.place_location or ''}-{parser_res.where.country_iso2 or ''}".strip("-") if parser_res.where else None,
                input_where_state=parser_res.where.state.value if parser_res.where else None,
                diagnostic_notes=["Travel schedule reference is unavailable or failed integrity check"],
            )
            self._persist_review(res, applied_enrichment=False)
            return res

        # Run engine evaluation
        res = engine.evaluate(parser_res, tool2_context=tool2_context)

        # Automatic Renamer Handoff: only for safe unique PROVISIONAL_ENRICHMENT
        applied = False
        if auto_enrich and res.decision == TravelReviewDecision.PROVISIONAL_ENRICHMENT and res.provisional_enrichment:
            ev = res.provisional_enrichment
            enrichment_payload = EnrichmentEvidence(
                tracking_id=tracking_id,
                when_val=ev.when_val,
                when_state=ev.when_state,
                where_val=ev.where_val,
                where_state=ev.where_state,
                source_tool=ev.source_tool,
                details="; ".join(ev.evidence),
            )
            try:
                self.renamer_service.apply_enrichment(enrichment_payload)
                applied = True
            except Exception as e:
                logger.warning(f"Failed to auto-apply travel schedule enrichment to file {tracking_id}: {e}")
                res.diagnostic_notes.append(f"Auto-enrichment error: {e}")

        # Persist review result to registry
        self._persist_review(res, applied_enrichment=applied)
        return res

    def review_batch(
        self,
        tracking_ids: Optional[List[str]] = None,
        auto_enrich: bool = True,
    ) -> List[TravelReviewResult]:
        """Review multiple files in batch, isolating individual failures."""
        if tracking_ids is None:
            files = self.registry.list_files()
            tracking_ids = [f["tracking_id"] for f in files]

        results = []
        for tid in tracking_ids:
            try:
                r = self.review_file(
                    target=tid,
                    auto_enrich=auto_enrich,
                )
                results.append(r)
            except Exception as e:
                logger.error(f"Batch travel review failed for tracking_id {tid}: {e}")
                ref_checksum = ""
                ref_row_count = 0
                try:
                    engine = self.get_engine()
                    if engine is not None:
                        ref_checksum = engine.manifest.canonical_sha256
                        ref_row_count = engine.manifest.row_count
                except Exception:
                    pass

                err_res = TravelReviewResult(
                    tracking_id=tid,
                    decision=TravelReviewDecision.PROCESSING_ERROR,
                    reference_checksum=ref_checksum,
                    reference_row_count=ref_row_count,
                    diagnostic_notes=[f"Batch item processing error for {tid}: {e}"],
                    review_required=True,
                    review_reasons=[f"Processing error: {e}"],
                )
                self._persist_review(err_res, applied_enrichment=False)
                results.append(err_res)
        return results

    def search_by_when(self, date_str: str) -> List[TravelCandidate]:
        """Diagnostic helper to search schedule candidates by ISO date."""
        engine = self.get_engine()
        if not engine:
            return []
        rows = engine.index.get_rows_by_date(date_str)
        from .engine import group_candidates_semantically
        return group_candidates_semantically(rows, index=engine.index)

    def search_by_where(self, place_str: str) -> List[TravelCandidate]:
        """Diagnostic helper to search schedule candidates by place."""
        engine = self.get_engine()
        if not engine:
            return []
        rows = engine.index.get_rows_by_place(place_str)
        from .engine import group_candidates_semantically
        return group_candidates_semantically(rows, index=engine.index)

    def get_stored_review(self, tracking_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve previously persisted Tool 3 review from local registry."""
        return self.registry.get_travel_review(tracking_id)

    def _persist_review(self, res: TravelReviewResult, applied_enrichment: bool):
        try:
            self.registry.save_travel_review(
                tracking_id=res.tracking_id,
                decision=res.decision.value,
                reference_checksum=res.reference_checksum,
                reference_row_count=res.reference_row_count,
                selected_row_ids=res.selected_schedule_row_ids,
                result_json=res.model_dump_json(),
                applied_enrichment=applied_enrichment,
                tool2_decision=res.tool2_decision,
            )
        except Exception as e:
            logger.warning(f"Could not persist travel review for {res.tracking_id}: {e}")
