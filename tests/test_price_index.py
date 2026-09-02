"""Tests for the pooled price reference.

The index is what lets a tutti run use prices facebook observed twenty minutes
earlier, so most of these are about what survives between runs.
"""

import pathlib
import time
from typing import Iterator, List

import pytest
from diskcache import Cache

from ai_marketplace_monitor.price_index import (
    FRESHNESS_SECONDS,
    PriceObservation,
    observations,
    record,
    reference,
)
from ai_marketplace_monitor.price_stats import describe_composition, describe_price
from ai_marketplace_monitor.utils import CacheType


@pytest.fixture
def store(tmp_path: object) -> Iterator[Cache]:
    cache = Cache(str(tmp_path))  # type: ignore[arg-type]
    yield cache
    cache.close()


def _obs(
    marketplace: str, listing_id: str, amount: int, currency: str = "CHF", age: float = 0
) -> PriceObservation:
    return PriceObservation(
        marketplace=marketplace,
        listing_id=listing_id,
        amount=amount,
        currency=currency,
        seen_at=time.time() - age,
    )


def test_nothing_recorded_yields_no_reference(store: Cache) -> None:
    stats, composition = reference("gopro", "CHF", store)
    assert stats is None
    assert composition == {}


def test_observations_from_two_marketplaces_pool_into_one_median(store: Cache) -> None:
    """The point of the whole module: a GoPro is worth what it is worth."""
    record("gopro", [_obs("tutti", str(i), 100 + i * 10) for i in range(4)], store)
    record("facebook", [], store)  # a different hunt must not leak in
    record("gopro", [_obs("facebook", str(i), 300 + i * 10) for i in range(4)], store)

    stats, composition = reference("gopro", "CHF", store)

    assert stats is not None
    assert stats.count == 8
    assert composition == {"tutti": 4, "facebook": 4}
    assert stats.minimum == 100
    assert stats.maximum == 330


def test_hunts_do_not_share_observations(store: Cache) -> None:
    record("gopro", [_obs("tutti", str(i), 100) for i in range(4)], store)
    record("velo", [_obs("tutti", str(i), 900) for i in range(4)], store)

    stats, _ = reference("velo", "CHF", store)

    assert stats is not None
    assert stats.median == 900


def test_reseeing_a_listing_does_not_count_it_twice(store: Cache) -> None:
    """The same offer in ten consecutive runs must not outweigh its neighbours."""
    for _ in range(10):
        record("gopro", [_obs("tutti", "same", 999)], store)
    record("gopro", [_obs("tutti", str(i), 100) for i in range(4)], store)

    stats, composition = reference("gopro", "CHF", store)

    assert stats is not None
    assert stats.count == 5
    assert composition == {"tutti": 5}


def test_observations_survive_between_runs(store: Cache) -> None:
    """A tutti run must be able to use what facebook saw earlier."""
    record("gopro", [_obs("facebook", str(i), 200) for i in range(4)], store)

    later = observations("gopro", store)

    assert len(later) == 4
    assert {o.marketplace for o in later} == {"facebook"}


def test_stale_observations_are_dropped(store: Cache) -> None:
    """A three-week-old price is not the going rate any more."""
    stale = [_obs("tutti", f"old{i}", 900, age=FRESHNESS_SECONDS + 60) for i in range(4)]
    record("gopro", stale, store)
    record("gopro", [_obs("tutti", f"new{i}", 100) for i in range(4)], store)

    stats, _ = reference("gopro", "CHF", store)

    assert stats is not None
    assert stats.count == 4
    assert stats.maximum == 100


def test_a_malformed_record_is_ignored_not_fatal(store: Cache) -> None:
    store.set(
        (CacheType.PRICE_OBSERVATION.value, "gopro"),
        {"broken": {"marketplace": "tutti"}},
        tag=CacheType.PRICE_OBSERVATION.value,
    )
    assert observations("gopro", store) == []


#
# Currency
#


def test_same_currency_needs_no_conversion(store: Cache) -> None:
    record("gopro", [_obs("tutti", str(i), 100 + i, "CHF") for i in range(4)], store)
    stats, _ = reference("gopro", "CHF", store)
    assert stats is not None
    assert stats.minimum == 100


def test_a_convertible_currency_is_pooled(store: Cache) -> None:
    record("gopro", [_obs("tutti", str(i), 100, "CHF") for i in range(4)], store)
    record("gopro", [_obs("facebook", str(i), 100, "EUR") for i in range(4)], store)

    stats, composition = reference("gopro", "CHF", store)

    assert stats is not None
    assert composition == {"tutti": 4, "facebook": 4}
    # EUR 100 is worth more than CHF 100 is not assumed — only that both landed
    assert stats.count == 8


def test_an_unconvertible_currency_is_dropped_not_mixed_in(store: Cache) -> None:
    """A CHF median polluted by an unknown unit is worse than a smaller sample."""
    record("gopro", [_obs("tutti", str(i), 100, "CHF") for i in range(4)], store)
    record("gopro", [_obs("facebook", f"x{i}", 100, "GALLEONS") for i in range(4)], store)

    stats, composition = reference("gopro", "CHF", store)

    assert stats is not None
    assert stats.count == 4
    assert composition == {"tutti": 4}


#
# How it reads
#


def test_composition_is_named_when_several_marketplaces_contributed() -> None:
    assert describe_composition({"tutti": 21, "facebook": 9}) == " (9 facebook, 21 tutti)"


def test_composition_is_silent_for_a_single_marketplace() -> None:
    """Naming one source adds noise without adding information."""
    assert describe_composition({"tutti": 21}) == ""
    assert describe_composition({}) == ""
    assert describe_composition(None) == ""


def test_readout_names_its_basis(store: Cache) -> None:
    record("gopro", [_obs("tutti", str(i), 200 + i * 50) for i in range(4)], store)
    record("gopro", [_obs("facebook", str(i), 300 + i * 50) for i in range(4)], store)
    stats, composition = reference("gopro", "CHF", store)

    text = describe_price(150, stats, "CHF", composition)

    assert "comparable listings (4 facebook, 4 tutti)" in text


def test_readout_without_composition_is_unchanged(store: Cache) -> None:
    """The argument is optional, so existing callers keep their wording."""
    record("gopro", [_obs("tutti", str(i), 200 + i * 50) for i in range(4)], store)
    stats, _ = reference("gopro", "CHF", store)

    assert "(" in describe_price(150, stats, "CHF")  # only the range parenthesis
    assert "tutti" not in describe_price(150, stats, "CHF")


#
# The marketplaces feed it
#


def test_tutti_builds_observations_from_keyword_matches() -> None:
    from ai_marketplace_monitor.listing import Listing
    from ai_marketplace_monitor.tutti import (
        TuttiItemConfig,
        TuttiMarketplace,
        TuttiMarketplaceConfig,
    )

    marketplace = TuttiMarketplace("tutti", None)
    marketplace.configure(TuttiMarketplaceConfig(name="tutti"))
    item_config = TuttiItemConfig(name="gopro", search_phrases=["gopro"], keywords="13")

    def listing(listing_id: str, title: str, price: str) -> Listing:
        return Listing(
            marketplace="tutti",
            name="gopro",
            id=listing_id,
            title=title,
            image="",
            price=price,
            post_url=f"https://www.tutti.ch/de/vi/x/{listing_id}",
            location="8050 Zürich, ZH",
            seller="s",
            condition="",
            description="",
        )

    found: List[PriceObservation] = marketplace.observations(
        [
            listing("1", "GoPro Hero 13", "CHF 300.-"),
            listing("2", "GoPro Hero 9", "CHF 100.-"),
            listing("3", "GoPro Hero 13", "Gratis"),
        ],
        item_config,
    )

    assert [o.listing_id for o in found] == ["1"]
    assert found[0].amount == 300
    assert found[0].currency == "CHF"
    assert found[0].marketplace == "tutti"


def test_tutti_readout_names_the_pooled_basis() -> None:
    """The composition must reach the listing text, not just the index.

    This was missed once: the index returned it but the call site dropped it,
    and only a live run showed the difference.
    """
    import ai_marketplace_monitor.tutti as tutti_module

    source = pathlib.Path(tutti_module.__file__).read_text(encoding="utf-8")
    assert "stats, TUTTI_CURRENCY, composition" in source


def test_facebook_readout_names_the_pooled_basis() -> None:
    import ai_marketplace_monitor.facebook as facebook_module

    source = pathlib.Path(facebook_module.__file__).read_text(encoding="utf-8")
    assert "stats, currency_label, composition" in source
