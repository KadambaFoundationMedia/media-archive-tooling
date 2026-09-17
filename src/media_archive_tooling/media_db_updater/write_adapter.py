"""Baserow write adapter for Tool 4 (Media Database Updater).

Enforces:
- Secret protection: API tokens are never leaked in logs, __repr__, or __str__.
- Live schema verification: validates field existence and types.
- Strict select-column policy: only Country and Place, location may have new options created.
  All other select fields (Category, Language, Status, Tag) strictly disallow automatic creation.
- Hermetic test fake: FakeBaserowWriteAdapter for offline testing and verification.
"""
from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional
import re
import httpx

from ..common.ascii_latin import to_ascii_latin

logger = logging.getLogger(__name__)


class BaserowWriteError(RuntimeError):
    """Base error for Baserow write operations."""
    pass


class BaserowUnavailableError(BaserowWriteError):
    """Raised when Baserow is unreachable, unconfigured, or times out."""
    pass


class BaserowSchemaError(BaserowWriteError):
    """Raised when a field does not exist or has an incompatible type."""
    pass


class TaxonomyForbiddenError(BaserowWriteError):
    """Raised when an automatic option addition is attempted on a non-country/location field."""
    pass


class AmbiguousOptionError(BaserowWriteError):
    """Raised when multiple existing select options match similarly."""
    pass


ALLOWED_SELECT_CREATION_FIELDS = {
    "country",
    "place, location",
    "place_location",
    "location",
}

SECRET_PATTERNS = [
    re.compile(r"(Token\s+)[A-Za-z0-9_\-\.]+", re.IGNORECASE),
    re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]+", re.IGNORECASE),
    re.compile(r"((?:password|api[_-]?token|api[_-]?key|secret|access[_-]?token|auth[_-]?token)\s*[:=]\s*)[^\s&\"',;]+", re.IGNORECASE),
    re.compile(r"([?&](?:key|api_key|token|access_token|password|secret)=)[^&\s\"']+", re.IGNORECASE),
]

SECRET_KEY_PATTERN = re.compile(r"(?:authorization|token|key|password|secret)", re.IGNORECASE)


def redact_secrets(val: Any) -> Any:
    """Redact API tokens, bearer tokens, or secrets from strings, keys, JSON, or recursive structures."""
    if isinstance(val, str):
        # Check if the string is serialized JSON
        clean_s = val.strip()
        if (clean_s.startswith("{") and clean_s.endswith("}")) or (clean_s.startswith("[") and clean_s.endswith("]")):
            try:
                parsed = json.loads(clean_s)
                redacted_parsed = redact_secrets(parsed)
                return json.dumps(redacted_parsed)
            except Exception:
                pass
        res = val
        for pat in SECRET_PATTERNS:
            res = pat.sub(r"\1[REDACTED]", res)
        return res
    elif isinstance(val, dict):
        redacted_dict = {}
        for k, v in val.items():
            if isinstance(k, str) and SECRET_KEY_PATTERN.search(k):
                redacted_dict[k] = "[REDACTED]"
            else:
                redacted_dict[k] = redact_secrets(v)
        return redacted_dict
    elif isinstance(val, list):
        return [redact_secrets(x) for x in val]
    return val


def _normalize_option_text(text: str) -> str:
    """Normalize text conservatively for comparison: trim, lower, ascii-latin."""
    return to_ascii_latin(text).strip().lower()


def validate_field_schema(field_name: str, value: Any, live_field: Dict[str, Any], allow_new_options: bool = False) -> None:
    """Validate that value is compatible with the live field definition in Baserow.
    Raises BaserowSchemaError on incompatible types or unpermitted values.
    """
    if value is None:
        return

    f_type = live_field.get("type", "text")
    norm_fname = _normalize_option_text(field_name)
    can_create_option = allow_new_options or (norm_fname in ALLOWED_SELECT_CREATION_FIELDS)

    if f_type in ("text", "long_text", "url"):
        if not isinstance(value, str):
            raise BaserowSchemaError(f"Field '{field_name}' expects string type, got {type(value).__name__}")
    elif f_type == "date":
        if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
            raise BaserowSchemaError(f"Field '{field_name}' expects YYYY-MM-DD date string, got {value!r}")
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError as e:
            raise BaserowSchemaError(f"Field '{field_name}' received invalid calendar date: {value}") from e
    elif f_type == "single_select":
        if not isinstance(value, str):
            raise BaserowSchemaError(f"Field '{field_name}' expects single_select option string, got {type(value).__name__}")
        valid_opts = {_normalize_option_text(opt.get("value", "")) for opt in live_field.get("select_options", [])}
        if _normalize_option_text(value) not in valid_opts:
            if not can_create_option:
                raise BaserowSchemaError(f"Field '{field_name}' value '{value}' is not among live select options")
            if norm_fname == "country":
                from .country_mapper import is_valid_country_display_name
                if not is_valid_country_display_name(value):
                    raise BaserowSchemaError(f"Field '{field_name}' value '{value}' is not an authoritative country display name")
    elif f_type == "multiple_select":
        if not isinstance(value, list):
            raise BaserowSchemaError(f"Field '{field_name}' expects list for multiple_select, got {type(value).__name__}")
        valid_opts = {_normalize_option_text(opt.get("value", "")) for opt in live_field.get("select_options", [])}
        for item in value:
            if not isinstance(item, str):
                raise BaserowSchemaError(f"Field '{field_name}' items must be strings, got {type(item).__name__}")
            if _normalize_option_text(item) not in valid_opts:
                if not can_create_option:
                    raise BaserowSchemaError(f"Field '{field_name}' contains option '{item}' not in live select options")
    elif f_type == "file":
        if not isinstance(value, list):
            raise BaserowSchemaError(f"Field '{field_name}' is a file field and cannot be assigned a raw scalar")
    else:
        # Incompatible or unsupported live column type (e.g. number, boolean, formula)
        raise BaserowSchemaError(f"Field '{field_name}' has unsupported or incompatible live type '{f_type}'")


def index_fields_by_name(live_fields: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Index fields by normalized lowercase name.
    Raises BaserowSchemaError if duplicate or ambiguous column names exist in schema after normalization.
    """
    indexed: Dict[str, Dict[str, Any]] = {}
    for f in live_fields:
        raw_name = f.get("name", "")
        norm_name = raw_name.strip().lower()
        if norm_name in indexed:
            existing_raw = indexed[norm_name].get("name", "")
            raise BaserowSchemaError(
                f"Duplicate or ambiguous field name in live schema: '{raw_name}' conflicts with '{existing_raw}'"
            )
        indexed[norm_name] = f
    return indexed



class BaserowWriteAdapter:
    """Production Baserow write adapter performing live schema reads and row mutations."""

    def __init__(
        self,
        api_url: str = "https://api.baserow.io",
        api_token: Optional[str] = None,
        media_table_id: Optional[str] = None,
        timeout: float = 15.0,
    ):
        self.api_url = api_url.rstrip("/")
        self.api_token = api_token
        self.media_table_id = media_table_id
        self.timeout = timeout

    def __repr__(self) -> str:
        tok = "***" if self.api_token else "None"
        return f"<BaserowWriteAdapter url={self.api_url} media_table_id={self.media_table_id} token={tok}>"

    def __str__(self) -> str:
        return self.__repr__()

    def _headers(self) -> Dict[str, str]:
        if not self.api_token:
            raise BaserowUnavailableError("Baserow API token not configured")
        return {
            "Authorization": f"Token {self.api_token}",
            "Content-Type": "application/json",
        }

    def fetch_table_fields(self) -> List[Dict[str, Any]]:
        """Fetch live field definitions for the Media table."""
        if not (self.api_token and self.media_table_id):
            raise BaserowUnavailableError("Baserow credentials or media_table_id not configured")

        url = f"{self.api_url}/api/database/fields/table/{self.media_table_id}/"
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                resp = client.get(url, headers=self._headers())
                if resp.status_code != 200:
                    raise BaserowUnavailableError(f"Failed to fetch table fields: HTTP {resp.status_code}")
                return resp.json()
        except BaserowWriteError:
            raise
        except Exception as e:
            raise BaserowUnavailableError(f"Network error fetching table fields: {e}") from e

    def fetch_row_raw(self, row_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a single row raw with user_field_names=true."""
        if not (self.api_token and self.media_table_id):
            raise BaserowUnavailableError("Baserow credentials or media_table_id not configured")

        url = f"{self.api_url}/api/database/rows/table/{self.media_table_id}/{row_id}/?user_field_names=true"
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                resp = client.get(url, headers=self._headers())
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 404:
                    return None
                else:
                    raise BaserowUnavailableError(f"Failed to fetch row {row_id}: HTTP {resp.status_code}")
        except BaserowWriteError:
            raise
        except Exception as e:
            raise BaserowUnavailableError(f"Network error fetching row {row_id}: {e}") from e

    def create_row(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new row in the Media table."""
        if not (self.api_token and self.media_table_id):
            raise BaserowUnavailableError("Baserow credentials or media_table_id not configured")

        url = f"{self.api_url}/api/database/rows/table/{self.media_table_id}/?user_field_names=true"
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                resp = client.post(url, headers=self._headers(), json=fields)
                if resp.status_code in (200, 201):
                    return resp.json()
                else:
                    raise BaserowWriteError(redact_secrets(f"Failed to create row: HTTP {resp.status_code} - {resp.text}"))
        except BaserowWriteError:
            raise
        except Exception as e:
            raise BaserowUnavailableError(redact_secrets(f"Network error creating row: {e}")) from e

    def patch_row(self, row_id: int, fields: Dict[str, Any]) -> Dict[str, Any]:
        """PATCH minimal fields into an existing Media row."""
        if not (self.api_token and self.media_table_id):
            raise BaserowUnavailableError("Baserow credentials or media_table_id not configured")

        url = f"{self.api_url}/api/database/rows/table/{self.media_table_id}/{row_id}/?user_field_names=true"
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                resp = client.patch(url, headers=self._headers(), json=fields)
                if resp.status_code == 200:
                    return resp.json()
                else:
                    raise BaserowWriteError(redact_secrets(f"Failed to patch row {row_id}: HTTP {resp.status_code} - {resp.text}"))
        except BaserowWriteError:
            raise
        except Exception as e:
            raise BaserowUnavailableError(redact_secrets(f"Network error patching row {row_id}: {e}")) from e

    def ensure_select_option(self, field_name: str, option_name: str) -> str:
        """Ensure a select option exists, creating it if permitted.

        Strictly permitted ONLY for Country and Place, location.
        """
        norm_field = field_name.strip().lower()
        if norm_field not in ALLOWED_SELECT_CREATION_FIELDS:
            raise TaxonomyForbiddenError(
                f"Automatic creation of select option '{option_name}' is forbidden for field '{field_name}'. "
                "Only Country and Place, location allow automatic option creation."
            )

        fields = self.fetch_table_fields()
        target_field = None
        for f in fields:
            if f.get("name", "").strip().lower() == norm_field:
                target_field = f
                break

        if not target_field:
            raise BaserowSchemaError(f"Field '{field_name}' not found in live Media table schema")

        field_id = target_field["id"]
        existing_options = target_field.get("select_options", [])
        norm_target_val = _normalize_option_text(option_name)

        matches = []
        for opt in existing_options:
            if _normalize_option_text(opt.get("value", "")) == norm_target_val:
                matches.append(opt)

        if len(matches) == 1:
            return matches[0]["value"]
        elif len(matches) > 1:
            raise AmbiguousOptionError(
                f"Multiple options match '{option_name}' in field '{field_name}': {[m['value'] for m in matches]}"
            )

        # Append new option preserving all existing options
        new_select_options = [
            {"id": opt["id"], "value": opt["value"], "color": opt.get("color", "blue")}
            for opt in existing_options
        ]
        new_select_options.append({"value": option_name.strip(), "color": "blue"})

        url = f"{self.api_url}/api/database/fields/{field_id}/"
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                resp = client.patch(url, headers=self._headers(), json={"select_options": new_select_options})
                if resp.status_code != 200:
                    raise BaserowWriteError(
                        redact_secrets(f"Failed to add select option '{option_name}' to field {field_id}: HTTP {resp.status_code} - {resp.text}")
                    )
                updated_field = resp.json()
                # Verify option exists in live response
                for opt in updated_field.get("select_options", []):
                    if _normalize_option_text(opt.get("value", "")) == norm_target_val:
                        return opt["value"]
                raise BaserowSchemaError(f"Option '{option_name}' was not found in schema response after mutation")
        except BaserowWriteError:
            raise
        except Exception as e:
            raise BaserowUnavailableError(redact_secrets(f"Network error updating field options: {e}")) from e


DEFAULT_MEDIA_TABLE_FIELDS = [
    {"id": 101, "name": "Title", "type": "text"},
    {"id": 102, "name": "Date", "type": "date"},
    {
        "id": 103,
        "name": "Category",
        "type": "single_select",
        "select_options": [
            {"id": 1, "value": "Srimad Bhagavatam", "color": "blue"},
            {"id": 2, "value": "Bhagavad-gita", "color": "red"},
            {"id": 3, "value": "Caitanya caritamrta", "color": "green"},
            {"id": 4, "value": "Festival", "color": "purple"},
            {"id": 5, "value": "Initiation", "color": "orange"},
            {"id": 6, "value": "Kirtan", "color": "yellow"},
            {"id": 7, "value": "Sunday Feast", "color": "brown"},
            {"id": 8, "value": "Seminar", "color": "pink"},
        ],
    },
    {
        "id": 104,
        "name": "Tag",
        "type": "multiple_select",
        "select_options": [
            {"id": 10, "value": "1.3.4", "color": "blue"},
            {"id": 11, "value": "intro", "color": "gray"},
            {"id": 12, "value": "1.18", "color": "green"},
        ],
    },
    {
        "id": 105,
        "name": "Language",
        "type": "single_select",
        "select_options": [
            {"id": 20, "value": "English", "color": "blue"},
            {"id": 21, "value": "Russian", "color": "red"},
        ],
    },
    {
        "id": 106,
        "name": "Status Media",
        "type": "single_select",
        "select_options": [
            {"id": 30, "value": "Not-started", "color": "gray"},
            {"id": 31, "value": "Completed", "color": "green"},
        ],
    },
    {
        "id": 107,
        "name": "Status thumb",
        "type": "single_select",
        "select_options": [
            {"id": 40, "value": "Not-started", "color": "gray"},
            {"id": 41, "value": "Completed", "color": "green"},
        ],
    },
    {
        "id": 108,
        "name": "Status Transcript",
        "type": "single_select",
        "select_options": [
            {"id": 50, "value": "Not-started", "color": "gray"},
            {"id": 51, "value": "Completed", "color": "green"},
        ],
    },
    {"id": 109, "name": "Filename", "type": "text"},
    {"id": 110, "name": "Youtube", "type": "url"},
    {"id": 111, "name": "Youtube descr", "type": "long_text"},
    {"id": 112, "name": "Audio link", "type": "url"},
    {"id": 113, "name": "Notes", "type": "long_text"},
    {"id": 114, "name": "Created_on", "type": "date"},
    {"id": 115, "name": "Last modified by", "type": "date"},
    {"id": 116, "name": "Last modified", "type": "date"},
    {"id": 117, "name": "Thumb image", "type": "file"},
    {"id": 118, "name": "Transcriber", "type": "text"},
    {"id": 119, "name": "imported_on", "type": "date"},
    {"id": 120, "name": "Alt. Links", "type": "long_text"},
    {"id": 121, "name": "Article Link", "type": "url"},
    {"id": 122, "name": "Themes", "type": "multiple_select", "select_options": []},
    {"id": 123, "name": "Transcript Archive link", "type": "url"},
    {"id": 124, "name": "Media Archive link", "type": "url"},
    {"id": 125, "name": "media_archive_path", "type": "text"},
    {
        "id": 126,
        "name": "Country",
        "type": "single_select",
        "select_options": [
            {"id": 60, "value": "Germany", "color": "blue"},
            {"id": 61, "value": "India", "color": "orange"},
            {"id": 62, "value": "Slovakia", "color": "red"},
            {"id": 63, "value": "United Kingdom", "color": "green"},
        ],
    },
    {
        "id": 127,
        "name": "Place, location",
        "type": "single_select",
        "select_options": [
            {"id": 70, "value": "Leipzig", "color": "blue"},
            {"id": 71, "value": "Vrindavan", "color": "orange"},
            {"id": 72, "value": "Bratislava", "color": "red"},
            {"id": 73, "value": "London", "color": "green"},
        ],
    },
]


class FakeBaserowWriteAdapter:
    """In-memory test double of BaserowWriteAdapter for hermetic offline testing."""

    def __init__(
        self,
        initial_rows: Optional[List[Dict[str, Any]]] = None,
        initial_fields: Optional[List[Dict[str, Any]]] = None,
    ):
        import copy
        self.fields: List[Dict[str, Any]] = copy.deepcopy(initial_fields or DEFAULT_MEDIA_TABLE_FIELDS)
        self.rows: Dict[int, Dict[str, Any]] = {}
        if initial_rows:
            for r in initial_rows:
                rid = r.get("id")
                if rid:
                    self.rows[int(rid)] = copy.deepcopy(r)
        self.next_row_id: int = max(self.rows.keys(), default=1000) + 1
        self.next_option_id: int = 500
        self.calls: List[Dict[str, Any]] = []

        # Fault injection toggles
        self.simulate_network_failure: bool = False
        self.simulate_timeout_on_create: bool = False
        self.simulate_timeout_on_patch: bool = False
        self.simulate_schema_error: bool = False

    def __repr__(self) -> str:
        return f"<FakeBaserowWriteAdapter rows={len(self.rows)} fields={len(self.fields)}>"

    def __str__(self) -> str:
        return self.__repr__()

    def fetch_table_fields(self) -> List[Dict[str, Any]]:
        import copy
        self.calls.append({"action": "fetch_table_fields"})
        if self.simulate_network_failure:
            raise BaserowUnavailableError("Simulated network failure fetching fields")
        return copy.deepcopy(self.fields)

    def fetch_row_raw(self, row_id: int) -> Optional[Dict[str, Any]]:
        import copy
        self.calls.append({"action": "fetch_row_raw", "row_id": row_id})
        if self.simulate_network_failure:
            raise BaserowUnavailableError("Simulated network failure fetching row")
        r = self.rows.get(row_id)
        return copy.deepcopy(r) if r else None

    def create_row(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        import copy
        self.calls.append({"action": "create_row", "fields": copy.deepcopy(fields)})
        if self.simulate_network_failure:
            raise BaserowUnavailableError("Simulated network failure creating row")
        if self.simulate_timeout_on_create:
            # Emulate scenario where write committed in DB but response timed out
            rid = self.next_row_id
            self.next_row_id += 1
            new_row = copy.deepcopy(fields)
            new_row["id"] = rid
            self.rows[rid] = new_row
            raise BaserowUnavailableError("Simulated timeout on create (uncertain transport)")
        if self.simulate_schema_error:
            raise BaserowSchemaError("Simulated schema error on create")

        rid = self.next_row_id
        self.next_row_id += 1
        new_row = copy.deepcopy(fields)
        new_row["id"] = rid
        self.rows[rid] = new_row
        return copy.deepcopy(new_row)

    def patch_row(self, row_id: int, fields: Dict[str, Any]) -> Dict[str, Any]:
        import copy
        self.calls.append({"action": "patch_row", "row_id": row_id, "fields": copy.deepcopy(fields)})
        if self.simulate_network_failure:
            raise BaserowUnavailableError("Simulated network failure patching row")
        if self.simulate_timeout_on_patch:
            # Emulate timeout where mutation applied in DB before transport cut
            if row_id in self.rows:
                self.rows[row_id].update(copy.deepcopy(fields))
            raise BaserowUnavailableError("Simulated timeout on patch (uncertain transport)")
        if row_id not in self.rows:
            raise BaserowWriteError(f"Row {row_id} does not exist in fake")

        self.rows[row_id].update(copy.deepcopy(fields))
        return copy.deepcopy(self.rows[row_id])

    def ensure_select_option(self, field_name: str, option_name: str) -> str:
        self.calls.append({"action": "ensure_select_option", "field_name": field_name, "option_name": option_name})
        if self.simulate_network_failure:
            raise BaserowUnavailableError("Simulated network failure ensuring select option")

        norm_field = field_name.strip().lower()
        if norm_field not in ALLOWED_SELECT_CREATION_FIELDS:
            raise TaxonomyForbiddenError(
                f"Automatic creation of select option '{option_name}' is forbidden for field '{field_name}'. "
                "Only Country and Place, location allow automatic option creation."
            )

        target_field = None
        for f in self.fields:
            if f.get("name", "").strip().lower() == norm_field:
                target_field = f
                break

        if not target_field:
            raise BaserowSchemaError(f"Field '{field_name}' not found in fake schema")

        existing_options = target_field.get("select_options", [])
        norm_target_val = _normalize_option_text(option_name)

        matches = []
        for opt in existing_options:
            if _normalize_option_text(opt.get("value", "")) == norm_target_val:
                matches.append(opt)

        if len(matches) == 1:
            return matches[0]["value"]
        elif len(matches) > 1:
            raise AmbiguousOptionError(
                f"Multiple options match '{option_name}' in field '{field_name}': {[m['value'] for m in matches]}"
            )

        # Create option
        new_opt = {
            "id": self.next_option_id,
            "value": option_name.strip(),
            "color": "blue",
        }
        self.next_option_id += 1
        existing_options.append(new_opt)
        return new_opt["value"]
