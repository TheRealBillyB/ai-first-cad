"""The workspace server answers this computer only.

A foreign host, a foreign origin, and a WebSocket upgrade are refused.
The session token is not copied into the log.
"""

from __future__ import annotations

import json
import threading

import pytest
from http.client import HTTPConnection

from aicad.app import make_server


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    workspace = tmp_path / "project"
    workspace.mkdir()
    running = make_server(workspace, "127.0.0.1", 0)
    thread = threading.Thread(target=running.serve_forever, daemon=True)
    thread.start()
    try:
        yield running
    finally:
        running.shutdown()
        running.server_close()


def _exchange(server, method, path, body=None, headers=None, token="page", origin=True):
    host, port = server.server_address
    connection = HTTPConnection(host, port, timeout=30)
    sent = dict(headers or {})
    if origin is True:
        sent.setdefault("Origin", f"http://127.0.0.1:{port}")
    elif origin:
        sent["Origin"] = origin
    if token == "page":
        sent.setdefault("X-Aicad-Token", server.page_token)
    elif token == "agent":
        sent.setdefault("X-Aicad-Token", server.agent_token)
    if body is not None and "Content-Type" not in sent:
        sent["Content-Type"] = "application/json"
    connection.request(method, path, body=body, headers=sent)
    response = connection.getresponse()
    payload = response.read()
    status = response.status
    response_headers = {key.lower(): value for key, value in response.getheaders()}
    connection.close()
    return status, payload, response_headers


def _tokens_absent(server, payload: bytes, headers: dict) -> None:
    blob = payload + json.dumps(headers).encode("utf-8")
    assert server.page_token.encode("utf-8") not in blob
    assert server.agent_token.encode("utf-8") not in blob
    assert "access-control-allow-origin" not in headers


def test_a_foreign_host_and_origin_are_refused(server, capsys):
    port = int(server.server_address[1])
    status, payload, headers = _exchange(
        server,
        "GET",
        "/",
        headers={"Host": f"evil.example:{port}"},
        token=None,
        origin=None,
    )
    assert status == 403
    _tokens_absent(server, payload, headers)
    captured = capsys.readouterr()
    assert server.page_token not in captured.out
    assert server.agent_token not in captured.err

    status, payload, headers = _exchange(
        server,
        "POST",
        "/api/command",
        body=json.dumps({"name": "list_parts", "source": "ui"}),
        origin="http://evil.example",
    )
    assert status == 403
    _tokens_absent(server, payload, headers)

    status, payload, headers = _exchange(
        server,
        "POST",
        "/api/command",
        body=json.dumps({"name": "list_parts"}),
        origin=None,
    )
    assert status == 403
    _tokens_absent(server, payload, headers)


def test_a_websocket_upgrade_is_not_accepted(server):
    status, payload, headers = _exchange(
        server,
        "GET",
        "/api/events",
        headers={"Connection": "Upgrade", "Upgrade": "websocket"},
        origin="http://evil.example",
    )
    assert status != 101
    assert status == 403
    _tokens_absent(server, payload, headers)
    assert headers.get("upgrade", "").lower() != "websocket"
