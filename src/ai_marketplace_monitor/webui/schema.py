"""Describe the config's shape so the web UI can build forms from it.

Why derive rather than hardcode
-------------------------------
The options a marketplace accepts already live in its config dataclass, with
`handle_*` validators next to them. Restating that list in JavaScript means two
sources of truth that drift the moment someone adds an option — and the tutti
work added five. Here the field list comes from ``dataclasses.fields()``, so a
new option shows up in the UI the day it is added.

What cannot be derived is the human part. A validator like ``handle_canton``
proves a value is one of 26 abbreviations, but there is no honest way to read
the list back out of the code that checks it; nor what an option means, nor
whether it holds a secret. Those live in ``FIELD_HINTS``.

Three further things the dataclass cannot say, and this module does:

*Order and grouping.* Fields come out of a dataclass in definition order and
inheritance order, which is neither the order the decisions arise in nor a
grouping anyone would recognise. ``STEPS_BY_KIND`` names the steps of each
form, and is the only source of order — a field's position is where its step
puts it.

*Which control to draw.* The annotation says a value is ``List[str]``; it does
not say that the 26 legal values belong in a chip picker rather than a comma
box, or that ``search_interval`` accepts "30m" despite being typed ``int``.
``FieldSchema.control`` carries that, separately from ``type``, which stays
what the value *is* and still drives reading and writing.

*What an empty field does.* Every option defaults to ``None``, and ``None``
means something different for each one: no ``rating`` means 3, no ``notify``
means everyone. ``FieldHint.default_note`` states it, with the consuming line
of code named in a comment so the claim can be rechecked.

A field with no hint still appears — as a plain text box in the last step's
disclosure — so forgetting one degrades the form rather than hiding the option.
"""

from __future__ import annotations

import dataclasses
import typing
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Tuple, Type

from ..ai import AIConfig
from ..config import supported_ai_backends, supported_marketplaces
from ..facebook import Category, Condition, DeliveryMethod, SortBy
from ..notification import NotificationConfig, PushNotificationConfig
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


# Steps whose content the UI supplies rather than the dataclass: the channel
# picker and the marketplace picker have no config field behind them. Without
# this they would be dropped for being empty, and the picker with them.
UI_STEPS = frozenset({"channels", "where"})


@dataclass(frozen=True)
class Step:
    """One stage of a form: a heading, a sentence, and the fields under it."""

    id: str
    title: str
    subtitle: str
    fields: Tuple[str, ...]
    # Rarely touched options, folded away at the end of this step rather than
    # collected into one pile at the end of the form. Prompts belong next to
    # the rating they change, rate limits next to delivery.
    advanced: Tuple[str, ...] = ()


# The order the decisions actually arise in. A marketplace and a hunt inherit
# 25 of their 30 options from MarketItemCommonConfig; on a marketplace those
# are defaults for its hunts, which is why they get a step of their own with a
# title that says so.
STEPS_BY_KIND: Dict[str, Tuple[Step, ...]] = {
    "marketplace": (
        Step(
            "access",
            "Zugang",
            "Womit sich der Monitor beim Marktplatz anmeldet.",
            ("username", "password", "enabled"),
            ("login_wait_time", "language"),
        ),
        Step(
            "where",
            "Suchgebiet",
            "Wo gesucht wird, solange eine Jagd nichts anderes sagt.",
            ("search_city", "search_region"),
            (),
        ),
        Step(
            "defaults",
            "Vorgaben für alle Jagden",
            "Gilt für jede Jagd auf diesem Marktplatz, die es nicht selbst regelt.",
            ("search_interval", "notify", "rating", "ai"),
            (
                "max_search_interval",
                "start_at",
                "min_price",
                "max_price",
                "condition",
                "category",
                "date_listed",
                "availability",
                "delivery_method",
                "sort_by",
                "seller_locations",
                "exclude_sellers",
                "currency",
                "prompt",
                "extra_prompt",
                "rating_prompt",
            ),
        ),
    ),
    "item": (
        Step(
            "what",
            "Was gesucht wird",
            "Die Begriffe, mit denen der Marktplatz durchsucht wird.",
            ("search_phrases", "enabled"),
            (),
        ),
        Step(
            "where",
            "Wo gesucht wird",
            "Marktplätze und, falls gewünscht, eine engere Eingrenzung als deren Vorgabe.",
            ("search_city", "search_region", "canton"),
            (),
        ),
        Step(
            "filter",
            "Filter",
            "Was ein Inserat erfüllen muss, bevor es überhaupt bewertet wird.",
            (
                "min_price",
                "max_price",
                "keywords",
                "antikeywords",
                "condition",
                "category",
                "date_listed",
            ),
            ("availability", "delivery_method", "seller_locations", "exclude_sellers", "sort_by"),
        ),
        Step(
            "judge",
            "Bewertung",
            "Woran die KI erkennt, ob ein Treffer wirklich passt.",
            ("description", "rating", "ai"),
            ("prompt", "extra_prompt", "rating_prompt"),
        ),
        Step(
            "when",
            "Wann und wer",
            "Wie oft gesucht wird und wer die Treffer bekommt.",
            ("search_interval", "notify"),
            ("max_search_interval", "start_at", "currency", "max_pages", "fetch_details"),
        ),
    ),
    "user": (
        # Both steps are filled by `_channel_steps`: the picker has no field of
        # its own, and which credentials belong here depends on what is picked.
        Step("channels", "Wege", "Worüber dieser Empfänger benachrichtigt wird.", ()),
        Step("credentials", "Zugangsdaten", "Nur für die gewählten Wege.", ()),
        Step(
            "delivery",
            "Zustellung",
            "Wie viel in der Nachricht steht und wann erinnert wird.",
            ("remind", "with_description", "enabled"),
            (
                "message_format",
                "notify_with",
                "rate_limit_enabled",
                "instance_rate_limit",
                "global_rate_limit",
                "max_retries",
                "retry_delay",
            ),
        ),
    ),
    "ai": (
        Step(
            "connection",
            "Verbindung",
            "Wo der Dienst erreichbar ist und welches Modell antwortet.",
            ("base_url", "model", "api_key", "enabled"),
            ("max_retries", "timeout"),
        ),
    ),
    "notification": (
        Step(
            "channel",
            "Weg",
            "Ein wiederverwendbarer Weg, den mehrere Empfänger nutzen können.",
            ("enabled",),
            (
                "max_retries",
                "retry_delay",
                "rate_limit_enabled",
                "instance_rate_limit",
                "global_rate_limit",
            ),
        ),
    ),
    "monitor": (
        Step(
            "display",
            "Anzeige",
            "In welcher Währung Preise erscheinen und woher die Kurse kommen.",
            ("currency", "fixer_api_key"),
            ("proxy_server", "proxy_bypass", "proxy_username", "proxy_password"),
        ),
    ),
}

# Where a variant's steps differ from its kind's. tutti needs no login and
# searches cantons, so its first step is the search area rather than a set of
# credentials it would never use.
STEPS_BY_VARIANT: Dict[Tuple[str, str], Tuple[Step, ...]] = {
    ("marketplace", "tutti"): (
        Step(
            "where",
            "Suchgebiet",
            "tutti braucht kein Konto — nur, wo gesucht werden soll.",
            ("canton", "site_language", "enabled"),
            ("max_pages", "fetch_details"),
        ),
        STEPS_BY_KIND["marketplace"][2],
    ),
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
    "start_at": "Startzeiten",
    "ai": "KI-Dienste",
    "with_description": "Beschreibung mitschicken",
    "prompt": "Prompt",
    "extra_prompt": "Prompt-Zusatz",
    "rating_prompt": "Notenskala",
    "rate_limit_enabled": "Tempolimit",
    "global_rate_limit": "Tempolimit gesamt",
    "instance_rate_limit": "Tempolimit je Weg",
    # facebook
    "username": "Benutzername",
    "password": "Passwort",
    "login_wait_time": "Wartezeit beim Login",
    "language": "Sprache",
    "search_city": "Orte",
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
    # What happens with no value. Every option defaults to None and None means
    # something different for each; the form has to say which.
    default_note: str = ""
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
    # A `bool | None` whose unset state means on. The dataclass default is
    # `None` for both answers, so which one it stands for is only readable in
    # the code that consumes it (`enabled is False`, `fetch_details or True`).
    # A checkbox has two states, so the form has to be told which one unset is.
    on_when_unset: bool = False
    # Overrides the control derived from the annotation. Needed wherever the
    # type is a poor guide: a duration is typed `int` but accepts "30m", a
    # rating is a list that is one number in practice.
    control: str = ""
    # The section kind whose names are this field's legal values. The schema
    # cannot know which recipients exist; the UI fills the options in.
    references: str = ""
    # Other fields this control edits alongside its own. Facebook zips
    # search_city, city_name, radius and currency positionally, and a shorter
    # list silently truncates the search — one row per place makes the lengths
    # equal by construction.
    composite: List[str] = field(default_factory=list)


FIELD_HINTS: Dict[str, FieldHint] = {
    # ---- shared across marketplaces and items
    "search_phrases": FieldHint(
        help="Wonach gesucht wird. Mehrere Begriffe werden einzeln gesucht.",
        placeholder="GoPro Hero",
        required=True,
    ),
    "keywords": FieldHint(
        help="Muss in Titel oder Beschreibung vorkommen. UND, ODER und Klammern sind erlaubt.",
        default_note="Leer: kein Wort wird verlangt.",
        placeholder="(gopro OR 'go pro') AND (12 OR 13)",
    ),
    "antikeywords": FieldHint(
        help="Schliesst ein Inserat aus, wenn es vorkommt.",
        default_note="Leer: nichts wird ausgeschlossen.",
    ),
    "description": FieldHint(
        help="Freitext für die KI-Bewertung. Beeinflusst nur die Note, nicht die Suche.",
        default_note="Leer: die KI urteilt nur nach den Suchbegriffen.",
        multiline=True,
    ),
    "min_price": FieldHint(
        help="Untergrenze, optional mit Währung.",
        default_note="Leer: keine Untergrenze.",
        placeholder="100",
        control="money",
    ),
    "max_price": FieldHint(
        help="Obergrenze, optional mit Währung.",
        default_note="Leer: keine Obergrenze.",
        placeholder="300 CHF",
        control="money",
    ),
    # monitor.py:241 — falls back to 3 when neither hunt nor marketplace says.
    "rating": FieldHint(
        help="Erst ab dieser KI-Note benachrichtigen.",
        default_note="Leer: ab Note 3.",
        choices=list("12345"),
        control="rating",
    ),
    # monitor.py:391 — `item or marketplace or 30 * 60`.
    "search_interval": FieldHint(
        help="Wie oft diese Suche läuft.",
        default_note="Leer: alle 30 Minuten.",
        control="duration",
    ),
    # monitor.py:396 — `item or marketplace or 60 * 60`, floor of the interval.
    "max_search_interval": FieldHint(
        help="Der Abstand wird zufällig zwischen beiden Werten gewählt.",
        default_note="Leer: höchstens eine Stunde.",
        control="duration",
    ),
    # monitor.py:172 — `item or marketplace or list(config.user)`.
    "notify": FieldHint(
        help="Wer die Treffer dieser Suche bekommt.",
        default_note="Leer: alle Empfänger.",
        references="user",
    ),
    "exclude_sellers": FieldHint(
        help="Inserate dieser Verkäufer werden übersprungen.",
        default_note="Leer: kein Verkäufer wird übersprungen.",
    ),
    # config.py:366 / monitor.py:333 — only `is False` disables.
    "enabled": FieldHint(
        help="Abgeschaltet wird nichts gesucht, bleibt aber erhalten.",
        on_when_unset=True,
    ),
    "prompt": FieldHint(
        help="Ersetzt den Standard-Prompt der KI.",
        default_note="Leer: der mitgelieferte Prompt.",
        multiline=True,
    ),
    "extra_prompt": FieldHint(help="Wird an den Standard-Prompt angehängt.", multiline=True),
    "rating_prompt": FieldHint(
        help="Ersetzt die Beschreibung der Notenskala.",
        default_note="Leer: die mitgelieferte Skala von 1 bis 5.",
        multiline=True,
    ),
    # monitor.py:128 — every enabled [ai.*] section is asked.
    "ai": FieldHint(
        help="Welche KI-Dienste bewerten sollen.",
        default_note="Leer: alle eingerichteten Dienste.",
        references="ai",
    ),
    "start_at": FieldHint(
        help="Feste Uhrzeiten statt eines Abstands.",
        default_note="Leer: es gilt der Suchabstand.",
        control="times",
    ),
    # ---- facebook
    "username": FieldHint(help="Facebook-Login.", secret=True),
    "password": FieldHint(help="Facebook-Passwort.", secret=True),
    "search_city": FieldHint(
        help="Der Teil der Marketplace-URL nach /marketplace/.",
        placeholder="zurich",
        control="locations",
        composite=["city_name", "radius", "currency"],
    ),
    "city_name": FieldHint(help="Nur zur Anzeige."),
    # facebook.py:557 — no radius means Facebook's own default.
    "radius": FieldHint(
        help="Umkreis in Meilen um die Stadt.", default_note="Leer: Facebooks Standardumkreis."
    ),
    "seller_locations": FieldHint(
        help="Nur Inserate aus diesen Orten.", default_note="Leer: aus allen Orten."
    ),
    "search_region": FieldHint(
        help="Vordefinierte Region statt einzelner Städte.",
        default_note="Leer: es gelten die Orte oben.",
    ),
    # facebook.py:435 — the URL carries no itemCondition when the list is empty.
    "condition": FieldHint(
        help="Zustand. Facebook kennt feste Werte, tutti schreibt sie aus.",
        default_note="Leer: jeder Zustand.",
        choices=[c.value for c in Condition],
        open_choices=True,
    ),
    "category": FieldHint(
        help="Auf eine Kategorie einschränken.",
        default_note="Leer: alle Kategorien.",
        choices=[c.value for c in Category],
    ),
    "delivery_method": FieldHint(
        help="Abholung oder Versand.",
        default_note="Leer: beides.",
        choices=[d.value for d in DeliveryMethod],
    ),
    "sort_by": FieldHint(
        help="Sortierung der Trefferliste.",
        default_note="Leer: Facebooks Standardsortierung.",
        choices=[s.value for s in SortBy],
    ),
    # facebook.py:447 — no daysSinceListed parameter without a value.
    "date_listed": FieldHint(
        help="Nur Inserate aus den letzten n Tagen.",
        default_note="Leer: ohne Altersgrenze.",
        choices=["1", "7", "30"],
    ),
    "availability": FieldHint(
        help="Verfügbarkeit.", default_note="Leer: alle.", choices=["all", "in", "out"]
    ),
    "login_wait_time": FieldHint(
        help="Zeit für CAPTCHA und Anmeldung, bevor weitergemacht wird.",
        default_note="Leer: eine Minute.",
        control="duration",
    ),
    "language": FieldHint(
        help="Sprache der Facebook-Oberfläche.", default_note="Leer: wie im Browser eingestellt."
    ),
    # ---- tutti
    "canton": FieldHint(
        help="Nur Inserate aus diesen Kantonen.",
        default_note="Leer: die ganze Schweiz.",
        choices=list(SWISS_CANTONS),
    ),
    # tutti.py:345 — `item or marketplace or DEFAULT_MAX_PAGES`.
    "max_pages": FieldHint(
        help="Ergebnisseiten pro Suchbegriff. 30 Inserate je Seite.",
        default_note="Leer: eine Seite, also 30 Inserate.",
    ),
    # tutti.py:297 — `config.site_language or SiteLanguage.DE.value`.
    "site_language": FieldHint(
        help="Sprachversion von tutti.ch.",
        default_note="Leer: die deutsche Fassung.",
        choices=[lang.value for lang in SiteLanguage],
    ),
    # tutti.py:350 — `fetch_details` falls through to True.
    "fetch_details": FieldHint(
        help="Inseratsseite öffnen, um den Zustand zu lesen. Aus ist schneller.",
        on_when_unset=True,
    ),
    # ---- ai
    "api_key": FieldHint(
        help="Schlüssel des Anbieters.", default_note="Leer: für Ollama richtig.", secret=True
    ),
    "base_url": FieldHint(
        help="Adresse des Dienstes.",
        default_note="Leer: die offizielle Adresse des Anbieters.",
        placeholder="http://192.168.1.169:11434/v1",
    ),
    "model": FieldHint(
        help="Modellname beim Anbieter.",
        default_note="Leer: das Standardmodell des Anbieters.",
        placeholder="qwen2.5:7b",
    ),
    "provider": FieldHint(help="Welcher Dienst.", choices=sorted(supported_ai_backends)),
    "max_retries": FieldHint(help="Versuche, bevor aufgegeben wird."),
    "timeout": FieldHint(
        help="Wie lange auf eine Antwort gewartet wird.",
        default_note="Leer: ohne Zeitlimit.",
        control="duration",
    ),
    "retry_delay": FieldHint(help="Wartezeit zwischen zwei Versuchen.", control="duration"),
    # ---- monitor
    "currency": FieldHint(
        help="Währung, in der alle Preise angezeigt werden. Preise anderer "
        "Währungen werden umgerechnet, der Originalpreis bleibt daneben stehen.",
        default_note="Leer: die Währung des jeweiligen Marktplatzes.",
        choices=["CHF", "EUR", "USD", "GBP"],
        open_choices=True,
    ),
    "fixer_api_key": FieldHint(
        help="Zugangsschlüssel von fixer.io für aktuelle Wechselkurse.",
        default_note="Leer: Kurse von Frankfurter (EZB), ohne Anmeldung.",
        secret=True,
        placeholder="von fixer.io/product",
    ),
    "proxy_server": FieldHint(
        help="Über diesen Proxy wird der Browser geführt.", default_note="Leer: ohne Proxy."
    ),
    "proxy_bypass": FieldHint(help="Adressen, die den Proxy umgehen."),
    "proxy_username": FieldHint(help="Benutzername des Proxys.", secret=True),
    "proxy_password": FieldHint(help="Passwort des Proxys.", secret=True),
    # ---- notification
    "pushbullet_token": FieldHint(help="Access Token von pushbullet.com.", secret=True),
    "pushbullet_proxy_server": FieldHint(help="Adresse des Proxys für Pushbullet."),
    "pushbullet_proxy_type": FieldHint(
        help="Art des Proxys.", choices=["http", "https", "socks5"], open_choices=True
    ),
    "pushover_user_key": FieldHint(help="User Key von pushover.net.", secret=True),
    "pushover_api_token": FieldHint(help="API Token von pushover.net.", secret=True),
    "ntfy_server": FieldHint(help="Adresse des ntfy-Servers.", placeholder="https://ntfy.sh"),
    "ntfy_topic": FieldHint(help="Topic, das abonniert wird."),
    "telegram_token": FieldHint(help="Bot-Token von @BotFather.", secret=True),
    "telegram_chat_id": FieldHint(help="An welchen Chat gesendet wird."),
    "smtp_server": FieldHint(
        help="Postausgangsserver.", default_note="Leer: aus der Adresse erraten."
    ),
    "smtp_port": FieldHint(help="Port des Postausgangsservers.", default_note="Leer: 587."),
    "smtp_username": FieldHint(help="Benutzername des Mailkontos.", secret=True),
    "smtp_password": FieldHint(help="Passwort des Mailkontos.", secret=True),
    "smtp_from": FieldHint(
        help="Absenderadresse.", default_note="Leer: die Empfängeradresse wird verwendet."
    ),
    "email": FieldHint(help="Empfängeradresse."),
    # user.py:42 — None means no reminder at all, True would mean one day.
    "remind": FieldHint(
        help="Nach dieser Zeit erneut an einen Treffer erinnern.",
        default_note="Leer: keine Erinnerung.",
        control="duration",
    ),
    # notification.py:293
    "message_format": FieldHint(
        help="Wie die Nachricht formatiert wird.",
        default_note="Leer: einfacher Text.",
        choices=["plain_text", "markdown", "html"],
    ),
    "with_description": FieldHint(
        help="Beschreibung mitschicken, wenn sie kürzer als so viele Zeichen ist. "
        "1 schickt sie immer mit.",
        default_note="Leer: ohne Beschreibung.",
    ),
    "notify_with": FieldHint(
        help="Zusätzliche Wege aus eigenen [notification]-Abschnitten.",
        default_note="Leer: nur die Wege dieses Empfängers.",
        references="notification",
    ),
    # notification.py:32 — rate_limit_enabled is a real False, not a None.
    "rate_limit_enabled": FieldHint(help="Nachrichten künstlich verlangsamen."),
    "instance_rate_limit": FieldHint(help="Nachrichten je Sekunde und Weg."),
    "global_rate_limit": FieldHint(help="Nachrichten je Sekunde insgesamt."),
}


@dataclass
class FieldSchema:
    """One editable option, as the form needs to know it."""

    name: str
    label: str
    type: str  # what the value is: "text" | "number" | "boolean" | "list"
    control: str  # what to draw: "text" | "multi" | "duration" | "locations" | …
    required: bool
    help: str
    default_note: str
    choices: List[str]
    open_choices: bool
    secret: bool
    multiline: bool
    placeholder: str
    on_when_unset: bool
    references: str
    composite: List[str]


@dataclass
class StepSchema:
    """One step of a form, with its fields already described."""

    id: str
    title: str
    subtitle: str
    fields: List[FieldSchema]
    advanced: List[FieldSchema]


@dataclass
class Channel:
    """One way of reaching a recipient, read off its notification class."""

    id: str
    label: str
    required: List[str]
    optional: List[str]


CHANNEL_LABELS = {
    "pushbullet": "Pushbullet",
    "pushover": "Pushover",
    "telegram": "Telegram",
    "ntfy": "ntfy",
    "email": "E-Mail",
}


def _field_type(annotation: Any) -> str:
    """Map a dataclass annotation onto the kind of value a field holds.

    ``List[str] | None`` is a list of values, ``bool | None`` a switch,
    ``int | None`` a number, everything else text. Optionality is read
    separately, so the ``| None`` is stripped first.
    """
    text = str(annotation)
    if "List[" in text or "list[" in text:
        return "list"
    # bool before int: bool is a subclass of int and would otherwise be a number
    if "bool" in text:
        return "boolean"
    if "int" in text or "float" in text:
        return "number"
    return "text"


def _control(hint: FieldHint, value_type: str) -> str:
    """Which control to draw, when the hint does not say."""
    if hint.control:
        return hint.control
    if hint.references:
        return "reference"
    if value_type == "boolean":
        return "checkbox"
    if hint.multiline:
        return "textarea"
    if hint.choices:
        if hint.open_choices:
            return "combo"
        return "multi" if value_type == "list" else "select"
    return "number" if value_type == "number" else "text"


def _is_required(f: "dataclasses.Field[Any]") -> bool:
    """A field is required when it has no default of any kind."""
    return (
        f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING  # type: ignore[misc]
    )


def describe_field(f: "dataclasses.Field[Any]") -> FieldSchema:
    """Turn one dataclass field into the description a form needs."""
    hint = FIELD_HINTS.get(f.name, FieldHint())
    value_type = _field_type(f.type)
    return FieldSchema(
        name=f.name,
        label=LABELS.get(f.name, f.name.replace("_", " ")),
        type=value_type,
        control=_control(hint, value_type),
        required=_is_required(f) if hint.required is None else hint.required,
        help=hint.help,
        default_note=hint.default_note,
        choices=list(hint.choices),
        open_choices=hint.open_choices,
        secret=hint.secret,
        multiline=hint.multiline,
        placeholder=hint.placeholder,
        on_when_unset=hint.on_when_unset,
        references=hint.references,
        composite=list(hint.composite),
    )


def describe_dataclass(
    cls: Type[Any], kind: str = "", variant: str = "", hide: FrozenSet[str] = frozenset()
) -> List[StepSchema]:
    """Lay a config dataclass out as the ordered steps of a form.

    Every field lands in exactly one place. Fields named by a step go where the
    step puts them; a field no step mentions falls into the last step's
    disclosure, so a newly added option is visible from the day it exists
    rather than silently absent until someone remembers this file.
    """
    hidden = HIDDEN_BY_KIND.get(kind, frozenset()) | hide
    described = {
        f.name: describe_field(f)
        for f in dataclasses.fields(cls)
        if f.name not in INTERNAL_FIELDS and f.name not in hidden and not f.name.startswith("_")
    }
    # A composite control edits its members itself; they must not also appear
    # on their own, or the same value would have two boxes.
    for schema in list(described.values()):
        if schema.name in described:
            for member in schema.composite:
                described.pop(member, None)

    steps = STEPS_BY_VARIANT.get((kind, variant)) or STEPS_BY_KIND.get(kind)
    if not steps:
        steps = (Step("all", "Optionen", "", tuple(described)),)
    if kind == "user":
        steps = _with_channel_credentials(steps)

    placed: List[StepSchema] = []
    used: set[str] = set()
    for step in steps:
        main = [described[n] for n in step.fields if n in described and n not in used]
        used.update(f.name for f in main)
        extra = [described[n] for n in step.advanced if n in described and n not in used]
        used.update(f.name for f in extra)
        placed.append(StepSchema(step.id, step.title, step.subtitle, main, extra))

    leftovers = [f for name, f in described.items() if name not in used]
    if leftovers and placed:
        placed[-1].advanced.extend(leftovers)
    return [s for s in placed if s.fields or s.advanced or s.id in UI_STEPS]


def _with_channel_credentials(steps: Tuple[Step, ...]) -> Tuple[Step, ...]:
    """Give the credentials step the fields of every channel, in channel order.

    Which of them a person actually sees is the picker's business — the form
    shows only the channels that are ticked. Listing them here keeps them out
    of the leftovers pile, where all thirteen ended up in one flat heap.
    """
    ordered: List[str] = []
    extra: List[str] = []
    for channel in channels():
        ordered.extend(channel.required)
        extra.extend(channel.optional)
    return tuple(
        (
            Step(s.id, s.title, s.subtitle, tuple(ordered), tuple(extra))
            if s.id == "credentials"
            else s
        )
        for s in steps
    )


def channels() -> List[Channel]:
    """The ways a recipient can be reached, derived from the config classes.

    ``UserConfig`` inherits from one class per channel, and each declares its
    ``required_fields``. Reading them here means a new channel class shows up
    in the form on its own, and that the form demands exactly what the sending
    code checks for — the two cannot drift.
    """
    # Everything on the two shared bases belongs to every push channel, not to
    # any one of them — `message_format` under "Pushbullet" would be a lie.
    base = {f.name for f in dataclasses.fields(NotificationConfig)} | {
        f.name for f in dataclasses.fields(PushNotificationConfig)
    }
    found: List[Channel] = []
    for cls in UserConfig.__mro__:
        if not dataclasses.is_dataclass(cls) or cls is UserConfig or cls is NotificationConfig:
            continue
        required = list(getattr(cls, "required_fields", []))
        if not required:
            continue
        own = [f.name for f in dataclasses.fields(cls) if f.name not in base]
        identifier = required[0].split("_")[0]
        found.append(
            Channel(
                id=identifier,
                label=CHANNEL_LABELS.get(identifier, identifier),
                required=required,
                optional=[n for n in own if n not in required],
            )
        )
    return sorted(found, key=lambda c: c.label)


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


def _irrelevant(kind: str, variant: str) -> FrozenSet[str]:
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
                dataclasses.asdict(step)
                for step in describe_dataclass(cls, kind, variant, _irrelevant(kind, variant))
            ]
            for variant, cls in variants.items()
        }
    return {
        "kinds": kinds,
        "channels": [dataclasses.asdict(c) for c in channels()],
        "marketplaces": sorted(supported_marketplaces),
        "ai_providers": sorted(supported_ai_backends),
    }


# Kept for the tests and any caller that wants a flat list rather than steps.
def all_fields(kind: str, variant: str) -> List[FieldSchema]:
    """Every field of one form, steps flattened away."""
    cls = _variants()[kind][variant]
    out: List[FieldSchema] = []
    for step in describe_dataclass(cls, kind, variant, _irrelevant(kind, variant)):
        out.extend(step.fields)
        out.extend(step.advanced)
    return out
