"""Core Synchronization Engine for Tool 4 (Media Database Updater).

Enforces:
- Tool 2 is the create-vs-update gate.
- Mandatory pre-write revalidation (both pre-create and pre-update race guards).
- Minimal PATCH-style writes; never full-row replacement.
- Preservation of collaborator edits and unrelated online fields.
- Field merge policies for Title, Date, Category, Tag, Language, Statuses, Timestamps, Notes.
- Strict select-option rules (only Country and Place, location may create options).
"""
from datetime import datetime, timezone
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from ..common.ascii_latin import to_ascii_latin
from ..renamer.parser.what import SB_REGEX, BG_REGEX, CC_REGEX
from .models import (
    FieldAction,
    FieldDiff,
    MediaDbSyncRequest,
    MediaDbSyncResult,
    SyncOperation,
    SyncStatus,
)
from .write_adapter import (
    AmbiguousOptionError,
    BaserowSchemaError,
    BaserowUnavailableError,
    BaserowWriteError,
    FakeBaserowWriteAdapter,
    TaxonomyForbiddenError,
    _normalize_option_text,
)

logger = logging.getLogger(__name__)

# Fields that Tool 4 considers unrelated to local archive file metadata
UNRELATED_ONLINE_FIELDS = {
    "Youtube",
    "Youtube descr",
    "Audio link",
    "Thumb image",
    "Transcriber",
    "Alt. Links",
    "Article Link",
    "Themes",
    "Transcript Archive link",
    "Media Archive link",
}

# Fields that define collaborator conflict if changed concurrently on update
RELEVANT_COLLABORATOR_FIELDS = {
    "Title",
    "Date",
    "Category",
    "Filename",
    "media_archive_path",
    "Place, location",
    "Country",
}


def _is_complete_date(val: Optional[str]) -> bool:
    """True if val is an exact calendar date YYYY-MM-DD without DD placeholders."""
    if not val or not isinstance(val, str):
        return False
    clean = val.strip().replace("/", "-")
    if re.match(r"^\d{4}-\d{2}-\d{2}$", clean):
        if not clean.endswith("-DD") and not clean.endswith("-00"):
            return True
    return False


def _extract_scripture_verse(what_val: Optional[str], what_verse: Optional[str]) -> Optional[str]:
    """Extract scripture verse representation (e.g. '1.3.4' or '1.18' or 'Adi 1.1')."""
    if what_verse and what_verse.strip():
        parts = what_verse.strip().split(".")
        return ".".join(str(int(p)) if p.isdigit() else p for p in parts)
    if not what_val:
        return None
    val = str(what_val).strip()
    # Check SB
    m = SB_REGEX.search(val)
    if m:
        canto, ch, v, v_end = m.groups()
        c_n = str(int(canto))
        ch_n = str(int(ch))
        v_n = str(int(v))
        return f"{c_n}.{ch_n}.{v_n}-{int(v_end)}" if v_end else f"{c_n}.{ch_n}.{v_n}"
    # Check BG
    m = BG_REGEX.search(val)
    if m:
        ch, v, v_end = m.groups()
        ch_n = str(int(ch))
        v_n = str(int(v))
        return f"{ch_n}.{v_n}-{int(v_end)}" if v_end else f"{ch_n}.{v_n}"
    # Check CC
    m = CC_REGEX.search(val)
    if m:
        lila, ch, v, v_end = m.groups()
        prefix = f"{lila} " if lila else ""
        ch_n = str(int(ch))
        v_n = str(int(v))
        return f"{prefix}{ch_n}.{v_n}-{int(v_end)}" if v_end else f"{prefix}{ch_n}.{v_n}"
    return None


def _resolve_title(request: MediaDbSyncRequest, for_create: bool = True) -> Optional[str]:
    """Resolve Title based on Section 11 priority:
    1. Explicit usable title in committed filename / structured WHAT title evidence.
    2. Parent folder context (excluding year/country/format folders).
    3. Current filename fallback (only for create).
    """
    # 1. Structured WHAT / title evidence
    if request.what_val:
        wv = request.what_val.strip()
        pure_scripture = False
        for rx in (SB_REGEX, BG_REGEX, CC_REGEX):
            m = rx.search(wv)
            if m:
                rem = rx.sub("", wv).strip(" -_")
                if not rem or len(rem) < 3:
                    pure_scripture = True
                break
        if not pure_scripture:
            return wv

    # 2. Parent folder context
    p_ctx = request.parent_folder_context
    if p_ctx and p_ctx.strip():
        clean_p = p_ctx.strip()
        is_year = bool(re.match(r"^\d{4}$", clean_p))
        is_technical = clean_p.lower() in ("mp3", "audio", "video", "edited", "archive", "incoming", "raw")
        if not is_year and not is_technical and len(clean_p) > 2:
            return clean_p

    # 3. Fallback: filename stem only for create
    if for_create:
        fn = request.current_filename
        if fn:
            stem = Path(fn).stem
            return stem or fn
        return "Untitled Archive File"

    return None



def merge_notes(
    existing_notes: Optional[str],
    incomplete_date: Optional[str] = None,
    full_date_resolved: bool = False,
) -> str:
    """Merge notes idempotently in accordance with Section 13:
    - 'Added from archive' begins Notes exactly once when archive linkage is established.
    - Incomplete recording date marker is idempotent.
    - Full date removes/replaces only the incomplete-date marker, preserving all human text.
    """
    marker_prefix = "Incomplete recording date:"
    lines: List[str] = []
    if existing_notes:
        for l in existing_notes.splitlines():
            lines.append(l)

    # 1. Clean lines
    if full_date_resolved:
        lines = [l for l in lines if not l.strip().startswith(marker_prefix)]
    elif incomplete_date:
        marker_line = f"Incomplete recording date: {incomplete_date}"
        lines = [l for l in lines if not l.strip().startswith(marker_prefix)]
        lines.append(marker_line)

    # 2. Ensure "Added from archive" is prepended exactly once
    content_str = "\n".join(lines).strip()
    if not content_str.startswith("Added from archive"):
        if content_str:
            content_str = f"Added from archive\n{content_str}"
        else:
            content_str = "Added from archive"

    return content_str


class MediaDatabaseUpdateEngine:
    """Engine implementing Tool 4 business rules, race guards, and merge policies."""

    def __init__(self, write_adapter: Any, tool2_service: Optional[Any] = None):
        self.write_adapter = write_adapter
        self.tool2_service = tool2_service

    def _match_select_option(
        self,
        options: List[Dict[str, Any]],
        value: str,
    ) -> Tuple[Optional[str], bool]:
        """Find matching select option by normalized text. Returns (matched_value, is_ambiguous)."""
        target = _normalize_option_text(value)
        matches = [opt["value"] for opt in options if _normalize_option_text(opt.get("value", "")) == target]
        if len(matches) == 1:
            return matches[0], False
        if len(matches) > 1:
            return None, True
        return None, False

    def plan_and_revalidate(
        self,
        request: MediaDbSyncRequest,
        live_fields: List[Dict[str, Any]],
        live_row: Optional[Dict[str, Any]] = None,
    ) -> MediaDbSyncResult:
        """Compute the intended sync operation, field diffs, and validation status."""
        # 1. Tool 2 Gate Evaluation
        t2_decision = (request.tool2_decision or "").upper()

        if t2_decision == "DATABASE_UNAVAILABLE":
            return MediaDbSyncResult(
                tracking_id=request.tracking_id,
                status=SyncStatus.DATABASE_UNAVAILABLE,
                operation=SyncOperation.BLOCKED,
                diagnostic_notes=["Tool 2 reported DATABASE_UNAVAILABLE; mutation blocked"],
            )

        if t2_decision in (
            "PROBABLE_EXISTING_MEDIA",
            "MULTIPLE_CANDIDATES",
            "CONFLICT_WITH_EXISTING",
            "INSUFFICIENT_EVIDENCE",
        ):
            return MediaDbSyncResult(
                tracking_id=request.tracking_id,
                status=SyncStatus.REVIEW_REQUIRED,
                operation=SyncOperation.BLOCKED,
                review_required=True,
                diagnostic_notes=[f"Tool 2 decision {t2_decision} requires review; automatic mutation disallowed"],
            )

        # Map field schema
        fields_by_name = {f["name"].strip().lower(): f for f in live_fields}

        # Handle CREATE
        if t2_decision == "NEW_MEDIA_CANDIDATE":
            return self._plan_create(request, fields_by_name)

        # Handle UPDATE
        if t2_decision == "EXISTING_MEDIA_MATCH" or (request.is_human_approved and request.selected_media_row_id):
            target_id = request.selected_media_row_id
            if not target_id:
                return MediaDbSyncResult(
                    tracking_id=request.tracking_id,
                    status=SyncStatus.FAILED_BLOCKED,
                    operation=SyncOperation.BLOCKED,
                    diagnostic_notes=["Existing media match missing selected_media_row_id"],
                )
            if live_row is None:
                return MediaDbSyncResult(
                    tracking_id=request.tracking_id,
                    status=SyncStatus.FAILED_BLOCKED,
                    operation=SyncOperation.BLOCKED,
                    media_row_id=target_id,
                    diagnostic_notes=[f"Target Media row {target_id} not found in database"],
                )
            return self._plan_update(request, fields_by_name, live_row)

        # Any unrecognized state
        return MediaDbSyncResult(
            tracking_id=request.tracking_id,
            status=SyncStatus.REVIEW_REQUIRED,
            operation=SyncOperation.BLOCKED,
            review_required=True,
            diagnostic_notes=[f"Unrecognized Tool 2 decision '{request.tool2_decision}'"],
        )

    def _plan_create(
        self,
        request: MediaDbSyncRequest,
        fields_by_name: Dict[str, Dict[str, Any]],
    ) -> MediaDbSyncResult:
        diffs: List[FieldDiff] = []
        conflicts: List[str] = []
        diag_notes: List[str] = []
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # 1. Title
        title_val = _resolve_title(request)
        diffs.append(FieldDiff(field_name="Title", old_value=None, new_value=title_val, action=FieldAction.SET))

        # 2. Date / Incomplete Date
        date_written: Optional[str] = None
        incomplete_marker: Optional[str] = None
        if str(request.when_state or "").lower() == "provisional":
            # Provisional schedule date is not eligible for Date write (Test 19)
            diag_notes.append("Provisional WHEN date excluded from authoritative Date field")
        elif _is_complete_date(request.when_val):
            date_written = request.when_val.strip().replace("/", "-")
            diffs.append(FieldDiff(field_name="Date", old_value=None, new_value=date_written, action=FieldAction.SET))
        elif request.when_val and request.when_val.strip():
            incomplete_marker = request.when_val.strip()
            diffs.append(FieldDiff(
                field_name="Date",
                old_value=None,
                new_value=None,
                action=FieldAction.PRESERVED,
                details=f"Incomplete date stored in Notes: {incomplete_marker}",
            ))

        # 3. Category
        cat_fld = fields_by_name.get("category")
        if cat_fld and request.what_category:
            matched_cat, ambig = self._match_select_option(cat_fld.get("select_options", []), request.what_category)
            if matched_cat:
                diffs.append(FieldDiff(field_name="Category", old_value=None, new_value=matched_cat, action=FieldAction.SET))
            elif ambig:
                conflicts.append(f"Ambiguous Category option for '{request.what_category}'")
            else:
                conflicts.append(f"Category option '{request.what_category}' not found in live schema (creation disallowed)")

        # 4. Tag (Scripture verse)
        verse = _extract_scripture_verse(request.what_val, request.what_verse)
        tag_fld = fields_by_name.get("tag")
        if tag_fld and verse:
            if tag_fld.get("type") == "multiple_select":
                matched_tag, _ = self._match_select_option(tag_fld.get("select_options", []), verse)
                if matched_tag:
                    diffs.append(FieldDiff(field_name="Tag", old_value=None, new_value=[matched_tag], action=FieldAction.SET))
                else:
                    conflicts.append(f"Scripture Tag option '{verse}' not found in live schema (creation disallowed)")
            else:
                diffs.append(FieldDiff(field_name="Tag", old_value=None, new_value=verse, action=FieldAction.SET))

        # 5. Language (defaults to English)
        lang_fld = fields_by_name.get("language")
        if lang_fld:
            matched_lang, _ = self._match_select_option(lang_fld.get("select_options", []), "English")
            if matched_lang:
                diffs.append(FieldDiff(field_name="Language", old_value=None, new_value=matched_lang, action=FieldAction.SET))
            else:
                conflicts.append("Required Language option 'English' not found in live schema")

        # 6. Statuses: Status Media, Status thumb, Status Transcript (default to Not-started)
        for stat_name in ("Status Media", "Status thumb", "Status Transcript"):
            fld = fields_by_name.get(stat_name.lower())
            if fld:
                matched_stat, _ = self._match_select_option(fld.get("select_options", []), "Not-started")
                if matched_stat:
                    diffs.append(FieldDiff(field_name=stat_name, old_value=None, new_value=matched_stat, action=FieldAction.SET))
                else:
                    conflicts.append(f"Required status option 'Not-started' for '{stat_name}' not found in live schema")

        # 7. Filename & media_archive_path
        diffs.append(FieldDiff(field_name="Filename", old_value=None, new_value=request.current_filename, action=FieldAction.SET))
        diffs.append(FieldDiff(field_name="media_archive_path", old_value=None, new_value=request.current_path, action=FieldAction.SET))

        # 8. Notes
        notes_val = merge_notes(existing_notes=None, incomplete_date=incomplete_marker, full_date_resolved=bool(date_written))
        diffs.append(FieldDiff(field_name="Notes", old_value=None, new_value=notes_val, action=FieldAction.SET))

        # 9. Dates/Timestamps
        diffs.append(FieldDiff(field_name="Created_on", old_value=None, new_value=today, action=FieldAction.SET))
        diffs.append(FieldDiff(field_name="Last modified by", old_value=None, new_value=today, action=FieldAction.SET))
        diffs.append(FieldDiff(field_name="Last modified", old_value=None, new_value=today, action=FieldAction.SET))
        diffs.append(FieldDiff(field_name="imported_on", old_value=None, new_value=today, action=FieldAction.SET))

        # 10. Country & Place, location
        if str(request.where_state or "").lower() == "provisional":
            diag_notes.append("Provisional WHERE location excluded from authoritative fields")
        else:
            if request.where_country:
                diffs.append(FieldDiff(field_name="Country", old_value=None, new_value=request.where_country, action=FieldAction.SET))
            if request.where_place:
                diffs.append(FieldDiff(field_name="Place, location", old_value=None, new_value=request.where_place, action=FieldAction.SET))

        # Preserved fields on create (unrelated fields)
        preserved = list(UNRELATED_ONLINE_FIELDS)

        has_conflicts = len(conflicts) > 0
        return MediaDbSyncResult(
            tracking_id=request.tracking_id,
            status=SyncStatus.REVIEW_REQUIRED if has_conflicts else SyncStatus.SYNCING,
            operation=SyncOperation.CREATE,
            field_diffs=diffs,
            fields_preserved=preserved,
            fields_modified=[d.field_name for d in diffs if d.action == FieldAction.SET],
            conflicts=conflicts,
            diagnostic_notes=diag_notes,
            review_required=has_conflicts,
        )

    def _plan_update(
        self,
        request: MediaDbSyncRequest,
        fields_by_name: Dict[str, Dict[str, Any]],
        live_row: Dict[str, Any],
    ) -> MediaDbSyncResult:
        diffs: List[FieldDiff] = []
        conflicts: List[str] = []
        diag_notes: List[str] = []
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row_id = live_row["id"]

        # Helper to get scalar text from Baserow raw row
        def _get_text(field_key: str) -> Optional[str]:
            v = live_row.get(field_key)
            if v is None:
                return None
            if isinstance(v, dict) and "value" in v:
                return str(v["value"]).strip()
            if isinstance(v, list) and v and isinstance(v[0], dict) and "value" in v[0]:
                return str(v[0]["value"]).strip()
            s = str(v).strip()
            return s if s else None

        # 1. Filename & media_archive_path (Archive linkage)
        curr_fn = _get_text("Filename")
        curr_path = _get_text("media_archive_path")

        path_needs_update = False
        fn_needs_update = False

        if not curr_path:
            path_needs_update = True
        elif curr_path == request.current_path:
            diffs.append(FieldDiff(field_name="media_archive_path", old_value=curr_path, new_value=curr_path, action=FieldAction.PRESERVED))
        elif curr_path == request.original_path or curr_fn == request.original_filename:
            # Proven tracked rename!
            path_needs_update = True
        else:
            # Path collision with unproven representation!
            conflicts.append(f"media_archive_path collision: row has '{curr_path}', incoming file is '{request.current_path}'")
            diffs.append(FieldDiff(
                field_name="media_archive_path",
                old_value=curr_path,
                new_value=request.current_path,
                action=FieldAction.CONFLICT,
                details="Unproven archive representation conflict",
            ))

        if not curr_fn:
            fn_needs_update = True
        elif curr_fn == request.current_filename:
            diffs.append(FieldDiff(field_name="Filename", old_value=curr_fn, new_value=curr_fn, action=FieldAction.PRESERVED))
        elif curr_fn == request.original_filename or curr_path == request.original_path:
            fn_needs_update = True
        else:
            diffs.append(FieldDiff(field_name="Filename", old_value=curr_fn, new_value=request.current_filename, action=FieldAction.PRESERVED))

        if path_needs_update:
            diffs.append(FieldDiff(field_name="media_archive_path", old_value=curr_path, new_value=request.current_path, action=FieldAction.SET))
        if fn_needs_update:
            diffs.append(FieldDiff(field_name="Filename", old_value=curr_fn, new_value=request.current_filename, action=FieldAction.SET))

        # 2. Semantic Fields: Title, Date, Category, Place, Country
        # Date
        curr_date = _get_text("Date")
        date_needs_update = False
        new_date_val: Optional[str] = None
        date_is_incomplete = False

        if str(request.when_state or "").lower() != "provisional":
            if _is_complete_date(request.when_val):
                clean_incoming_date = request.when_val.strip().replace("/", "-")
                if not curr_date:
                    date_needs_update = True
                    new_date_val = clean_incoming_date
                elif curr_date == clean_incoming_date:
                    diffs.append(FieldDiff(field_name="Date", old_value=curr_date, new_value=curr_date, action=FieldAction.PRESERVED))
                else:
                    if request.is_human_approved:
                        date_needs_update = True
                        new_date_val = clean_incoming_date
                    else:
                        conflicts.append(f"Date conflict: DB has '{curr_date}', incoming is '{clean_incoming_date}'")
                        diffs.append(FieldDiff(field_name="Date", old_value=curr_date, new_value=clean_incoming_date, action=FieldAction.CONFLICT))
            elif request.when_val and request.when_val.strip():
                date_is_incomplete = True

        if date_needs_update and new_date_val:
            diffs.append(FieldDiff(field_name="Date", old_value=curr_date, new_value=new_date_val, action=FieldAction.SET))

        # Title
        curr_title = _get_text("Title")
        title_val = _resolve_title(request, for_create=False)
        if title_val:
            if not curr_title:
                diffs.append(FieldDiff(field_name="Title", old_value=None, new_value=title_val, action=FieldAction.SET))
            elif curr_title.strip() == title_val.strip():
                diffs.append(FieldDiff(field_name="Title", old_value=curr_title, new_value=curr_title, action=FieldAction.PRESERVED))
            else:
                if request.is_human_approved:
                    diffs.append(FieldDiff(field_name="Title", old_value=curr_title, new_value=title_val, action=FieldAction.SET))
                else:
                    # Contradictory title preserved and marked conflict for review
                    conflicts.append(f"Title conflict: DB has '{curr_title}', incoming resolved '{title_val}'")
                    diffs.append(FieldDiff(field_name="Title", old_value=curr_title, new_value=title_val, action=FieldAction.CONFLICT))
        else:
            if curr_title:
                diffs.append(FieldDiff(field_name="Title", old_value=curr_title, new_value=curr_title, action=FieldAction.PRESERVED))

        # Category
        curr_cat = _get_text("Category")
        if request.what_category:
            cat_fld = fields_by_name.get("category")
            matched_cat, ambig = self._match_select_option(cat_fld.get("select_options", []) if cat_fld else [], request.what_category)
            if matched_cat:
                if not curr_cat:
                    diffs.append(FieldDiff(field_name="Category", old_value=None, new_value=matched_cat, action=FieldAction.SET))
                elif curr_cat == matched_cat:
                    diffs.append(FieldDiff(field_name="Category", old_value=curr_cat, new_value=curr_cat, action=FieldAction.PRESERVED))
                else:
                    if request.is_human_approved:
                        diffs.append(FieldDiff(field_name="Category", old_value=curr_cat, new_value=matched_cat, action=FieldAction.SET))
                    else:
                        conflicts.append(f"Category conflict: DB has '{curr_cat}', incoming is '{matched_cat}'")
                        diffs.append(FieldDiff(field_name="Category", old_value=curr_cat, new_value=matched_cat, action=FieldAction.CONFLICT))
            elif ambig:
                conflicts.append(f"Ambiguous Category option for '{request.what_category}'")
            else:
                conflicts.append(f"Category option '{request.what_category}' not found in live schema")

        # Country & Place, location
        if str(request.where_state or "").lower() != "provisional":
            curr_country = _get_text("Country")
            if request.where_country:
                if not curr_country:
                    diffs.append(FieldDiff(field_name="Country", old_value=None, new_value=request.where_country, action=FieldAction.SET))
                elif _normalize_option_text(curr_country) == _normalize_option_text(request.where_country):
                    diffs.append(FieldDiff(field_name="Country", old_value=curr_country, new_value=curr_country, action=FieldAction.PRESERVED))
                else:
                    if request.is_human_approved:
                        diffs.append(FieldDiff(field_name="Country", old_value=curr_country, new_value=request.where_country, action=FieldAction.SET))
                    else:
                        conflicts.append(f"Country conflict: DB has '{curr_country}', incoming is '{request.where_country}'")
                        diffs.append(FieldDiff(field_name="Country", old_value=curr_country, new_value=request.where_country, action=FieldAction.CONFLICT))

            curr_place = _get_text("Place, location")
            if request.where_place:
                if not curr_place:
                    diffs.append(FieldDiff(field_name="Place, location", old_value=None, new_value=request.where_place, action=FieldAction.SET))
                elif _normalize_option_text(curr_place) == _normalize_option_text(request.where_place):
                    diffs.append(FieldDiff(field_name="Place, location", old_value=curr_place, new_value=curr_place, action=FieldAction.PRESERVED))
                else:
                    if request.is_human_approved:
                        diffs.append(FieldDiff(field_name="Place, location", old_value=curr_place, new_value=request.where_place, action=FieldAction.SET))
                    else:
                        conflicts.append(f"Place conflict: DB has '{curr_place}', incoming is '{request.where_place}'")
                        diffs.append(FieldDiff(field_name="Place, location", old_value=curr_place, new_value=request.where_place, action=FieldAction.CONFLICT))

        # Tag (Additive / union)
        verse = _extract_scripture_verse(request.what_val, request.what_verse)
        curr_tags_raw = live_row.get("Tag")
        curr_tags: List[str] = []
        if isinstance(curr_tags_raw, list):
            for t in curr_tags_raw:
                if isinstance(t, dict) and "value" in t:
                    curr_tags.append(str(t["value"]))
                elif isinstance(t, str):
                    curr_tags.append(t)
        elif isinstance(curr_tags_raw, str) and curr_tags_raw.strip():
            curr_tags.append(curr_tags_raw.strip())

        if verse:
            tag_fld = fields_by_name.get("tag")
            if tag_fld and tag_fld.get("type") == "multiple_select":
                matched_verse_opt, _ = self._match_select_option(tag_fld.get("select_options", []), verse)
                if matched_verse_opt:
                    if matched_verse_opt not in curr_tags:
                        new_tags = list(curr_tags) + [matched_verse_opt]
                        diffs.append(FieldDiff(field_name="Tag", old_value=curr_tags, new_value=new_tags, action=FieldAction.SET))
                    else:
                        diffs.append(FieldDiff(field_name="Tag", old_value=curr_tags, new_value=curr_tags, action=FieldAction.PRESERVED))
                else:
                    conflicts.append(f"Scripture Tag option '{verse}' not found in live schema")
            else:
                if verse not in curr_tags:
                    new_tags = list(curr_tags) + [verse]
                    diffs.append(FieldDiff(field_name="Tag", old_value=curr_tags, new_value=new_tags, action=FieldAction.SET))

        # Notes Merge
        curr_notes = _get_text("Notes")
        inc_date_arg = request.when_val.strip() if date_is_incomplete else None
        merged_notes = merge_notes(
            existing_notes=curr_notes,
            incomplete_date=inc_date_arg,
            full_date_resolved=bool(curr_date or date_needs_update),
        )
        if curr_notes != merged_notes:
            diffs.append(FieldDiff(field_name="Notes", old_value=curr_notes, new_value=merged_notes, action=FieldAction.SET))
        else:
            diffs.append(FieldDiff(field_name="Notes", old_value=curr_notes, new_value=curr_notes, action=FieldAction.PRESERVED))

        # Modifications check: if any fields modified, update Last modified and Last modified by
        modified_diffs = [d for d in diffs if d.action == FieldAction.SET]
        if modified_diffs:
            diffs.append(FieldDiff(field_name="Last modified by", old_value=_get_text("Last modified by"), new_value=today, action=FieldAction.SET))
            diffs.append(FieldDiff(field_name="Last modified", old_value=_get_text("Last modified"), new_value=today, action=FieldAction.SET))

        # Always preserve unrelated online fields, statuses, language, import date
        preserved_fields = list(UNRELATED_ONLINE_FIELDS) + [
            "Status Media", "Status thumb", "Status Transcript", "Language", "Created_on", "imported_on"
        ]

        has_conflicts = len(conflicts) > 0
        status = SyncStatus.REVIEW_REQUIRED if has_conflicts else SyncStatus.SYNCING
        op = SyncOperation.UPDATE if modified_diffs else SyncOperation.NOOP

        return MediaDbSyncResult(
            tracking_id=request.tracking_id,
            status=SyncStatus.SYNCED if op == SyncOperation.NOOP else status,
            operation=op,
            media_row_id=row_id,
            precondition_row_snapshot=live_row,
            field_diffs=diffs,
            fields_preserved=preserved_fields,
            fields_modified=[d.field_name for d in modified_diffs],
            conflicts=conflicts,
            diagnostic_notes=diag_notes,
            review_required=has_conflicts,
        )

    def execute_sync(
        self,
        request: MediaDbSyncRequest,
        commit: bool = False,
    ) -> MediaDbSyncResult:
        """Execute synchronization plan with live revalidations and safe commit."""
        # 1. Fetch live fields schema
        try:
            live_fields = self.write_adapter.fetch_table_fields()
        except BaserowUnavailableError as e:
            return MediaDbSyncResult(
                tracking_id=request.tracking_id,
                status=SyncStatus.DATABASE_UNAVAILABLE,
                operation=SyncOperation.BLOCKED,
                diagnostic_notes=[f"Failed to fetch live table schema: {e}"],
            )
        except Exception as e:
            return MediaDbSyncResult(
                tracking_id=request.tracking_id,
                status=SyncStatus.FAILED_RETRYABLE,
                operation=SyncOperation.BLOCKED,
                diagnostic_notes=[f"Unexpected error fetching table schema: {e}"],
            )

        # 2. If target row specified, fetch it live
        live_row: Optional[Dict[str, Any]] = None
        if request.selected_media_row_id:
            try:
                live_row = self.write_adapter.fetch_row_raw(request.selected_media_row_id)
            except BaserowUnavailableError as e:
                return MediaDbSyncResult(
                    tracking_id=request.tracking_id,
                    status=SyncStatus.DATABASE_UNAVAILABLE,
                    operation=SyncOperation.BLOCKED,
                    media_row_id=request.selected_media_row_id,
                    diagnostic_notes=[f"Failed to fetch live target row {request.selected_media_row_id}: {e}"],
                )
            except Exception as e:
                return MediaDbSyncResult(
                    tracking_id=request.tracking_id,
                    status=SyncStatus.FAILED_RETRYABLE,
                    operation=SyncOperation.BLOCKED,
                    media_row_id=request.selected_media_row_id,
                    diagnostic_notes=[f"Unexpected error fetching target row: {e}"],
                )

        # 3. Compute plan & diffs
        plan = self.plan_and_revalidate(request, live_fields, live_row)

        if plan.status in (SyncStatus.DATABASE_UNAVAILABLE, SyncStatus.REVIEW_REQUIRED, SyncStatus.FAILED_BLOCKED):
            return plan

        if plan.operation == SyncOperation.NOOP:
            return plan

        # If preview / dry-run mode, return plan without mutating database
        if not commit:
            return plan

        # 4. Commit Mutations with Pre-Write Guards
        if plan.operation == SyncOperation.CREATE:
            return self._commit_create(request, plan)
        elif plan.operation == SyncOperation.UPDATE:
            return self._commit_update(request, plan, live_row)

        return plan

    def _commit_create(
        self,
        request: MediaDbSyncRequest,
        plan: MediaDbSyncResult,
    ) -> MediaDbSyncResult:
        """Execute CREATE with pre-create candidate revalidation race guard."""
        # 1. Pre-create revalidation via Tool 2 service if available
        if self.tool2_service is not None:
            try:
                # Perform fresh live review check
                fresh_rev = self.tool2_service.review_file(request.tracking_id, force_refresh=True)
                if fresh_rev and fresh_rev.decision != "NEW_MEDIA_CANDIDATE":
                    return MediaDbSyncResult(
                        tracking_id=request.tracking_id,
                        status=SyncStatus.REVIEW_REQUIRED,
                        operation=SyncOperation.CONFLICT,
                        review_required=True,
                        conflicts=["COLLABORATOR_NEW_ROW_CREATED"],
                        diagnostic_notes=[
                            f"Pre-create race guard: live Tool 2 check changed from NEW_MEDIA_CANDIDATE to {fresh_rev.decision}"
                        ],
                    )
            except Exception as e:
                logger.warning(f"Pre-create live check encountered error: {e}")

        # 2. Build payload from plan's SET diffs
        payload: Dict[str, Any] = {}
        for diff in plan.field_diffs:
            if diff.action == FieldAction.SET:
                payload[diff.field_name] = diff.new_value

        # 3. Ensure Country / Place, location select options if needed
        for fld_name in ("Country", "Place, location"):
            val = payload.get(fld_name)
            if val and isinstance(val, str):
                try:
                    canonical_opt = self.write_adapter.ensure_select_option(fld_name, val)
                    payload[fld_name] = canonical_opt
                except AmbiguousOptionError as e:
                    return MediaDbSyncResult(
                        tracking_id=request.tracking_id,
                        status=SyncStatus.REVIEW_REQUIRED,
                        operation=SyncOperation.CONFLICT,
                        review_required=True,
                        conflicts=[str(e)],
                        diagnostic_notes=[f"Ambiguous select option in {fld_name}: {e}"],
                    )
                except Exception as e:
                    return MediaDbSyncResult(
                        tracking_id=request.tracking_id,
                        status=SyncStatus.FAILED_RETRYABLE,
                        operation=SyncOperation.CONFLICT,
                        diagnostic_notes=[f"Failed to ensure select option for {fld_name}: {e}"],
                    )

        # 4. Perform write
        try:
            created_row = self.write_adapter.create_row(payload)
            new_id = created_row.get("id")
            plan.media_row_id = new_id
            plan.status = SyncStatus.SYNCED
            return plan
        except BaserowUnavailableError as e:
            # Uncertain outcome check
            return self.reconcile_uncertain_create(request, plan, error_message=str(e))
        except Exception as e:
            plan.status = SyncStatus.FAILED_RETRYABLE
            plan.error_message = str(e)
            return plan

    def _commit_update(
        self,
        request: MediaDbSyncRequest,
        plan: MediaDbSyncResult,
        initial_live_row: Optional[Dict[str, Any]],
    ) -> MediaDbSyncResult:
        """Execute UPDATE with pre-update relevant-field race guard and minimal PATCH."""
        row_id = plan.media_row_id
        if not row_id:
            plan.status = SyncStatus.FAILED_BLOCKED
            return plan

        # 1. Fetch fresh live row immediately before write
        try:
            fresh_row = self.write_adapter.fetch_row_raw(row_id)
        except Exception as e:
            plan.status = SyncStatus.FAILED_RETRYABLE
            plan.error_message = f"Failed to re-fetch live row before update: {e}"
            return plan

        if not fresh_row:
            plan.status = SyncStatus.FAILED_BLOCKED
            plan.error_message = f"Target row {row_id} no longer exists"
            return plan

        # 2. Check relevant preconditions: did a collaborator change any relevant field?
        if initial_live_row:
            for fld in RELEVANT_COLLABORATOR_FIELDS:
                old_val = initial_live_row.get(fld)
                new_val = fresh_row.get(fld)
                # Compare scalar values
                if old_val != new_val:
                    # Check if Tool 4 intended to modify this field or if it was modified concurrently
                    return MediaDbSyncResult(
                        tracking_id=request.tracking_id,
                        status=SyncStatus.REVIEW_REQUIRED,
                        operation=SyncOperation.CONFLICT,
                        media_row_id=row_id,
                        review_required=True,
                        conflicts=[f"COLLABORATOR_CONFLICT: Field '{fld}' changed concurrently from '{old_val}' to '{new_val}'"],
                        diagnostic_notes=["Pre-update revalidation failed: collaborator changed relevant field"],
                    )

        # 3. Build minimal PATCH payload
        payload: Dict[str, Any] = {}
        for diff in plan.field_diffs:
            if diff.action == FieldAction.SET:
                payload[diff.field_name] = diff.new_value

        # 4. Ensure select options if country / location are being set
        for fld_name in ("Country", "Place, location"):
            val = payload.get(fld_name)
            if val and isinstance(val, str):
                try:
                    canonical_opt = self.write_adapter.ensure_select_option(fld_name, val)
                    payload[fld_name] = canonical_opt
                except AmbiguousOptionError as e:
                    return MediaDbSyncResult(
                        tracking_id=request.tracking_id,
                        status=SyncStatus.REVIEW_REQUIRED,
                        operation=SyncOperation.CONFLICT,
                        media_row_id=row_id,
                        review_required=True,
                        conflicts=[str(e)],
                        diagnostic_notes=[f"Ambiguous select option in {fld_name}: {e}"],
                    )

        # 5. Send minimal PATCH
        try:
            self.write_adapter.patch_row(row_id, payload)
            plan.status = SyncStatus.SYNCED
            return plan
        except BaserowUnavailableError as e:
            return self.reconcile_uncertain_update(request, plan, payload, error_message=str(e))
        except Exception as e:
            plan.status = SyncStatus.FAILED_RETRYABLE
            plan.error_message = str(e)
            return plan

    def reconcile_uncertain_create(
        self,
        request: MediaDbSyncRequest,
        plan: MediaDbSyncResult,
        error_message: str,
    ) -> MediaDbSyncResult:
        """Reconcile uncertain transport outcome for create without blind duplicate creation."""
        logger.info(f"Reconciling uncertain create for {request.tracking_id}...")
        # Check if the row actually got created in the table
        try:
            if self.tool2_service is not None:
                rev = self.tool2_service.review_file(request.tracking_id, force_refresh=True)
                if rev and rev.selected_media_row_id:
                    plan.media_row_id = rev.selected_media_row_id
                    plan.status = SyncStatus.SYNCED
                    plan.diagnostic_notes.append("Reconciled uncertain create: row was confirmed created in live table")
                    return plan
        except Exception as e:
            logger.warning(f"Reconciliation query failed: {e}")

        plan.status = SyncStatus.FAILED_RETRYABLE
        plan.error_message = f"Create timeout (transport uncertain): {error_message}"
        return plan

    def reconcile_uncertain_update(
        self,
        request: MediaDbSyncRequest,
        plan: MediaDbSyncResult,
        intended_payload: Dict[str, Any],
        error_message: str,
    ) -> MediaDbSyncResult:
        """Reconcile uncertain transport outcome for update by checking if minimal PATCH was applied."""
        row_id = plan.media_row_id
        if not row_id:
            plan.status = SyncStatus.FAILED_RETRYABLE
            plan.error_message = error_message
            return plan

        try:
            row = self.write_adapter.fetch_row_raw(row_id)
            if row:
                # Check if all intended fields in payload are present in row
                all_applied = True
                for k, v in intended_payload.items():
                    curr_val = row.get(k)
                    if isinstance(curr_val, dict) and "value" in curr_val:
                        curr_val = curr_val["value"]
                    if curr_val != v:
                        all_applied = False
                        break
                if all_applied:
                    plan.status = SyncStatus.SYNCED
                    plan.diagnostic_notes.append("Reconciled uncertain update: changes were verified applied in live row")
                    return plan
        except Exception as e:
            logger.warning(f"Reconciliation fetch failed: {e}")

        plan.status = SyncStatus.FAILED_RETRYABLE
        plan.error_message = f"Update timeout (transport uncertain): {error_message}"
        return plan
