"""Tests for the tutti.ch marketplace.

The html fixtures are trimmed captures of real tutti.ch pages: tutti.ch is a
Next.js site that server-renders every search result into a ``__NEXT_DATA__``
json blob, and that blob is what the parser reads.
"""

from pathlib import Path
from typing import Callable

import pytest
from pytest_playwright.pytest_playwright import CreateContextCallback  # type: ignore

from ai_marketplace_monitor.config import Config
from ai_marketplace_monitor.facebook import FacebookMarketplace
from ai_marketplace_monitor.listing import Listing
from ai_marketplace_monitor.tutti import (
    SWISS_CANTONS,
    TuttiItemConfig,
    TuttiMarketplace,
    TuttiMarketplaceConfig,
    TuttiSearchResultPage,
    canton_of,
    format_location,
    format_price,
    image_of,
    parse_listing,
    parse_price,
    with_page,
)
from ai_marketplace_monitor.utils import Translator


def test_search_page(
    new_context: CreateContextCallback, filename: str = "tutti_search_result.html"
) -> None:
    local_file_path = Path(__file__).parent / filename
    page = new_context(java_script_enabled=False).new_page()
    page.goto(f"file://{local_file_path}")
    page.wait_for_load_state("domcontentloaded")

    listings = TuttiSearchResultPage(page).get_listings()

    assert len(listings) == 30

    for idx, listing in enumerate(listings):
        assert listing.marketplace == "tutti"
        assert listing.id.isnumeric(), f"wrong id for listing {idx + 1}"
        assert listing.title, f"no title for listing {idx + 1}"
        assert listing.image.startswith(
            "https://"
        ), f"wrong image for listing {idx + 1} with title {listing.title}"
        assert listing.post_url.startswith(
            "https://www.tutti.ch/de/vi/"
        ), f"wrong post_url for listing {idx + 1} with title {listing.title}"
        assert listing.post_url.endswith(
            listing.id
        ), f"post_url of listing {idx + 1} should end with its id"
        assert listing.price, f"no price for listing {idx + 1} with title {listing.title}"
        assert listing.location, f"no location for listing {idx + 1} with title {listing.title}"
        assert canton_of(listing.location) in SWISS_CANTONS, (
            f"listing {idx + 1} with title {listing.title} has no canton "
            f"in location {listing.location}"
        )
        assert listing.seller, f"no seller for listing {idx + 1} with title {listing.title}"
        # the search page carries the full description but never a condition
        assert listing.description, f"no description for listing {idx + 1}"
        assert listing.condition == "", "condition is only available on the listing page"


def test_search_page_first_listing(
    new_context: CreateContextCallback, filename: str = "tutti_search_result.html"
) -> None:
    """The first listing of the capture is parsed field by field."""
    local_file_path = Path(__file__).parent / filename
    page = new_context(java_script_enabled=False).new_page()
    page.goto(f"file://{local_file_path}")
    page.wait_for_load_state("domcontentloaded")

    listing = TuttiSearchResultPage(page).get_listings()[0]

    assert listing.id == "82741730"
    assert listing.title == "SCOTT Velo Schuh Klick / Gr. 44"
    assert listing.price == "CHF 10.-"
    assert listing.location == "6340 Baar, ZG"
    assert listing.seller == "Blumer"
    assert listing.post_url == (
        "https://www.tutti.ch/de/vi/zug/kleidung-accessoires/schuhe-fuer-herren/"
        "scott-velo-schuh-klick-gr-44/82741730"
    )


def test_search_page_french_urls(
    new_context: CreateContextCallback, filename: str = "tutti_search_result.html"
) -> None:
    """A french search yields french listing urls."""
    local_file_path = Path(__file__).parent / filename
    page = new_context(java_script_enabled=False).new_page()
    page.goto(f"file://{local_file_path}")
    page.wait_for_load_state("domcontentloaded")

    listing = TuttiSearchResultPage(page).get_listings("fr")[0]

    assert listing.post_url == (
        "https://www.tutti.ch/fr/vi/zoug/vetements-accessoires/chaussures-pour-hommes/"
        "scott-velo-schuh-klick-gr-44/82741730"
    )


def test_search_page_without_payload(new_context: CreateContextCallback) -> None:
    """A page without a __NEXT_DATA__ blob yields no listings instead of raising."""
    page = new_context(java_script_enabled=False).new_page()
    page.goto("data:text/html,<html><body>no payload here</body></html>")
    page.wait_for_load_state("domcontentloaded")

    assert TuttiSearchResultPage(page).get_listings() == []


def test_listing_page(
    new_context: CreateContextCallback, filename: str = "tutti_listing.html"
) -> None:
    local_file_path = Path(__file__).parent / filename
    page = new_context(java_script_enabled=False).new_page()
    page.goto(f"file://{local_file_path}")
    page.wait_for_load_state("domcontentloaded")

    post_url = (
        "https://www.tutti.ch/de/vi/zug/kleidung-accessoires/schuhe-fuer-herren/"
        "scott-velo-schuh-klick-gr-44/82741730"
    )
    listing = parse_listing(page, post_url, None)

    assert listing is not None, f"Should be able to parse {filename}"
    assert listing.marketplace == "tutti"
    assert listing.id == "82741730"
    assert listing.title == "SCOTT Velo Schuh Klick / Gr. 44"
    assert listing.price == "CHF 10.-"
    assert listing.location == "6340 Baar, ZG"
    assert listing.seller == "Blumer"
    # the condition is exposed as a listing property and is the reason the
    # listing page is visited at all
    assert listing.condition == "Gebraucht"
    assert listing.description.startswith("Gebraucht aber in gutem Zustand")
    assert listing.image == "https://c.tutti.ch/big/5384535963.jpg"
    assert listing.post_url == post_url


def test_listing_page_without_payload(new_context: CreateContextCallback) -> None:
    """parse_listing returns None rather than raising on an unexpected page."""
    page = new_context(java_script_enabled=False).new_page()
    page.goto("data:text/html,<html><body>not a listing</body></html>")
    page.wait_for_load_state("domcontentloaded")

    assert parse_listing(page, "https://www.tutti.ch/de/vi/x/1", None) is None


@pytest.mark.parametrize(
    "formatted,expected",
    [
        ("300.-", 300),
        ("1'496.- pro Monat", 1496),
        # Listing.price holds the rendered value, so the currency prefix must
        # not stop the min_price/max_price bounds from being applied.
        ("CHF 300.-", 300),
        ("CHF 1'490.-", 1490),
        ("CHF 945.- pro Woche", 945),
        ("38'900.-", 38900),
        ("945.- pro Woche", 945),
        # a giveaway is worth zero, so a min_price of 0 still matches it
        ("Gratis", 0),
        ("Gratuit", 0),
        # negotiable listings have no comparable amount
        ("Preis auf Anfrage", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_price(formatted: str | None, expected: int | None) -> None:
    assert parse_price(formatted) == expected


@pytest.mark.parametrize(
    "formatted,expected",
    [
        ("300.-", "CHF 300.-"),
        ("1'496.- pro Monat", "CHF 1'496.- pro Monat"),
        ("Gratis", "Gratis"),
        ("Preis auf Anfrage", "Preis auf Anfrage"),
        ("", "**unspecified**"),
        (None, "**unspecified**"),
    ],
)
def test_format_price(formatted: str | None, expected: str) -> None:
    assert format_price(formatted, Translator()) == expected


@pytest.mark.parametrize(
    "postcode_information,expected",
    [
        (
            {"postcode": "8570", "locationName": "Weinfelden", "canton": {"shortName": "TG"}},
            "8570 Weinfelden, TG",
        ),
        ({"postcode": "8570", "locationName": "Weinfelden"}, "8570 Weinfelden"),
        ({"locationName": "Weinfelden", "canton": {"shortName": "TG"}}, "Weinfelden, TG"),
        ({}, ""),
        (None, ""),
    ],
)
def test_format_location(postcode_information: dict | None, expected: str) -> None:
    assert format_location(postcode_information) == expected


@pytest.mark.parametrize(
    "location,expected",
    [
        ("8570 Weinfelden, TG", "TG"),
        ("8050 Zürich, ZH", "ZH"),
        ("8570 Weinfelden", ""),
        ("", ""),
    ],
)
def test_canton_of(location: str, expected: str) -> None:
    assert canton_of(location) == expected


def test_image_of_prefers_the_largest_rendition() -> None:
    assert (
        image_of({"normalRendition": {"src": "small.jpg"}, "retinaRendition": {"src": "big.jpg"}})
        == "big.jpg"
    )
    assert image_of({"normalRendition": {"src": "small.jpg"}}) == "small.jpg"
    # listing pages use a single `rendition` key instead
    assert image_of({"rendition": {"src": "one.jpg"}}) == "one.jpg"
    assert image_of({}) == ""
    assert image_of(None) == ""


def test_with_page() -> None:
    base = "https://www.tutti.ch/de/q/suche/Ak6R2ZWxvwJTAwMDA"
    assert with_page(base, 3) == f"{base}?page=3"
    # an existing page parameter is replaced rather than duplicated
    assert with_page(f"{base}?page=2", 5) == f"{base}?page=5"


def test_canton_accepts_abbreviations() -> None:
    config = TuttiItemConfig(name="test", search_phrases=["velo"], canton=["zh", " be "])
    assert config.canton == ["ZH", "BE"]


def test_canton_accepts_a_single_string() -> None:
    assert TuttiItemConfig(name="test", search_phrases=["velo"], canton="ZH").canton == ["ZH"]


def test_canton_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="is not a Swiss canton"):
        TuttiItemConfig(name="test", search_phrases=["velo"], canton=["XX"])


def test_max_pages_rejects_zero() -> None:
    with pytest.raises(ValueError, match="max_pages must be at least 1"):
        TuttiItemConfig(name="test", search_phrases=["velo"], max_pages=0)


def test_site_language_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="site_language must be one of"):
        TuttiMarketplaceConfig(name="tutti", site_language="es")


def test_site_language_is_normalised() -> None:
    assert TuttiMarketplaceConfig(name="tutti", site_language="FR").site_language == "fr"


def test_tutti_needs_neither_login_nor_search_city() -> None:
    """Tutti is browsed anonymously and searches the whole country."""
    assert TuttiMarketplace.requires_login is False
    assert TuttiMarketplace.requires_search_city is False
    # facebook keeps requiring both
    assert FacebookMarketplace.requires_login is True
    assert FacebookMarketplace.requires_search_city is True


#
# Configuration of a [marketplace.tutti] section, end to end through Config.
#

tutti_marketplace_cfg = """
[marketplace.tutti]
market_type = 'tutti'
canton = ['ZH', 'BE']
max_pages = 2
site_language = 'de'
"""

tutti_item_cfg = """
[item.velo]
search_phrases = 'velo'
marketplace = 'tutti'
max_price = 300
"""

tutti_user_cfg = """
[user.user1]
pushbullet_token = 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
"""


def test_tutti_config_is_accepted(config_file: Callable) -> None:
    """market_type = "tutti" validates and builds tutti config objects."""
    cfg = config_file(tutti_marketplace_cfg + tutti_item_cfg + tutti_user_cfg)
    config = Config([Path(cfg)])

    marketplace_config = config.marketplace["tutti"]
    assert isinstance(marketplace_config, TuttiMarketplaceConfig)
    assert marketplace_config.market_type == "tutti"
    assert marketplace_config.canton == ["ZH", "BE"]
    assert marketplace_config.max_pages == 2
    assert marketplace_config.site_language == "de"

    item_config = config.item["velo"]
    assert isinstance(item_config, TuttiItemConfig)
    assert item_config.search_phrases == ["velo"]


def test_tutti_section_defaults_to_the_tutti_market_type(config_file: Callable) -> None:
    """A [marketplace.tutti] section needs no explicit market_type."""
    cfg = config_file(
        tutti_marketplace_cfg.replace("market_type = 'tutti'\n", "")
        + tutti_item_cfg
        + tutti_user_cfg
    )
    config = Config([Path(cfg)])

    assert config.marketplace["tutti"].market_type == "tutti"
    assert isinstance(config.marketplace["tutti"], TuttiMarketplaceConfig)


def test_tutti_config_needs_no_search_city(config_file: Callable) -> None:
    """Unlike facebook, a tutti item validates without a search_city."""
    cfg = config_file("[marketplace.tutti]\n" + tutti_item_cfg + tutti_user_cfg)
    config = Config([Path(cfg)])

    assert config.marketplace["tutti"].search_city is None


def test_facebook_still_requires_a_search_city(config_file: Callable) -> None:
    """The tutti change must not relax the facebook requirement."""
    cfg = config_file(
        "[marketplace.facebook]\n[item.gopro]\nsearch_phrases = 'gopro'\n" + tutti_user_cfg
    )
    with pytest.raises(ValueError, match="No search_city or search_region"):
        Config([Path(cfg)])


def test_unknown_market_type_is_rejected(config_file: Callable) -> None:
    cfg = config_file(
        "[marketplace.shop]\nmarket_type = 'ebay'\nsearch_city = 'x'\n"
        "[item.a]\nsearch_phrases = 'a'\n" + tutti_user_cfg
    )
    with pytest.raises(ValueError, match="is not supported"):
        Config([Path(cfg)])


def test_tutti_invalid_canton_is_rejected(config_file: Callable) -> None:
    cfg = config_file("[marketplace.tutti]\ncanton = 'XX'\n" + tutti_item_cfg + tutti_user_cfg)
    with pytest.raises(ValueError, match="is not a Swiss canton"):
        Config([Path(cfg)])


def _marketplace_with(config: TuttiMarketplaceConfig) -> TuttiMarketplace:
    marketplace = TuttiMarketplace("tutti", None)
    marketplace.configure(config)
    return marketplace


def _listing(price: str, location: str = "8570 Weinfelden, TG") -> Listing:
    return Listing(
        marketplace="tutti",
        name="velo",
        id="1",
        title="Rennvelo",
        image="https://c.tutti.ch/big/1.jpg",
        price=price,
        post_url="https://www.tutti.ch/de/vi/thurgau/velos/rennvelo/1",
        location=location,
        seller="Someone",
        condition="Gebraucht",
        description="ein gutes Velo",
    )


@pytest.mark.parametrize(
    "price,kept",
    [
        ("CHF 300.-", True),
        ("CHF 100.-", True),
        ("CHF 600.-", True),
        ("CHF 99.-", False),
        ("CHF 601.-", False),
        ("CHF 1'490.-", False),
        # a listing whose price cannot be compared is kept rather than dropped
        ("Preis auf Anfrage", True),
    ],
)
def test_price_bounds_are_applied_to_the_rendered_price(price: str, kept: bool) -> None:
    """Tutti keeps its price filter client side, so the bounds run on the listing."""
    marketplace = _marketplace_with(TuttiMarketplaceConfig(name="tutti"))
    item_config = TuttiItemConfig(
        name="velo", search_phrases=["rennvelo"], min_price="100", max_price="600 CHF"
    )
    assert marketplace.check_price(_listing(price), item_config) is kept


def test_free_listing_is_excluded_by_a_min_price() -> None:
    marketplace = _marketplace_with(TuttiMarketplaceConfig(name="tutti"))
    item_config = TuttiItemConfig(name="velo", search_phrases=["velo"], min_price="10")
    assert marketplace.check_price(_listing("Gratis"), item_config) is False


def test_canton_filter_excludes_other_cantons() -> None:
    marketplace = _marketplace_with(TuttiMarketplaceConfig(name="tutti"))
    item_config = TuttiItemConfig(name="velo", search_phrases=["velo"], canton=["ZH"])

    assert marketplace.check_listing(_listing("CHF 10.-", "8050 Zürich, ZH"), item_config)
    assert not marketplace.check_listing(_listing("CHF 10.-", "8570 Weinfelden, TG"), item_config)


def test_condition_filter_only_applies_once_the_condition_is_known() -> None:
    marketplace = _marketplace_with(TuttiMarketplaceConfig(name="tutti"))
    item_config = TuttiItemConfig(name="velo", search_phrases=["velo"], condition=["Neu"])
    listing = _listing("CHF 10.-")

    # while parsing the search page the condition is still unknown
    assert marketplace.check_listing(listing, item_config, condition_available=False)
    # once the listing page has been read, a "Gebraucht" item is excluded
    assert not marketplace.check_listing(listing, item_config)


def test_marketplace_config_supplies_defaults_for_items() -> None:
    """Options set on the marketplace apply to items that do not override them."""
    marketplace = _marketplace_with(TuttiMarketplaceConfig(name="tutti", canton=["ZH"]))
    item_config = TuttiItemConfig(name="velo", search_phrases=["velo"])

    assert marketplace.check_listing(_listing("CHF 10.-", "8050 Zürich, ZH"), item_config)
    assert not marketplace.check_listing(_listing("CHF 10.-", "8570 Weinfelden, TG"), item_config)
