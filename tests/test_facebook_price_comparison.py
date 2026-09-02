"""Tests for the facebook price comparison."""

from typing import List

import pytest

from ai_marketplace_monitor.facebook import (
    FacebookItemConfig,
    FacebookMarketplace,
    FacebookMarketplaceConfig,
    parse_price,
    price_symbol,
    without_price_params,
)
from ai_marketplace_monitor.listing import Listing
from ai_marketplace_monitor.price_stats import PriceStats, describe_price, format_amount

SEARCH_URL = (
    "https://www.facebook.com/marketplace/houston/search?"
    "query=gopro&minPrice=100&maxPrice=300&radius=50"
)


@pytest.mark.parametrize(
    "rendered,expected",
    [
        ("$180", 180),
        ("$1,234", 1234),
        ("€6,695", 6695),
        ("$1,234.56", 1234),
        # extract_price joins a discounted listing; the current price comes first
        ("$90 | $120", 90),
        ("Free", None),
        ("**unspecified**", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_price(rendered: str | None, expected: int | None) -> None:
    assert parse_price(rendered) == expected


@pytest.mark.parametrize(
    "rendered,expected", [("$180", "$"), ("€6,695", "€"), ("Free", ""), (None, "")]
)
def test_price_symbol(rendered: str | None, expected: str) -> None:
    assert price_symbol(rendered) == expected


def test_without_price_params_strips_only_the_bounds() -> None:
    assert without_price_params(SEARCH_URL) == (
        "https://www.facebook.com/marketplace/houston/search?query=gopro&radius=50"
    )


def test_without_price_params_is_a_noop_without_bounds() -> None:
    url = "https://www.facebook.com/marketplace/houston/search?query=gopro"
    assert without_price_params(url) == url


def test_amount_formatting_follows_the_currency() -> None:
    """A code is separated from the number, a symbol is not."""
    assert format_amount("CHF", 300) == "CHF 300"
    assert format_amount("$", 300) == "$300"


def test_describe_price_uses_the_symbol_without_a_space() -> None:
    stats = PriceStats.from_prices([100, 200, 300, 400])
    assert describe_price(120, stats, "$").startswith("$120 is ")


def _listing(price: str, title: str = "GoPro Hero 13", description: str = "top") -> Listing:
    return Listing(
        marketplace="facebook",
        name="gopro",
        id="1",
        title=title,
        image="i",
        price=price,
        post_url="https://www.facebook.com/marketplace/item/1",
        location="Houston, TX",
        seller="s",
        condition="New",
        description=description,
    )


def _marketplace() -> FacebookMarketplace:
    marketplace = FacebookMarketplace("facebook", None)
    marketplace.configure(FacebookMarketplaceConfig(name="facebook", search_city=["houston"]))
    return marketplace


def test_reference_prices_apply_keyword_filters() -> None:
    item_config = FacebookItemConfig(
        name="gopro", search_phrases=["gopro"], keywords="13", antikeywords=["broken"]
    )
    listings: List[Listing] = [
        _listing("$400"),
        _listing("$50", title="GoPro Hero 13 broken", description="for parts"),
        _listing("$300", title="GoPro Hero 9"),
    ]
    assert _marketplace().reference_prices(listings, item_config) == [400]


def test_no_extra_request_when_no_price_bounds_are_set() -> None:
    """No bounds means no second search.

    Without bounds the results already span the full price range, so the
    extra unfiltered request would be wasted.
    """
    marketplace = _marketplace()
    item_config = FacebookItemConfig(name="gopro", search_phrases=["gopro"])
    listings = [_listing(f"${p}") for p in (100, 200, 300, 400)]

    # a real request would raise, since this marketplace has no page
    stats = marketplace.price_reference(SEARCH_URL, listings, item_config, None, None)

    assert stats is not None
    assert stats.count == 4
    assert stats.median == 250
