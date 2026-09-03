"""Compare a listing price against the other offers the same search returned.

A language model has no market data. Asked whether a price is good it can only
recall figures from its training set, which are dated and, for a specific
currency, often simply wrong. The listings a search has already returned are a
much better yardstick: they are current, they come from the same marketplace,
and collecting them costs no extra request.

The reference set is deliberately narrow — it is built from the listings that
passed the item's keyword filters, so a Hero 13 is compared against other
Hero 13 offers rather than against every camera on the site.
"""

from dataclasses import dataclass
from statistics import median
from typing import Any, Dict, Iterable, List, Sequence, Tuple

# A handful of offers says very little about a market price, so no comparison
# is reported below this many reference prices.
MIN_REFERENCE_PRICES = 4

# How far outside a hunt's own price range an offer may sit and still count
# towards the going rate.
#
# Keyword filters cannot tell a motorcycle from an exhaust pipe for one: a
# "BMW S1000RR Auspuff" at CHF 29 matches "(BMW) AND (1000)" exactly as well as
# the bike does. On a real hunt for a CHF 5000-15000 machine, 64 of 104
# observations were under CHF 500, dragging the median to 194 — a figure that
# made every actual bike look wildly overpriced, and that went into the AI
# prompt as "the going rate".
#
# The price range is the one thing a person has already told us about the size
# of the thing they want. Using it *as* the comparison window would be circular
# — everything would sit mid-pack — so it is widened generously and used only
# to keep a different category of object out. A quarter of the floor and four
# times the ceiling leaves the distribution wide enough to be worth comparing
# against, while an accessory two orders of magnitude cheaper drops out.
CATEGORY_FLOOR = 0.25
CATEGORY_CEILING = 4.0


def category_window(minimum: int | None, maximum: int | None) -> Tuple[float, float]:
    """The range of prices that plausibly describe the same kind of object.

    Open at whichever end the hunt left open; fully open when it named neither,
    which is the honest answer — nothing has been said about the size of the
    thing, so nothing can be ruled out.
    """
    low = minimum * CATEGORY_FLOOR if minimum else 0.0
    high = maximum * CATEGORY_CEILING if maximum else float("inf")
    return low, high


# Differences this small are noise rather than a bargain.
NOTEWORTHY_PERCENT = 5


@dataclass(frozen=True)
class PriceStats:
    """Distribution of the comparable prices of a single search."""

    count: int
    minimum: int
    median: int
    maximum: int

    @classmethod
    def from_prices(cls, prices: Iterable[int | None]) -> "PriceStats | None":
        """Summarise reference prices, or None when there are too few.

        Giveaways and listings without a comparable price are dropped: a "Gratis"
        offer would drag the median down without saying anything about what the
        item is worth.
        """
        values: List[int] = sorted(x for x in prices if x is not None and x > 0)
        if len(values) < MIN_REFERENCE_PRICES:
            return None
        return cls(
            count=len(values),
            minimum=values[0],
            median=int(median(values)),
            maximum=values[-1],
        )

    def percent_from_median(self, price: int) -> int:
        """Signed distance from the median, in percent. Negative is cheaper."""
        if self.median <= 0:
            return 0
        return round((price - self.median) * 100 / self.median)

    def cheaper_than_percent(self, price: int, prices: Sequence[int]) -> int:
        """Share of reference offers that are more expensive than `price`."""
        if not prices:
            return 0
        dearer = sum(1 for x in prices if x > price)
        return round(dearer * 100 / len(prices))


def format_amount(currency: str, value: int) -> str:
    """Render an amount the way its currency is normally written.

    A code is separated from the number ("CHF 300"), a symbol is not ("$300").
    """
    if currency and currency[-1].isalpha():
        return f"{currency} {value}"
    return f"{currency}{value}"


def describe_composition(composition: Dict[str, int] | None) -> str:
    """Render which marketplaces a reference was built from, e.g. "21 tutti, 9 facebook".

    Pooling would otherwise hide that two sites price the same goods
    differently; a median is easier to trust when its basis is visible.
    """
    if not composition or len(composition) < 2:
        return ""
    parts = [f"{count} {name}" for name, count in sorted(composition.items())]
    return " (" + ", ".join(parts) + ")"


def describe_price(
    price: int | None,
    stats: "PriceStats | None",
    currency: str,
    composition: Dict[str, int] | None = None,
) -> str:
    """One line putting a price in context, or "" when there is nothing to say.

    The wording is deliberately factual rather than a verdict — it feeds both a
    notification and an AI prompt, and the model should draw its own conclusion
    from the numbers instead of being told one.
    """
    if price is None or price <= 0 or stats is None:
        return ""

    offset = stats.percent_from_median(price)
    if offset <= -NOTEWORTHY_PERCENT:
        position = f"{abs(offset)}% below"
    elif offset >= NOTEWORTHY_PERCENT:
        position = f"{offset}% above"
    else:
        position = "about level with"

    return (
        f"{format_amount(currency, price)} is {position} "
        f"the median {format_amount(currency, stats.median)} "
        f"of {stats.count} comparable listings{describe_composition(composition)} "
        f"(range {format_amount(currency, stats.minimum)}-{stats.maximum})"
    )


def price_basis(price: int | None, stats: "PriceStats | None") -> Dict[str, int]:
    """The numbers the price ruler needs, or {} when there is nothing to draw."""
    if price is None or price <= 0 or stats is None:
        return {}
    return {
        "amount": price,
        "minimum": stats.minimum,
        "median": stats.median,
        "maximum": stats.maximum,
        "count": stats.count,
    }


def convert_for_display(
    amount: int | None,
    source_currency: str,
    monitor_config: Any,
    logger: Any = None,
) -> str:
    """Render a price in the configured display currency, or "" if unneeded.

    Empty means "show the original alone": either no display currency is set,
    the marketplace already quotes it, or no rate could be found. The original
    is never replaced — a converted figure is an estimate and the listing's own
    number is the fact.
    """
    from .currency import convert

    target = getattr(monitor_config, "currency", None) if monitor_config else None
    if not target or amount is None or amount <= 0:
        return ""
    source = (source_currency or "").upper()
    if not source or source == target.upper():
        return ""

    result = convert(
        amount,
        source,
        target,
        api_key=getattr(monitor_config, "fixer_api_key", None),
        logger=logger,
    )
    if result is None:
        return ""
    return format_amount(result.target, result.amount)
