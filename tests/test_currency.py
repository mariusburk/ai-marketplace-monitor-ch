"""Tests for currency conversion.

No test reaches the network: `fetch_with_retry` is patched, so fixer.io's
100-call monthly budget is never touched by the suite.
"""

import json
import time
from typing import Any, Iterator
from unittest.mock import patch

import pytest
from diskcache import Cache

from ai_marketplace_monitor.currency import (
    FIXER_BASE,
    RATE_TTL_SECONDS,
    convert,
    fixer_rates,
    frankfurter_rates,
)
from ai_marketplace_monitor.listing import Listing
from ai_marketplace_monitor.price_stats import convert_for_display
from ai_marketplace_monitor.utils import CacheType, MonitorConfig

# EUR-based, as the free plan serves them.
RATES = {"CHF": 0.94, "USD": 1.08, "GBP": 0.84}


@pytest.fixture
def store(tmp_path: object) -> Iterator[Cache]:
    cache = Cache(str(tmp_path))  # type: ignore[arg-type]
    yield cache
    cache.close()


def _response(payload: dict) -> tuple:
    return (json.dumps(payload).encode("utf-8"), "application/json")


#
# Fetching
#


def test_rates_are_fetched_and_cached(store: Cache) -> None:
    """The free plan allows 100 calls a month, so a second call must not happen."""
    with patch("ai_marketplace_monitor.currency.fetch_with_retry") as fetch:
        fetch.return_value = _response({"success": True, "rates": RATES})
        first = fixer_rates("key", store)
        second = fixer_rates("key", store)

    assert first == second == RATES
    assert fetch.call_count == 1


def test_stale_rates_are_refetched(store: Cache) -> None:
    store.set(
        (CacheType.EXCHANGE_RATES.value, "fixer", FIXER_BASE),
        {"fetched_at": time.time() - RATE_TTL_SECONDS - 60, "rates": {"CHF": 1.0}},
        tag=CacheType.EXCHANGE_RATES.value,
    )
    with patch("ai_marketplace_monitor.currency.fetch_with_retry") as fetch:
        fetch.return_value = _response({"success": True, "rates": RATES})
        rates = fixer_rates("key", store)

    assert rates == RATES
    assert fetch.call_count == 1


def test_a_refused_key_yields_no_rates(store: Cache) -> None:
    with patch("ai_marketplace_monitor.currency.fetch_with_retry") as fetch:
        fetch.return_value = _response({"success": False, "error": {"info": "invalid access key"}})
        assert fixer_rates("wrong", store) is None


def test_an_unreachable_api_yields_no_rates(store: Cache) -> None:
    with patch("ai_marketplace_monitor.currency.fetch_with_retry") as fetch:
        fetch.return_value = None
        assert fixer_rates("key", store) is None


#
# Converting
#


def test_same_currency_is_returned_unchanged(store: Cache) -> None:
    result = convert(100, "CHF", "CHF", "key", store)
    assert result is not None
    assert result.amount == 100
    assert not result.live


def test_cross_rate_through_eur(store: Cache) -> None:
    """The free plan has no choice of base, so USD to CHF crosses through EUR."""
    with patch("ai_marketplace_monitor.currency.fetch_with_retry") as fetch:
        fetch.return_value = _response({"success": True, "rates": RATES})
        result = convert(108, "USD", "CHF", "key", store)

    assert result is not None
    assert result.live
    # 108 USD -> 100 EUR -> 94 CHF
    assert result.amount == 94


def test_conversion_from_the_base_currency(store: Cache) -> None:
    with patch("ai_marketplace_monitor.currency.fetch_with_retry") as fetch:
        fetch.return_value = _response({"success": True, "rates": RATES})
        result = convert(100, "EUR", "CHF", "key", store)

    assert result is not None
    assert result.amount == 94


def test_without_a_key_frankfurter_is_used(store: Cache) -> None:
    """Conversion must work with no key at all — that is the default path."""
    with patch("ai_marketplace_monitor.currency.fetch_with_retry") as fetch:
        fetch.return_value = _response({"base": "EUR", "rates": {"CHF": 0.94}})
        result = convert(100, "EUR", "CHF", None, store)

    assert result is not None
    assert result.provider == "frankfurter"
    assert result.amount == 94


def test_frankfurter_needs_no_key_and_asks_for_the_real_base(store: Cache) -> None:
    """Its free tier allows any base, so no cross rate is needed."""
    with patch("ai_marketplace_monitor.currency.fetch_with_retry") as fetch:
        fetch.return_value = _response({"base": "USD", "rates": {"CHF": 0.81}})
        frankfurter_rates("USD", store)

    assert "base=USD" in fetch.call_args.args[0]
    assert "access_key" not in fetch.call_args.args[0]


def test_the_bundled_snapshot_is_the_last_resort(store: Cache) -> None:
    """A slightly stale rate beats refusing to compare."""
    with patch("ai_marketplace_monitor.currency.fetch_with_retry") as fetch:
        fetch.return_value = None
        result = convert(100, "EUR", "CHF", None, store)

    assert result is not None
    assert result.provider == "bundled"
    assert result.amount > 0


def test_a_failing_fixer_falls_through_to_frankfurter(store: Cache) -> None:
    """Each source is a fallback for the one before it."""
    with patch("ai_marketplace_monitor.currency.fetch_with_retry") as fetch:
        fetch.side_effect = [None, _response({"base": "EUR", "rates": {"CHF": 0.94}})]
        result = convert(100, "EUR", "CHF", "key", store)

    assert result is not None
    assert result.provider == "frankfurter"


def test_an_unknown_currency_yields_nothing(store: Cache) -> None:
    with patch("ai_marketplace_monitor.currency.fetch_with_retry") as fetch:
        fetch.return_value = None
        assert convert(100, "GALLEONS", "CHF", None, store) is None


#
# What gets shown
#


def _monitor(**kwargs: Any) -> MonitorConfig:
    return MonitorConfig(name="monitor", **kwargs)


def test_no_display_currency_means_no_conversion() -> None:
    assert convert_for_display(100, "USD", _monitor()) == ""


def test_a_price_already_in_the_display_currency_is_not_converted() -> None:
    """Showing "CHF 100 (CHF 100.-)" would be noise."""
    assert convert_for_display(100, "CHF", _monitor(currency="CHF")) == ""


def test_a_foreign_price_is_converted_for_display(store: Cache) -> None:
    with patch("ai_marketplace_monitor.currency.fetch_with_retry") as fetch:
        fetch.return_value = _response({"base": "EUR", "rates": {"CHF": 0.94}})
        shown = convert_for_display(100, "EUR", _monitor(currency="CHF"))

    assert shown.startswith("CHF ")


def test_monitor_currency_is_normalised() -> None:
    assert _monitor(currency="chf").currency == "CHF"


def test_an_unknown_monitor_currency_is_rejected() -> None:
    with pytest.raises(ValueError, match="not recognized"):
        _monitor(currency="GALLEONS")


#
# The listing carries both
#


def test_converted_price_defaults_to_empty() -> None:
    """Entries cached before this field existed must still load."""
    listing = Listing(
        marketplace="tutti",
        name="n",
        id="1",
        title="t",
        image="i",
        price="CHF 100.-",
        post_url="u",
        location="l",
        seller="s",
        condition="c",
        description="d",
    )
    assert listing.converted_price == ""


def test_the_original_price_is_never_replaced() -> None:
    """A conversion is an estimate; the listing's own number is the fact."""
    listing = Listing(
        marketplace="facebook",
        name="n",
        id="1",
        title="t",
        image="i",
        price="$108",
        post_url="u",
        location="l",
        seller="s",
        condition="c",
        description="d",
        converted_price="CHF 94",
    )
    assert listing.price == "$108"
    assert listing.converted_price == "CHF 94"
