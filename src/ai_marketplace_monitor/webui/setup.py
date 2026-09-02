"""First-run account setup for the web UI.

Why this exists
---------------
The docker image binds the UI to ``0.0.0.0``, and an exposed UI refuses to
start without credentials. Until now those credentials could only come from a
``[marketplace.*]`` section or the ``FACEBOOK_USERNAME`` / ``FACEBOOK_PASSWORD``
environment variables. That leaves a fresh container with no UI in which to
enter them, and gives a tutti-only install — which needs no marketplace login at
all — no UI ever.

Setup mode breaks the deadlock. With no account on disk the server still starts,
serves only the setup routes, and prints a one-time token to stdout. Being able
to read ``docker compose logs`` is what stands in for a password until one
exists; it is the same bargain Portainer and Paperless strike, and it is the one
command-line step that cannot honestly be removed, since something has to prove
access to the host.

The account lives in its own file rather than in ``config.toml``, for three
reasons: the monitor's config validator rejects unknown top-level sections, the
UI rewrites ``config.toml`` on every edit, and a bcrypt hash has no business in
the file a user opens to change a search.
"""

from __future__ import annotations

import os
import re
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - legacy runtimes
    import tomli as tomllib

from .auth import hash_password

ACCOUNT_FILENAME = "webui.toml"

# Environment variables that provision an account without a browser, for
# unattended deployments.
ENV_USERNAME = "AIMM_ADMIN_USERNAME"
ENV_PASSWORD = "AIMM_ADMIN_PASSWORD"
DEFAULT_USERNAME = "admin"

MIN_PASSWORD_LENGTH = 8
# bcrypt silently truncates beyond 72 bytes, so reject longer inputs outright
# rather than accept a password whose tail is ignored.
MAX_PASSWORD_BYTES = 72
MAX_USERNAME_LENGTH = 64

_USERNAME_RE = re.compile(r"^[\w.@+-]+$", re.UNICODE)


class SetupError(ValueError):
    """A setup input was rejected. The message is safe to show a user."""


@dataclass(frozen=True)
class WebUIAccount:
    """The single administrator account of this instance."""

    username: str
    password_hash: str
    session_secret: str


def default_account_file() -> Path:
    """Where the account lives when nothing overrides it."""
    return Path.home() / ".ai-marketplace-monitor" / ACCOUNT_FILENAME


def validate_username(username: str) -> str:
    """Return a cleaned username, or raise SetupError explaining the problem."""
    cleaned = (username or "").strip()
    if not cleaned:
        raise SetupError("Bitte einen Benutzernamen angeben.")
    if len(cleaned) > MAX_USERNAME_LENGTH:
        raise SetupError(f"Der Benutzername darf höchstens {MAX_USERNAME_LENGTH} Zeichen haben.")
    if not _USERNAME_RE.match(cleaned):
        raise SetupError("Der Benutzername darf nur Buchstaben, Ziffern und . @ + - _ enthalten.")
    return cleaned


def validate_password(password: str) -> str:
    """Return the password, or raise SetupError explaining the problem."""
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise SetupError(f"Das Passwort braucht mindestens {MIN_PASSWORD_LENGTH} Zeichen.")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise SetupError(f"Das Passwort darf höchstens {MAX_PASSWORD_BYTES} Bytes lang sein.")
    return password


def _quote(value: str) -> str:
    """Render a string as a TOML basic string."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def read_account(path: Path) -> WebUIAccount | None:
    """Load the account, or None when there is none to load.

    A malformed or half-written file counts as "no account" so that a broken
    file drops the instance back into setup mode rather than locking it out
    with no way in.
    """
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None

    section = data.get("webui")
    if not isinstance(section, dict):
        return None
    username = section.get("username")
    password_hash = section.get("password_hash")
    session_secret = section.get("session_secret")
    if not (
        isinstance(username, str)
        and isinstance(password_hash, str)
        and isinstance(session_secret, str)
        and username
        and password_hash
        and session_secret
    ):
        return None
    return WebUIAccount(
        username=username, password_hash=password_hash, session_secret=session_secret
    )


def write_account(path: Path, account: WebUIAccount) -> None:
    """Persist the account with owner-only permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "# Web UI account for the AI Marketplace Monitor.\n"
        "# Written by the setup flow — edit through the UI rather than by hand.\n"
        "# Delete this file to return the instance to first-run setup.\n"
        "[webui]\n"
        f"username = {_quote(account.username)}\n"
        f"password_hash = {_quote(account.password_hash)}\n"
        f"session_secret = {_quote(account.session_secret)}\n"
    )
    # Create with 0600 from the start so the hash is never briefly world
    # readable between write and chmod.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
    except BaseException:
        os.close(descriptor)
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows and some mounted filesystems — best effort.
        pass


def create_account(path: Path, username: str, password: str) -> WebUIAccount:
    """Validate, hash and persist a new administrator account."""
    account = WebUIAccount(
        username=validate_username(username),
        password_hash=hash_password(validate_password(password)),
        session_secret=secrets.token_urlsafe(32),
    )
    write_account(path, account)
    return account


def provision_from_environment(path: Path) -> WebUIAccount | None:
    """Create the account from environment variables, for unattended installs.

    Returns None when ``AIMM_ADMIN_PASSWORD`` is unset — the normal case, where
    the browser-based setup flow takes over instead.
    """
    password = os.environ.get(ENV_PASSWORD)
    if not password:
        return None
    username = os.environ.get(ENV_USERNAME) or DEFAULT_USERNAME
    return create_account(path, username, password)


def generate_setup_token() -> str:
    """A one-time token, printed to the log, that grants access to setup."""
    return secrets.token_urlsafe(24)


def token_matches(expected: str | None, given: str | None) -> bool:
    """Constant-time comparison that tolerates missing values."""
    if not expected or not given:
        return False
    return secrets.compare_digest(expected, given)
