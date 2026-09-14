"""Local immutable reference store and bootstrap manager for travel_schedule."""
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, List, Optional

from ..media_db_reviewer.baserow_provider import (
    BaserowSnapshotProvider,
    BaserowUnavailableError,
    normalize_travel_schedule_row,
)
from ..media_db_reviewer.engine import _norm_country
from .models import NormalizedTravelRow, TravelScheduleManifest

logger = logging.getLogger(__name__)

DEFAULT_REFERENCE_PATH = Path(".renamer/reference/travel_schedule.json")


def compute_canonical_sha256(rows: List[NormalizedTravelRow]) -> str:
    """Compute deterministic SHA-256 over normalized rows, excluding volatile fields."""
    sorted_rows = sorted(rows, key=lambda r: r.id)
    canonical_data = [
        {
            "id": r.id,
            "start_date": r.start_date,
            "end_date": r.end_date,
            "place": r.place,
            "country": r.country,
            "schedule_text": r.schedule_text,
        }
        for r in sorted_rows
    ]
    payload = json.dumps(canonical_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def normalize_to_travel_row(raw_row: Dict[str, Any]) -> NormalizedTravelRow:
    """Convert raw Baserow travel_schedule dictionary to NormalizedTravelRow."""
    norm = normalize_travel_schedule_row(raw_row)
    country = norm.get("country", "")
    country_iso = _norm_country(country) if country else None
    return NormalizedTravelRow(
        id=norm["id"],
        start_date=norm["start_date"],
        end_date=norm["end_date"],
        place=norm["place"],
        country=country,
        country_iso2=country_iso.lower() if country_iso and len(country_iso) == 2 else None,
        schedule_text=norm["schedule_text"],
        raw_row=raw_row,
    )


class TravelReferenceStore:
    """Manages the local immutable travel_schedule reference file with integrity validation."""

    def __init__(
        self,
        reference_path: Optional[Path] = None,
        provider: Optional[BaserowSnapshotProvider] = None,
    ):
        self.reference_path = Path(reference_path) if reference_path else DEFAULT_REFERENCE_PATH
        self.provider = provider

    def load_reference(self) -> Optional[TravelScheduleManifest]:
        """Load and verify the local immutable reference JSON file without network access.

        Returns None if missing or if checksum / content fails verification.
        """
        if not self.reference_path.exists():
            return None

        try:
            with open(self.reference_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            manifest = TravelScheduleManifest.model_validate(data)
            if not manifest.complete:
                logger.warning(f"Reference file at {self.reference_path} is marked incomplete")
                return None

            calculated_sha = compute_canonical_sha256(manifest.normalized_rows)
            if calculated_sha != manifest.canonical_sha256:
                logger.warning(
                    f"Integrity check failed for {self.reference_path}: "
                    f"stored {manifest.canonical_sha256} != computed {calculated_sha}"
                )
                return None

            if len(manifest.normalized_rows) != manifest.row_count:
                logger.warning(
                    f"Row count mismatch in {self.reference_path}: "
                    f"stored {manifest.row_count} != actual {len(manifest.normalized_rows)}"
                )
                return None

            return manifest
        except Exception as e:
            logger.warning(f"Error loading reference from {self.reference_path}: {e}")
            return None

    def save_reference(self, manifest: TravelScheduleManifest) -> None:
        """Atomically persist manifest to disk."""
        self.reference_path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write via tempfile in same directory
        temp_dir = self.reference_path.parent
        with tempfile.NamedTemporaryFile("w", dir=temp_dir, delete=False, encoding="utf-8") as tf:
            tf.write(manifest.model_dump_json(indent=2))
            temp_name = tf.name

        os.replace(temp_name, str(self.reference_path))

    def bootstrap_from_provider(self) -> TravelScheduleManifest:
        """Fetch entire table from Baserow via provider and atomically persist reference."""
        if not self.provider:
            raise BaserowUnavailableError("No Baserow provider configured for schedule bootstrap")

        raw_rows = self.provider.fetch_all_travel_schedule_rows()
        normalized_rows = [normalize_to_travel_row(r) for r in raw_rows]
        sha256_hash = compute_canonical_sha256(normalized_rows)

        manifest = TravelScheduleManifest(
            format_version="1.0",
            source_table_id=str(self.provider.travel_schedule_table_id or ""),
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            complete=True,
            row_count=len(normalized_rows),
            canonical_sha256=sha256_hash,
            normalized_rows=normalized_rows,
        )

        self.save_reference(manifest)

        # Reload and verify before returning
        loaded = self.load_reference()
        if not loaded:
            raise RuntimeError(f"Failed to reload and verify newly written reference at {self.reference_path}")
        return loaded

    def ensure_reference(self) -> TravelScheduleManifest:
        """Return a verified reference, bootstrapping from provider only if missing or corrupt.

        Never replaces an existing verified reference.
        """
        manifest = self.load_reference()
        if manifest is not None:
            return manifest

        return self.bootstrap_from_provider()

    def accept_remote_reference(self, expected_remote_sha: Optional[str] = None) -> TravelScheduleManifest:
        """Explicit administrative replacement: verify remote table and deliberately accept new reference."""
        verification = self.verify_remote_reference()
        if verification["matches"]:
            logger.info("Remote reference matches local reference; no update needed")
            manifest = self.load_reference()
            if manifest is not None:
                return manifest

        if expected_remote_sha and verification["remote_sha256"] != expected_remote_sha:
            raise ValueError(
                f"Remote SHA256 {verification['remote_sha256']} does not match expected {expected_remote_sha}"
            )

        logger.info(f"Deliberately accepting unexpected remote reference change: {verification['remote_sha256']}")
        return self.bootstrap_from_provider()

    def verify_remote_reference(self) -> Dict[str, Any]:
        """Admin check: compare current remote Baserow table with local reference.

        Never silently replaces local reference.
        """
        local = self.load_reference()
        if not local:
            raise RuntimeError("Local reference is unavailable or corrupt; cannot compare")

        if not self.provider:
            raise BaserowUnavailableError("No Baserow provider configured for remote verification")

        raw_rows = self.provider.fetch_all_travel_schedule_rows()
        remote_normalized = [normalize_to_travel_row(r) for r in raw_rows]
        remote_sha = compute_canonical_sha256(remote_normalized)

        matches = (local.canonical_sha256 == remote_sha)
        return {
            "matches": matches,
            "local_sha256": local.canonical_sha256,
            "remote_sha256": remote_sha,
            "local_row_count": local.row_count,
            "remote_row_count": len(remote_normalized),
            "unexpected_change": not matches,
        }
