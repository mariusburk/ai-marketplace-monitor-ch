"""What the search learns has to reach the cache the web UI reads.

The price comparison, the converted price and the numbers behind the ruler are
computed on the listing the search yields. The listing the *cache* holds is a
different object — written by `get_listing_details` straight off the item page,
before any of that is known. Nothing wrote the finished one back, so the finds
feed had no comparison to draw, ever, on either marketplace.
"""

import ast
from pathlib import Path
from typing import List

import pytest

from ai_marketplace_monitor.listing import Listing

SOURCES = {
    "facebook": Path("src/ai_marketplace_monitor/facebook.py"),
    "tutti": Path("src/ai_marketplace_monitor/tutti.py"),
}


def _search_body(path: Path) -> ast.AST:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "search":
            return node
    raise AssertionError(f"{path} has no search()")


@pytest.mark.parametrize("marketplace", sorted(SOURCES))
def test_the_finished_listing_is_written_back(marketplace: str) -> None:
    """`search` must cache the listing it yields, not only the raw detail.

    A unit test cannot reach this without driving a browser, so the guard is on
    the source: somewhere in `search` the enriched listing goes to the cache.
    """
    body = _search_body(SOURCES[marketplace])
    calls = [
        node
        for node in ast.walk(body)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "to_cache"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "listing"
    ]

    assert calls, f"{marketplace}: search() never caches the listing it yields"


def test_the_round_trip_keeps_what_the_ruler_needs(tmp_path: Path) -> None:
    """And the cached shape has to carry it back."""
    from diskcache import Cache

    listing = Listing(
        marketplace="tutti",
        name="gopro",
        id="1",
        title="GoPro Hero 13",
        image="i",
        price="CHF 280",
        post_url="https://www.tutti.ch/de/vi/x/1",
        location="Zürich",
        seller="s",
        condition="Gebraucht",
        description="d",
        price_comparison="CHF 280 is 7% below the median CHF 300 of 43 listings",
        converted_price="CHF 280",
        price_basis={"amount": 280, "minimum": 5, "median": 300, "maximum": 600, "count": 43},
        original_price="CHF 350",
    )
    cache = Cache(str(tmp_path))
    try:
        listing.to_cache(listing.post_url, local_cache=cache)
        back = Listing.from_cache(listing.post_url, local_cache=cache)
    finally:
        cache.close()

    assert back is not None
    assert back.price_basis["median"] == 300
    assert back.price_comparison == listing.price_comparison
    assert back.original_price == "CHF 350"


def test_a_listing_cached_before_these_fields_existed_still_loads(tmp_path: Path) -> None:
    """Old cache entries predate every one of them."""
    from diskcache import Cache

    from ai_marketplace_monitor.utils import CacheType

    cache = Cache(str(tmp_path))
    try:
        cache.set(
            (CacheType.LISTING_DETAILS.value, "https://www.tutti.ch/de/vi/x/2"),
            {
                "marketplace": "tutti",
                "name": "gopro",
                "id": "2",
                "title": "t",
                "image": "i",
                "price": "CHF 100",
                "post_url": "https://www.tutti.ch/de/vi/x/2",
                "location": "l",
                "seller": "s",
                "condition": "c",
                "description": "d",
            },
        )
        back = Listing.from_cache("https://www.tutti.ch/de/vi/x/2", local_cache=cache)
    finally:
        cache.close()

    assert back is not None
    assert back.original_price == ""
    assert back.price_basis == {}


def test_the_reduction_does_not_make_a_listing_look_new() -> None:
    """`original_price` is display only; it must stay out of the hash.

    Otherwise a neighbour's price moving — or the marketplace changing its mind
    about what to strike through — would re-notify an unchanged listing.
    """
    fields = {
        "marketplace": "facebook",
        "name": "gopro",
        "id": "1",
        "title": "t",
        "image": "i",
        "price": "$280",
        "post_url": "https://www.facebook.com/marketplace/item/1",
        "location": "l",
        "seller": "s",
        "condition": "c",
        "description": "d",
    }
    plain = Listing(**fields)
    reduced = Listing(**fields, original_price="$350")

    assert plain.hash == reduced.hash


def test_but_the_asked_price_still_does() -> None:
    fields = {
        "marketplace": "facebook",
        "name": "gopro",
        "id": "1",
        "title": "t",
        "image": "i",
        "post_url": "https://www.facebook.com/marketplace/item/1",
        "location": "l",
        "seller": "s",
        "condition": "c",
        "description": "d",
    }
    before: List[str] = ["$350", "$280"]

    assert Listing(**fields, price=before[0]).hash != Listing(**fields, price=before[1]).hash
