"""Convert prices into one currency, so a find can be judged at a glance.

Three sources, tried in order, each a fallback for the one before:

1. **fixer.io**, when an access key is configured. Its free tier allows 100
   calls a month and does not include a choice of base currency, so every
   conversion is a cross rate through EUR — which needs one call and works on
   every tier.
2. **Frankfurter**, otherwise. No key, no signup, no quota, any base, ~30 ECB
   currencies — which covers every currency the supported marketplaces quote.
   It means conversion works out of the box, and that matters for a product
   whose whole point is that starting the container is the only setup step.
3. The **ECB snapshot bundled with `currency_converter`**, when neither
   answers. As old as the installed package, but always there; a slightly stale
   rate beats refusing to compare.

Rates are cached for a day. They move far too slowly for that to cost any
accuracy here, and it keeps fixer's 100-call budget from being spent in an
afternoon.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from logging import Logger
from typing import Any, Dict, Tuple

from diskcache import Cache  # type: ignore

from .utils import CacheType, fetch_with_retry, hilight
from .utils import cache as default_cache

FIXER_URL = "https://data.fixer.io/api/latest"
FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest"

# One day: ~31 fixer calls a month against a free budget of 100, leaving
# headroom for restarts.
RATE_TTL_SECONDS = 24 * 60 * 60

# fixer's free tier has no choice of base, so its conversions cross through EUR.
FIXER_BASE = "EUR"


@dataclass(frozen=True)
class ConversionResult:
    """An amount in the target currency, and which source produced the rate."""

    amount: int
    source: str
    target: str
    provider: str

    @property
    def live(self) -> bool:
        return self.provider not in ("bundled", "none")


def _cache_key(provider: str, base: str) -> Tuple[str, str, str]:
    return (CacheType.EXCHANGE_RATES.value, provider, base)


def _cached_rates(provider: str, base: str, store: Cache) -> Dict[str, float] | None:
    entry = store.get(_cache_key(provider, base))
    if not isinstance(entry, dict):
        return None
    if time.time() - float(entry.get("fetched_at", 0)) >= RATE_TTL_SECONDS:
        return None
    rates = entry.get("rates")
    if not isinstance(rates, dict):
        return None
    return {str(k): float(v) for k, v in rates.items()}


def _remember(provider: str, base: str, rates: Dict[str, float], store: Cache) -> None:
    store.set(
        _cache_key(provider, base),
        {"fetched_at": time.time(), "rates": rates},
        tag=CacheType.EXCHANGE_RATES.value,
    )


def _payload(url: str, logger: Logger | None) -> Any:
    fetched = fetch_with_retry(url, logger=logger)
    if fetched is None:
        return None
    try:
        return json.loads(fetched[0])
    except (ValueError, TypeError):
        return None


def fixer_rates(
    api_key: str, cache: Cache | None = None, logger: Logger | None = None
) -> Dict[str, float] | None:
    """Rates against EUR from fixer.io, cached for a day."""
    store = default_cache if cache is None else cache
    cached = _cached_rates("fixer", FIXER_BASE, store)
    if cached is not None:
        return cached

    payload = _payload(f"{FIXER_URL}?access_key={api_key}", logger)
    if not isinstance(payload, dict):
        return None
    if not payload.get("success"):
        if logger:
            error = (payload.get("error") or {}).get("info") or payload.get("error")
            logger.warning(
                f"""{hilight("[Currency]", "fail")} fixer.io refused the request: {error}"""
            )
        return None
    rates = payload.get("rates")
    if not isinstance(rates, dict) or not rates:
        return None
    clean = {str(k): float(v) for k, v in rates.items()}
    _remember("fixer", FIXER_BASE, clean, store)
    return clean


def frankfurter_rates(
    base: str, cache: Cache | None = None, logger: Logger | None = None
) -> Dict[str, float] | None:
    """Rates against `base` from Frankfurter, cached for a day. Needs no key."""
    store = default_cache if cache is None else cache
    cached = _cached_rates("frankfurter", base, store)
    if cached is not None:
        return cached

    payload = _payload(f"{FRANKFURTER_URL}?base={base}", logger)
    if not isinstance(payload, dict):
        return None
    rates = payload.get("rates")
    if not isinstance(rates, dict) or not rates:
        return None
    clean = {str(k): float(v) for k, v in rates.items()}
    _remember("frankfurter", base, clean, store)
    return clean


def _bundled(amount: float, source: str, target: str) -> int | None:
    """The ECB snapshot shipped inside `currency_converter`."""
    try:
        from currency_converter import CurrencyConverter  # type: ignore

        return int(CurrencyConverter().convert(amount, source, target))
    except Exception:
        return None


def convert(
    amount: float,
    source: str,
    target: str,
    api_key: str | None = None,
    cache: Cache | None = None,
    logger: Logger | None = None,
) -> ConversionResult | None:
    """Convert between currencies, preferring the freshest source available.

    Returns None only when no source can express the pair at all.
    """
    source = (source or "").upper()
    target = (target or "").upper()
    if not source or not target:
        return None
    if source == target:
        return ConversionResult(int(amount), source, target, provider="none")

    if api_key:
        rates = fixer_rates(api_key, cache, logger)
        if rates:
            # Cross rate through EUR, so this works on the free tier too.
            from_rate = 1.0 if source == FIXER_BASE else rates.get(source)
            to_rate = 1.0 if target == FIXER_BASE else rates.get(target)
            if from_rate and to_rate:
                return ConversionResult(
                    int(amount / from_rate * to_rate), source, target, provider="fixer"
                )

    direct = frankfurter_rates(source, cache, logger)
    if direct and direct.get(target):
        return ConversionResult(
            int(amount * direct[target]), source, target, provider="frankfurter"
        )

    bundled = _bundled(amount, source, target)
    if bundled is None:
        return None
    return ConversionResult(bundled, source, target, provider="bundled")


__all__ = [
    "FIXER_BASE",
    "RATE_TTL_SECONDS",
    "ConversionResult",
    "convert",
    "fixer_rates",
    "frankfurter_rates",
]
