"""The assistant token cannot widen what the person has allowed.

The page token is the person. The assistant token may edit the open project.
It cannot allow design scripts, grant the network, save settings, or start a
chat. Project text in the command body cannot choose which token that is.
"""

from __future__ import annotations

import json
import socket
import threading
from http.client import HTTPConnection

import pytest

from aicad.app import make_server
from aicad.sandbox import design_scripts_enabled


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("AICAD_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
    monkeypatch.delenv("AICAD_SCRIPT_NETWORK", raising=False)
    project = tmp_path / "project"
    project.mkdir()
    running = make_server(project, "127.0.0.1", 0)
    thread = threading.Thread(target=running.serve_forever, daemon=True)
    thread.start()
    try:
        yield running
    finally:
        running.shutdown()
        running.server_close()


def _exchange(server, method, path, body=None, *, token="page", origin=True):
    host, port = server.server_address
    connection = HTTPConnection(host, port, timeout=30)
    sent = {}
    if origin is True:
        sent["Origin"] = f"http://127.0.0.1:{port}"
    elif origin:
        sent["Origin"] = origin
    if token == "page":
        sent["X-Aicad-Token"] = server.page_token
    elif token == "agent":
        sent["X-Aicad-Token"] = server.agent_token
    elif token:
        sent["X-Aicad-Token"] = token
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        sent["Content-Type"] = "application/json"
    connection.request(method, path, body=payload, headers=sent)
    response = connection.getresponse()
    raw = response.read()
    status = response.status
    connection.close()
    return status, raw


def _listener():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    sock.settimeout(0.2)
    return sock


def test_agent_token_cannot_allow_scripts_or_network(server):
    project = server.workspace.root
    for path in ("/api/allow-scripts", "/api/script-network-once"):
        status, raw = _exchange(server, "POST", path, {"source": "ui", "actor": "user"}, token="agent")
        assert status == 403
        assert server.page_token not in raw.decode("utf-8")
        assert server.agent_token not in raw.decode("utf-8")
    assert design_scripts_enabled(project) is False
    assert server.workspace.script_status()["network"] == "off"

    status, raw = _exchange(server, "POST", "/api/allow-scripts", {}, token="page")
    assert status == 200
    assert json.loads(raw.decode("utf-8"))["result"]["enabled"] is True
    assert design_scripts_enabled(project) is True

    status, raw = _exchange(server, "POST", "/api/script-network-once", {}, token="page")
    assert status == 200
    assert server.workspace.script_status()["network"] == "next run"


def test_agent_token_cannot_save_settings_or_start_chat(server):
    sock = _listener()
    port = sock.getsockname()[1]
    try:
        status, _raw = _exchange(
            server,
            "POST",
            "/api/settings",
            {
                "source": "ui",
                "provider": "compatible",
                "base_url": f"http://127.0.0.1:{port}/v1",
                "model": "marker",
                "api_key": "CANARY_SETTINGS_KEY",
            },
            token="agent",
        )
        assert status == 403
        settings_path = server.workspace.root.parent / "settings.json"
        assert not settings_path.exists() or "CANARY_SETTINGS_KEY" not in settings_path.read_text(encoding="utf-8")
        status, raw = _exchange(
            server,
            "POST",
            "/api/chat",
            {"source": "ui", "messages": [{"role": "user", "content": "connect"}]},
            token="agent",
        )
        assert status == 403
        assert b"CANARY_SETTINGS_KEY" not in raw
        try:
            sock.accept()
            hit = True
        except (TimeoutError, socket.timeout):
            hit = False
        assert hit is False
    finally:
        sock.close()

    status, raw = _exchange(
        server,
        "POST",
        "/api/settings",
        {"provider": "compatible", "base_url": "http://127.0.0.1:9/v1", "model": "marker", "api_key": "page-key-value"},
        token="page",
    )
    assert status == 200
    body = json.loads(raw.decode("utf-8"))["result"]
    assert body["has_key"] is True
    assert "page-key-value" not in json.dumps(body)


def test_command_body_cannot_name_the_person(server):
    status, raw = _exchange(
        server,
        "POST",
        "/api/command",
        {
            "name": "run_design_script",
            "source": "ui",
            "actor": "user",
            "arguments": {"script": "print('CANARY_HIT')\n", "source": "ui"},
        },
        token="agent",
    )
    assert status == 400
    text = raw.decode("utf-8")
    assert "allow-scripts" in text or "design script" in text.lower() or "not allowed" in text.lower() or "opt" in text.lower()
    assert design_scripts_enabled(server.workspace.root) is False
    assert server.page_token not in text
    assert server.agent_token not in text
