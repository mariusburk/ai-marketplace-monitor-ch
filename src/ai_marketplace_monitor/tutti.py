import json
import re
import time
from dataclasses import dataclass
from enum import Enum
from logging import Logger
from typing import Any, Dict, Generator, List, Tuple, Type, cast
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from currency_converter import CurrencyConverter  # type: ignore
from playwright.sync_api import Browser, Page  # type: ignore
from rich.pretty import pretty_repr

from .listing import Listing
from .marketplace import ItemConfig, Marketplace, MarketplaceConfig, WebPage
from .price_index import PriceObservation, record, reference
from .price_stats import convert_for_display, describe_price, price_basis
from .utils import (
    BaseConfig,
    CounterItem,
    KeyboardMonitor,
    Translator,
    counter,
    hilight,
    is_substring,
)

TUTTI_BASE_URL = "https://www.tutti.ch"

# tutti.ch renders 30 listings per result page. Knowing the page size lets the
# search stop early instead of requesting a page that cannot exist.
LISTINGS_PER_PAGE = 30

# Seconds to wait between page loads. This mirrors the pause the facebook
# marketplace takes and keeps the monitor from hammering tutti.ch.
REQUEST_INTERVAL = 5

# Number of result pages retrieved per search phrase unless configured otherwise.
DEFAULT_MAX_PAGES = 1

# The two-letter abbreviations of the 26 Swiss cantons, used to validate the
# `canton` option and to recognise the canton in a listing location.
SWISS_CANTONS = (
    "AG", "AI", "AR", "BE", "BL", "BS", "FR", "GE", "GL", "GR", "JU", "LU", "NE",
    "NW", "OW", "SG", "SH", "SO", "SZ", "TG", "TI", "UR", "VD", "VS", "ZG", "ZH",
)  # fmt: skip

# tutti prices are free-form strings such as "300.-", "1'496.- pro Monat" or
# "Gratis". Everything is quoted in Swiss francs.
TUTTI_CURRENCY = "CHF"

# Words tutti uses for a giveaway, in the three site languages.
FREE_PRICE_WORDS = ("gratis", "gratuit", "geschenkt", "regalo")

# Labels of the "condition" property on a listing detail page, per site language.
CONDITION_LABELS = ("zustand", "état", "etat", "stato", "condition")

# What tutti prints for a condition, per canonical value and site language.
#
# Facebook grades used goods three ways; tutti only says whether an item is new
# or not. So one shared vocabulary is written in a config, and each marketplace
# takes it as far as it goes — picking "used, good" here means every used
# listing, because tutti cannot tell them apart.
#
# The German words are the ones the shipped fixtures show ("Neu", "Gebraucht").
# The French and Italian ones are the plain translations; a listing in those
# languages simply fails to match if they turn out to be wrong, which filters
# nothing out rather than filtering the wrong things.
CONDITION_WORDS: Dict[str, Dict[str, str]] = {
    "de": {"new": "Neu", "used": "Gebraucht"},
    "fr": {"new": "Neuf", "used": "Occasion"},
    "it": {"new": "Nuovo", "used": "Usato"},
}

# The canonical values, as Facebook's Condition enum spells them.
CANONICAL_CONDITIONS = {
    "new": "new",
    "used_like_new": "used",
    "used_good": "used",
    "used_fair": "used",
}


def tutti_conditions(conditions: List[str], site_language: str) -> List[str]:
    """Translate canonical condition values into the words tutti prints.

    Anything that is not a canonical value is passed through untouched, so a
    hand-written config that already says "Gebraucht" keeps working.
    """
    words = CONDITION_WORDS.get(site_language, CONDITION_WORDS["de"])
    out: List[str] = []
    for value in conditions:
        canonical = CANONICAL_CONDITIONS.get(str(value).strip().lower())
        translated = words[canonical] if canonical else str(value)
        if translated not in out:
            out.append(translated)
    return out


NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)

# Leading amount of a formatted price. Swiss thousands separators appear as a
# straight or typographic apostrophe, a plain space or a non-breaking space.
THOUSANDS_SEPARATORS = "'\u2019\u00a0 "
PRICE_RE = re.compile(rf"(\d[\d{THOUSANDS_SEPARATORS}]*)")


class SiteLanguage(Enum):
    """Languages tutti.ch is served in. The value is the first path segment."""

    DE = "de"
    FR = "fr"
    IT = "it"


def extract_next_data(html: str) -> Dict[str, Any]:
    """Return the ``__NEXT_DATA__`` payload embedded in a tutti.ch page.

    tutti.ch is a Next.js application that server-renders every search result
    and listing into a single JSON blob. Reading that blob is far more robust
    than matching against the generated CSS classes.
    """
    matched = NEXT_DATA_RE.search(html)
    if matched is None:
        raise ValueError("Page does not contain a __NEXT_DATA__ payload.")
    return cast(Dict[str, Any], json.loads(matched.group(1)))


def query_data(next_data: Dict[str, Any], query_name: str) -> Any:
    """Return the data of a react-query entry stored in ``__NEXT_DATA__``."""
    queries = (
        next_data.get("props", {})
        .get("pageProps", {})
        .get("dehydratedState", {})
        .get("queries", [])
    )
    for query in queries:
        key = query.get("queryKey")
        if isinstance(key, list) and key and key[0] == query_name:
            return query.get("state", {}).get("data")
    return None


def parse_price(formatted_price: str | None) -> int | None:
    """Return the numeric CHF amount of a tutti price, or None if there is none.

    tutti writes prices as ``300.-``, ``1'496.- pro Monat`` or ``Gratis``, and
    `format_price` renders those with a ``CHF`` prefix. A giveaway counts as
    zero; a price without any number (for instance "Preis auf Anfrage") has no
    comparable amount.
    """
    if not formatted_price:
        return None
    text = formatted_price.strip()
    if not text:
        return None
    if any(word in text.lower() for word in FREE_PRICE_WORDS):
        return 0
    # `search` rather than `match` so that both the raw tutti value ("300.-") and
    # the rendered one carrying the currency ("CHF 300.-") are understood.
    matched = PRICE_RE.search(text)
    if matched is None:
        return None
    digits = re.sub(r"\D", "", matched.group(1))
    return int(digits) if digits else None


def format_price(formatted_price: str | None, translator: Translator) -> str:
    """Render a tutti price for notifications, prefixing the currency."""
    if not formatted_price or not formatted_price.strip():
        return translator("**unspecified**")
    text = " ".join(formatted_price.split())
    # "300.-" becomes "CHF 300.-", while "Gratis" is already self-explanatory.
    return f"{TUTTI_CURRENCY} {text}" if text[0].isdigit() else text


def format_location(postcode_information: Dict[str, Any] | None) -> str:
    """Render a listing location as "8570 Weinfelden, TG"."""
    if not postcode_information:
        return ""
    postcode = postcode_information.get("postcode") or ""
    location_name = postcode_information.get("locationName") or ""
    parts = [" ".join(x for x in (postcode, location_name) if x)]
    canton = (postcode_information.get("canton") or {}).get("shortName") or ""
    if canton:
        parts.append(canton)
    return ", ".join(x for x in parts if x)


def canton_of(location: str) -> str:
    """Return the canton abbreviation of a location produced by format_location."""
    candidate = location.rsplit(",", 1)[-1].strip().upper()
    return candidate if candidate in SWISS_CANTONS else ""


def image_of(image_holder: Dict[str, Any] | None) -> str:
    """Return the best image URL from a thumbnail or image node.

    Search results expose ``normalRendition``/``retinaRendition`` while listing
    pages use a single ``rendition``.
    """
    if not image_holder:
        return ""
    for key in ("retinaRendition", "normalRendition", "rendition"):
        src = (image_holder.get(key) or {}).get("src")
        if src:
            return cast(str, src)
    return ""


def with_page(url: str, page_number: int) -> str:
    """Return `url` with the tutti `page` query parameter set to page_number."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "page"]
    query.append(("page", str(page_number)))
    return urlunsplit(parts._replace(query=urlencode(query)))


@dataclass
class TuttiMarketItemCommonConfig(BaseConfig):
    """Item options that can be defined in both marketplace and item sections

    This class defines and processes options specific to tutti.ch.
    """

    canton: List[str] | None = None
    condition: List[str] | None = None
    max_pages: int | None = None
    site_language: str | None = None
    fetch_details: bool | None = None

    def handle_canton(self: "TuttiMarketItemCommonConfig") -> None:
        if self.canton is None:
            return

        if isinstance(self.canton, str):
            self.canton = [self.canton]

        if not isinstance(self.canton, list) or not all(isinstance(x, str) for x in self.canton):
            raise ValueError(
                f"Item {hilight(self.name)} canton must be a string or list of string."
            )

        cantons = [x.strip().upper() for x in self.canton]
        for value in cantons:
            if value not in SWISS_CANTONS:
                raise ValueError(
                    f"Item {hilight(self.name)} canton {hilight(value)} is not a Swiss canton. "
                    f"Use one of the two-letter abbreviations {', '.join(SWISS_CANTONS)}."
                )
        self.canton = cantons

    def handle_condition(self: "TuttiMarketItemCommonConfig") -> None:
        if self.condition is None:
            return

        if isinstance(self.condition, str):
            self.condition = [self.condition]

        if not isinstance(self.condition, list) or not all(
            isinstance(x, str) for x in self.condition
        ):
            raise ValueError(
                f"Item {hilight(self.name)} condition must be a string or list of string."
            )

    def handle_max_pages(self: "TuttiMarketItemCommonConfig") -> None:
        if self.max_pages is None:
            return

        if not isinstance(self.max_pages, int) or isinstance(self.max_pages, bool):
            raise ValueError(f"Item {hilight(self.name)} max_pages must be an integer.")
        if self.max_pages < 1:
            raise ValueError(f"Item {hilight(self.name)} max_pages must be at least 1.")

    def handle_site_language(self: "TuttiMarketItemCommonConfig") -> None:
        if self.site_language is None:
            return

        supported = [x.value for x in SiteLanguage]
        if not isinstance(self.site_language, str) or self.site_language.lower() not in supported:
            raise ValueError(
                f"Item {hilight(self.name)} site_language must be one of {', '.join(supported)}."
            )
        self.site_language = self.site_language.lower()

    def handle_fetch_details(self: "TuttiMarketItemCommonConfig") -> None:
        if self.fetch_details is None:
            return
        if not isinstance(self.fetch_details, bool):
            raise ValueError(f"Item {hilight(self.name)} fetch_details must be a boolean.")


@dataclass
class TuttiMarketplaceConfig(MarketplaceConfig, TuttiMarketItemCommonConfig):
    """Options specific to the tutti marketplace

    This class defines and processes options that can be specified in the
    marketplace.tutti section only. None of the options are required.
    """


@dataclass
class TuttiItemConfig(ItemConfig, TuttiMarketItemCommonConfig):
    pass


class TuttiMarketplace(Marketplace):
    name = "tutti"

    # narrowed from the base class so that tutti-specific options type-check
    config: TuttiMarketplaceConfig

    # tutti.ch can be browsed without an account, and its searches are national
    # rather than scoped to a city.
    requires_login = False
    requires_search_city = False

    def __init__(
        self: "TuttiMarketplace",
        name: str,
        browser: Browser | None,
        keyboard_monitor: KeyboardMonitor | None = None,
        logger: Logger | None = None,
    ) -> None:
        assert name == self.name
        super().__init__(name, browser, keyboard_monitor, logger)
        self.page: Page | None = None

    @classmethod
    def get_config(cls: Type["TuttiMarketplace"], **kwargs: Any) -> TuttiMarketplaceConfig:
        return TuttiMarketplaceConfig(**kwargs)

    @classmethod
    def get_item_config(cls: Type["TuttiMarketplace"], **kwargs: Any) -> TuttiItemConfig:
        return TuttiItemConfig(**kwargs)

    @property
    def site_language(self: "TuttiMarketplace") -> str:
        return self.config.site_language or SiteLanguage.DE.value

    def login(self: "TuttiMarketplace") -> None:
        """Open tutti.ch and dismiss the cookie banner.

        tutti.ch needs no account, but the consent dialog covers the page until
        it is acknowledged, so a session always starts by accepting it.
        """
        assert self.browser is not None

        self.page = self.create_page(swap_proxy=True)
        self.goto_url(f"{TUTTI_BASE_URL}/{self.site_language}")

        try:
            accept_button = self.page.get_by_role(
                "button",
                name=re.compile(
                    r"Akzeptieren|Alle akzeptieren|Accepter|Accetta|Accept", re.IGNORECASE
                ),
            )
            if accept_button.count() > 0 and accept_button.first.is_visible():
                accept_button.first.click()
                self.page.wait_for_timeout(2000)
                if self.logger:
                    self.logger.debug(f"""{hilight("[Login]", "succ")} Cookie consent accepted.""")
            elif self.logger:
                self.logger.debug(
                    f"{hilight('[Login]', 'succ')} Cookie consent pop-up not found or not visible."
                )
        except KeyboardInterrupt:
            raise
        except Exception as e:
            if self.logger:
                self.logger.warning(
                    f"{hilight('[Login]', 'fail')} Could not handle cookie pop-up (or it was not present): {e!s}"
                )

    def search(
        self: "TuttiMarketplace", item_config: TuttiItemConfig
    ) -> Generator[Listing, None, None]:
        if not self.page:
            self.login()
            assert self.page is not None

        site_language = (
            item_config.site_language or self.config.site_language or SiteLanguage.DE.value
        )
        max_pages = item_config.max_pages or self.config.max_pages or DEFAULT_MAX_PAGES
        fetch_details = item_config.fetch_details
        if fetch_details is None:
            fetch_details = self.config.fetch_details
        if fetch_details is None:
            fetch_details = True

        # increase the searched_count to differentiate first and subsequent searches
        item_config.searched_count += 1

        # there is a small chance that different search phrases return the same item
        found: Dict[str, bool] = {}

        for search_phrase in item_config.search_phrases:
            if self.logger:
                self.logger.info(
                    f"""{hilight("[Search]", "info")} Searching {item_config.marketplace} for """
                    f"""{hilight(item_config.name)} with phrase {hilight(search_phrase)}"""
                )

            self.goto_url(f"{TUTTI_BASE_URL}/{site_language}/q?query={quote(search_phrase)}")
            counter.increment(CounterItem.SEARCH_PERFORMED, item_config.name)

            # tutti rewrites ?query= to a canonical /q/suche/<token> url that encodes
            # the search. Only `page` survives on that url, so paginate from it.
            canonical_url = self.page.url

            # Collect every page before yielding anything: the price comparison
            # needs the whole result set of this phrase as its reference.
            all_listings: List[Listing] = []
            for page_number in range(1, max_pages + 1):
                if page_number > 1:
                    time.sleep(REQUEST_INTERVAL)
                    self.goto_url(with_page(canonical_url, page_number))

                page_listings = TuttiSearchResultPage(
                    self.page, self.translator, self.logger
                ).get_listings(site_language)

                if not page_listings:
                    break
                all_listings.extend(page_listings)
                # a short page is the last page
                if len(page_listings) < LISTINGS_PER_PAGE:
                    break

            # Hand what this run saw to the shared index, then read the going
            # rate back out of it — so this hunt's facebook observations count
            # too, even though they were made by a different search.
            record(item_config.name, self.observations(all_listings, item_config))
            stats, composition = reference(item_config.name, TUTTI_CURRENCY)

            if stats is not None and self.logger:
                self.logger.debug(
                    f"""{hilight("[Search]", "info")} Price reference for {hilight(search_phrase)}: """
                    f"""median {TUTTI_CURRENCY} {stats.median} of {stats.count} listings """
                    f"""across {", ".join(sorted(composition))}"""
                )

            for listing in all_listings:
                if listing.post_url.split("?")[0] in found:
                    continue
                if self.keyboard_monitor is not None and self.keyboard_monitor.is_paused():
                    return
                counter.increment(CounterItem.LISTING_EXAMINED, item_config.name)
                found[listing.post_url.split("?")[0]] = True
                listing.name = item_config.name
                amount = parse_price(listing.price)
                listing.price_comparison = describe_price(
                    amount, stats, TUTTI_CURRENCY, composition
                )
                listing.price_basis = price_basis(amount, stats)
                listing.converted_price = convert_for_display(
                    amount, TUTTI_CURRENCY, self.config.monitor_config, self.logger
                )

                # filter on what the search page already provides; the condition is
                # only known after the listing page has been retrieved.
                if not self.check_listing(listing, item_config, condition_available=False):
                    counter.increment(CounterItem.EXCLUDED_LISTING, item_config.name)
                    continue

                if fetch_details:
                    try:
                        details, from_cache = self.get_listing_details(
                            listing.post_url,
                            item_config,
                            price=listing.price,
                            title=listing.title,
                        )
                        if not from_cache:
                            time.sleep(REQUEST_INTERVAL)
                    except KeyboardInterrupt:
                        raise
                    except Exception as e:
                        if self.logger:
                            self.logger.error(
                                f"""{hilight("[Retrieve]", "fail")} Failed to get item details: {e}"""
                            )
                        continue
                    # the search page is the more reliable source of title and price,
                    # so only the fields it cannot provide are copied over.
                    for attr in ("condition", "seller", "description", "image"):
                        value = getattr(details, attr)
                        if value:
                            setattr(listing, attr, value)

                if self.logger:
                    self.logger.debug(
                        f"""{hilight("[Retrieve]", "succ")} New item "{listing.title}" from {listing.post_url} is sold by "{listing.seller}" and with description "{listing.description[:100]}..." """
                    )

                if self.check_listing(listing, item_config):
                    yield listing
                else:
                    counter.increment(CounterItem.EXCLUDED_LISTING, item_config.name)

    def observations(
        self: "TuttiMarketplace", listings: List[Listing], item_config: TuttiItemConfig
    ) -> List[PriceObservation]:
        """The priced listings this search may contribute to the going rate.

        Only the keyword filters are applied, so a Hero 13 is measured against
        other Hero 13 offers. The price bounds are deliberately ignored — they
        are the question being asked, and applying them would clip the very
        distribution the comparison rests on. The canton filter is skipped too,
        because what an item is worth does not stop at a cantonal border.
        """
        keywords = item_config.keywords
        antikeywords = item_config.antikeywords
        seen_at = time.time()
        found: List[PriceObservation] = []
        for listing in listings:
            haystack = f"{listing.title}  {listing.description}"
            if antikeywords and is_substring(antikeywords, haystack):
                continue
            if keywords and not is_substring(keywords, haystack):
                continue
            amount = parse_price(listing.price)
            if amount is None or amount <= 0:
                continue
            found.append(
                PriceObservation(
                    marketplace=self.name,
                    listing_id=listing.id,
                    amount=amount,
                    currency=TUTTI_CURRENCY,
                    seen_at=seen_at,
                )
            )
        return found

    def get_listing_details(
        self: "TuttiMarketplace",
        post_url: str,
        item_config: ItemConfig,
        price: str | None = None,
        title: str | None = None,
    ) -> Tuple[Listing, bool]:
        assert post_url.startswith(TUTTI_BASE_URL)
        details = Listing.from_cache(post_url)
        if (
            details is not None
            and (price is None or details.price == price)
            and (title is None or details.title == title)
        ):
            # if the price and title are the same, we assume everything else is unchanged.
            return details, True

        if not self.page:
            self.login()

        assert self.page is not None
        self.goto_url(post_url)
        counter.increment(CounterItem.LISTING_QUERY, item_config.name)
        details = parse_listing(self.page, post_url, self.translator, self.logger)
        if details is None:
            raise ValueError(f"Failed to get item details of listing {post_url}.")
        details.to_cache(post_url)
        return details, False

    def check_listing(
        self: "TuttiMarketplace",
        item: Listing,
        item_config: TuttiItemConfig,
        condition_available: bool = True,
    ) -> bool:

        antikeywords = item_config.antikeywords
        if antikeywords and is_substring(
            antikeywords, item.title + " " + item.description, logger=self.logger
        ):
            if self.logger:
                self.logger.info(
                    f"""{hilight("[Skip]", "fail")} Exclude {hilight(item.title)} due to {hilight("excluded keywords", "fail")}: {", ".join(antikeywords)}"""
                )
            return False

        keywords = item_config.keywords
        if keywords and not is_substring(
            keywords, item.title + "  " + item.description, logger=self.logger
        ):
            if self.logger:
                self.logger.info(
                    f"""{hilight("[Skip]", "fail")} Exclude {hilight(item.title)} {hilight("without required keywords", "fail")} in title and description."""
                )
            return False

        # tutti searches the whole country, so the canton filter is applied here
        # instead of being pushed into the search url.
        cantons = item_config.canton if item_config.canton is not None else self.config.canton
        if cantons and canton_of(item.location) not in cantons:
            if self.logger:
                self.logger.info(
                    f"""{hilight("[Skip]", "fail")} Exclude {hilight("out of canton", "fail")} item {hilight(item.title)} from location {hilight(item.location)}"""
                )
            return False

        if not self.check_price(item, item_config):
            return False

        if condition_available:
            conditions = (
                item_config.condition
                if item_config.condition is not None
                else self.config.condition
            )
            if conditions:
                conditions = tutti_conditions(conditions, self.site_language)
            if conditions and not is_substring(conditions, item.condition, logger=self.logger):
                if self.logger:
                    self.logger.info(
                        f"""{hilight("[Skip]", "fail")} Exclude {hilight(item.title)} in {hilight("unwanted condition", "fail")} {hilight(item.condition)}"""
                    )
                return False

        if item_config.exclude_sellers is not None:
            exclude_sellers = item_config.exclude_sellers
        else:
            exclude_sellers = self.config.exclude_sellers or []
        if (
            item.seller
            and exclude_sellers
            and is_substring(exclude_sellers, item.seller, logger=self.logger)
        ):
            if self.logger:
                self.logger.info(
                    f"""{hilight("[Skip]", "fail")} Exclude {hilight(item.title)} sold by {hilight("banned seller", "fail")} {hilight(item.seller)}"""
                )
            return False

        return True

    def check_price(self: "TuttiMarketplace", item: Listing, item_config: TuttiItemConfig) -> bool:
        """Apply the min_price/max_price bounds to a listing.

        tutti keeps its price filter in client-side state rather than in the url,
        so the bounds are enforced on the parsed price instead.
        """
        min_price = item_config.min_price or self.config.min_price
        max_price = item_config.max_price or self.config.max_price
        if not min_price and not max_price:
            return True

        amount = parse_price(item.price)
        if amount is None:
            # a listing without a comparable price (e.g. "Preis auf Anfrage") is kept
            # so that a negotiable offer is not silently dropped.
            return True

        for bound, is_max in ((min_price, False), (max_price, True)):
            if not bound:
                continue
            limit = self.price_in_chf(bound)
            if limit is None:
                continue
            if (is_max and amount > limit) or (not is_max and amount < limit):
                if self.logger:
                    self.logger.info(
                        f"""{hilight("[Skip]", "fail")} Exclude {hilight(item.title)} with {hilight("out of range price", "fail")} {hilight(item.price)}"""
                    )
                return False
        return True

    def price_in_chf(self: "TuttiMarketplace", price: str) -> int | None:
        """Convert a configured `<amount> [currency]` bound to Swiss francs."""
        if " " not in price:
            return int(price) if price.isdigit() else None
        amount, currency = price.split(" ", 1)
        if not amount.isdigit():
            return None
        if currency == TUTTI_CURRENCY:
            return int(amount)
        try:
            return int(CurrencyConverter().convert(int(amount), currency, TUTTI_CURRENCY))
        except KeyboardInterrupt:
            raise
        except Exception as e:
            if self.logger:
                self.logger.debug(
                    f"""{hilight("[Search]", "fail")} Cannot convert {price} to {TUTTI_CURRENCY}: {e}"""
                )
            return None


class TuttiSearchResultPage(WebPage):
    def get_listings(
        self: "TuttiSearchResultPage", site_language: str = SiteLanguage.DE.value
    ) -> List[Listing]:
        try:
            data = extract_next_data(self.page.content())
        except KeyboardInterrupt:
            raise
        except Exception as e:
            if self.logger:
                self.logger.error(
                    f"{hilight('[Retrieve]', 'fail')} Failed to parse searching result: {e}"
                )
            return []

        search_result = query_data(data, "SearchListingsByConstraints") or {}
        edges = (search_result.get("listings") or {}).get("edges") or []
        if not edges and self.logger:
            self.logger.info(
                f"{hilight('[Retrieve]', 'dim')} {self.translator('No listings found')}"
            )

        listings: List[Listing] = []
        for idx, edge in enumerate(edges):
            node = edge.get("node") if isinstance(edge, dict) else None
            if not node:
                continue
            try:
                listings.append(listing_from_node(node, site_language, self.translator))
            except KeyboardInterrupt:
                raise
            except Exception as e:
                if self.logger:
                    self.logger.error(
                        f"{hilight('[Retrieve]', 'fail')} Failed to parse search results {idx + 1} listing: {e}"
                    )
                continue
        return listings


def listing_url(listing_id: str, slug: str, site_language: str) -> str:
    """Build the canonical url of a listing from its slug and id."""
    path = f"vi/{slug}/{listing_id}" if slug else f"vi/{listing_id}"
    return f"{TUTTI_BASE_URL}/{site_language}/{path}"


def listing_from_node(node: Dict[str, Any], site_language: str, translator: Translator) -> Listing:
    """Build a Listing from a tutti search-result node."""
    localization = node.get("localization") or {}
    seo_information = node.get("seoInformation") or {}
    listing_id = str(node.get("listingID") or "")
    slug = seo_information.get(f"{site_language}Slug") or seo_information.get("deSlug") or ""

    return Listing(
        marketplace="tutti",
        name="",
        id=listing_id,
        title=(localization.get("title") or "").strip(),
        image=image_of(node.get("thumbnail")),
        price=format_price(node.get("formattedPrice"), translator),
        post_url=listing_url(listing_id, slug, site_language),
        location=format_location(node.get("postcodeInformation")),
        # the search page does not expose the condition, it comes from the listing page
        condition="",
        seller=(node.get("sellerInfo") or {}).get("alias") or "",
        description=(localization.get("body") or "").strip(),
    )


class TuttiItemPage(WebPage):
    def get_condition(self: "TuttiItemPage", listing_data: Dict[str, Any]) -> str:
        """Return the "Zustand" property of a listing, if it has one.

        Not every tutti category records a condition, so an empty string is a
        valid result rather than an error.
        """
        for prop in listing_data.get("properties") or []:
            property_id = prop.get("listingPropertyID") or ""
            label = (prop.get("label") or "").strip().lower()
            if property_id.endswith("itemCondition") or label in CONDITION_LABELS:
                return (prop.get("text") or "").strip()
        return ""

    def parse(self: "TuttiItemPage", post_url: str) -> Listing:
        data = extract_next_data(self.page.content())
        listing_data = (query_data(data, "GetListingDetails") or {}).get("listing")
        if not listing_data:
            raise ValueError(f"Failed to parse {post_url}")

        localization = listing_data.get("localization") or {}
        seo_information = listing_data.get("seoInformation") or {}
        title = (localization.get("title") or "").strip()
        if not title:
            raise ValueError(f"Failed to parse {post_url}")

        site_language = (listing_data.get("language") or SiteLanguage.DE.value).lower()
        listing_id = str(listing_data.get("listingID") or "")
        slug = seo_information.get(f"{site_language}Slug") or seo_information.get("deSlug") or ""
        images = listing_data.get("images") or []

        if self.logger:
            self.logger.info(f"{hilight('[Retrieve]', 'succ')} Parsing {hilight(title)}")

        res = Listing(
            marketplace="tutti",
            name="",
            id=listing_id,
            title=title,
            image=image_of(images[0]) if images else "",
            price=format_price(listing_data.get("formattedPrice"), self.translator),
            post_url=post_url or listing_url(listing_id, slug, site_language),
            location=format_location(listing_data.get("postcodeInformation")),
            condition=self.get_condition(listing_data),
            description=(localization.get("body") or "").strip(),
            seller=(listing_data.get("sellerInfo") or {}).get("alias") or "",
        )
        if self.logger:
            self.logger.debug(f"{hilight('[Retrieve]', 'succ')} {pretty_repr(res)}")
        return res


def parse_listing(
    page: Page, post_url: str, translator: Translator | None = None, logger: Logger | None = None
) -> Listing | None:
    try:
        return TuttiItemPage(page, translator, logger).parse(post_url)
    except KeyboardInterrupt:
        raise
    except Exception as e:
        if logger:
            logger.debug(f"{hilight('[Retrieve]', 'fail')} Failed to parse {post_url}: {e}")
        return None
