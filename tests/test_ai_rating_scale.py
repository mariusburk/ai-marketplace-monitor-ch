"""The scale the model is asked to rate on, and the words put on its answer.

A camera backpack came back as "Poor match ... (3)" — and 3 was both what the
scale called an acceptable rung and the default threshold for notifying, so
everything the model itself called poor was passed on.
"""

from typing import Any

import pytest

from ai_marketplace_monitor.ai import AIBackend, AIConfig, AIResponse, price_placement
from ai_marketplace_monitor.listing import Listing
from ai_marketplace_monitor.marketplace import ItemConfig, MarketplaceConfig


def _listing(price: str = "CHF 320", amount: int | None = 320) -> Listing:
    return Listing(
        marketplace="tutti",
        name="gopro",
        id="1",
        title="GoPro Hero 13 Black",
        image="",
        price=price,
        post_url="https://www.tutti.ch/de/vi/x/1",
        location="Zürich",
        seller="s",
        condition="Gebraucht",
        description="kaum benutzt",
        price_basis={"amount": amount} if amount else {},
    )


def _prompt(item: ItemConfig, listing: Listing | None = None) -> str:
    backend: Any = AIBackend(config=AIConfig(name="probe"))
    return backend.get_prompt(listing or _listing(), item, MarketplaceConfig(name="tutti"))


#
# The ladder has to run one way
#


@pytest.mark.parametrize("score", [1, 2, 3, 4, 5])
def test_every_rung_has_a_name(score: int) -> None:
    assert AIResponse(score=score, comment="x").conclusion


def test_a_bad_match_is_not_called_acceptable() -> None:
    """3 used to be "Poor match", which is also the default notify threshold.

    So the model rating a camera bag "poor" was enough to be told about it.
    """
    assert "Poor" not in AIResponse(score=3, comment="x").conclusion
    assert AIResponse(score=1, comment="x").conclusion == "Not the item"
    assert AIResponse(score=2, comment="x").conclusion == "Wrong one"


def test_the_scale_puts_the_wrong_object_at_the_bottom() -> None:
    """The two cases that prompted this: an accessory, and a service."""
    text = _prompt(ItemConfig(name="gopro", search_phrases=["GoPro Hero 13"]))
    rung_one = text.split("1 - ")[1].split("2 - ")[0]

    for phrase in ("accessory", "spare", "service", "wanted ad"):
        assert phrase in rung_one, phrase


def test_the_scale_names_the_local_words_for_a_wanted_ad() -> None:
    """The listings are German; the prompt is English."""
    text = _prompt(ItemConfig(name="gopro", search_phrases=["GoPro"]))

    assert "Suche" in text
    assert "Cherche" in text


def test_the_scale_says_which_way_it_runs() -> None:
    text = _prompt(ItemConfig(name="gopro", search_phrases=["GoPro"]))

    assert "worst to best" in text


#
# Arithmetic the model should not be doing
#


def test_a_price_inside_the_range_is_stated_as_inside() -> None:
    """A 7B model called CHF 320 "slightly above" a range of 150 to 450.

    A wrong premise costs a good listing a whole rung, so the comparison is
    made here and handed over as a fact.
    """
    item = ItemConfig(name="gopro", search_phrases=["GoPro"], min_price="150", max_price="450 CHF")

    assert "inside the user's range" in price_placement(_listing(), item)


def test_a_price_outside_is_stated_as_outside() -> None:
    item = ItemConfig(name="gopro", search_phrases=["GoPro"], min_price="150", max_price="450")

    assert "above" in price_placement(_listing(amount=900), item)
    assert "below" in price_placement(_listing(amount=20), item)


def test_nothing_is_claimed_without_a_range_or_an_amount() -> None:
    unbounded = ItemConfig(name="gopro", search_phrases=["GoPro"])
    bounded = ItemConfig(name="gopro", search_phrases=["GoPro"], max_price="450")

    assert price_placement(_listing(), unbounded) == ""
    assert price_placement(_listing(amount=None), bounded) == ""


def test_the_placement_reaches_the_prompt() -> None:
    item = ItemConfig(name="gopro", search_phrases=["GoPro"], min_price="150", max_price="450")

    assert "inside the user's range" in _prompt(item)


def test_a_bound_with_a_currency_still_parses() -> None:
    assert AIBackend._bound("450 CHF") == 450
    assert AIBackend._bound("1'500") == 1500
    assert AIBackend._bound(None) is None
    assert AIBackend._bound("keine Zahl") is None
