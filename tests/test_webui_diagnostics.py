"""Tests for the diagnostics that replaced the shell commands.

Nothing here touches the network: the notification and AI paths are exercised
through their real code with the outermost call patched, so a regression in the
wiring still fails the test while CI stays offline.
"""

from pathlib import Path
from typing import Any, Iterator, List
from unittest.mock import patch

import pytest
from diskcache import Cache
from fastapi.testclient import TestClient

from ai_marketplace_monitor.utils import CacheType, CounterItem
from ai_marketplace_monitor.webui.config_api import ConfigFileService
from ai_marketplace_monitor.webui.diagnostics import (
    SAMPLE_LISTING,
    check_ai,
    check_notification,
    clear_cache,
    health,
)
from ai_marketplace_monitor.webui.log_handler import LogBroadcastHandler
from ai_marketplace_monitor.webui.server import WebUIConfig, _resolve_auth, create_app

CONFIG = """\
[marketplace.tutti]
canton = ['ZH']

[marketplace.facebook]
search_city = 'zurich'
enabled = false

[item.gopro]
marketplace = 'tutti'
search_phrases = 'GoPro Hero'

[user.me]
pushbullet_token = 'o.geheim'

[ai.ollama]
provider = 'ollama'
base_url = 'http://192.168.1.169:11434/v1'
model = 'qwen2.5:7b'
"""


@pytest.fixture
def config_file(tmp_path: Path) -> List[Path]:
    path = tmp_path / "config.toml"
    path.write_text(CONFIG, encoding="utf-8")
    return [path]


@pytest.fixture
def temp_store(tmp_path: Path) -> Iterator[Cache]:
    store = Cache(str(tmp_path / "cache"))
    yield store
    store.close()


#
# The sample listing
#


def test_sample_listing_is_recognisable_as_a_test() -> None:
    """It reaches a real phone; it must not look like a real find."""
    assert "TESTLAUF" in SAMPLE_LISTING.title
    assert "Selbsttest" in SAMPLE_LISTING.description


#
# Notification
#


def test_notification_reports_an_unknown_user(config_file: List[Path]) -> None:
    result = check_notification(config_file, "gibtsnicht")
    assert not result.ok
    assert "gibtsnicht" in result.message


def test_notification_sends_through_the_real_path(config_file: List[Path]) -> None:
    with patch("ai_marketplace_monitor.notification.NotificationConfig.notify_all") as notify_all:
        notify_all.return_value = True
        result = check_notification(config_file, "me")

    assert result.ok
    assert notify_all.called
    listings = notify_all.call_args.args[1]
    assert listings[0].title == SAMPLE_LISTING.title


def test_notification_forces_a_resend(config_file: List[Path]) -> None:
    """A test that only works the first time is not a test.

    The sample counts as already notified from the previous run, so the
    send has to be forced.
    """
    with patch("ai_marketplace_monitor.notification.NotificationConfig.notify_all") as notify_all:
        notify_all.return_value = True
        check_notification(config_file, "me")

    assert notify_all.call_args.kwargs["force"] is True


def test_notification_surfaces_a_rejected_token(config_file: List[Path]) -> None:
    with patch("ai_marketplace_monitor.notification.NotificationConfig.notify_all") as notify_all:
        notify_all.side_effect = RuntimeError("401 invalid access token")
        result = check_notification(config_file, "me")

    assert not result.ok
    assert "401" in result.message


#
# AI
#


def test_ai_reports_an_unknown_section(config_file: List[Path]) -> None:
    result = check_ai(config_file, "gibtsnicht")
    assert not result.ok


def test_ai_reports_an_unreachable_service(config_file: List[Path]) -> None:
    with patch("ai_marketplace_monitor.ai.OllamaBackend.connect") as connect:
        connect.side_effect = OSError("connection refused")
        result = check_ai(config_file, "ollama")

    assert not result.ok
    assert "connection refused" in result.message


def test_ai_returns_the_parsed_rating(config_file: List[Path]) -> None:
    """The parsed score is what matters, not mere reachability.

    A model that answers but cannot follow the rating format is just as
    broken.
    """
    from ai_marketplace_monitor.ai import AIResponse

    with (
        patch("ai_marketplace_monitor.ai.OllamaBackend.connect"),
        patch("ai_marketplace_monitor.ai.OllamaBackend.evaluate") as evaluate,
    ):
        evaluate.return_value = AIResponse(score=4, comment="Guter Fund.", name="ollama")
        result = check_ai(config_file, "ollama")

    assert result.ok
    assert result.detail["score"] == 4
    assert result.detail["model"] == "qwen2.5:7b"
    assert "Guter Fund." in result.message


def test_ai_distinguishes_a_bad_answer_from_no_answer(config_file: List[Path]) -> None:
    """A model that replies but cannot follow the rating format is broken too."""
    with (
        patch("ai_marketplace_monitor.ai.OllamaBackend.connect"),
        patch("ai_marketplace_monitor.ai.OllamaBackend.evaluate") as evaluate,
    ):
        evaluate.side_effect = ValueError("Empty or invalid response")
        result = check_ai(config_file, "ollama")

    assert not result.ok
    assert "antwortet" in result.message  # reached the service, failed to rate


#
# Cache
#


def test_clear_cache_rejects_an_unknown_scope(temp_store: Cache) -> None:
    assert not clear_cache("alles", temp_store).ok


def test_clear_cache_all(temp_store: Cache) -> None:
    temp_store.set("a", 1)
    temp_store.set("b", 2)

    result = clear_cache("all", temp_store)

    assert result.ok
    assert len(temp_store) == 0


def test_clear_cache_only_one_scope(temp_store: Cache) -> None:
    """One scope at a time.

    Clearing listings must not throw away the AI answers, which are the
    expensive ones to rebuild.
    """
    temp_store.set("l", 1, tag=CacheType.LISTING_DETAILS.value)
    temp_store.set("a", 2, tag=CacheType.AI_INQUIRY.value)

    clear_cache(CacheType.LISTING_DETAILS.value, temp_store)

    assert temp_store.get("a") == 2
    assert temp_store.get("l") is None


#
# Health
#


def test_health_reports_marketplaces_and_their_state(config_file: List[Path]) -> None:
    report = health(config_file)
    by_name = {m["name"]: m for m in report["marketplaces"]}

    assert report["ok"]
    assert by_name["tutti"]["enabled"] is True
    assert by_name["tutti"]["needs_login"] is False
    assert by_name["facebook"]["enabled"] is False
    assert by_name["facebook"]["needs_login"] is True


def test_health_reports_configured_notification_methods(config_file: List[Path]) -> None:
    report = health(config_file)
    assert report["users"][0]["methods"] == ["pushbullet"]


def test_health_reports_the_ai_backend(config_file: List[Path]) -> None:
    report = health(config_file)
    assert report["ai_configured"]
    assert report["ai"][0]["model"] == "qwen2.5:7b"


def test_health_includes_counters(config_file: List[Path], temp_store: Cache) -> None:
    temp_store.set(
        (CacheType.COUNTERS.value, CounterItem.SEARCH_PERFORMED.value, "gopro"),
        7,
        tag=CacheType.COUNTERS.value,
    )

    report = health(config_file, temp_store)
    item = next(i for i in report["items"] if i["name"] == "gopro")

    assert item["counters"][CounterItem.SEARCH_PERFORMED.value] == 7


def test_health_survives_a_broken_config(tmp_path: Path) -> None:
    """A broken config must produce a readable answer, not a 500."""
    path = tmp_path / "config.toml"
    path.write_text("[marketplace.tutti]\ncanton = ['XX']\n", encoding="utf-8")

    report = health([path])

    assert report["ok"] is False
    assert "error" in report


#
# Through HTTP
#


def _client(tmp_path: Path, config_file: List[Path]) -> TestClient:
    config = WebUIConfig(
        host="127.0.0.1",
        port=8467,
        config_files=config_file,
        log_handler=LogBroadcastHandler(),
        account_file=tmp_path / "webui.toml",
    )
    state, _ = _resolve_auth(config)
    assert config.log_handler is not None
    app = create_app(config, state, ConfigFileService(config_file), config.log_handler)
    return TestClient(app)


def test_health_endpoint(tmp_path: Path, config_file: List[Path]) -> None:
    body: Any = _client(tmp_path, config_file).get("/api/health").json()
    assert body["ok"]
    assert len(body["marketplaces"]) == 2


def test_notification_endpoint(tmp_path: Path, config_file: List[Path]) -> None:
    client = _client(tmp_path, config_file)
    with patch("ai_marketplace_monitor.notification.NotificationConfig.notify_all") as notify_all:
        notify_all.return_value = True
        body = client.post("/api/test/notification", json={"user": "me"}).json()

    assert body["ok"]
    assert "me" in body["message"]


def test_ai_endpoint_reports_failure_without_raising(
    tmp_path: Path, config_file: List[Path]
) -> None:
    client = _client(tmp_path, config_file)
    with patch("ai_marketplace_monitor.ai.OllamaBackend.connect") as connect:
        connect.side_effect = OSError("connection refused")
        response = client.post("/api/test/ai", json={"ai": "ollama"})

    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_ai_does_not_borrow_a_users_hunt(config_file: List[Path]) -> None:
    """The probe is neutral, so a narrow hunt cannot make the backend look bad.

    A hunt filtered to "Hero 13" rates the HERO11 sample 1/5, which reads as a
    broken backend when the backend is fine.
    """
    from ai_marketplace_monitor.ai import AIResponse

    with (
        patch("ai_marketplace_monitor.ai.OllamaBackend.connect"),
        patch("ai_marketplace_monitor.ai.OllamaBackend.evaluate") as evaluate,
    ):
        evaluate.return_value = AIResponse(score=4, comment="ok", name="ollama")
        check_ai(config_file, "ollama")

    item_config = evaluate.call_args.args[1]
    assert item_config.name == "testlauf"
    assert item_config.keywords is None


def test_ai_works_before_any_hunt_exists(tmp_path: Path) -> None:
    """A fresh install must be able to check its AI before configuring a hunt."""
    from ai_marketplace_monitor.ai import AIResponse

    path = tmp_path / "config.toml"
    path.write_text(
        "[marketplace.tutti]\n\n[item.platzhalter]\nmarketplace = 'tutti'\n"
        "search_phrases = 'x'\n\n[user.me]\npushbullet_token = 'o.x'\n\n"
        "[ai.ollama]\nprovider = 'ollama'\nbase_url = 'http://x/v1'\nmodel = 'm'\n",
        encoding="utf-8",
    )

    with (
        patch("ai_marketplace_monitor.ai.OllamaBackend.connect"),
        patch("ai_marketplace_monitor.ai.OllamaBackend.evaluate") as evaluate,
    ):
        evaluate.return_value = AIResponse(score=3, comment="ok", name="ollama")
        result = check_ai([path], "ollama")

    assert result.ok
