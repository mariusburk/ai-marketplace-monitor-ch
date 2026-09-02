"""Tests for the form description the web UI builds its forms from.

The point of this module is that a marketplace and a hunt must not produce the
same form. They share 25 of 30 options by inheritance, so "derive the fields
from the dataclass" on its own produces two identical, unreadable walls.
"""

from typing import Dict, List

from ai_marketplace_monitor.webui.schema import (
    CITY_FIELDS,
    LABELS,
    config_schema,
    describe_dataclass,
)


def _fields(kind: str, variant: str) -> List[Dict[str, object]]:
    return config_schema()["kinds"][kind][variant]  # type: ignore[no-any-return]


def _names(kind: str, variant: str, group: str) -> List[str]:
    return [f["name"] for f in _fields(kind, variant) if f["group"] == group]


#
# The two forms have to differ
#


def test_a_marketplace_and_a_hunt_lead_with_different_things() -> None:
    """Shared inheritance must not mean a shared first impression."""
    marketplace = set(_names("marketplace", "facebook", "primary"))
    hunt = set(_names("item", "facebook", "primary"))

    assert "username" in marketplace and "username" not in hunt
    assert "search_phrases" in hunt and "search_phrases" not in marketplace
    # they may overlap, but not to the point of being the same form
    assert len(marketplace & hunt) < min(len(marketplace), len(hunt))


def test_the_shared_options_are_folded_away_not_dropped() -> None:
    """Folding is a presentation choice; every option stays reachable."""
    fields = _fields("marketplace", "facebook")
    names = {f["name"] for f in fields}

    assert "prompt" in names
    assert [f for f in fields if f["group"] == "secondary"]
    # the discriminator is set by the type picker, so the form must not ask
    assert "market_type" not in names


def test_every_kind_names_its_folded_group() -> None:
    labels = config_schema()["secondary_labels"]
    for kind in config_schema()["kinds"]:
        assert labels[kind].strip()


#
# Options a marketplace never reads
#


def test_tutti_is_not_asked_for_a_facebook_city() -> None:
    """Tutti searches cantons. A city and a radius change nothing there."""
    names = {f["name"] for f in _fields("marketplace", "tutti")}

    assert not (names & CITY_FIELDS)
    assert "canton" in names


def test_facebook_still_is() -> None:
    names = {f["name"] for f in _fields("marketplace", "facebook")}

    assert "search_city" in names
    assert "radius" in names


#
# The settings form
#


def test_the_settings_form_has_fields() -> None:
    """The monitor kind had no description at all, so its form came up blank.

    Every other kind is discriminated by a type; the monitor is a singleton and
    was simply missing from the variant table, which the form has no way to
    report — it just renders nothing.
    """
    names = [f["name"] for f in _fields("monitor", "monitor")]

    assert "currency" in names
    assert "fixer_api_key" in names
    assert "enabled" not in names


#
# Three-state booleans in a two-state box
#


def test_an_option_that_is_on_when_unset_says_so() -> None:
    """`enabled` and `fetch_details` are `bool | None`, and None means on.

    A checkbox has two states, so without this the form drew an empty box on an
    active marketplace and could never write the off value.
    """
    fields = {f["name"]: f for f in _fields("marketplace", "tutti")}

    assert fields["enabled"]["on_when_unset"] is True
    assert fields["fetch_details"]["on_when_unset"] is True


def test_an_option_that_is_off_when_unset_does_not() -> None:
    fields = {f["name"]: f for f in _fields("user", "user")}

    assert fields["rate_limit_enabled"]["on_when_unset"] is False


#
# Names a person can read
#


def test_fields_carry_a_german_label() -> None:
    for field in _fields("marketplace", "tutti"):
        assert field["label"], field["name"]
        assert "_" not in str(field["label"])


def test_a_field_without_a_label_still_renders() -> None:
    """A new option must degrade to its prettified name, not disappear."""
    import dataclasses

    @dataclasses.dataclass
    class Sample:
        brand_new_option: str | None = None

    assert describe_dataclass(Sample)[0].label == "brand new option"


def test_no_label_names_a_field_that_no_longer_exists() -> None:
    """A renamed option must not leave a caption behind for a ghost."""
    known = {
        f["name"]
        for kind in config_schema()["kinds"].values()
        for fields in kind.values()
        for f in fields
    } | {"market_type", "marketplace", "provider"}

    assert not set(LABELS) - known
