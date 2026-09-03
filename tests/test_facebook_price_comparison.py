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


def _listing(
    price: str,
    title: str = "GoPro Hero 13",
    description: str = "top",
    listing_id: str = "1",
) -> Listing:
    return Listing(
        marketplace="facebook",
        name="gopro",
        id=listing_id,
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


def test_observations_apply_keyword_filters() -> None:
    item_config = FacebookItemConfig(
        name="gopro", search_phrases=["gopro"], keywords="13", antikeywords=["broken"]
    )
    listings: List[Listing] = [
        _listing("$400"),
        _listing("$50", title="GoPro Hero 13 broken", description="for parts"),
        _listing("$300", title="GoPro Hero 9"),
    ]

    found = _marketplace().observations(listings, item_config, "$")

    assert [o.amount for o in found] == [400]
    assert found[0].marketplace == "facebook"
    assert found[0].currency == "$"


def test_no_extra_request_when_no_price_bounds_are_set() -> None:
    """No bounds means no second search.

    Without bounds the results already span the full price range, so the extra
    unfiltered request would be wasted — and this marketplace has no page, so
    attempting one would raise.
    """
    marketplace = _marketplace()
    item_config = FacebookItemConfig(name="gopro_probe", search_phrases=["gopro"])
    listings = [_listing(f"${p}", listing_id=str(p)) for p in (100, 200, 300, 400)]

    held = marketplace.record_observations(SEARCH_URL, listings, item_config, "$", None, None)

    assert held >= 4


def test_the_same_listing_seen_twice_counts_once() -> None:
    """A listing that reappears in every run must not outweigh its neighbours."""
    from ai_marketplace_monitor.price_index import reference

    marketplace = _marketplace()
    item_config = FacebookItemConfig(name="gopro_dedupe", search_phrases=["gopro"])
    listings = [_listing("$100", listing_id="same")]

    for _ in range(5):
        marketplace.record_observations(SEARCH_URL, listings, item_config, "$", None, None)

    _, composition = reference("gopro_dedupe", "$")
    assert composition == {"facebook": 1}


#
# A price the seller cut
#


def test_a_reduced_price_keeps_only_what_is_being_asked() -> None:
    """Facebook renders the old price struck through next to the new one.

    Joined into "CHF280 | CHF380" it reads as a range or a typo. The second
    number is not on offer — it is what the thing used to cost.
    """
    from ai_marketplace_monitor.utils import split_price

    assert split_price("$280 | $350") == ("$280", "$350")
    assert split_price("CHF 280") == ("CHF 280", "")
    # a listing that was never reduced must not claim it was
    assert split_price("$280 | $280") == ("$280", "")


def test_the_price_filter_reads_the_asked_price_not_the_old_one() -> None:
    """It always did, via the pipe; keep it that way now the split is explicit."""
    from ai_marketplace_monitor.facebook import parse_price

    assert parse_price("$280 | $350") == 280
    assert parse_price("$280") == 280


#
# What counts towards the going rate
#


def test_an_accessory_does_not_set_the_price_of_the_machine() -> None:
    """Keywords cannot tell a motorcycle from a spare part for one.

    On a real hunt for a CHF 5000-15000 bike, 64 of 104 observations were under
    CHF 500 — exhausts, decals, phone mounts, all matching "(BMW) AND (1000)" —
    and dragged the median to 194. Every actual bike then read as wildly
    overpriced, and that figure went to the AI as "the going rate".
    """
    from ai_marketplace_monitor.price_stats import category_window

    low, high = category_window(5000, 15000)

    assert not low <= 29 <= high  # an exhaust
    assert low <= 12000 <= high  # the bike
    assert low <= 21000 <= high  # a dealer's, still the same kind of object


def test_the_window_stays_open_where_nothing_was_said() -> None:
    """A hunt that named no price range has told us nothing to filter on."""
    from ai_marketplace_monitor.price_stats import category_window

    assert category_window(None, None) == (0.0, float("inf"))
    assert category_window(None, 300)[1] == 1200.0
    assert category_window(100, None) == (25.0, float("inf"))


def test_observations_drop_a_price_from_a_different_league() -> None:
    """The window follows the hunt's own range, end to end."""
    item_config = FacebookItemConfig(
        name="s1k", search_phrases=["BMW S1000RR"], min_price="5000", max_price="15000"
    )
    listings: List[Listing] = [
        _listing("$29", title="BMW S1000RR Auspuff-Aufkleber"),
        _listing("$450", title="BMW S1000RR Helm 1000"),
        _listing("$12000", title="BMW S1000RR 2019"),
        _listing("$21000", title="BMW S1000RR 2024"),
    ]

    found = _marketplace().observations(listings, item_config, "$")

    assert sorted(o.amount for o in found) == [12000, 21000]


def test_observations_keep_everything_when_no_range_was_given() -> None:
    item_config = FacebookItemConfig(name="offen", search_phrases=["gopro"])
    listings = [_listing(f"${p}", listing_id=str(p)) for p in (5, 300, 20000)]

    found = _marketplace().observations(listings, item_config, "$")

    assert sorted(o.amount for o in found) == [5, 300, 20000]
