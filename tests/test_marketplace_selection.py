"""Tests for choosing where a hunt searches.

Two levels, answering different questions: a global switch per marketplace, and
a per-hunt selection of one or several marketplaces.
"""

from pathlib import Path
from typing import Callable

import pytest

from ai_marketplace_monitor.config import Config
from ai_marketplace_monitor.facebook import FacebookItemConfig
from ai_marketplace_monitor.tutti import TuttiItemConfig

BOTH_MARKETPLACES = """
[marketplace.tutti]
canton = ['ZH']

[marketplace.facebook]
search_city = 'zurich'

[user.me]
pushbullet_token = 'o.geheim'
"""


def _config(config_file: Callable, body: str) -> Config:
    return Config([Path(config_file(BOTH_MARKETPLACES + body))])


#
# Per-hunt selection
#


def test_a_string_still_names_one_marketplace(config_file: Callable) -> None:
    """Existing configs must keep working untouched."""
    config = _config(
        config_file, "\n[item.velo]\nmarketplace = 'tutti'\nsearch_phrases = 'velo'\n"
    )

    assert set(config.item) == {"velo"}
    assert isinstance(config.item["velo"], TuttiItemConfig)
    assert config.item["velo"].marketplace == "tutti"


def test_a_list_searches_every_named_marketplace(config_file: Callable) -> None:
    config = _config(
        config_file,
        "\n[item.gopro]\nmarketplace = ['tutti', 'facebook']\nsearch_phrases = 'GoPro'\n",
    )

    assert set(config.item) == {"gopro@tutti", "gopro@facebook"}
    assert isinstance(config.item["gopro@tutti"], TuttiItemConfig)
    assert isinstance(config.item["gopro@facebook"], FacebookItemConfig)


def test_expanded_items_keep_the_authored_name(config_file: Callable) -> None:
    """Counters, notifications and log lines must keep talking about one hunt."""
    config = _config(
        config_file,
        "\n[item.gopro]\nmarketplace = ['tutti', 'facebook']\nsearch_phrases = 'GoPro'\n",
    )

    assert {item.name for item in config.item.values()} == {"gopro"}
    assert config.item_names() == ["gopro"]


def test_each_copy_knows_its_own_marketplace(config_file: Callable) -> None:
    """The monitor schedules by comparing this to the marketplace section."""
    config = _config(
        config_file,
        "\n[item.gopro]\nmarketplace = ['tutti', 'facebook']\nsearch_phrases = 'GoPro'\n",
    )

    assert config.item["gopro@tutti"].marketplace == "tutti"
    assert config.item["gopro@facebook"].marketplace == "facebook"


def test_an_absent_marketplace_still_means_the_first_one(config_file: Callable) -> None:
    """Not "all of them" — that would silently double an existing config's searches."""
    config = _config(config_file, "\n[item.velo]\nsearch_phrases = 'velo'\n")

    assert set(config.item) == {"velo"}
    assert config.item["velo"].marketplace == "tutti"


def test_duplicates_in_the_list_are_collapsed(config_file: Callable) -> None:
    config = _config(
        config_file,
        "\n[item.velo]\nmarketplace = ['tutti', 'tutti']\nsearch_phrases = 'velo'\n",
    )

    assert set(config.item) == {"velo"}


def test_an_unknown_marketplace_in_the_list_is_rejected(config_file: Callable) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        _config(
            config_file,
            "\n[item.velo]\nmarketplace = ['tutti', 'ricardo']\nsearch_phrases = 'velo'\n",
        )


def test_an_empty_list_is_rejected(config_file: Callable) -> None:
    with pytest.raises(ValueError, match="lists no marketplace"):
        _config(config_file, "\n[item.velo]\nmarketplace = []\nsearch_phrases = 'velo'\n")


def test_options_are_validated_per_marketplace(config_file: Callable) -> None:
    """A hunt on both marketplaces may only use options both accept.

    `canton` is tutti-only, so putting it on a hunt that also runs on facebook
    has to fail loudly here rather than crash mid-search.
    """
    with pytest.raises(TypeError, match="canton"):
        _config(
            config_file,
            "\n[item.gopro]\nmarketplace = ['tutti', 'facebook']\n"
            "search_phrases = 'GoPro'\ncanton = ['ZH']\n",
        )


#
# Lookup by name
#


def test_find_item_resolves_a_suffixed_key(config_file: Callable) -> None:
    """A person types the hunt's name, not the internal key."""
    config = _config(
        config_file,
        "\n[item.gopro]\nmarketplace = ['tutti', 'facebook']\nsearch_phrases = 'GoPro'\n",
    )

    found = config.find_item("gopro")

    assert found is not None
    assert found.name == "gopro"


def test_find_item_returns_none_for_an_unknown_name(config_file: Callable) -> None:
    config = _config(config_file, "\n[item.velo]\nsearch_phrases = 'velo'\n")
    assert config.find_item("gibtsnicht") is None


#
# The global switch
#


def test_a_disabled_marketplace_is_still_loaded_but_marked(config_file: Callable) -> None:
    """Switching a marketplace off must not delete its hunts."""
    body = "\n[item.velo]\nmarketplace = 'tutti'\nsearch_phrases = 'velo'\n"
    config = Config(
        [Path(config_file(BOTH_MARKETPLACES.replace("canton = ['ZH']", "enabled = false") + body))]
    )

    assert config.marketplace["tutti"].enabled is False
    assert "velo" in config.item


def test_a_disabled_marketplace_skips_its_search_city_requirement(config_file: Callable) -> None:
    """validate_items must not demand a search_city from a marketplace that is off."""
    body = "\n[item.gopro]\nmarketplace = 'facebook'\nsearch_phrases = 'GoPro'\n"
    config = Config(
        [
            Path(
                config_file(
                    "\n[marketplace.facebook]\nenabled = false\n"
                    "\n[user.me]\npushbullet_token = 'o.x'\n" + body
                )
            )
        ]
    )

    assert config.marketplace["facebook"].enabled is False
