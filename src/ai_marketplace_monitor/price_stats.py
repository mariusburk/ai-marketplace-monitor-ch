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
from typing import Iterable, List, Sequence

# A handful of offers says very little about a market price, so no comparison
# is reported below this many reference prices.
MIN_REFERENCE_PRICES = 4

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


def describe_price(price: int | None, stats: "PriceStats | None", currency: str) -> str:
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
        f"of {stats.count} comparable listings "
        f"(range {format_amount(currency, stats.minimum)}-{stats.maximum})"
    )
