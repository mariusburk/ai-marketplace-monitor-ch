"""Pool the prices a hunt has seen, across every marketplace it watches.

What a GoPro is worth does not depend on which site it is listed on, and
pooling roughly doubles the sample — which matters when a result page holds 30
listings and `MIN_REFERENCE_PRICES` is 4.

That cannot be computed inside a single `search()` call any more. tutti runs
hourly and facebook every half hour, so a tutti run has to be able to use
prices facebook observed twenty minutes earlier. The observations therefore
outlive the search that made them, in the same diskcache everything else uses.

Records are kept per hunt rather than one key per listing: a hunt is what gets
read back, the monitor is single threaded so read-modify-write is safe, and
scanning every key in the cache to find one hunt's prices would not be.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Tuple

from diskcache import Cache  # type: ignore

from .price_stats import PriceStats
from .utils import CacheType
from .utils import cache as default_cache

# How long an observation counts towards the going rate. Long enough to fill
# the sample from several runs, short enough that a second-hand price is still
# current.
FRESHNESS_SECONDS = 14 * 24 * 60 * 60


@dataclass(frozen=True)
class PriceObservation:
    """One priced listing, seen once, by one marketplace."""

    marketplace: str
    listing_id: str
    amount: int
    currency: str
    seen_at: float

    @property
    def key(self) -> str:
        return f"{self.marketplace}|{self.listing_id}"


def _key(hunt: str) -> Tuple[str, str]:
    return (CacheType.PRICE_OBSERVATION.value, hunt)


def _store(cache: Cache | None) -> Cache:
    return default_cache if cache is None else cache


def record(hunt: str, observations: Iterable[PriceObservation], cache: Cache | None = None) -> int:
    """Remember what a search saw. Returns how many observations are now held.

    Re-seeing a listing overwrites the old record rather than counting twice —
    the same offer appearing in ten consecutive runs must not weigh ten times
    as much as its neighbours.
    """
    store = _store(cache)
    held: Dict[str, Any] = store.get(_key(hunt)) or {}
    cutoff = time.time() - FRESHNESS_SECONDS

    held = {k: v for k, v in held.items() if float(v.get("seen_at", 0)) >= cutoff}
    for observation in observations:
        held[observation.key] = {
            "marketplace": observation.marketplace,
            "listing_id": observation.listing_id,
            "amount": observation.amount,
            "currency": observation.currency,
            "seen_at": observation.seen_at,
        }
    store.set(_key(hunt), held, tag=CacheType.PRICE_OBSERVATION.value)
    return len(held)


def observations(hunt: str, cache: Cache | None = None) -> List[PriceObservation]:
    """Every fresh observation held for a hunt."""
    held: Dict[str, Any] = _store(cache).get(_key(hunt)) or {}
    cutoff = time.time() - FRESHNESS_SECONDS
    found: List[PriceObservation] = []
    for value in held.values():
        try:
            seen_at = float(value["seen_at"])
            if seen_at < cutoff:
                continue
            found.append(
                PriceObservation(
                    marketplace=str(value["marketplace"]),
                    listing_id=str(value["listing_id"]),
                    amount=int(value["amount"]),
                    currency=str(value["currency"]),
                    seen_at=seen_at,
                )
            )
        except (KeyError, TypeError, ValueError):
            # A record written by an older version, or a half-written one.
            # Dropping it beats letting it poison the median.
            continue
    return found


def _converted(amount: int, source: str, target: str) -> int | None:
    """Amount in `target` currency, or None when it cannot be converted.

    A CHF median polluted by unconverted euros is worse than a smaller sample,
    so anything that will not convert is dropped rather than mixed in.
    """
    if source == target:
        return amount
    try:
        from currency_converter import CurrencyConverter  # type: ignore

        return int(CurrencyConverter().convert(amount, source, target))
    except Exception:
        return None


def reference(
    hunt: str, currency: str, cache: Cache | None = None
) -> Tuple[PriceStats | None, Dict[str, int]]:
    """The going rate for a hunt, and which marketplaces it was built from.

    The composition is returned alongside because pooling would otherwise hide
    that two sites price the same goods differently — a median is more
    trustworthy when you can see what it rests on.
    """
    amounts: List[int] = []
    composition: Dict[str, int] = {}
    for observation in observations(hunt, cache):
        converted = _converted(observation.amount, observation.currency, currency)
        if converted is None or converted <= 0:
            continue
        amounts.append(converted)
        composition[observation.marketplace] = composition.get(observation.marketplace, 0) + 1
    return PriceStats.from_prices(amounts), composition


__all__ = [
    "FRESHNESS_SECONDS",
    "PriceObservation",
    "observations",
    "record",
    "reference",
]
