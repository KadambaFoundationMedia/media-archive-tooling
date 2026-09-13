import tempfile
import shutil
from pathlib import Path
import httpx
import pytest

from media_archive_tooling.adapters.location import LocationLookupProvider
from media_archive_tooling.renamer.parser.where import WhereResolver
from media_archive_tooling.renamer.models import ResolutionState


@pytest.fixture
def location_env():
    temp_dir = Path(tempfile.mkdtemp())
    cache_db = temp_dir / "location_test.db"
    yield cache_db
    shutil.rmtree(temp_dir)


def test_location_online_lookup_and_cache(location_env):
    called = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        called.append(url)
        if "bratislava" in url.lower():
            mock_data = [{
                "name": "Bratislava",
                "address": {
                    "city": "Bratislava",
                    "country": "Slovakia",
                    "country_code": "sk"
                }
            }]
            return httpx.Response(200, json=mock_data)
        return httpx.Response(200, json=[])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = LocationLookupProvider(cache_db=location_env, client=client)

    # 1. First lookup triggers online query
    res = provider.lookup("Bratislava")
    assert res is not None
    assert res["canonical_place"] == "Bratislava"
    assert res["country"] == "Slovakia"
    assert res["country_iso2"] == "sk"
    assert len(called) == 1

    # 2. Second lookup hits local SQLite cache
    res_cached = provider.lookup("Bratislava")
    assert res_cached is not None
    assert res_cached["country_iso2"] == "sk"
    assert len(called) == 1  # No additional network query


def test_where_resolver_with_online_fallback(location_env):
    """Verify WhereResolver uses LocationLookupProvider as fallback when offline dictionary misses."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "zagreb" in url.lower():
            return httpx.Response(200, json=[{
                "name": "Zagreb",
                "address": {
                    "city": "Zagreb",
                    "country": "Croatia",
                    "country_code": "hr"
                }
            }])
        return httpx.Response(200, json=[])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    loc_provider = LocationLookupProvider(cache_db=location_env, client=client)

    resolver = WhereResolver(
        locations_data=[],  # empty offline locations
        countries_data={},
        location_lookup_provider=loc_provider
    )

    where_res, remaining = resolver.resolve("Lecture in Zagreb today")
    assert where_res.place_location == "Zagreb"
    assert where_res.country_iso2 == "hr"
    assert where_res.state == ResolutionState.PROVISIONAL
    assert any(e.source == "online_location_lookup" for e in where_res.evidence)

