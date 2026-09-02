"""Answer "is it actually working?" from the browser.

Every check here existed already — as a shell command. Sending a test push
meant `docker exec … python`, checking the AI backend meant `curl`, clearing
the cache meant `rm`. Those are the last reasons to open a terminal after the
container is up, so they move into the UI.

The checks run against the *saved* config, not against a form, because that is
what the monitor will use. They are deliberately read-only apart from the two
that are not — sending a notification and clearing the cache — and both say so
in their names.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from diskcache import Cache  # type: ignore

from ..ai import AIBackend, AIResponse
from ..config import Config, supported_ai_backends, supported_marketplaces
from ..listing import Listing
from ..marketplace import ItemConfig, MarketplaceConfig
from ..user import User
from ..utils import CacheType, CounterItem
from ..utils import cache as default_cache

# The listing every test uses. Recognisable as a test at a glance, and priced
# so the AI has something to say about it.
SAMPLE_LISTING = Listing(
    marketplace="tutti",
    name="testlauf",
    id="0",
    title="TESTLAUF — GoPro HERO11 Black mit 2 Zusatzakkus",
    image="",
    price="CHF 265.-",
    post_url="https://www.tutti.ch/de/q?query=gopro",
    location="8050 Zürich, ZH",
    seller="Testlauf",
    condition="Gebraucht",
    description=(
        "Diese Anzeige stammt aus dem Selbsttest des Marktplatz-Monitors. "
        "Wenn sie auf deinem Handy ankommt, funktionieren Konfiguration, "
        "Benachrichtigung und Container."
    ),
    price_comparison="CHF 265 is 4% below the median CHF 275 of 30 comparable listings",
)


@dataclass
class CheckResult:
    """The outcome of one diagnostic, shaped for showing next to a button."""

    ok: bool
    message: str
    detail: Dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0


def _load(config_files: List[Path]) -> Config:
    return Config(config_files)


def check_notification(config_files: List[Path], user_name: str) -> CheckResult:
    """Send one real notification, so the whole path is proven end to end.

    Nothing is mocked on purpose: a token that the provider rejects is exactly
    what this is meant to catch, and only a real send catches it.
    """
    started = time.monotonic()
    try:
        config = _load(config_files)
    except Exception as exc:
        return CheckResult(False, f"Die Konfiguration lässt sich nicht laden: {exc}")

    user_config = config.user.get(user_name)
    if user_config is None:
        return CheckResult(False, f"Es gibt keinen Benutzer {user_name}.")

    item_config = next(iter(config.item.values()), None)
    if item_config is None:
        return CheckResult(False, "Es ist noch keine Jagd angelegt.")

    rating = AIResponse(score=5, comment="Testlauf des Benachrichtigungswegs.", name="test")
    try:
        # force=True: the sample was "notified" the last time this ran, and a
        # test that only works once is not a test.
        User(user_config).notify([SAMPLE_LISTING], [rating], item_config, force=True)
    except Exception as exc:
        return CheckResult(
            False,
            f"Senden fehlgeschlagen: {exc}",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    return CheckResult(
        True,
        f"Testnachricht an {user_name} abgeschickt. Sie sollte gleich ankommen.",
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def check_ai(config_files: List[Path], ai_name: str) -> CheckResult:
    """Ask the configured model to rate the sample listing.

    Reachability alone would not prove much — a model that answers but cannot
    follow the rating format is just as broken, and the parsed score is what
    the monitor depends on.
    """
    started = time.monotonic()
    try:
        config = _load(config_files)
    except Exception as exc:
        return CheckResult(False, f"Die Konfiguration lässt sich nicht laden: {exc}")

    ai_config = config.ai.get(ai_name)
    if ai_config is None:
        return CheckResult(False, f"Es gibt keinen KI-Abschnitt {ai_name}.")

    backend_class = supported_ai_backends.get(getattr(ai_config, "provider", ai_name) or ai_name)
    if backend_class is None:
        return CheckResult(False, f"Unbekannter KI-Anbieter für {ai_name}.")

    # A neutral probe rather than one of the user's hunts. Borrowing a real
    # hunt makes the answer depend on its keywords — a hunt narrowed to "Hero
    # 13" rates the sample 1/5, which reads as a broken backend when the
    # backend is fine. It also lets this run before any hunt exists.
    item_config = ItemConfig(name="testlauf", search_phrases=["GoPro Hero"])
    marketplace_config = MarketplaceConfig(name="testlauf")

    backend: AIBackend = backend_class(config=ai_config)
    try:
        backend.connect()
    except Exception as exc:
        return CheckResult(False, f"Keine Verbindung zum Dienst: {exc}")

    try:
        response = backend.evaluate(SAMPLE_LISTING, item_config, marketplace_config)
    except Exception as exc:
        return CheckResult(
            False,
            f"Der Dienst antwortet, aber die Bewertung schlug fehl: {exc}",
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    elapsed = int((time.monotonic() - started) * 1000)
    model = getattr(ai_config, "model", ai_name)
    return CheckResult(
        True,
        f"{model} antwortet, die Bewertung lässt sich lesen. "
        f"Note {response.score}/5 für das Testinserat: {response.comment}",
        detail={
            "score": response.score,
            "comment": response.comment,
            "model": getattr(ai_config, "model", None),
            "base_url": getattr(ai_config, "base_url", None),
        },
        duration_ms=elapsed,
    )


def clear_cache(scope: str, cache: Cache | None = None) -> CheckResult:
    """Forget what has been seen, so the next run reports everything again."""
    store = default_cache if cache is None else cache
    allowed = {item.value for item in CacheType} | {"all"}
    if scope not in allowed:
        return CheckResult(False, f"Unbekannter Bereich {scope}.")
    try:
        removed = store.clear() if scope == "all" else store.evict(tag=scope)
    except Exception as exc:
        return CheckResult(False, f"Der Zwischenspeicher liess sich nicht leeren: {exc}")
    return CheckResult(
        True,
        f"{removed} Einträge entfernt. Der nächste Lauf meldet wieder alles.",
        detail={"removed": removed, "scope": scope},
    )


def _counters(item_name: str, cache: Cache) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in CounterItem:
        value = cache.get((CacheType.COUNTERS.value, item.value, item_name))
        if value:
            counts[item.value] = int(value)
    return counts


def health(config_files: List[Path], cache: Cache | None = None) -> Dict[str, Any]:
    """A read-only picture of what is configured and what it has done.

    Deliberately does not claim a "next run" time: the scheduler lives in the
    monitor process and the web UI cannot see it, and a made-up number is worse
    than none.
    """
    store = default_cache if cache is None else cache
    try:
        config = _load(config_files)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    marketplaces = []
    for name, mp in config.marketplace.items():
        market_type = mp.market_type or name
        cls = supported_marketplaces.get(market_type)
        marketplaces.append(
            {
                "name": name,
                "type": market_type,
                "enabled": mp.enabled is not False,
                "needs_login": bool(cls and cls.requires_login),
                "has_credentials": bool(
                    getattr(mp, "username", None) and getattr(mp, "password", None)
                ),
            }
        )

    items = []
    for name, item in config.item.items():
        items.append(
            {
                "name": name,
                "marketplace": item.marketplace,
                "enabled": item.enabled is not False,
                "search_phrases": list(item.search_phrases or []),
                "counters": _counters(name, store),
            }
        )

    ai = [
        {
            "name": name,
            "provider": getattr(cfg, "provider", name),
            "model": getattr(cfg, "model", None),
            "base_url": getattr(cfg, "base_url", None),
        }
        for name, cfg in config.ai.items()
    ]

    users = [
        {"name": name, "methods": _notification_methods(cfg)} for name, cfg in config.user.items()
    ]

    return {
        "ok": True,
        "marketplaces": marketplaces,
        "items": items,
        "ai": ai,
        "users": users,
        "ai_configured": bool(ai),
    }


# Which config keys stand for which notification channel. Presence of the key
# is what the notifier itself checks, so presence is what gets reported.
_METHOD_KEYS = {
    "pushbullet": ("pushbullet_token",),
    "pushover": ("pushover_user_key", "pushover_api_token"),
    "ntfy": ("ntfy_server", "ntfy_topic"),
    "telegram": ("telegram_token", "telegram_chat_id"),
    "email": ("email", "smtp_username"),
}


def _notification_methods(user_config: Any) -> List[str]:
    return [
        method
        for method, keys in _METHOD_KEYS.items()
        if all(getattr(user_config, key, None) for key in keys)
    ]


__all__ = [
    "SAMPLE_LISTING",
    "CheckResult",
    "check_ai",
    "check_notification",
    "clear_cache",
    "health",
]
