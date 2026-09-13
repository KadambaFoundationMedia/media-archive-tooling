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


def test_location_lookup_rate_limiting(location_env):
    """Verify that LocationLookupProvider enforces min_request_interval."""
    import time
    call_times = []

    def handler(request: httpx.Request) -> httpx.Response:
        call_times.append(time.time())
        return httpx.Response(200, json=[])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = LocationLookupProvider(cache_db=location_env, client=client)
    provider.min_request_interval = 0.1  # Short interval for test

    provider.lookup("LocationOne")
    provider.lookup("LocationTwo")

    assert len(call_times) == 2
    assert call_times[1] - call_times[0] >= 0.09


def test_where_resolver_near_tie_ambiguity():
    """Verify near-tie fuzzy candidates produce AMBIGUOUS resolution state with alternatives."""
    locations = [
        {"canonical_place": "Springfield-IL", "country": "United States", "country_iso2": "us", "aliases": ["springfielda"]},
        {"canonical_place": "Springfield-MO", "country": "United States", "country_iso2": "us", "aliases": ["springfieldb"]}
    ]
    resolver = WhereResolver(locations_data=locations, countries_data={"United States": "us"})
    res, _ = resolver.resolve("Lecture in Springfield")

    assert res.state == ResolutionState.AMBIGUOUS
    assert len(res.alternatives) >= 1
    assert "Springfield-MO-us" in res.alternatives


def test_where_resolver_candidate_filtering_for_online(location_env):
    """Verify short words and technical/stop words are skipped for online lookups."""
    queried = []

    def handler(request: httpx.Request) -> httpx.Response:
        queried.append(str(request.url))
        return httpx.Response(200, json=[])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    loc_provider = LocationLookupProvider(cache_db=location_env, client=client)

    resolver = WhereResolver(
        locations_data=[],
        countries_data={},
        location_lookup_provider=loc_provider
    )

    # All tokens are stop words ("kks", "part", "lecture", "clean") or short (<4 chars)
    resolver.resolve("kks part 1 lecture clean 01")
    assert len(queried) == 0


