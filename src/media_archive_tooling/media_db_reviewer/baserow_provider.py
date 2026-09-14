"""Read-only Baserow snapshot and schema provider for Tool 2 (Build Plan Sections 4-7)."""
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import httpx

from ..adapters.baserow import (
    _baserow_single_text,
    _baserow_text_values,
    _row_value,
)
from ..common.ascii_latin import to_ascii_latin
from .models import BaserowSnapshot

logger = logging.getLogger(__name__)


def normalize_media_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Project a raw Baserow Media row into canonical normalized semantic fields."""
    row_id = row.get("id") or 0

    # 1. Date / WHEN
    raw_date = _baserow_single_text(_row_value(
        row, "Date", "date", "Recording Date", "when", "When", "recording_date"
    ))
    # Standardize YYYY-MM-DD
    norm_date = raw_date.replace("/", "-").strip() if raw_date else ""

    # 2. Title
    title = _baserow_single_text(_row_value(
        row, "Title", "title", "Media Title", "media_title", "Topic", "topic"
    ))

    # 3. Specific WHAT / scripture reference
    what_ref = _baserow_single_text(_row_value(
        row, "What", "what", "Scripture", "scripture", "Verse", "verse", "Content Title", "category_title"
    ))

    # 4. Category
    category = _baserow_single_text(_row_value(row, "Category", "category"))

    # 5. Place / location
    place = _baserow_single_text(_row_value(
        row, "Place, location", "place_location", "Place", "place", "Location", "location", "City", "city"
    ))

    # 6. Country
    country = _baserow_single_text(_row_value(row, "Country", "country"))

    # 7. Filename / source filename
    filename = _baserow_single_text(_row_value(
        row, "Filename", "filename", "Source filename", "source_filename", "Original Filename", "original_filename"
    ))

    # 8. Source identifiers
    source_ids: List[str] = []
    for fld in ("Source ID", "source_id", "Audio ID", "audio_id", "ID", "Tracking ID", "tracking_id"):
        vals = _baserow_text_values(_row_value(row, fld))
        for v in vals:
            if v and v not in source_ids:
                source_ids.append(v)

    # 9. URLs
    urls: List[str] = []
    for fld in ("URL", "url", "Link", "link", "Youtube URL", "youtube_url", "Media URL"):
        vals = _baserow_text_values(_row_value(row, fld))
        for u in vals:
            if u and u not in urls:
                urls.append(u)

    # 10. Attachments / files
    attachments: List[str] = []
    raw_files = row.get("Files") or row.get("Attachments") or row.get("files")
    if isinstance(raw_files, list):
        for item in raw_files:
            if isinstance(item, dict):
                name = item.get("name") or item.get("visible_name")
                if name:
                    attachments.append(name)
            elif isinstance(item, str):
                attachments.append(item)

    # 11. Format / media type
    format_val = _baserow_single_text(_row_value(row, "Format", "format", "Media Type", "media_type", "type"))

    # 12. Fact-checked / confirmed indicator
    confirmed_raw = _row_value(row, "Fact Checked", "fact_checked", "Confirmed", "confirmed", "Verified", "verified", "Status", "status")
    fact_checked = False
    if isinstance(confirmed_raw, bool):
        fact_checked = confirmed_raw
    elif isinstance(confirmed_raw, str):
        fact_checked = confirmed_raw.lower() in ("true", "yes", "confirmed", "verified", "checked")

    return {
        "id": row_id,
        "date": norm_date,
        "title": title,
        "what": what_ref,
        "category": category,
        "place": place,
        "country": country,
        "filename": filename,
        "source_ids": source_ids,
        "urls": urls,
        "attachments": attachments,
        "format": format_val,
        "fact_checked": fact_checked,
    }


def normalize_category_title_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a category_title reference row."""
    cat = _baserow_single_text(_row_value(row, "category", "Category"))
    terms_raw = _baserow_text_values(_row_value(row, "title_matching_terms", "Title matching terms", "matching_terms"))
    terms: List[str] = []
    for item in terms_raw:
        terms.extend([t.strip() for t in item.split(",") if t.strip()])

    folder_path = _baserow_single_text(_row_value(row, "folder_path", "Folder path")) or cat
    color = _baserow_single_text(_row_value(row, "color", "Color")) or "#7f8c8d"

    return {
        "id": row.get("id") or 0,
        "category": cat,
        "title_matching_terms": list(dict.fromkeys(terms)),
        "folder_path": folder_path,
        "color": color,
    }


def normalize_travel_schedule_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a travel_schedule row for supporting context."""
    start_date = _baserow_single_text(_row_value(row, "Start Date", "start_date", "Date", "date", "when", "When"))
    end_date = _baserow_single_text(_row_value(row, "End Date", "end_date"))
    place = _baserow_single_text(_row_value(row, "Place", "place", "Location", "location", "City", "city"))
    country = _baserow_single_text(_row_value(row, "Country", "country"))
    text = _baserow_single_text(_row_value(row, "Schedule text", "schedule_text", "Notes", "notes", "Event", "event"))

    return {
        "id": row.get("id") or 0,
        "start_date": start_date.replace("/", "-").strip() if start_date else "",
        "end_date": end_date.replace("/", "-").strip() if end_date else "",
        "place": place,
        "country": country,
        "schedule_text": text,
    }


class BaserowSnapshotProvider:
    """Read-only live query provider managing live fetching, schema normalization, and audit persistence."""

    def __init__(
        self,
        api_url: str = "https://api.baserow.io",
        api_token: Optional[str] = None,
        media_table_id: Optional[str] = None,
        category_table_id: Optional[str] = None,
        travel_schedule_table_id: Optional[str] = None,
        snapshot_path: Optional[Path] = None,
        initial_snapshot: Optional[BaserowSnapshot] = None,
    ):
        self.api_url = api_url.rstrip("/")
        self.api_token = api_token
        self.media_table_id = media_table_id
        self.category_table_id = category_table_id
        self.travel_schedule_table_id = travel_schedule_table_id
        self.snapshot_path = snapshot_path or Path(".renamer/baserow_snapshot.json")
        self._injected_snapshot = initial_snapshot
        self._current_snapshot: Optional[BaserowSnapshot] = None

    def load_snapshot(self, force_refresh: bool = False) -> BaserowSnapshot:
        """Obtain current Baserow state for decision-making.

        In accordance with the Live Baserow Policy, decisions must be made from
        successful live queries. Stale cached data is never used as an operational substitute.
        """
        if self._injected_snapshot is not None:
            return self._injected_snapshot

        if not force_refresh and self._current_snapshot is not None:
            return self._current_snapshot

        # Query live Baserow if credentials exist
        if self.api_token and (self.media_table_id or self.category_table_id):
            try:
                live_snapshot = self._fetch_live_snapshot()
                self._save_audit_snapshot(live_snapshot)
                self._current_snapshot = live_snapshot
                return live_snapshot
            except Exception as e:
                logger.warning(f"Live Baserow query failed: {e}. Live authority disallows stale cache fallback.")

        # Live query unavailable or no token: return DATABASE_UNAVAILABLE
        unavailable_snapshot = BaserowSnapshot(
            snapshot_at=datetime.now(timezone.utc).isoformat(),
            state="DATABASE_UNAVAILABLE",
            complete=False,
            media_rows=[],
            category_title_rows=[],
            travel_schedule_rows=[],
        )
        self._current_snapshot = unavailable_snapshot
        return unavailable_snapshot

    def fetch_media_row_live(self, row_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a single Media row directly from live Baserow for revalidation."""
        if self._injected_snapshot is not None:
            for r in self._injected_snapshot.media_rows:
                if r.get("id") == row_id:
                    return normalize_media_row(r)
            return None

        if not (self.api_token and self.media_table_id):
            return None

        headers = {"Authorization": f"Token {self.api_token}"}
        url = f"{self.api_url}/api/database/rows/table/{self.media_table_id}/{row_id}/?user_field_names=true"
        try:
            with httpx.Client(timeout=15.0, follow_redirects=True) as client:
                resp = client.get(url, headers=headers)
                if resp.status_code == 200:
                    return normalize_media_row(resp.json())
                elif resp.status_code == 404:
                    return None
                else:
                    logger.warning(f"Baserow row {row_id} fetch failed: HTTP {resp.status_code}")
                    return None
        except Exception as e:
            logger.warning(f"Error fetching live Baserow row {row_id}: {e}")
            return None

    def search_media_candidates_live(self, query_text: Optional[str] = None) -> List[Dict[str, Any]]:
        """Perform a targeted live search for media candidates."""
        if self._injected_snapshot is not None:
            if not query_text:
                return [normalize_media_row(r) for r in self._injected_snapshot.media_rows]
            results = []
            q_lower = query_text.lower()
            for r in self._injected_snapshot.media_rows:
                norm = normalize_media_row(r)
                if (
                    q_lower in (norm["title"] or "").lower()
                    or q_lower in (norm["what"] or "").lower()
                    or q_lower in (norm["filename"] or "").lower()
                    or any(q_lower in sid.lower() for sid in norm["source_ids"])
                ):
                    results.append(norm)
            return results

        if not (self.api_token and self.media_table_id):
            return []

        headers = {"Authorization": f"Token {self.api_token}"}
        url = f"{self.api_url}/api/database/rows/table/{self.media_table_id}/?user_field_names=true&size=100"
        if query_text:
            import urllib.parse
            url += f"&search={urllib.parse.quote(query_text)}"

        try:
            with httpx.Client(timeout=15.0, follow_redirects=True) as client:
                resp = client.get(url, headers=headers)
                if resp.status_code == 200:
                    return [normalize_media_row(r) for r in resp.json().get("results", [])]
                return []
        except Exception as e:
            logger.warning(f"Error querying live candidates: {e}")
            return []

    def _fetch_table_rows(self, client: httpx.Client, table_id: str) -> List[Dict[str, Any]]:
        """Paginate through all rows in a Baserow table."""
        headers = {"Authorization": f"Token {self.api_token}"}
        rows: List[Dict[str, Any]] = []
        next_url: Optional[str] = f"{self.api_url}/api/database/rows/table/{table_id}/?user_field_names=true&size=200"

        while next_url:
            resp = client.get(next_url, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"Baserow table {table_id} request failed: HTTP {resp.status_code}")
            data = resp.json()
            rows.extend(data.get("results", []))
            next_url = data.get("next")

        return rows

    def _fetch_live_snapshot(self) -> BaserowSnapshot:
        """Fetch complete live tables from Baserow API."""
        now_str = datetime.now(timezone.utc).isoformat()
        media_rows: List[Dict[str, Any]] = []
        cat_rows: List[Dict[str, Any]] = []
        travel_rows: List[Dict[str, Any]] = []

        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            if self.media_table_id:
                media_rows = self._fetch_table_rows(client, self.media_table_id)
            if self.category_table_id:
                cat_rows = self._fetch_table_rows(client, self.category_table_id)
            if self.travel_schedule_table_id:
                travel_rows = self._fetch_table_rows(client, self.travel_schedule_table_id)

        return BaserowSnapshot(
            snapshot_at=now_str,
            state="LIVE_CURRENT",
            complete=True,
            media_rows=media_rows,
            category_title_rows=cat_rows,
            travel_schedule_rows=travel_rows,
        )

    def _save_audit_snapshot(self, snapshot: BaserowSnapshot):
        """Persist snapshot JSON to local path for audit and historical inspection only."""
        try:
            self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.snapshot_path, "w", encoding="utf-8") as f:
                f.write(snapshot.model_dump_json(indent=2))
        except Exception as e:
            logger.warning(f"Failed to persist Baserow audit record: {e}")

    def load_audit_snapshot(self) -> Optional[BaserowSnapshot]:
        """Load historical audit snapshot from disk for inspection. Never use for operational decisions."""
        if not self.snapshot_path.exists():
            return None
        try:
            with open(self.snapshot_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return BaserowSnapshot.model_validate(data)
        except Exception as e:
            logger.warning(f"Failed to load Baserow audit record: {e}")
            return None


# Alias for explicit live-naming
BaserowLiveProvider = BaserowSnapshotProvider
