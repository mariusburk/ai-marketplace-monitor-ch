"""The bridge between the browser's noVNC and the container's x11vnc.

It answered every handshake with `Sec-WebSocket-Protocol: binary`. noVNC has
not asked for that subprotocol since 1.3 — it sets `binaryType` on the socket
instead — and RFC 6455 forbids a server naming one that was not offered, so
Chrome refused the upgrade and the "Browser" button led to noVNC's
"Failed to connect to server".
"""

import os
import socket
import threading
from pathlib import Path
from typing import Any, Iterator, List

import pytest
from fastapi.testclient import TestClient

from ai_marketplace_monitor.webui.config_api import ConfigFileService
from ai_marketplace_monitor.webui.log_handler import LogBroadcastHandler
from ai_marketplace_monitor.webui.server import WebUIConfig, _resolve_auth, create_app

CONFIG = """\
[marketplace.tutti]
canton = ['ZH']

[user.me]
pushbullet_token = 'o.x'

[item.velo]
marketplace = 'tutti'
search_phrases = 'velo'
"""


class FakeVnc:
    """Stands in for x11vnc: greets, then echoes whatever it is sent."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.received: List[bytes] = []
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        with conn:
            conn.sendall(b"RFB 003.008\n")
            while True:
                try:
                    data = conn.recv(1024)
                except OSError:
                    return
                if not data:
                    return
                self.received.append(data)
                conn.sendall(data)

    def close(self) -> None:
        self.sock.close()


@pytest.fixture
def bridge(tmp_path: Path, monkeypatch: Any) -> Iterator[TestClient]:
    vnc = FakeVnc()
    novnc = tmp_path / "novnc"
    novnc.mkdir()
    (novnc / "vnc.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setenv("AIMM_ENABLE_VNC", "1")
    monkeypatch.setenv("AIMM_NOVNC_DIR", str(novnc))
    monkeypatch.setenv("AIMM_VNC_HOST", "127.0.0.1")
    monkeypatch.setenv("AIMM_VNC_PORT", str(vnc.port))

    path = tmp_path / "config.toml"
    path.write_text(CONFIG, encoding="utf-8")
    config = WebUIConfig(
        host="127.0.0.1",
        port=8467,
        config_files=[path],
        log_handler=LogBroadcastHandler(),
        account_file=tmp_path / "webui.toml",
    )
    # loopback with no account is the open mode, so the socket needs no cookie
    state, _ = _resolve_auth(config)
    assert config.log_handler is not None
    app = create_app(config, state, ConfigFileService([path]), config.log_handler)
    try:
        yield TestClient(app)
    finally:
        vnc.close()


def test_a_client_offering_no_subprotocol_is_accepted(bridge: TestClient) -> None:
    """This is how noVNC connects, and it used to be refused."""
    with bridge.websocket_connect("/ws/vnc") as socket_:
        assert socket_.receive_bytes() == b"RFB 003.008\n"


def test_the_response_names_no_subprotocol_that_was_not_asked_for(bridge: TestClient) -> None:
    with bridge.websocket_connect("/ws/vnc") as socket_:
        socket_.receive_bytes()
        accepted = socket_.scope.get("subprotocol")

    assert not accepted


def test_what_gets_answered_for_each_offer() -> None:
    """The test client cannot show the accepted subprotocol, so ask directly."""
    from ai_marketplace_monitor.webui.server import negotiated_subprotocol

    # how noVNC connects
    assert negotiated_subprotocol(None) is None
    assert negotiated_subprotocol("") is None
    # older builds and other clients that do ask
    assert negotiated_subprotocol("binary") == "binary"
    assert negotiated_subprotocol("base64, binary") == "binary"
    # never one that was not offered
    assert negotiated_subprotocol("base64") is None


def test_a_client_that_does_ask_for_binary_is_still_served(bridge: TestClient) -> None:
    """Older noVNC builds and other clients do offer it."""
    with bridge.websocket_connect("/ws/vnc", subprotocols=["binary"]) as socket_:
        assert socket_.receive_bytes() == b"RFB 003.008\n"


def test_bytes_travel_in_both_directions(bridge: TestClient) -> None:
    """A handshake that opens but relays nothing looks the same from outside."""
    with bridge.websocket_connect("/ws/vnc") as socket_:
        assert socket_.receive_bytes() == b"RFB 003.008\n"
        socket_.send_bytes(b"RFB 003.008\n")

        assert socket_.receive_bytes() == b"RFB 003.008\n"


def test_the_bridge_stays_out_of_the_way_when_it_is_not_wanted(tmp_path: Path) -> None:
    """Without AIMM_ENABLE_VNC there is no socket and no /vnc mount."""
    os.environ.pop("AIMM_ENABLE_VNC", None)
    path = tmp_path / "config.toml"
    path.write_text(CONFIG, encoding="utf-8")
    config = WebUIConfig(
        host="127.0.0.1",
        port=8467,
        config_files=[path],
        log_handler=LogBroadcastHandler(),
        account_file=tmp_path / "webui.toml",
    )
    state, _ = _resolve_auth(config)
    assert config.log_handler is not None
    client = TestClient(create_app(config, state, ConfigFileService([path]), config.log_handler))

    assert client.get("/api/status").json()["vnc_enabled"] is False
