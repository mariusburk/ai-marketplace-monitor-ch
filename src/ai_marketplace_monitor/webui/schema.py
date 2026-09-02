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

# Fields every section carries for bookkeeping rather than configuration. They
# are set by the loader, not by a person, and have no place in a form.
INTERNAL_FIELDS = frozenset({"name", "monitor_config", "searched_count"})


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
    "enabled": FieldHint(help="Abgeschaltet wird nichts gesucht, bleibt aber erhalten."),
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
        help="Inseratsseite öffnen, um den Zustand zu lesen. Aus ist schneller."
    ),
    # ---- ai
    "api_key": FieldHint(help="Schlüssel des Anbieters. Ollama braucht keinen.", secret=True),
    "base_url": FieldHint(
        help="Adresse des Dienstes.", placeholder="http://192.168.1.169:11434/v1"
    ),
    "model": FieldHint(help="Modellname beim Anbieter.", placeholder="qwen2.5:7b"),
    "provider": FieldHint(help="Welcher Dienst.", choices=sorted(supported_ai_backends)),
    "max_retries": FieldHint(help="Versuche, bevor aufgegeben wird."),
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
    type: str  # "text" | "number" | "boolean" | "list"
    required: bool
    help: str
    choices: List[str]
    open_choices: bool
    secret: bool
    multiline: bool
    placeholder: str


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


def describe_dataclass(cls: Type[Any]) -> List[FieldSchema]:
    """Turn a config dataclass into the list of options a form should show."""
    described: List[FieldSchema] = []
    for f in dataclasses.fields(cls):
        if f.name in INTERNAL_FIELDS or f.name.startswith("_"):
            continue
        hint = FIELD_HINTS.get(f.name, FieldHint())
        described.append(
            FieldSchema(
                name=f.name,
                type=_field_type(f.type),
                required=_is_required(f) if hint.required is None else hint.required,
                help=hint.help,
                choices=list(hint.choices),
                open_choices=hint.open_choices,
                secret=hint.secret,
                multiline=hint.multiline,
                placeholder=hint.placeholder,
            )
        )
    described.sort(key=lambda item: (not item.required, item.name))
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
    }


def _ai_config_class(backend: Type[Any]) -> Type[Any]:
    """The config dataclass of an AI backend, without instantiating it."""
    annotation = typing.get_type_hints(backend.get_config).get("return")
    if isinstance(annotation, type) and issubclass(annotation, AIConfig):
        return annotation
    return AIConfig


def config_schema() -> Dict[str, Any]:
    """The full description the web UI needs to render every config form."""
    kinds: Dict[str, Any] = {}
    for kind, variants in _variants().items():
        kinds[kind] = {
            variant: [dataclasses.asdict(f) for f in describe_dataclass(cls)]
            for variant, cls in variants.items()
        }
    return {
        "kinds": kinds,
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
