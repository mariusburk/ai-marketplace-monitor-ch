"""Tests for the structured config API the forms are built on."""

from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from ai_marketplace_monitor.webui.config_api import ConfigFileService
from ai_marketplace_monitor.webui.log_handler import LogBroadcastHandler
from ai_marketplace_monitor.webui.schema import config_schema
from ai_marketplace_monitor.webui.secrets_redact import MASK
from ai_marketplace_monitor.webui.sections import (
    SectionError,
    SectionService,
    render_section,
    to_toml,
    validate_name,
    validate_values,
)
from ai_marketplace_monitor.webui.server import WebUIConfig, _resolve_auth, create_app

BASE_CONFIG = """\
# Kommentar, der überleben muss
[marketplace.tutti]
canton = ['ZH']

[item.velo]
marketplace = 'tutti'
search_phrases = 'rennvelo'

[user.me]
pushbullet_token = 'o.geheim'
"""


#
# Schema derivation
#


def test_schema_covers_every_marketplace() -> None:
    schema = config_schema()
    assert set(schema["marketplaces"]) == {"facebook", "tutti"}
    assert set(schema["kinds"]["item"]) == {"facebook", "tutti"}


#
# Validation
#


def test_valid_section_has_no_errors() -> None:
    assert validate_values("item", "tutti", "velo", {"search_phrases": ["velo"]}) == {}


def test_error_is_attached_to_the_field_that_caused_it() -> None:
    errors = validate_values(
        "item", "tutti", "velo", {"search_phrases": ["velo"], "canton": ["XX"]}
    )
    assert set(errors) == {"canton"}
    assert "Swiss canton" in errors["canton"]


def test_error_message_has_no_rich_markup() -> None:
    """Validator messages carry [cyan] tags meant for a terminal."""
    errors = validate_values("item", "tutti", "velo", {"search_phrases": ["velo"], "max_pages": 0})
    assert errors
    assert "[cyan]" not in "".join(errors.values())


def test_unknown_option_is_reported_against_its_own_field() -> None:
    errors = validate_values(
        "item", "tutti", "velo", {"search_phrases": ["velo"], "delivery_method": "shipping"}
    )
    assert "delivery_method" in errors


def test_unknown_variant_is_refused() -> None:
    with pytest.raises(SectionError):
        validate_values("item", "ricardo", "velo", {"search_phrases": ["velo"]})


@pytest.mark.parametrize("name", ["velo", "gopro_2", "a-b"])
def test_valid_names(name: str) -> None:
    assert validate_name(name) == name


@pytest.mark.parametrize("name", ["", "a.b", "a b", "[x]"])
def test_invalid_names(name: str) -> None:
    with pytest.raises(SectionError):
        validate_name(name)


#
# TOML rendering
#


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, "true"),
        (False, "false"),
        (3, "3"),
        ("velo", '"velo"'),
        (["ZH", "BE"], '["ZH", "BE"]'),
        ('sagt "hallo"', '"sagt \\"hallo\\""'),
    ],
)
def test_to_toml(value: Any, expected: str) -> None:
    assert to_toml(value) == expected


def test_render_skips_empty_values() -> None:
    rendered = render_section(
        "item", "velo", {"search_phrases": ["velo"], "canton": [], "x": None}
    )
    assert rendered == '[item.velo]\nsearch_phrases = ["velo"]\n'


#
# The service, against a real file
#


def _service(tmp_path: Path) -> SectionService:
    path = tmp_path / "config.toml"
    path.write_text(BASE_CONFIG, encoding="utf-8")
    return SectionService(ConfigFileService([path]))


def test_list_sections_reports_kind_name_and_variant(tmp_path: Path) -> None:
    listed = {(s["kind"], s["name"]): s for s in _service(tmp_path).list_sections()}
    assert set(listed) == {("marketplace", "tutti"), ("item", "velo"), ("user", "me")}
    assert listed[("marketplace", "tutti")]["variant"] == "tutti"
    assert listed[("item", "velo")]["variant"] == "tutti"


def test_secrets_are_masked_on_read(tmp_path: Path) -> None:
    user = _service(tmp_path).get_section("user", "me")
    assert user["values"]["pushbullet_token"] == MASK


def test_create_and_read_back(tmp_path: Path) -> None:
    service = _service(tmp_path)
    errors = service.save_section(
        "item",
        "gopro",
        "tutti",
        {"search_phrases": ["GoPro Hero"], "canton": ["ZH", "BE"], "max_pages": 2},
        create=True,
    )
    assert errors == {}

    stored = service.get_section("item", "gopro")
    assert stored["values"]["search_phrases"] == ["GoPro Hero"]
    assert stored["values"]["canton"] == ["ZH", "BE"]
    assert stored["values"]["max_pages"] == 2


def test_editing_one_section_leaves_the_rest_alone(tmp_path: Path) -> None:
    """Comments and neighbouring sections must survive a form save."""
    path = tmp_path / "config.toml"
    service = _service(tmp_path)
    service.save_section("item", "velo", "tutti", {"search_phrases": ["rennrad"]}, create=False)
    content = path.read_text(encoding="utf-8")

    assert "# Kommentar, der überleben muss" in content
    assert "[marketplace.tutti]" in content
    assert "[user.me]" in content
    assert "rennrad" in content


def test_masked_secret_round_trips_instead_of_being_overwritten(tmp_path: Path) -> None:
    """A form that never showed the token must not blank it on save."""
    path = tmp_path / "config.toml"
    service = _service(tmp_path)
    service.save_section(
        "user", "me", None, {"pushbullet_token": MASK, "remind": "1 day"}, create=False
    )
    assert "o.geheim" in path.read_text(encoding="utf-8")
    assert MASK not in path.read_text(encoding="utf-8")


def test_invalid_values_are_rejected_without_touching_the_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    service = _service(tmp_path)
    before = path.read_text(encoding="utf-8")

    errors = service.save_section(
        "item", "velo", "tutti", {"search_phrases": ["velo"], "canton": ["XX"]}, create=False
    )

    assert "canton" in errors
    assert path.read_text(encoding="utf-8") == before


def test_creating_a_duplicate_is_refused(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with pytest.raises(SectionError):
        service.save_section("item", "velo", "tutti", {"search_phrases": ["x"]}, create=True)


def test_delete_removes_only_that_section(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    service = _service(tmp_path)
    service.save_section("item", "gopro", "tutti", {"search_phrases": ["x"]}, create=True)

    service.delete_section("item", "gopro")

    content = path.read_text(encoding="utf-8")
    assert "[item.gopro]" not in content
    assert "[item.velo]" in content
    assert "[marketplace.tutti]" in content


def test_deleting_something_absent_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(SectionError):
        _service(tmp_path).delete_section("item", "gibtsnicht")


#
# Through HTTP
#


def _client(tmp_path: Path) -> TestClient:
    path = tmp_path / "config.toml"
    path.write_text(BASE_CONFIG, encoding="utf-8")
    (tmp_path / "webui.toml").write_text("", encoding="utf-8")
    config = WebUIConfig(
        host="127.0.0.1",
        port=8467,
        config_files=[path],
        log_handler=LogBroadcastHandler(),
        account_file=tmp_path / "webui.toml",
    )
    state, _ = _resolve_auth(config)
    assert config.log_handler is not None
    app = create_app(config, state, ConfigFileService([path]), config.log_handler)
    return TestClient(app)


def test_schema_endpoint(tmp_path: Path) -> None:
    body: Dict[str, Any] = _client(tmp_path).get("/api/schema").json()
    assert "tutti" in body["kinds"]["item"]


def test_sections_endpoint_lists_and_masks(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/api/sections").json()
    users = [s for s in body["sections"] if s["kind"] == "user"]
    assert users[0]["values"]["pushbullet_token"] == MASK


def test_create_via_http(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post(
        "/api/sections/item",
        json={"name": "gopro", "variant": "tutti", "values": {"search_phrases": ["GoPro"]}},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "errors": {}}
    assert client.get("/api/sections/item/gopro").status_code == 200


def test_validate_endpoint_reports_field_errors(tmp_path: Path) -> None:
    response = _client(tmp_path).post(
        "/api/sections/item/validate",
        json={
            "name": "velo",
            "variant": "tutti",
            "values": {"search_phrases": ["velo"], "canton": ["XX"]},
        },
    )
    body = response.json()
    assert body["ok"] is False
    assert "canton" in body["errors"]


def test_missing_section_is_a_404(tmp_path: Path) -> None:
    assert _client(tmp_path).get("/api/sections/item/gibtsnicht").status_code == 404


def test_saved_item_records_which_marketplace_it_belongs_to(tmp_path: Path) -> None:
    """A saved item must say which marketplace it belongs to.

    Without the discriminator the loader binds it to whichever marketplace
    section comes first, and tutti options land on a facebook config. Found
    against a live container, not by a unit test.
    """
    path = tmp_path / "config.toml"
    service = _service(tmp_path)

    errors = service.save_section(
        "item", "gopro", "tutti", {"search_phrases": ["GoPro"], "canton": ["ZH"]}, create=True
    )

    assert errors == {}
    assert 'marketplace = "tutti"' in path.read_text(encoding="utf-8")


def test_saved_marketplace_records_its_market_type(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    service = _service(tmp_path)

    service.save_section("marketplace", "zweitmarkt", "tutti", {"canton": ["BE"]}, create=True)

    assert 'market_type = "tutti"' in path.read_text(encoding="utf-8")


def test_item_variant_follows_a_renamed_marketplace_section(tmp_path: Path) -> None:
    """An item follows its marketplace section, whatever it is called.

    The section name is free; its market_type decides which options the
    item accepts.
    """
    path = tmp_path / "config.toml"
    path.write_text(
        "[marketplace.schweiz]\nmarket_type = 'tutti'\ncanton = ['ZH']\n\n"
        "[user.me]\npushbullet_token = 'o.geheim'\n",
        encoding="utf-8",
    )
    service = SectionService(ConfigFileService([path]))

    errors = service.save_section(
        "item", "gopro", "schweiz", {"search_phrases": ["GoPro"], "canton": ["ZH"]}, create=True
    )

    assert errors == {}
    assert 'marketplace = "schweiz"' in path.read_text(encoding="utf-8")


def test_add_delete_is_stable(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    service = _service(tmp_path)

    service.save_section("item", "gopro", "tutti", {"search_phrases": ["x"]}, create=True)
    service.delete_section("item", "gopro")
    once = path.read_text(encoding="utf-8")

    service.save_section("item", "gopro", "tutti", {"search_phrases": ["x"]}, create=True)
    service.delete_section("item", "gopro")

    # no gap grows, and the neighbours are untouched
    assert path.read_text(encoding="utf-8") == once
    assert "\n\n\n" not in once
    assert "# Kommentar, der überleben muss" in once
    assert "[marketplace.tutti]" in once
    assert "[user.me]" in once


#
# Hunts on several marketplaces
#


def test_saving_a_hunt_on_two_marketplaces(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "[marketplace.tutti]\ncanton = ['ZH']\n\n"
        "[marketplace.facebook]\nsearch_city = 'zurich'\n\n"
        "[user.me]\npushbullet_token = 'o.x'\n",
        encoding="utf-8",
    )
    service = SectionService(ConfigFileService([path]))

    errors = service.save_section(
        "item", "gopro", ["tutti", "facebook"], {"search_phrases": ["GoPro"]}, create=True
    )

    assert errors == {}
    assert 'marketplace = ["tutti", "facebook"]' in path.read_text(encoding="utf-8")


def test_an_option_only_one_marketplace_accepts_is_saved_anyway(tmp_path: Path) -> None:
    """Canton is tutti-only; on a hunt that also runs on facebook it still counts.

    The loader hands each marketplace only the options it understands, so the
    hunt can narrow tutti by canton while facebook keeps its own city.
    """
    path = tmp_path / "config.toml"
    path.write_text(
        "[marketplace.tutti]\ncanton = ['ZH']\n\n"
        "[marketplace.facebook]\nsearch_city = 'zurich'\n\n"
        "[user.me]\npushbullet_token = 'o.x'\n",
        encoding="utf-8",
    )
    service = SectionService(ConfigFileService([path]))

    errors = service.save_section(
        "item",
        "gopro",
        ["tutti", "facebook"],
        {"search_phrases": ["GoPro"], "canton": ["ZH"]},
        create=True,
    )

    assert errors == {}
    assert "canton" in path.read_text(encoding="utf-8")


def test_an_option_no_chosen_marketplace_knows_is_a_field_error(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "[marketplace.tutti]\n\n[marketplace.facebook]\nsearch_city = 'zurich'\n\n"
        "[user.me]\npushbullet_token = 'o.x'\n",
        encoding="utf-8",
    )
    service = SectionService(ConfigFileService([path]))

    errors = service.save_section(
        "item",
        "gopro",
        ["tutti", "facebook"],
        {"search_phrases": ["GoPro"], "kanton": ["ZH"]},
        create=True,
    )

    assert "kanton" in errors


def test_validate_values_accepts_a_single_variant_unchanged() -> None:
    assert validate_values("item", "tutti", "velo", {"search_phrases": ["velo"]}) == {}


#
# A config that is not finished yet
#


def test_a_half_built_config_can_be_saved(tmp_path: Path) -> None:
    """The setup path adds a marketplace, a recipient and a hunt one at a time.

    A whole-file check that demands all three refuses the first of them, which
    makes the flow impossible to finish — so an incomplete config is a state
    the UI writes, and the monitor waits for the rest.
    """
    path = tmp_path / "config.toml"
    path.write_text("[monitor]\ncurrency = 'CHF'\n", encoding="utf-8")
    service = SectionService(ConfigFileService([path]))

    errors = service.save_section("marketplace", "tutti", "tutti", {"canton": ["ZH"]}, create=True)

    assert errors == {}
    assert "[marketplace.tutti]" in path.read_text(encoding="utf-8")


def test_a_real_mistake_is_still_refused(tmp_path: Path) -> None:
    """Incomplete is not a licence to write anything."""
    path = tmp_path / "config.toml"
    path.write_text("[monitor]\n", encoding="utf-8")
    service = SectionService(ConfigFileService([path]))

    errors = service.save_section("marketplace", "tutti", "tutti", {"canton": ["XX"]}, create=True)

    assert errors
    assert "[marketplace.tutti]" not in path.read_text(encoding="utf-8")


def test_the_missing_pieces_are_named_separately_from_errors() -> None:
    """`IncompleteConfigError` is what lets the UI and the log tell them apart."""
    from ai_marketplace_monitor.config import IncompleteConfigError

    assert issubclass(IncompleteConfigError, ValueError)


def test_a_replaced_section_keeps_a_blank_line_after_it(tmp_path: Path) -> None:
    """Replacing a one-line section used to glue the next header onto it."""
    path = tmp_path / "config.toml"
    path.write_text(
        "[monitor]\n\n[marketplace.tutti]\ncanton = ['ZH']\n\n"
        "[user.me]\npushbullet_token = 'o.x'\n\n"
        "[item.g]\nmarketplace = 'tutti'\nsearch_phrases = 'g'\n",
        encoding="utf-8",
    )
    service = SectionService(ConfigFileService([path]))

    service.save_section("monitor", "monitor", None, {"currency": "CHF"}, create=False)

    written = path.read_text(encoding="utf-8")
    assert 'currency = "CHF"\n\n[marketplace.tutti]' in written
