import json
import httpx
import pytest
from unittest.mock import patch

from media_archive_tooling.adapters.baserow import BaserowReferenceProvider


def test_baserow_pagination_and_loading():
    """Verify that BaserowReferenceProvider paginates category_title and Media tables."""
    page_1_cat = {
        "count": 2,
        "next": "https://api.baserow.io/api/database/rows/table/100/?page=2",
        "results": [
            {"category": "Kirtan", "title_matching_terms": "kirtan, bhajans", "folder_path": "Kirtan", "color": "#e74c3c"}
        ]
    }
    page_2_cat = {
        "count": 2,
        "next": None,
        "results": [
            {"category": "Sunday Feast", "title_matching_terms": "sunday feast", "folder_path": "Sunday Feast", "color": "#8e44ad"}
        ]
    }
    page_1_media = {
        "count": 2,
        "next": "https://api.baserow.io/api/database/rows/table/200/?page=2",
        "results": [
            {"place_location": "Vrindavan", "Country": "India"}
        ]
    }
    page_2_media = {
        "count": 2,
        "next": None,
        "results": [
            {"place_location": "Bratislava", "Country": "Slovakia"}
        ]
    }

    def custom_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/table/100/" in url:
            if "page=2" in url:
                return httpx.Response(200, json=page_2_cat)
            return httpx.Response(200, json=page_1_cat)
        elif "/table/200/" in url:
            if "page=2" in url:
                return httpx.Response(200, json=page_2_media)
            return httpx.Response(200, json=page_1_media)
        return httpx.Response(404)

    mock_client = httpx.Client(transport=httpx.MockTransport(custom_handler))

    provider = BaserowReferenceProvider(
        api_token="dummy-token",
        category_table_id="100",
        media_table_id="200"
    )

    with patch("httpx.Client", return_value=mock_client):
        provider.load_all_references()

    # Verify categories from all pages are present
    cats = provider.get_category_titles()
    cat_names = [c["category"] for c in cats]
    assert "Kirtan" in cat_names
    assert "Sunday Feast" in cat_names

    # Verify locations from all pages are present
    loc = provider.find_place("Bratislava")
    assert loc is not None
    assert loc["country"] == "Slovakia"
    assert loc["country_iso2"] == "sk"


def test_baserow_guarded_write_duplicate_prevention():
    """Verify that create_missing_reference_value prevents duplicate writes."""
    provider = BaserowReferenceProvider(
        api_token="dummy-token",
        media_table_id="200"
    )
    provider.load_all_references()

    # Vrindavan already exists in known locations
    success, err = provider.create_missing_reference_value("Vrindavan", "India")
    assert success is False
    assert "Already exists" in err


def test_baserow_guarded_write_missing_prerequisites():
    """Verify that write is refused without required credentials."""
    provider = BaserowReferenceProvider()
    provider.load_all_references()

    success, err = provider.create_missing_reference_value("NewPlace", "Germany")
    assert success is False
    assert "prerequisites unavailable" in err


def test_baserow_guarded_write_success():
    """Verify successful write creates row in Baserow and updates local snapshot."""
    created_payloads = []

    def custom_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            data = json.loads(request.content)
            created_payloads.append(data)
            return httpx.Response(201, json={"id": 42, **data})
        return httpx.Response(404)

    mock_client = httpx.Client(transport=httpx.MockTransport(custom_handler))

    provider = BaserowReferenceProvider(
        api_token="dummy-token",
        media_table_id="200"
    )
    provider.load_all_references()

    with patch("httpx.Client", return_value=mock_client):
        success, err = provider.create_missing_reference_value("Munich", "Germany")

    assert success is True
    assert err is None
    assert len(created_payloads) == 1
    assert created_payloads[0]["place_location"] == "Munich"
    assert created_payloads[0]["Country"] == "Germany"

    # Must be in memory now
    found = provider.find_place("Munich")
    assert found is not None
    assert found["country_iso2"] == "de"

