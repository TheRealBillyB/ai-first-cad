"""Workspace responses are not framed, and the session is not sent onward.

A picture address carries the session because an image tag cannot set a
header. The response asks the browser not to store that address and not to
send it as a referrer. The page itself refuses to be displayed in a frame.
"""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection

import pytest

from aicad.app import make_server


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
    project = tmp_path / "project"
    project.mkdir()
    (project / "mark.png").write_bytes(b"\x89PNG\r\n\x1a\nmark")
    running = make_server(project, "127.0.0.1", 0)
    thread = threading.Thread(target=running.serve_forever, daemon=True)
    thread.start()
    try:
        yield running
    finally:
        running.shutdown()
        running.server_close()


def _exchange(server, method, path, *, token="page", origin=True, headers=None):
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
    elif token:
        sent.setdefault("X-Aicad-Token", token)
    connection.request(method, path, headers=sent)
    response = connection.getresponse()
    payload = response.read()
    status = response.status
    got = {key.lower(): value for key, value in response.getheaders()}
    connection.close()
    return status, payload, got


def _assert_isolated(headers: dict, token: str) -> None:
    assert headers.get("x-frame-options") == "DENY"
    assert headers.get("content-security-policy") == "frame-ancestors 'none'"
    assert headers.get("referrer-policy") == "no-referrer"
    assert headers.get("x-content-type-options") == "nosniff"
    assert "access-control-allow-origin" not in headers
    blob = json.dumps(headers)
    assert token not in blob


def test_page_and_errors_deny_framing_and_referrers(server):
    status, payload, headers = _exchange(server, "GET", "/", token=None, origin=False)
    assert status == 200
    _assert_isolated(headers, server.page_token)
    assert server.page_token.encode("utf-8") in payload
    assert headers.get("cache-control") == "no-store"

    status, payload, headers = _exchange(
        server,
        "GET",
        "/",
        token=None,
        origin=False,
        headers={"Host": f"evil.example:{server.server_address[1]}"},
    )
    assert status == 403
    _assert_isolated(headers, server.page_token)
    assert server.page_token.encode("utf-8") not in payload
    assert server.agent_token.encode("utf-8") not in payload


def test_picture_address_is_not_stored_or_echoed(server, capsys):
    path = "/api/file?path=mark.png&token=" + server.page_token
    status, payload, headers = _exchange(server, "GET", path, token=None, origin=False)
    assert status == 200
    assert payload.startswith(b"\x89PNG")
    _assert_isolated(headers, server.page_token)
    assert headers.get("cache-control") == "no-store"
    captured = capsys.readouterr()
    assert server.page_token not in captured.out
    assert server.page_token not in captured.err
    assert server.agent_token not in captured.out
    assert server.agent_token not in captured.err


def test_a_foreign_upgrade_is_not_a_socket(server):
    status, payload, headers = _exchange(
        server,
        "GET",
        "/api/events",
        token="page",
        origin="http://evil.example",
        headers={"Connection": "Upgrade", "Upgrade": "websocket"},
    )
    assert status == 403
    assert status != 101
    _assert_isolated(headers, server.page_token)
    assert b"websocket" not in payload.lower()
