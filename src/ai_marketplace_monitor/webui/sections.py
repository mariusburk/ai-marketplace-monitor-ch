"""Read, write and validate one config section at a time.

The web UI has only ever been able to PUT the whole config file as text, which
is why editing a search meant knowing TOML. Forms need the opposite: read one
section as values, write one section back, and get errors attached to the field
that caused them rather than one string for the whole file.

Everything here operates on the *editable* config file — the last one in the
chain, the user's own — and leaves the rest untouched. Rendering a section
rewrites only the lines between its header and the next one, so comments and
ordering elsewhere in the file survive.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any, Dict, List, Type

from ..config import supported_ai_backends, supported_marketplaces
from ..notification import NotificationConfig
from ..user import UserConfig
from ..utils import MonitorConfig
from .config_api import ConfigFileService, scan_sections
from .schema import INTERNAL_FIELDS, describe_dataclass
from .secrets_redact import MASK

# Section kinds the UI may edit. `region` and `translation` stay in the expert
# editor: they are rare and both are lists of lists. `monitor` is here because
# the display currency and the fixer.io key belong in a settings form.
EDITABLE_KINDS = ("marketplace", "item", "user", "notification", "ai", "monitor")

# Sections that exist once and carry no name of their own: `[monitor]`, not
# `[monitor.something]`. Their kind doubles as their name.
SINGLETON_KINDS = ("monitor",)

# A name has to survive being written as [kind.name], so no dots or brackets.
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# The key each kind uses to say what it is. Writing it is not optional: without
# `marketplace = "tutti"` on an item the loader binds it to whichever
# marketplace section comes first, and tutti options land on a facebook config.
DISCRIMINATOR = {"marketplace": "market_type", "item": "marketplace", "ai": "provider"}


class SectionError(ValueError):
    """A section could not be read or written. Safe to show a user."""


def validate_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise SectionError("Bitte einen Namen angeben.")
    if not _NAME_RE.match(cleaned):
        raise SectionError("Der Name darf nur Buchstaben, Ziffern, - und _ enthalten.")
    return cleaned


def config_class(kind: str, variant: str | None) -> Type[Any]:
    """The dataclass a section of this kind and variant validates against."""
    if kind == "marketplace":
        cls = supported_marketplaces.get(variant or "facebook")
        if cls is None:
            raise SectionError(f"Unbekannter Marktplatz: {variant}")
        return type(cls.get_config(name="probe"))
    if kind == "item":
        cls = supported_marketplaces.get(variant or "facebook")
        if cls is None:
            raise SectionError(f"Unbekannter Marktplatz: {variant}")
        return type(cls.get_item_config(name="probe", search_phrases=["probe"]))
    if kind == "ai":
        backend = supported_ai_backends.get(variant or "")
        if backend is None:
            raise SectionError(f"Unbekannter KI-Anbieter: {variant}")
        return type(backend.get_config(name="probe", api_key="probe", model="probe"))
    if kind == "user":
        return UserConfig
    if kind == "notification":
        return NotificationConfig
    if kind == "monitor":
        return MonitorConfig
    raise SectionError(f"Unbekannte Art: {kind}")


def _known_fields(cls: Type[Any]) -> List[str]:
    return [f.name for f in dataclasses.fields(cls)]


def validate_values(
    kind: str, variant: str | List[str] | None, name: str, values: Dict[str, Any]
) -> Dict[str, str]:
    """Check one section against its real dataclass.

    A hunt may name several marketplaces, and each validates the section with
    its own class — so such a hunt may only use options every one of them
    accepts. Checking against all of them here turns that into a field error
    instead of a crash mid-search.

    Returns a mapping of field name to message, empty when the section is
    valid. The loader raises one ValueError at a time and names the offending
    option in the message, so the field is recovered by looking for a known
    option name in the text; anything unattributable is reported against ``""``
    and shown at the top of the form.
    """
    variants: List[str | None] = (
        list(variant) if isinstance(variant, list) else [variant]  # type: ignore[list-item]
    )
    for one in variants or [None]:
        errors = _validate_one(kind, one, name, values)
        if errors:
            return errors
    return {}


def _validate_one(
    kind: str, variant: str | None, name: str, values: Dict[str, Any]
) -> Dict[str, str]:
    cls = config_class(kind, variant)
    known = _known_fields(cls)

    unknown = [key for key in values if key not in known]
    if unknown:
        return dict.fromkeys(unknown, f"Diese Option kennt {variant or 'dieser Abschnitt'} nicht.")

    payload = {k: v for k, v in values.items() if v not in (None, "", [])}
    payload["name"] = name
    try:
        cls(**payload)
    except TypeError as exc:
        return {"": str(exc)}
    except ValueError as exc:
        message = _plain(str(exc))
        for candidate in sorted(known, key=len, reverse=True):
            if candidate in INTERNAL_FIELDS:
                continue
            if re.search(rf"\b{re.escape(candidate)}\b", message):
                return {candidate: message}
        return {"": message}
    return {}


_MARKUP_RE = re.compile(r"\[/?[a-z]+\]")


def _plain(message: str) -> str:
    """Strip the rich markup the config validators embed in their messages."""
    return _MARKUP_RE.sub("", message).strip()


def to_toml(value: Any) -> str:
    """Render one value as TOML."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(to_toml(v) for v in value) + "]"
    text = str(value)
    if "\n" in text:
        escaped = text.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
        return f'"""\n{escaped}"""'
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_section(kind: str, name: str, values: Dict[str, Any]) -> str:
    """Render a whole section, including its header, as TOML lines."""
    header = kind if kind in SINGLETON_KINDS else f"{kind}.{name}"
    lines = [f"[{header}]"]
    for key, value in values.items():
        if key in INTERNAL_FIELDS or value in (None, "", []):
            continue
        lines.append(f"{key} = {to_toml(value)}")
    return "\n".join(lines) + "\n"


class SectionService:
    """Section-level editing on top of the whole-file ConfigFileService."""

    def __init__(self, files: ConfigFileService) -> None:
        self._files = files

    # -- reading -------------------------------------------------------

    def list_sections(self) -> List[Dict[str, Any]]:
        """Every editable section across all config files, secrets masked."""
        listed: List[Dict[str, Any]] = []
        for info in self._files.list_files():
            content, _ = self._files.read(info.id)
            for section in scan_sections(content):
                if section.prefix not in EDITABLE_KINDS:
                    continue
                singleton = section.prefix in SINGLETON_KINDS
                if not section.suffix and not singleton:
                    continue
                listed.append(
                    {
                        "kind": section.prefix,
                        "name": section.suffix or section.prefix,
                        "variant": self._variant(section.prefix, section.suffix, section.fields),
                        "values": self._mask(section.fields),
                        "editable": info.id == self._files.list_files()[-1].id,
                    }
                )
        return listed

    def get_section(self, kind: str, name: str) -> Dict[str, Any]:
        for section in self.list_sections():
            if section["kind"] == kind and section["name"] == name:
                return section
        raise SectionError(f"Abschnitt {kind}.{name} existiert nicht.")

    @staticmethod
    def _variant(kind: str, name: str, fields: Dict[str, Any]) -> str | None:
        """Which concrete type a section is, as the loader would decide it."""
        if kind == "marketplace":
            declared = fields.get("market_type")
            if isinstance(declared, str):
                return declared
            return name if name in supported_marketplaces else "facebook"
        if kind == "ai":
            declared = fields.get("provider")
            return declared if isinstance(declared, str) else name
        if kind == "item":
            declared = fields.get("marketplace")
            return declared if isinstance(declared, str) else None
        return None

    @staticmethod
    def _mask(fields: Dict[str, Any]) -> Dict[str, Any]:
        from .secrets_redact import _is_sensitive  # local: private helper reuse

        return {
            key: (MASK if _is_sensitive(key) and isinstance(value, str) and value else value)
            for key, value in fields.items()
        }

    # -- writing -------------------------------------------------------

    def save_section(
        self,
        kind: str,
        name: str,
        variant: str | List[str] | None,
        values: Dict[str, Any],
        *,
        create: bool,
    ) -> Dict[str, str]:
        """Create or replace one section. Returns field errors, empty on success."""
        if kind not in EDITABLE_KINDS:
            raise SectionError(f"Abschnitt {kind} kann hier nicht bearbeitet werden.")
        name = kind if kind in SINGLETON_KINDS else validate_name(name)

        file_id = self._files.list_files()[-1].id
        content, mtime = self._files.read(file_id)
        existing = self._find(content, kind, name)
        if create and existing is not None:
            raise SectionError(f"{kind}.{name} gibt es schon.")
        if not create and existing is None:
            raise SectionError(f"Abschnitt {kind}.{name} existiert nicht.")

        merged = self._unmask(values, existing.fields if existing else {})
        key = DISCRIMINATOR.get(kind)
        if key and variant:
            merged[key] = variant
        errors = validate_values(kind, self._market_type(kind, variant), name, merged)
        if errors:
            return errors

        rendered = render_section(kind, name, merged)
        updated = self._splice(content, existing, rendered)
        _, ok, error = self._files.write(file_id, updated, mtime)
        if not ok:
            return {"": error or "Die Konfiguration wurde abgelehnt."}
        return {}

    def _market_type(self, kind: str, variant: Any) -> Any:
        """Which config class a section validates against.

        For an item, ``variant`` names a *marketplace section*, which may be
        called anything; its ``market_type`` decides the class. Falling back to
        the section name covers the common case where they are the same.
        """
        if kind != "item" or not variant:
            return variant
        if isinstance(variant, list):
            return [self._market_type(kind, one) for one in variant]
        for section in self.list_sections():
            if section["kind"] == "marketplace" and section["name"] == variant:
                declared = section["values"].get("market_type")
                if isinstance(declared, str):
                    return declared
                return section["variant"]
        return variant

    def delete_section(self, kind: str, name: str) -> None:
        file_id = self._files.list_files()[-1].id
        content, mtime = self._files.read(file_id)
        existing = self._find(content, kind, name)
        if existing is None:
            raise SectionError(f"Abschnitt {kind}.{name} existiert nicht.")
        updated = self._splice(content, existing, "")
        _, ok, error = self._files.write(file_id, updated, mtime)
        if not ok:
            raise SectionError(error or "Die Konfiguration wurde abgelehnt.")

    @staticmethod
    def _find(content: str, kind: str, name: str) -> Any:
        for section in scan_sections(content):
            if section.prefix != kind:
                continue
            if section.suffix == name or (kind in SINGLETON_KINDS and not section.suffix):
                return section
        return None

    @staticmethod
    def _unmask(values: Dict[str, Any], existing: Dict[str, Any]) -> Dict[str, Any]:
        """Keep the stored secret wherever the form sent the mask back."""
        merged = dict(values)
        for key, value in values.items():
            if value == MASK:
                if key in existing:
                    merged[key] = existing[key]
                else:
                    merged.pop(key, None)
        return merged

    @staticmethod
    def _splice(content: str, existing: Any, rendered: str) -> str:
        """Put `rendered` where `existing` was, or at the end.

        The seam is normalised to exactly one blank line between neighbours,
        rather than trying to make a delete the byte-exact inverse of the
        matching insert — that is not knowable, since nothing records whether a
        blank line was the file's own or a separator this code added. Comments
        and every other section keep their formatting untouched.
        """
        lines = content.splitlines(keepends=True)
        if existing is None:
            head, tail = lines, []
        else:
            head, tail = lines[: existing.line_start], lines[existing.line_end :]

        while head and not head[-1].strip():
            head.pop()
        if head:
            head.append("\n")

        result = "".join(head) + rendered + "".join(tail)
        return result if result.endswith("\n") else result + "\n"


__all__ = [
    "EDITABLE_KINDS",
    "SectionError",
    "SectionService",
    "config_class",
    "describe_dataclass",
    "render_section",
    "to_toml",
    "validate_name",
    "validate_values",
]
