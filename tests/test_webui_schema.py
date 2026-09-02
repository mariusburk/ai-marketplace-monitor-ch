"""Tests for the form description the web UI builds its forms from.

Two things are worth holding still here. A marketplace and a hunt inherit most
of their options from the same base class, so nothing but this module keeps
their forms from being the same wall of thirty fields. And every option
defaults to ``None`` while ``None`` means something different for each — the
notes that say so are claims about other files, and claims drift.
"""

import dataclasses
from typing import Any, Dict, List

import pytest

from ai_marketplace_monitor.notification import NotificationConfig
from ai_marketplace_monitor.utils import convert_to_seconds
from ai_marketplace_monitor.webui.schema import (
    CITY_FIELDS,
    LABELS,
    UI_STEPS,
    all_fields,
    channels,
    config_schema,
    describe_dataclass,
)

# Every kind and variant the UI can render a form for.
EVERY_FORM = [
    (kind, variant) for kind, variants in config_schema()["kinds"].items() for variant in variants
]


def _steps(kind: str, variant: str) -> List[Dict[str, Any]]:
    return config_schema()["kinds"][kind][variant]  # type: ignore[no-any-return]


def _names(kind: str, variant: str) -> List[str]:
    return [f.name for f in all_fields(kind, variant)]


#
# Every option keeps a place
#


@pytest.mark.parametrize("kind,variant", EVERY_FORM)
def test_every_option_appears_exactly_once(kind: str, variant: str) -> None:
    """Every option keeps exactly one box.

    Placed twice, one value would have two boxes; placed nowhere, it would
    vanish from the UI the day someone adds it.
    """
    names = _names(kind, variant)

    assert len(names) == len(set(names)), sorted(names)


@pytest.mark.parametrize("kind,variant", EVERY_FORM)
def test_a_form_leads_with_something(kind: str, variant: str) -> None:
    """The first step must not be empty, or the form opens on nothing."""
    first = _steps(kind, variant)[0]

    assert first["fields"] or first["id"] in UI_STEPS
    assert first["title"] and first["subtitle"]


def test_an_option_no_step_mentions_still_shows_up() -> None:
    """The fallback that keeps a newly added option reachable."""

    @dataclasses.dataclass
    class Sample:
        search_phrases: List[str] | None = None
        brand_new_option: str | None = None

    placed = describe_dataclass(Sample, "item")
    everywhere = [f.name for s in placed for f in list(s.fields) + list(s.advanced)]

    assert "brand_new_option" in everywhere


#
# The two forms have to differ
#


def test_a_marketplace_and_a_hunt_lead_with_different_things() -> None:
    marketplace = _steps("marketplace", "facebook")[0]
    hunt = _steps("item", "facebook")[0]

    assert marketplace["title"] == "Zugang"
    assert hunt["title"] == "Was gesucht wird"
    assert [f["name"] for f in marketplace["fields"]] != [f["name"] for f in hunt["fields"]]


def test_a_marketplace_says_its_shared_options_are_defaults() -> None:
    """On a marketplace the inherited options configure its hunts, not it."""
    titles = [s["title"] for s in _steps("marketplace", "tutti")]

    assert "Vorgaben für alle Jagden" in titles


#
# Options a variant never reads
#


def test_tutti_is_not_asked_for_a_facebook_city() -> None:
    names = set(_names("marketplace", "tutti"))

    assert not (names & CITY_FIELDS)
    assert "canton" in names


def test_facebook_still_is() -> None:
    names = _names("marketplace", "facebook")

    assert "search_city" in names


def test_the_places_are_edited_as_rows_not_four_parallel_lists() -> None:
    """The four place lists are edited as rows, not as four parallel lists.

    facebook.py zips search_city, city_name, radius and currency, and a shorter
    list truncates the search without a word of warning.
    """
    places = next(f for f in all_fields("marketplace", "facebook") if f.name == "search_city")

    assert places.control == "locations"
    assert set(places.composite) == {"city_name", "radius", "currency"}
    # the members must not also stand on their own
    assert "radius" not in _names("marketplace", "facebook")


def test_facebook_only_options_do_not_leak_into_tutti() -> None:
    names = _names("item", "tutti")

    assert "delivery_method" not in names
    assert "category" not in names


def test_bookkeeping_fields_never_reach_a_form() -> None:
    """`name`, `searched_count` and `monitor_config` are set by the loader."""
    for kind, variant in EVERY_FORM:
        assert not set(_names(kind, variant)) & {"name", "searched_count", "monitor_config"}


def test_the_value_type_still_says_what_the_value_is() -> None:
    """`control` says what to draw; `type` still drives reading and writing."""
    fields = {f.name: f for f in all_fields("item", "tutti")}

    assert fields["canton"].type == "list"
    assert fields["max_pages"].type == "number"
    assert fields["fetch_details"].type == "boolean"
    assert fields["description"].type == "text"


def test_choices_come_from_the_real_enums() -> None:
    fields = {f.name: f for f in all_fields("marketplace", "tutti")}

    assert "ZH" in fields["canton"].choices
    assert set(fields["site_language"].choices) == {"de", "fr", "it"}


def test_secrets_are_flagged() -> None:
    fields = {f.name: f for f in all_fields("marketplace", "facebook")}

    assert fields["password"].secret
    assert not fields["search_city"].secret


def test_search_phrases_is_required_even_though_it_has_a_default() -> None:
    """It defaults to an empty list, which its own validator then rejects."""
    fields = {f.name: f for f in all_fields("item", "tutti")}

    assert fields["search_phrases"].required
    assert not fields["canton"].required


#
# Controls that match the values
#


def test_a_closed_vocabulary_is_not_a_comma_box() -> None:
    fields = {f.name: f for f in all_fields("marketplace", "tutti")}

    assert fields["canton"].control == "multi"
    assert len(fields["canton"].choices) == 26


def test_an_open_vocabulary_stays_typable() -> None:
    """Tutti prints whatever condition it likes; the list is a suggestion."""
    fields = {f.name: f for f in all_fields("item", "tutti")}

    assert fields["condition"].control == "combo"


def test_a_reference_names_the_kind_it_points_at() -> None:
    fields = {f.name: f for f in all_fields("item", "tutti")}

    assert fields["notify"].references == "user"
    assert fields["ai"].references == "ai"
    assert fields["notify"].control == "reference"


@pytest.mark.parametrize(
    "value,seconds",
    [
        ("15m", 900),
        ("30m", 1800),
        ("45m", 2700),
        ("1h", 3600),
        ("2h", 7200),
        ("6h", 21600),
        ("12h", 43200),
        ("1d", 86400),
        ("2d", 172800),
        ("7d", 604800),
    ],
)
def test_every_duration_the_form_offers_converts_exactly(value: str, seconds: int) -> None:
    """The duration control writes text like "30m"; parsedatetime turns it back.

    A number box could not accept these at all — the fields are typed `int` but
    their validators run the string through `convert_to_seconds` first.
    """
    assert convert_to_seconds(value) == seconds


def test_durations_are_not_number_boxes() -> None:
    fields = {f.name: f for f in all_fields("item", "tutti")}

    assert fields["search_interval"].control == "duration"
    assert fields["max_search_interval"].control == "duration"


#
# Three-state booleans in a two-state box
#


def test_an_option_that_is_on_when_unset_says_so() -> None:
    """`enabled` and `fetch_details` are `bool | None`, and None means on."""
    fields = {f.name: f for f in all_fields("marketplace", "tutti")}

    assert fields["enabled"].on_when_unset is True
    assert fields["fetch_details"].on_when_unset is True


def test_an_option_that_is_off_when_unset_does_not() -> None:
    fields = {f.name: f for f in all_fields("user", "user")}

    assert fields["rate_limit_enabled"].on_when_unset is False


#
# What an empty field does
#


@pytest.mark.parametrize("kind,variant", EVERY_FORM)
def test_only_optional_fields_claim_a_default(kind: str, variant: str) -> None:
    """A note saying "leer: X" on a required field would be a lie."""
    for schema in all_fields(kind, variant):
        if schema.default_note:
            assert not schema.required, schema.name


def test_the_fields_people_leave_blank_say_what_happens() -> None:
    """These four are the ones whose blank state surprised people."""
    fields = {f.name: f for f in all_fields("item", "tutti")}

    assert "Note 3" in fields["rating"].default_note
    assert "30 Minuten" in fields["search_interval"].default_note
    assert "alle Empfänger" in fields["notify"].default_note
    assert "ganze Schweiz" in fields["canton"].default_note


#
# Ways of reaching someone
#


def test_every_notification_class_becomes_a_channel() -> None:
    """A new channel class must reach the form on its own.

    The catalogue is read off `required_fields`, so nobody has to edit the UI.
    """
    classes = {
        c
        for c in NotificationConfig.__subclasses__()
        + [s for c in NotificationConfig.__subclasses__() for s in c.__subclasses__()]
        if getattr(c, "required_fields", [])
    }
    catalogue = {tuple(c.required) for c in channels()}

    for cls in classes:
        if cls.__name__ == "UserConfig":
            continue
        assert tuple(cls.required_fields) in catalogue, cls.__name__


def test_a_channel_only_claims_its_own_fields() -> None:
    """`message_format` belongs to every push channel, so it is nobody's."""
    by_id = {c.id: c for c in channels()}

    assert by_id["pushbullet"].required == ["pushbullet_token"]
    assert "message_format" not in by_id["pushbullet"].optional
    assert set(by_id["email"].required) == {"email", "smtp_password"}


def test_the_credentials_step_offers_every_channel() -> None:
    step = next(s for s in _steps("user", "user") if s["id"] == "credentials")
    offered = {f["name"] for f in step["fields"]}

    for channel in channels():
        assert set(channel.required) <= offered, channel.id


#
# Names a person can read
#


@pytest.mark.parametrize("kind,variant", EVERY_FORM)
def test_fields_carry_a_readable_label(kind: str, variant: str) -> None:
    for schema in all_fields(kind, variant):
        assert schema.label
        assert "_" not in schema.label, schema.name


def test_no_label_names_a_field_that_no_longer_exists() -> None:
    """A renamed option must not leave a caption behind for a ghost."""
    known = {name for kind, variant in EVERY_FORM for name in _names(kind, variant)}
    known |= {"market_type", "marketplace", "provider", "city_name", "radius"}

    assert not set(LABELS) - known


#
# The settings form
#


def test_the_settings_form_has_fields() -> None:
    """The monitor kind had no description at all, so its form came up blank."""
    names = _names("monitor", "monitor")

    assert "currency" in names
    assert "fixer_api_key" in names
    assert "enabled" not in names
