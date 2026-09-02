"""Tests for first-run setup of the web UI."""

from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from ai_marketplace_monitor.webui.auth import verify_password
from ai_marketplace_monitor.webui.config_api import ConfigFileService
from ai_marketplace_monitor.webui.log_handler import LogBroadcastHandler
from ai_marketplace_monitor.webui.server import (
    AuthState,
    WebUIConfig,
    _resolve_auth,
    create_app,
)
from ai_marketplace_monitor.webui.setup import (
    ENV_PASSWORD,
    ENV_USERNAME,
    SetupError,
    WebUIAccount,
    create_account,
    provision_from_environment,
    read_account,
    token_matches,
    validate_password,
    validate_username,
    write_account,
)

EXPOSED = "0.0.0.0"  # noqa: S104 — the image binds this on purpose


#
# The account file
#


def test_read_account_returns_none_when_absent(tmp_path: Path) -> None:
    assert read_account(tmp_path / "webui.toml") is None


def test_account_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "webui.toml"
    account = create_account(path, "marius", "einGutesPasswort")

    loaded = read_account(path)
    assert loaded == account
    assert loaded is not None
    assert verify_password("einGutesPasswort", loaded.password_hash)
    assert not verify_password("falsch", loaded.password_hash)


def test_account_file_is_owner_only(tmp_path: Path) -> None:
    """The file holds a password hash; other users have no business reading it."""
    path = tmp_path / "webui.toml"
    create_account(path, "marius", "einGutesPasswort")
    assert path.stat().st_mode & 0o777 == 0o600


def test_broken_account_file_falls_back_to_setup(tmp_path: Path) -> None:
    """A half-written file must not lock the instance out with no way in."""
    path = tmp_path / "webui.toml"
    path.write_text('[webui]\nusername = "marius"\n', encoding="utf-8")
    assert read_account(path) is None

    path.write_text("this is not toml {{{", encoding="utf-8")
    assert read_account(path) is None


def test_account_survives_quotes_in_the_username(tmp_path: Path) -> None:
    path = tmp_path / "webui.toml"
    write_account(path, WebUIAccount('ma"ri\\us', "hash", "secret"))
    loaded = read_account(path)
    assert loaded is not None
    assert loaded.username == 'ma"ri\\us'


#
# Validation
#


@pytest.mark.parametrize("value", ["marius", "m.b", "a@b.ch", "user_1", "a+b-c"])
def test_valid_usernames(value: str) -> None:
    assert validate_username(value) == value


@pytest.mark.parametrize("value", ["", "   ", "with space", "a/b", "x" * 65])
def test_invalid_usernames(value: str) -> None:
    with pytest.raises(SetupError):
        validate_username(value)


def test_username_is_trimmed() -> None:
    assert validate_username("  marius  ") == "marius"


def test_password_needs_a_minimum_length() -> None:
    with pytest.raises(SetupError):
        validate_password("kurz")


def test_password_longer_than_bcrypt_handles_is_rejected() -> None:
    """Bcrypt truncates at 72 bytes — accepting more would ignore the tail."""
    with pytest.raises(SetupError):
        validate_password("x" * 73)


def test_token_matches_rejects_missing_values() -> None:
    assert token_matches("abc", "abc")
    assert not token_matches("abc", "abd")
    assert not token_matches(None, "abc")
    assert not token_matches("abc", None)
    assert not token_matches("abc", "")


#
# Unattended provisioning
#


def test_provision_from_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_PASSWORD, "einGutesPasswort")
    monkeypatch.setenv(ENV_USERNAME, "operator")

    account = provision_from_environment(tmp_path / "webui.toml")

    assert account is not None
    assert account.username == "operator"
    assert verify_password("einGutesPasswort", account.password_hash)


def test_provision_is_skipped_without_a_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_PASSWORD, raising=False)
    assert provision_from_environment(tmp_path / "webui.toml") is None


#
# Auth resolution
#


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep the developer's own environment out of these tests."""
    for name in ("FACEBOOK_USERNAME", "FACEBOOK_PASSWORD", ENV_PASSWORD, ENV_USERNAME):
        monkeypatch.delenv(name, raising=False)
    yield


def _config(tmp_path: Path, host: str = EXPOSED) -> WebUIConfig:
    config_file = tmp_path / "config.toml"
    if not config_file.exists():
        config_file.write_text("[marketplace.tutti]\n", encoding="utf-8")
    return WebUIConfig(
        host=host,
        port=8467,
        config_files=[config_file],
        log_handler=LogBroadcastHandler(),
        account_file=tmp_path / "webui.toml",
    )


def test_exposed_without_an_account_enters_setup(tmp_path: Path) -> None:
    """This is the case that used to refuse to boot and leave port 8467 dead."""
    state, info = _resolve_auth(_config(tmp_path))

    assert state.setup_required
    assert state.auth is None
    assert info.setup_token == state.setup_token
    assert info.setup_token


def test_existing_account_skips_setup(tmp_path: Path) -> None:
    create_account(tmp_path / "webui.toml", "marius", "einGutesPasswort")

    state, info = _resolve_auth(_config(tmp_path))

    assert not state.setup_required
    assert state.auth is not None
    assert state.auth.username == "marius"
    assert info.setup_token is None


def test_marketplace_credentials_still_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing installs must keep their login when they upgrade."""
    monkeypatch.setenv("FACEBOOK_USERNAME", "legacy")
    monkeypatch.setenv("FACEBOOK_PASSWORD", "legacyPasswort")

    state, _ = _resolve_auth(_config(tmp_path))

    assert not state.setup_required
    assert state.auth is not None
    assert state.auth.username == "legacy"


def test_loopback_without_an_account_stays_open(tmp_path: Path) -> None:
    """Local use keeps its password-free behaviour; setup is for exposed runs."""
    state, _ = _resolve_auth(_config(tmp_path, host="127.0.0.1"))

    assert not state.setup_required
    assert state.auth is None


#
# The HTTP flow
#


def _serve(tmp_path: Path) -> tuple[AuthState, TestClient]:
    """A running app in setup mode, plus the state so tests can read the token."""
    config = _config(tmp_path)
    state, _ = _resolve_auth(config)
    assert config.log_handler is not None
    service = ConfigFileService(config.config_files)
    return state, TestClient(create_app(config, state, service, config.log_handler))


def _client(tmp_path: Path) -> TestClient:
    return _serve(tmp_path)[1]


def test_setup_status_is_reachable_without_a_session(tmp_path: Path) -> None:
    assert _client(tmp_path).get("/api/setup/status").json() == {"setup_required": True}


def test_normal_routes_are_refused_during_setup(tmp_path: Path) -> None:
    """Setup must not be skippable by going straight to the app."""
    client = _client(tmp_path)
    assert client.get("/api/status").status_code == 403
    assert client.get("/api/logs").status_code == 403
    assert client.post("/api/login", data={"username": "x", "password": "y"}).status_code == 403


def test_setup_rejects_a_wrong_token(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post(
        "/api/setup/account",
        data={"token": "falsch", "username": "marius", "password": "einGutesPasswort"},
    )
    assert response.status_code == 401
    assert not (tmp_path / "webui.toml").exists()


def test_setup_rejects_a_weak_password(tmp_path: Path) -> None:
    state, client = _serve(tmp_path)

    response = client.post(
        "/api/setup/account",
        data={"token": state.setup_token, "username": "marius", "password": "kurz"},
    )
    assert response.status_code == 400
    assert state.setup_required


def test_setup_creates_the_account_and_opens_the_app(tmp_path: Path) -> None:
    state, client = _serve(tmp_path)

    response = client.post(
        "/api/setup/account",
        data={"token": state.setup_token, "username": "marius", "password": "einGutesPasswort"},
    )

    assert response.status_code == 200
    assert response.json()["username"] == "marius"
    # the account is on disk, and the instance has left setup mode
    stored = read_account(tmp_path / "webui.toml")
    assert stored is not None
    assert verify_password("einGutesPasswort", stored.password_hash)
    assert not state.setup_required
    # and the session issued by setup already works
    assert client.get("/api/status").status_code == 200


def test_setup_cannot_be_run_twice(tmp_path: Path) -> None:
    state, client = _serve(tmp_path)
    first_token = state.setup_token

    client.post(
        "/api/setup/account",
        data={"token": first_token, "username": "marius", "password": "einGutesPasswort"},
    )
    again = client.post(
        "/api/setup/account",
        data={"token": first_token, "username": "eindringling", "password": "einGutesPasswort"},
    )

    assert again.status_code == 409
    stored = read_account(tmp_path / "webui.toml")
    assert stored is not None
    assert stored.username == "marius"


#
# Unresolved ${ENV} placeholders are not credentials
#


def test_placeholder_credentials_do_not_count_as_an_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The config written on first run holds ``${FACEBOOK_USERNAME}``.

    Taken literally that reads as a valid credential, so the UI would demand a
    password nobody can type and setup would never start. A fresh container
    must land in setup mode instead.
    """
    monkeypatch.delenv("FACEBOOK_USERNAME", raising=False)
    monkeypatch.delenv("FACEBOOK_PASSWORD", raising=False)
    (tmp_path / "config.toml").write_text(
        '[marketplace.facebook]\nusername = "${FACEBOOK_USERNAME}"\n'
        'password = "${FACEBOOK_PASSWORD}"\n',
        encoding="utf-8",
    )

    state, _ = _resolve_auth(_config(tmp_path))

    assert state.setup_required
    assert state.auth is None


def test_placeholder_credentials_resolve_when_the_variable_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the variables actually present the indirection still works."""
    monkeypatch.setenv("FACEBOOK_USERNAME", "marius")
    monkeypatch.setenv("FACEBOOK_PASSWORD", "geheim")
    (tmp_path / "config.toml").write_text(
        '[marketplace.facebook]\nusername = "${FACEBOOK_USERNAME}"\n'
        'password = "${FACEBOOK_PASSWORD}"\n',
        encoding="utf-8",
    )

    state, _ = _resolve_auth(_config(tmp_path))

    assert not state.setup_required
    assert state.auth is not None
    assert state.auth.username == "marius"
