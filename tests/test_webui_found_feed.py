"""Tests for the finds feed.

The feed and the CSV export read the same three cache namespaces, so most of
these check that one join serves both rather than two drifting apart.
"""

from pathlib import Path
from typing import Any, Dict, Iterator

import pytest
from diskcache import Cache
from fastapi.testclient import TestClient

from ai_marketplace_monitor.utils import CacheType
from ai_marketplace_monitor.webui.config_api import ConfigFileService
from ai_marketplace_monitor.webui.found_export import (
    build_found_rows,
    iter_found_records,
)
from ai_marketplace_monitor.webui.log_handler import LogBroadcastHandler
from ai_marketplace_monitor.webui.server import WebUIConfig, _resolve_auth, create_app

CONFIG = """\
[marketplace.tutti]
canton = ['ZH']

[item.gopro]
marketplace = 'tutti'
search_phrases = 'GoPro'

[user.me]
pushbullet_token = 'o.x'
"""


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Cache]:
    cache = Cache(str(tmp_path / "cache"))
    yield cache
    cache.close()


def _seed(store: Cache, listing_id: str, item: str, price: str, when: str) -> None:
    """Write one find the way the monitor writes it: notified + details + rating."""
    listing_hash = f"hash-{listing_id}"
    store.set(
        (CacheType.USER_NOTIFIED.value, "tutti", listing_id, "me"),
        (when, listing_hash, price),
        tag=CacheType.USER_NOTIFIED.value,
    )
    store.set(
        (CacheType.LISTING_DETAILS.value, f"https://www.tutti.ch/de/vi/x/{listing_id}"),
        {
            "marketplace": "tutti",
            "name": item,
            "id": listing_id,
            "title": f"GoPro {listing_id}",
            "image": f"https://c.tutti.ch/big/{listing_id}.jpg",
            "price": price,
            "post_url": f"https://www.tutti.ch/de/vi/x/{listing_id}",
            "location": "8050 Zürich, ZH",
            "seller": "Verkäufer",
            "condition": "Gebraucht",
            "description": "gut erhalten",
            "converted_price": "CHF 188",
            "price_comparison": "CHF 188 is 32% below the median CHF 275 of 30 listings",
            "price_basis": {
                "amount": 188,
                "minimum": 15,
                "median": 275,
                "maximum": 560,
                "count": 30,
            },
        },
        tag=CacheType.LISTING_DETAILS.value,
    )
    store.set(
        (CacheType.AI_INQUIRY.value, "ollama", item, listing_hash),
        {"score": 4, "comment": "Guter Fund."},
        tag=CacheType.AI_INQUIRY.value,
    )


#
# The shared join
#


def test_a_record_carries_what_the_ruler_needs(store: Cache) -> None:
    """Without the numbers the feed could only print the sentence."""
    _seed(store, "1", "gopro", "CHF 188", "2026-09-02 10:00:00")

    record = next(iter_found_records(store))

    assert record["price_basis"]["median"] == 275
    assert record["price_basis"]["count"] == 30
    assert record["converted_price"] == "CHF 188"
    assert record["image"].endswith("1.jpg")
    assert record["rating"] == 4


def test_the_csv_export_still_produces_its_columns(store: Cache) -> None:
    """The CSV is a projection of the record now; its shape must not move."""
    _seed(store, "1", "gopro", "CHF 188", "2026-09-02 10:00:00")

    rows = build_found_rows(store)

    assert rows[0]["title"] == "GoPro 1"
    assert rows[0]["rating"] == "4"
    assert rows[0]["notified_user"] == "me"
    # the richer fields belong to the feed, not the spreadsheet
    assert "price_basis" not in rows[0]
    assert "image" not in rows[0]


def test_an_unrated_find_reports_no_score(store: Cache) -> None:
    _seed(store, "1", "gopro", "CHF 188", "2026-09-02 10:00:00")
    store.delete((CacheType.AI_INQUIRY.value, "ollama", "gopro", "hash-1"))

    record = next(iter_found_records(store))

    assert record["rating"] is None
    assert build_found_rows(store)[0]["rating"] == ""


def test_records_are_newest_first(store: Cache) -> None:
    _seed(store, "1", "gopro", "CHF 100", "2026-09-01 08:00:00")
    _seed(store, "2", "gopro", "CHF 200", "2026-09-02 08:00:00")

    records = list(iter_found_records(store))

    assert [r["listing_id"] for r in records] == ["2", "1"]


#
# Through HTTP
#


def _client(tmp_path: Path, store: Cache) -> TestClient:
    path = tmp_path / "config.toml"
    path.write_text(CONFIG, encoding="utf-8")
    config = WebUIConfig(
        host="127.0.0.1",
        port=8467,
        config_files=[path],
        log_handler=LogBroadcastHandler(),
        account_file=tmp_path / "webui.toml",
    )
    state, _ = _resolve_auth(config)
    assert config.log_handler is not None
    app = create_app(config, state, ConfigFileService([path]), config.log_handler)
    # the route reads the process-wide cache; point it at the test one
    import ai_marketplace_monitor.webui.server as server_module

    server_module.cache = store
    return TestClient(app)


def test_feed_endpoint_returns_finds(tmp_path: Path, store: Cache) -> None:
    _seed(store, "1", "gopro", "CHF 188", "2026-09-02 10:00:00")

    body: Dict[str, Any] = _client(tmp_path, store).get("/api/found").json()

    assert body["total"] == 1
    assert body["finds"][0]["title"] == "GoPro 1"
    assert body["finds"][0]["price_basis"]["median"] == 275


def test_feed_filters_by_hunt(tmp_path: Path, store: Cache) -> None:
    _seed(store, "1", "gopro", "CHF 100", "2026-09-02 10:00:00")
    _seed(store, "2", "velo", "CHF 200", "2026-09-02 11:00:00")
    client = _client(tmp_path, store)

    body = client.get("/api/found", params={"item": "velo"}).json()

    assert body["total"] == 1
    assert body["finds"][0]["item"] == "velo"
    # the unfiltered list still names every hunt, so the rail can be built
    assert client.get("/api/found").json()["items"] == ["gopro", "velo"]


def test_feed_pages(tmp_path: Path, store: Cache) -> None:
    for i in range(5):
        _seed(store, str(i), "gopro", f"CHF {i}", f"2026-09-0{i + 1} 10:00:00")
    client = _client(tmp_path, store)

    first = client.get("/api/found", params={"limit": 2}).json()
    second = client.get("/api/found", params={"limit": 2, "offset": 2}).json()

    assert first["total"] == second["total"] == 5
    assert len(first["finds"]) == len(second["finds"]) == 2
    assert first["finds"][0]["listing_id"] != second["finds"][0]["listing_id"]


def test_feed_caps_an_absurd_limit(tmp_path: Path, store: Cache) -> None:
    """A page size is a request, not an instruction."""
    body = _client(tmp_path, store).get("/api/found", params={"limit": 100000}).json()
    assert body["limit"] == 200


def test_feed_needs_a_session(tmp_path: Path, store: Cache) -> None:
    path = tmp_path / "config.toml"
    path.write_text(CONFIG, encoding="utf-8")
    config = WebUIConfig(
        host="0.0.0.0",  # noqa: S104 — exposed, as the image binds it
        port=8467,
        config_files=[path],
        log_handler=LogBroadcastHandler(),
        account_file=tmp_path / "webui.toml",
    )
    state, _ = _resolve_auth(config)
    assert config.log_handler is not None
    client = TestClient(create_app(config, state, ConfigFileService([path]), config.log_handler))

    # a fresh instance is in setup mode, so the feed must not answer
    assert client.get("/api/found").status_code == 403


#
# The hunt name
#


def test_the_hunt_name_comes_from_the_notification(store: Cache) -> None:
    """Cached listing details carry no hunt name.

    They are keyed by url and written before the search assigns one, which is
    why the CSV export's "item" column had always been empty. The notification
    entry is where the pairing actually lives.
    """
    _seed(store, "1", "gopro", "CHF 188", "2026-09-02 10:00:00")
    # details as the marketplace really writes them: no name
    key = (CacheType.LISTING_DETAILS.value, "https://www.tutti.ch/de/vi/x/1")
    details = dict(store.get(key))
    details["name"] = ""
    store.set(key, details, tag=CacheType.LISTING_DETAILS.value)
    store.set(
        (CacheType.USER_NOTIFIED.value, "tutti", "1", "me"),
        ("2026-09-02 10:00:00", "hash-1", "CHF 188", "gopro"),
        tag=CacheType.USER_NOTIFIED.value,
    )

    record = next(iter_found_records(store))

    assert record["item"] == "gopro"


@pytest.mark.parametrize(
    "value,expected_item",
    [
        ("2026-09-02 10:00:00", ""),  # oldest shape: a bare date
        (("2026-09-02 10:00:00", "hash-1"), ""),
        (("2026-09-02 10:00:00", "hash-1", "CHF 188"), ""),
        (("2026-09-02 10:00:00", "hash-1", "CHF 188", "gopro"), "gopro"),
    ],
)
def test_every_notification_shape_ever_written_still_reads(
    store: Cache, value: object, expected_item: str
) -> None:
    """Entries from older versions must keep working, without rewriting them."""
    _seed(store, "1", "", "CHF 188", "2026-09-02 10:00:00")
    key = (CacheType.LISTING_DETAILS.value, "https://www.tutti.ch/de/vi/x/1")
    details = dict(store.get(key))
    details["name"] = ""
    store.set(key, details, tag=CacheType.LISTING_DETAILS.value)
    store.set(
        (CacheType.USER_NOTIFIED.value, "tutti", "1", "me"),
        value,
        tag=CacheType.USER_NOTIFIED.value,
    )

    record = next(iter_found_records(store))

    assert record["item"] == expected_item


def test_user_records_the_hunt_name_when_notifying() -> None:
    """The value written must carry the name the feed then reads back."""
    from ai_marketplace_monitor.listing import Listing
    from ai_marketplace_monitor.user import User, UserConfig

    listing = Listing(
        marketplace="tutti",
        name="gopro",
        id="1",
        title="t",
        image="i",
        price="CHF 188",
        post_url="https://www.tutti.ch/de/vi/x/1",
        location="l",
        seller="s",
        condition="c",
        description="d",
    )
    cache = Cache()
    try:
        User(UserConfig(name="me", pushbullet_token="o.x")).to_cache(listing, local_cache=cache)
        stored = cache.get((CacheType.USER_NOTIFIED.value, "tutti", "1", "me"))
    finally:
        cache.close()

    assert stored[3] == "gopro"
