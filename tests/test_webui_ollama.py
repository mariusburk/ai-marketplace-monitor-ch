"""Setting up a local model server without typing anything from memory.

Ollama publishes its address and its model list; the form asks the server
rather than asking the person to remember what `ollama list` printed.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Iterator, List

import pytest

from ai_marketplace_monitor.webui.diagnostics import probe_ollama
from ai_marketplace_monitor.webui.sections import config_class, validate_values

MODELS = ["qwen2.5:7b", "llama3.1:8b"]


def _server(models: List[str]) -> Any:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.rstrip("/") != "/api/tags":
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps({"models": [{"name": m} for m in models]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    return HTTPServer(("127.0.0.1", 0), Handler)


@pytest.fixture
def ollama() -> Iterator[str]:
    server = _server(MODELS)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def empty_ollama() -> Iterator[str]:
    server = _server([])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_a_reachable_server_answers_with_its_models(ollama: str) -> None:
    result = probe_ollama(ollama)

    assert result.ok
    assert result.detail["models"] == sorted(MODELS)


def test_the_address_comes_back_shaped_for_the_config(ollama: str) -> None:
    """The config wants the OpenAI-compatible path; people type the bare host."""
    assert probe_ollama(ollama).detail["base_url"] == f"http://{ollama}/v1"
    assert probe_ollama(f"http://{ollama}/v1").detail["base_url"] == f"http://{ollama}/v1"


def test_a_server_with_no_models_says_what_to_do(empty_ollama: str) -> None:
    result = probe_ollama(empty_ollama)

    assert not result.ok
    assert "ollama pull" in result.message
    assert result.detail["models"] == []


def test_an_unreachable_server_names_the_address_it_tried() -> None:
    result = probe_ollama("127.0.0.1:9")

    assert not result.ok
    assert "127.0.0.1:9" in result.message


def test_an_empty_address_asks_for_one() -> None:
    assert not probe_ollama("").ok


#
# The section itself
#


def test_an_ollama_section_can_be_described_without_a_base_url() -> None:
    """Reading the class used to mean building one, with placeholder values.

    `OllamaConfig.handle_base_url` rejects a missing address, so that probe
    raised — and creating an Ollama service through the UI answered HTTP 500
    before the form could even be checked.
    """
    assert config_class("ai", "ollama").__name__ == "OllamaConfig"


def test_ollama_needs_an_address_and_a_model() -> None:
    assert validate_values("ai", "ollama", "ollama", {"model": "q"})
    assert validate_values("ai", "ollama", "ollama", {"base_url": "http://x:11434/v1"})
    assert (
        validate_values(
            "ai", "ollama", "ollama", {"base_url": "http://x:11434/v1", "model": "qwen2.5:7b"}
        )
        == {}
    )


def test_the_form_does_not_ask_ollama_for_a_key() -> None:
    """It exists only because the OpenAI client insists on one."""
    from ai_marketplace_monitor.webui.schema import all_fields

    names = {f.name for f in all_fields("ai", "ollama")}

    assert "api_key" not in names
    assert {"base_url", "model"} <= names


def test_a_hosted_provider_still_asks_for_its_key() -> None:
    from ai_marketplace_monitor.webui.schema import all_fields

    assert "api_key" in {f.name for f in all_fields("ai", "openai")}
