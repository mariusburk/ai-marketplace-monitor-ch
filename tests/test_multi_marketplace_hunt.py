"""A hunt that runs on more than one marketplace.

Facebook narrows by city and radius, tutti by canton. Neither class knows the
other's option, so one authored hunt has to be able to carry both — otherwise
choosing two marketplaces means giving up on narrowing either.
"""

import logging
from pathlib import Path

import pytest

from ai_marketplace_monitor.config import Config
from ai_marketplace_monitor.webui.sections import validate_values

BOTH = """\
[marketplace.facebook]
search_city = "zurich"

[marketplace.tutti]

[user.me]
pushbullet_token = 'o.x'

[item.gopro]
marketplace = ['facebook', 'tutti']
search_phrases = 'gopro'
search_city = 'zurich'
canton = ['ZH']
category = 'electronics'
"""


def _config(tmp_path: Path, body: str) -> Config:
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    return Config([path], logging.getLogger("test"))


def test_each_marketplace_gets_the_options_it_understands(tmp_path: Path) -> None:
    config = _config(tmp_path, BOTH)

    facebook = config.item["gopro@facebook"]
    tutti = config.item["gopro@tutti"]

    assert facebook.category == "electronics"
    assert tutti.canton == ["ZH"]
    # and neither is handed the other's
    assert not hasattr(facebook, "canton")
    assert not hasattr(tutti, "category")


def test_a_hunt_on_one_marketplace_still_refuses_a_foreign_option(tmp_path: Path) -> None:
    """Dropping unknown keys must not turn a typo into silence.

    Filtering only applies where another chosen marketplace claims the option;
    with one target there is nothing to defer to.
    """
    body = BOTH.replace("marketplace = ['facebook', 'tutti']", "marketplace = 'facebook'")

    with pytest.raises(TypeError):
        _config(tmp_path, body)


def test_an_option_no_marketplace_knows_is_still_an_error(tmp_path: Path) -> None:
    body = BOTH.replace("canton = ['ZH']", "kantone = ['ZH']")

    with pytest.raises(TypeError):
        _config(tmp_path, body)


#
# The same rule, one layer up, where the form checks before saving
#


def test_the_form_accepts_an_option_only_one_of_them_knows() -> None:
    errors = validate_values(
        "item",
        ["facebook", "tutti"],
        "gopro",
        {"search_phrases": ["gopro"], "canton": ["ZH"], "category": "electronics"},
    )

    assert errors == {}


def test_the_form_still_refuses_it_when_that_marketplace_is_not_chosen() -> None:
    errors = validate_values("item", "tutti", "gopro", {"search_phrases": ["g"], "category": "x"})

    assert "category" in errors


def test_the_form_still_refuses_a_typo() -> None:
    errors = validate_values(
        "item", ["facebook", "tutti"], "gopro", {"search_phrases": ["g"], "kantone": ["ZH"]}
    )

    assert "kantone" in errors


def test_a_bad_value_is_still_reported_against_its_field() -> None:
    errors = validate_values(
        "item", ["facebook", "tutti"], "gopro", {"search_phrases": ["g"], "canton": ["XX"]}
    )

    assert "canton" in errors
