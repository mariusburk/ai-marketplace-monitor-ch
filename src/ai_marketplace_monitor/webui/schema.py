"""Describe the config's shape so the web UI can build forms from it.

Why derive rather than hardcode
-------------------------------
The options a marketplace accepts already live in its config dataclass, with
`handle_*` validators next to them. Restating that list in JavaScript means two
sources of truth that drift the moment someone adds an option — and the tutti
work added five. Here the field list comes from ``dataclasses.fields()``, so a
new option shows up in the UI the day it is added.

What cannot be derived is the human part: which values an option accepts, what
it means, whether it holds a secret. A validator like ``handle_canton`` proves a
value is one of 26 abbreviations, but there is no honest way to read the list
back out of the code that checks it. Those live in ``FIELD_HINTS`` below. A
field with no hint still appears — as a plain text input — so forgetting a hint
degrades the form rather than hiding the option.
"""

from __future__ import annotations

import dataclasses
import typing
from dataclasses import dataclass, field
from typing import Any, Dict, List, Type

from ..ai import AIConfig
from ..config import supported_ai_backends, supported_marketplaces
from ..facebook import Category, Condition, DeliveryMethod, SortBy
from ..marketplace import ItemConfig, MarketplaceConfig
from ..notification import NotificationConfig
from ..tutti import SWISS_CANTONS, SiteLanguage
from ..user import UserConfig
from ..utils import MonitorConfig

# Fields every section carries for bookkeeping rather than configuration. They
# are set by the loader, not by a person, and have no place in a form.
INTERNAL_FIELDS = frozenset({"name", "monitor_config", "searched_count"})

# Facebook searches a city with a radius; tutti searches cantons. Both options
# sit on the shared base class, so a tutti form would otherwise offer three
# boxes that change nothing. The marketplace class already answers this in
# `requires_search_city`, so the form asks it rather than keeping its own list.
CITY_FIELDS = frozenset({"search_city", "city_name", "radius", "search_region"})

# Fields the form sets from its own type picker rather than a text box.
HIDDEN_BY_KIND = {
    "marketplace": frozenset({"market_type"}),
    "item": frozenset({"marketplace"}),
    "ai": frozenset({"provider"}),
    # `enabled` comes from BaseConfig; switching the monitor itself off is what
    # stopping the container is for.
    "monitor": frozenset({"enabled"}),
}

# A marketplace config and an item config share 25 of their 30 options, because
# both inherit MarketItemCommonConfig — on a marketplace those options are
# *defaults for every hunt on it*, not settings of the marketplace itself.
# Rendered flat, the two forms look identical and equally unreadable. So each
# kind names the handful of options that are actually about it, and the rest is
# folded away behind one disclosure.
PRIMARY_BY_KIND = {
    "marketplace": frozenset(
        {
            "enabled",
            "username",
            "password",
            "login_wait_time",
            "language",
            "search_city",
            "city_name",
            "radius",
            "search_region",
            "currency",
            "canton",
            "site_language",
            "max_pages",
            "fetch_details",
            "search_interval",
            "max_search_interval",
            "notify",
        }
    ),
    "item": frozenset(
        {
            "enabled",
            "search_phrases",
            "keywords",
            "antikeywords",
            "description",
            "min_price",
            "max_price",
            "condition",
            "rating",
            "notify",
        }
    ),
    "user": frozenset(
        {
            "enabled",
            "email",
            "pushbullet_token",
            "pushover_user_key",
            "pushover_api_token",
            "ntfy_server",
            "ntfy_topic",
            "telegram_token",
            "telegram_chat_id",
            "notify_with",
            "remind",
        }
    ),
    "ai": frozenset({"enabled", "provider", "api_key", "base_url", "model", "max_retries"}),
    "monitor": frozenset({"currency", "fixer_api_key"}),
    "notification": frozenset(),
}

# What the folded-away group is called, per kind.
SECONDARY_LABEL = {
    "marketplace": "Vorgaben für alle Jagden dieses Marktplatzes",
    "item": "Weitere Optionen",
    "user": "Weitere Optionen",
    "ai": "Weitere Optionen",
    "monitor": "Weitere Optionen",
    "notification": "Weitere Optionen",
}

# A field's name in the form. The dataclass attribute is an English identifier;
# prettifying it ("MAX SEARCH INTERVAL") is what made the forms read like a
# database schema. A field with no entry falls back to its prettified name, so
# a new option stays usable until it gets a name here.
LABELS: Dict[str, str] = {
    # shared
    "enabled": "Aktiv",
    "search_phrases": "Suchbegriffe",
    "keywords": "Muss enthalten",
    "antikeywords": "Darf nicht enthalten",
    "description": "Beschreibung für die KI",
    "min_price": "Mindestpreis",
    "max_price": "Höchstpreis",
    "rating": "Mindestnote",
    "search_interval": "Suchabstand",
    "max_search_interval": "Suchabstand, höchstens",
    "notify": "Benachrichtigt",
    "notify_with": "Benachrichtigungsweg",
    "exclude_sellers": "Verkäufer ausschliessen",
    "currency": "Währung",
    "start_at": "Startzeit",
    "ai": "KI-Dienste",
    "with_description": "Beschreibung mitschicken",
    "prompt": "Prompt",
    "extra_prompt": "Prompt-Zusatz",
    "rating_prompt": "Notenskala",
    "rate_limit_enabled": "Tempolimit",
    "global_rate_limit": "Tempolimit gesamt",
    "instance_rate_limit": "Tempolimit je Marktplatz",
    # facebook
    "username": "Benutzername",
    "password": "Passwort",
    "login_wait_time": "Wartezeit beim Login",
    "language": "Sprache",
    "search_city": "Stadt in der URL",
    "city_name": "Stadt, angezeigt",
    "radius": "Umkreis",
    "search_region": "Region",
    "seller_locations": "Orte der Verkäufer",
    "condition": "Zustand",
    "category": "Kategorie",
    "delivery_method": "Übergabe",
    "sort_by": "Sortierung",
    "date_listed": "Höchstalter",
    "availability": "Verfügbarkeit",
    # tutti
    "canton": "Kantone",
    "max_pages": "Ergebnisseiten",
    "site_language": "Sprachversion",
    "fetch_details": "Inseratsseite öffnen",
    # ai
    "provider": "Anbieter",
    "api_key": "API-Schlüssel",
    "base_url": "Adresse",
    "model": "Modell",
    "max_retries": "Versuche",
    "retry_delay": "Wartezeit zwischen Versuchen",
    "timeout": "Zeitlimit",
    # monitor
    "fixer_api_key": "fixer.io-Schlüssel",
    "proxy_server": "Proxy-Server",
    "proxy_bypass": "Proxy-Ausnahmen",
    "proxy_username": "Proxy-Benutzer",
    "proxy_password": "Proxy-Passwort",
    # notification
    "email": "E-Mail",
    "remind": "Erneut erinnern",
    "message_format": "Nachrichtenformat",
    "pushbullet_token": "Pushbullet-Token",
    "pushbullet_proxy_server": "Pushbullet-Proxy",
    "pushbullet_proxy_type": "Pushbullet-Proxytyp",
    "pushover_user_key": "Pushover User Key",
    "pushover_api_token": "Pushover API Token",
    "ntfy_server": "ntfy-Server",
    "ntfy_topic": "ntfy-Topic",
    "telegram_token": "Telegram-Bot-Token",
    "telegram_chat_id": "Telegram-Chat-ID",
    "smtp_server": "SMTP-Server",
    "smtp_port": "SMTP-Port",
    "smtp_from": "Absenderadresse",
    "smtp_username": "SMTP-Benutzer",
    "smtp_password": "SMTP-Passwort",
}


@dataclass
class FieldHint:
    """The part of a field's description that cannot be read off the code."""

    help: str = ""
    choices: List[str] = field(default_factory=list)
    # Free-form choices: the value need not be one of `choices`, they are only
    # offered as suggestions (e.g. a tutti condition is whatever tutti prints).
    open_choices: bool = False
    secret: bool = False
    multiline: bool = False
    placeholder: str = ""
    # A `bool | None` whose unset state means on. The dataclass default is
    # `None` for both answers, so which one it stands for is only readable in
    # the code that consumes it (`enabled is False`, `fetch_details or True`).
    # A checkbox has two states, so the form has to be told which one unset is.
    on_when_unset: bool = False
    # Overrides the derived requiredness. Some options carry a default the
    # validator then refuses — `search_phrases` defaults to an empty list and
    # `handle_search_phrases` rejects exactly that. The dataclass cannot say so;
    # the form still has to mark the field.
    required: bool | None = None


FIELD_HINTS: Dict[str, FieldHint] = {
    # ---- shared across marketplaces and items
    "search_phrases": FieldHint(
        help="Wonach gesucht wird. Mehrere Begriffe werden einzeln gesucht.",
        placeholder="GoPro Hero",
        required=True,
    ),
    "keywords": FieldHint(
        help="Muss in Titel oder Beschreibung vorkommen. UND, ODER und Klammern sind erlaubt.",
        placeholder="(gopro OR 'go pro') AND (12 OR 13)",
    ),
    "antikeywords": FieldHint(help="Schliesst ein Inserat aus, wenn es vorkommt."),
    "description": FieldHint(
        help="Freitext für die KI-Bewertung. Beeinflusst nur die Note, nicht die Suche.",
        multiline=True,
    ),
    "min_price": FieldHint(help="Untergrenze, optional mit Währung.", placeholder="100"),
    "max_price": FieldHint(help="Obergrenze, optional mit Währung.", placeholder="300 CHF"),
    "rating": FieldHint(help="Erst ab dieser KI-Note benachrichtigen.", choices=list("12345")),
    "search_interval": FieldHint(help="Wie oft gesucht wird.", placeholder="1h"),
    "max_search_interval": FieldHint(help="Obergrenze für den zufälligen Abstand."),
    "notify": FieldHint(help="Wer benachrichtigt wird."),
    "exclude_sellers": FieldHint(help="Inserate dieser Verkäufer werden übersprungen."),
    "enabled": FieldHint(
        help="Abgeschaltet wird nichts gesucht, bleibt aber erhalten.", on_when_unset=True
    ),
    "prompt": FieldHint(help="Ersetzt den Standard-Prompt der KI.", multiline=True),
    "extra_prompt": FieldHint(help="Wird an den Standard-Prompt angehängt.", multiline=True),
    "rating_prompt": FieldHint(help="Ersetzt die Beschreibung der Notenskala.", multiline=True),
    # ---- facebook
    "username": FieldHint(help="Facebook-Login.", secret=True),
    "password": FieldHint(help="Facebook-Passwort.", secret=True),
    "search_city": FieldHint(
        help="Der Teil der Marketplace-URL nach /marketplace/.", placeholder="zurich"
    ),
    "city_name": FieldHint(help="Nur zur Anzeige."),
    "radius": FieldHint(help="Umkreis in Meilen um die Stadt."),
    "seller_locations": FieldHint(help="Nur Inserate aus diesen Orten."),
    "search_region": FieldHint(help="Vordefinierte Region statt einzelner Städte."),
    "condition": FieldHint(
        help="Zustand. Facebook kennt feste Werte, tutti schreibt sie aus.",
        choices=[c.value for c in Condition],
        open_choices=True,
    ),
    "category": FieldHint(
        help="Auf eine Kategorie einschränken.", choices=[c.value for c in Category]
    ),
    "delivery_method": FieldHint(
        help="Abholung oder Versand.", choices=[d.value for d in DeliveryMethod]
    ),
    "sort_by": FieldHint(help="Sortierung der Trefferliste.", choices=[s.value for s in SortBy]),
    "date_listed": FieldHint(
        help="Nur Inserate aus den letzten n Tagen.", choices=["1", "7", "30"]
    ),
    "availability": FieldHint(help="Verfügbarkeit.", choices=["all", "in", "out"]),
    "login_wait_time": FieldHint(help="Wartezeit für CAPTCHA und Login, in Sekunden."),
    # ---- tutti
    "canton": FieldHint(help="Nur Inserate aus diesen Kantonen.", choices=list(SWISS_CANTONS)),
    "max_pages": FieldHint(help="Ergebnisseiten pro Suchbegriff. 30 Inserate je Seite."),
    "site_language": FieldHint(
        help="Sprachversion von tutti.ch.", choices=[lang.value for lang in SiteLanguage]
    ),
    "fetch_details": FieldHint(
        help="Inseratsseite öffnen, um den Zustand zu lesen. Aus ist schneller.",
        on_when_unset=True,
    ),
    # ---- ai
    "api_key": FieldHint(help="Schlüssel des Anbieters. Ollama braucht keinen.", secret=True),
    "base_url": FieldHint(
        help="Adresse des Dienstes.", placeholder="http://192.168.1.169:11434/v1"
    ),
    "model": FieldHint(help="Modellname beim Anbieter.", placeholder="qwen2.5:7b"),
    "provider": FieldHint(help="Welcher Dienst.", choices=sorted(supported_ai_backends)),
    "max_retries": FieldHint(help="Versuche, bevor aufgegeben wird."),
    # ---- monitor
    "currency": FieldHint(
        help="Währung, in der alle Preise angezeigt werden. Preise anderer "
        "Währungen werden umgerechnet, der Originalpreis bleibt daneben stehen.",
        choices=["CHF", "EUR", "USD", "GBP"],
        open_choices=True,
    ),
    "fixer_api_key": FieldHint(
        help="Zugangsschlüssel von fixer.io für aktuelle Wechselkurse. "
        "Ohne Schlüssel wird der mitgelieferte EZB-Stand verwendet — älter, aber immer da.",
        secret=True,
        placeholder="von fixer.io/product",
    ),
    "proxy_server": FieldHint(help="Über diesen Proxy wird der Browser geführt."),
    "proxy_bypass": FieldHint(help="Adressen, die den Proxy umgehen."),
    "proxy_username": FieldHint(help="Benutzername des Proxys.", secret=True),
    "proxy_password": FieldHint(help="Passwort des Proxys.", secret=True),
    # ---- notification
    "pushbullet_token": FieldHint(help="Access Token von pushbullet.com.", secret=True),
    "pushover_user_key": FieldHint(help="User Key von pushover.net.", secret=True),
    "pushover_api_token": FieldHint(help="API Token von pushover.net.", secret=True),
    "smtp_password": FieldHint(help="Passwort des Mailkontos.", secret=True),
    "smtp_username": FieldHint(help="Benutzername des Mailkontos.", secret=True),
    "telegram_token": FieldHint(help="Bot-Token von @BotFather.", secret=True),
    "email": FieldHint(help="Empfängeradresse."),
    "remind": FieldHint(help="Nach dieser Zeit erneut an einen Treffer erinnern."),
}


@dataclass
class FieldSchema:
    """One editable option, as the form needs to know it."""

    name: str
    label: str
    type: str  # "text" | "number" | "boolean" | "list"
    required: bool
    help: str
    choices: List[str]
    open_choices: bool
    secret: bool
    multiline: bool
    placeholder: str
    on_when_unset: bool
    group: str  # "primary" — about this section; "secondary" — folded away


def _field_type(annotation: Any) -> str:
    """Map a dataclass annotation onto a form control.

    ``List[str] | None`` is a list of values, ``bool | None`` a switch,
    ``int | None`` a number, everything else a text box. Optionality is read
    separately, so the ``| None`` is stripped first.
    """
    text = str(annotation)
    if "List[" in text or "list[" in text:
        return "list"
    # bool before int: bool is a subclass of int and would otherwise be a number
    if "bool" in text:
        return "boolean"
    if "int" in text:
        return "number"
    return "text"


def _is_required(f: "dataclasses.Field[Any]") -> bool:
    """A field is required when it has no default of any kind."""
    return (
        f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING  # type: ignore[misc]
    )


def describe_dataclass(
    cls: Type[Any], kind: str = "", hide: typing.FrozenSet[str] = frozenset()
) -> List[FieldSchema]:
    """Turn a config dataclass into the list of options a form should show."""
    hidden = HIDDEN_BY_KIND.get(kind, frozenset()) | hide
    primary = PRIMARY_BY_KIND.get(kind)
    described: List[FieldSchema] = []
    for f in dataclasses.fields(cls):
        if f.name in INTERNAL_FIELDS or f.name in hidden or f.name.startswith("_"):
            continue
        hint = FIELD_HINTS.get(f.name, FieldHint())
        described.append(
            FieldSchema(
                name=f.name,
                label=LABELS.get(f.name, f.name.replace("_", " ")),
                type=_field_type(f.type),
                required=_is_required(f) if hint.required is None else hint.required,
                help=hint.help,
                choices=list(hint.choices),
                open_choices=hint.open_choices,
                secret=hint.secret,
                multiline=hint.multiline,
                placeholder=hint.placeholder,
                on_when_unset=hint.on_when_unset,
                group="primary" if primary is None or f.name in primary else "secondary",
            )
        )
    described.sort(key=lambda item: (item.group != "primary", not item.required, item.label))
    return described


def _variants() -> Dict[str, Dict[str, Type[Any]]]:
    """The concrete config class behind every section kind and type.

    ``marketplace`` and ``item`` vary by marketplace, ``ai`` by provider. Users
    and notifications have a single shape each; notifications discriminate on
    the keys present rather than on a declared type, so one merged description
    is the honest answer there.
    """
    marketplaces = {name: cls.get_config for name, cls in supported_marketplaces.items()}
    return {
        "marketplace": {
            name: type(fn(name=name)) for name, fn in marketplaces.items()  # type: ignore[misc]
        },
        "item": {
            name: type(cls.get_item_config(name=name, search_phrases=["x"]))
            for name, cls in supported_marketplaces.items()
        },
        "ai": {name: _ai_config_class(cls) for name, cls in supported_ai_backends.items()},
        "user": {"user": UserConfig},
        "notification": {"notification": NotificationConfig},
        "monitor": {"monitor": MonitorConfig},
    }


def _ai_config_class(backend: Type[Any]) -> Type[Any]:
    """The config dataclass of an AI backend, without instantiating it."""
    annotation = typing.get_type_hints(backend.get_config).get("return")
    if isinstance(annotation, type) and issubclass(annotation, AIConfig):
        return annotation
    return AIConfig


def _irrelevant(kind: str, variant: str) -> typing.FrozenSet[str]:
    """Options a variant inherits but never reads."""
    if kind not in ("marketplace", "item"):
        return frozenset()
    marketplace = supported_marketplaces.get(variant)
    if marketplace is not None and not marketplace.requires_search_city:
        return CITY_FIELDS
    return frozenset()


def config_schema() -> Dict[str, Any]:
    """The full description the web UI needs to render every config form."""
    kinds: Dict[str, Any] = {}
    for kind, variants in _variants().items():
        kinds[kind] = {
            variant: [
                dataclasses.asdict(f)
                for f in describe_dataclass(cls, kind, _irrelevant(kind, variant))
            ]
            for variant, cls in variants.items()
        }
    return {
        "kinds": kinds,
        "secondary_labels": SECONDARY_LABEL,
        "marketplaces": sorted(supported_marketplaces),
        "ai_providers": sorted(supported_ai_backends),
    }


__all__ = [
    "FIELD_HINTS",
    "FieldHint",
    "FieldSchema",
    "ItemConfig",
    "MarketplaceConfig",
    "config_schema",
    "describe_dataclass",
]
