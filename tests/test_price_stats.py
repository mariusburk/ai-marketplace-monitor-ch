"""Tests for the price comparison built from a search's own results."""

import pytest

from ai_marketplace_monitor.listing import Listing
from ai_marketplace_monitor.price_stats import MIN_REFERENCE_PRICES, PriceStats, describe_price


def test_stats_from_prices() -> None:
    stats = PriceStats.from_prices([100, 200, 300, 400, 500])
    assert stats is not None
    assert (stats.count, stats.minimum, stats.median, stats.maximum) == (5, 100, 300, 500)


def test_too_few_prices_yields_no_stats() -> None:
    """A couple of offers say nothing about a market price."""
    assert PriceStats.from_prices([100] * (MIN_REFERENCE_PRICES - 1)) is None


def test_giveaways_and_unpriced_listings_are_ignored() -> None:
    """A "Gratis" offer would drag the median down without being comparable."""
    stats = PriceStats.from_prices([100, 200, 300, 400, 0, None, 0])
    assert stats is not None
    assert stats.count == 4
    assert stats.minimum == 100


@pytest.mark.parametrize(
    "price,expected",
    [(150, -50), (300, 0), (450, 50), (330, 10)],
)
def test_percent_from_median(price: int, expected: int) -> None:
    stats = PriceStats.from_prices([100, 200, 300, 400, 500])
    assert stats is not None
    assert stats.percent_from_median(price) == expected


def test_describe_price_below_median() -> None:
    stats = PriceStats.from_prices([200, 300, 300, 400, 500])
    text = describe_price(210, stats, "CHF")
    assert "30% below" in text
    assert "median CHF 300" in text
    assert "5 comparable listings" in text
    assert "range CHF 200-500" in text


def test_describe_price_above_median() -> None:
    stats = PriceStats.from_prices([200, 300, 300, 400, 500])
    assert "33% above" in describe_price(400, stats, "CHF")


def test_describe_price_ignores_noise() -> None:
    """A couple of percent either way is not a bargain worth reporting."""
    stats = PriceStats.from_prices([200, 300, 300, 400, 500])
    assert "about level with" in describe_price(305, stats, "CHF")


@pytest.mark.parametrize(
    "price,stats_prices",
    [(None, [100, 200, 300, 400]), (0, [100, 200, 300, 400]), (100, [])],
)
def test_describe_price_returns_empty_when_it_cannot_compare(
    price: int | None, stats_prices: list
) -> None:
    assert describe_price(price, PriceStats.from_prices(stats_prices), "CHF") == ""


def _listing(**kwargs: object) -> Listing:
    base = {
        "marketplace": "tutti",
        "name": "n",
        "id": "1",
        "title": "t",
        "image": "i",
        "price": "CHF 10.-",
        "post_url": "https://www.tutti.ch/de/vi/x/1",
        "location": "8050 Zürich, ZH",
        "seller": "s",
        "condition": "c",
        "description": "d",
    }
    base.update(kwargs)
    return Listing(**base)  # type: ignore[arg-type]


def test_listing_defaults_to_no_comparison() -> None:
    """Listings cached before this field existed must still load."""
    assert _listing().price_comparison == ""


def test_comparison_does_not_change_the_listing_hash() -> None:
    """An unchanged listing must not look new when a neighbour's price moves.

    The comparison is derived from the other offers, so hashing it would
    invent a change that the listing itself never had.
    """
    assert _listing().hash == _listing(price_comparison="CHF 10 is 20% below").hash
