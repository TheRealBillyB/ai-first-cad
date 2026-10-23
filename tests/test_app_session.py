"""The workspace server does not trust the caller to name themselves."""

import json
import os
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from aicad.app import make_server

_CREATE = {
    "name": "create_part_from_ops",
    "arguments": {"name": "block", "ops": [{"op": "add", "shape": "box", "size": [4, 4, 4]}]},
}


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
    workspace = tmp_path / "project"
    (workspace).mkdir()
    (workspace / "notes.txt").write_text("hello\n", encoding="utf-8")
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
    elif token:
        sent.setdefault("X-Aicad-Token", token)
    if body is not None and "Content-Type" not in sent:
        sent["Content-Type"] = "application/json"
    connection.request(method, path, body=body, headers=sent)
    response = connection.getresponse()
    payload = response.read()
    status = response.status
    response_headers = {key.lower(): value for key, value in response.getheaders()}
    connection.close()
    return status, payload, response_headers


def _parts(server) -> list:
    return server.workspace.execute("list_parts")["parts"]


def test_page_embeds_the_session_and_static_files_stay_open(server):
    status, payload, headers = _exchange(server, "GET", "/", token=None, origin=None)
    html = payload.decode("utf-8")
    assert status == 200
    assert headers["cache-control"] == "no-store"
    assert f'content="{server.page_token}"' in html
    assert "__AICAD_TOKEN__" not in html
    assert server.agent_token not in html

    status, payload, _headers = _exchange(server, "GET", "/favicon.ico", token=None, origin=None)
    assert status == 200
    assert payload.startswith(b"\x89PNG")
    status, payload, _headers = _exchange(server, "GET", "/static/app.js", token=None, origin=None)
    assert status == 200
    assert b"X-Aicad-Token" in payload

    port = int(server.server_address[1])
    token_file = Path(os.environ["AICAD_SERVER_TOKENS"]) / f"{port}.token"
    assert token_file.read_text(encoding="utf-8") == server.agent_token
    assert token_file.stat().st_mode & 0o777 == 0o600


def test_rebinding_host_is_rejected_before_the_page_or_a_command(server):
    port = int(server.server_address[1])
    evil = {"Host": f"evil.example:{port}"}
    status, payload, _headers = _exchange(server, "GET", "/", headers=evil, token=None, origin=None)
    body = json.loads(payload.decode("utf-8"))
    assert status == 403
    assert "localhost" in body["error"]
    assert server.page_token.encode("utf-8") not in payload

    status, payload, _headers = _exchange(
        server,
        "POST",
        "/api/command",
        body=json.dumps({**_CREATE, "source": "ui"}),
        headers=evil,
    )
    assert status == 403
    assert "localhost" in json.loads(payload.decode("utf-8"))["error"]
    assert _parts(server) == []

    status, _payload, _headers = _exchange(
        server, "GET", "/", headers={"Host": f"127.0.0.1:{port + 1}"}, token=None, origin=None
    )
    assert status == 403
    status, _payload, _headers = _exchange(
        server, "GET", "/", headers={"Host": f"[::1]:{port}"}, token=None, origin=None
    )
    assert status == 403
    status, payload, _headers = _exchange(
        server, "GET", "/", headers={"Host": f"localhost:{port}"}, token=None, origin=None
    )
    assert status == 200
    assert server.page_token in payload.decode("utf-8")


def test_foreign_origin_is_rejected(server):
    port = int(server.server_address[1])
    status, payload, _headers = _exchange(server, "GET", "/", token=None, origin=f"http://evil.example:{port}")
    assert status == 403
    assert "another site" in json.loads(payload.decode("utf-8"))["error"]

    status, payload, _headers = _exchange(
        server,
        "POST",
        "/api/command",
        body=json.dumps(_CREATE),
        origin="http://evil.example",
    )
    assert status == 403
    assert "another site" in json.loads(payload.decode("utf-8"))["error"]
    assert _parts(server) == []

    status, payload, _headers = _exchange(server, "GET", "/api/state", origin="http://evil.example")
    assert status == 403
    assert "another site" in json.loads(payload.decode("utf-8"))["error"]
    # The page's own reads do not send Origin. A command still has to.
    status, payload, _headers = _exchange(server, "GET", "/api/state", origin=None)
    assert status == 200
    assert json.loads(payload.decode("utf-8"))["ok"] is True
    status, payload, _headers = _exchange(
        server, "POST", "/api/command", body=json.dumps(_CREATE), origin=None
    )
    assert status == 403
    assert "another site" in json.loads(payload.decode("utf-8"))["error"]
    assert _parts(server) == []


def test_missing_or_wrong_session_is_rejected(server):
    status, payload, _headers = _exchange(server, "GET", "/api/state", token=None)
    assert status == 403
    assert "session" in json.loads(payload.decode("utf-8"))["error"]

    status, payload, _headers = _exchange(
        server, "POST", "/api/command", body=json.dumps(_CREATE), token=None
    )
    assert status == 403
    assert "session" in json.loads(payload.decode("utf-8"))["error"]

    status, payload, _headers = _exchange(
        server, "POST", "/api/command", body=json.dumps(_CREATE), token="not-the-session"
    )
    assert status == 403
    assert "session" in json.loads(payload.decode("utf-8"))["error"]
    assert _parts(server) == []

    leaked = f"/api/state?token={server.page_token}"
    status, payload, _headers = _exchange(server, "GET", leaked, token=None)
    assert status == 403
    assert "session" in json.loads(payload.decode("utf-8"))["error"]


def test_request_body_cannot_choose_source(server):
    status, payload, _headers = _exchange(
        server,
        "POST",
        "/api/command",
        body=json.dumps({**_CREATE, "source": "agent"}),
        token="page",
    )
    assert status == 200
    assert json.loads(payload.decode("utf-8"))["ok"] is True
    state = server.workspace.state()
    assert state["activity"][0]["source"] == "ui"
    assert state["parts"][0]["name"] == "block"

    status, _payload, _headers = _exchange(
        server,
        "POST",
        "/api/command",
        body=json.dumps(
            {
                "name": "place_part",
                "arguments": {"name": "block", "position": [10, 0, 0]},
                "source": "ui",
            }
        ),
        token="agent",
    )
    assert status == 200
    activity = server.workspace.state()["activity"]
    assert activity[-1]["command"] == "place_part"
    assert activity[-1]["source"] == "agent"
    assert activity[0]["source"] == "ui"


def test_picture_request_can_carry_the_token_in_the_query(server):
    status, payload, _headers = _exchange(server, "GET", "/api/file?path=notes.txt", token=None, origin=None)
    assert status == 403
    assert "session" in json.loads(payload.decode("utf-8"))["error"]

    port = int(server.server_address[1])
    status, payload, _headers = _exchange(
        server,
        "GET",
        f"/api/file?path=notes.txt&token={server.page_token}",
        token=None,
        origin=f"http://evil.example:{port}",
    )
    assert status == 403
    assert "another site" in json.loads(payload.decode("utf-8"))["error"]

    status, payload, _headers = _exchange(
        server,
        "GET",
        f"/api/file?path=notes.txt&token={server.page_token}",
        token=None,
        origin=None,
    )
    assert status == 200
    assert payload == b"hello\n"

    status, payload, _headers = _exchange(server, "GET", "/api/file?path=notes.txt", token="agent")
    assert status == 200
    text = payload.decode("utf-8")
    assert text.startswith("<<<UNTRUSTED PROJECT TEXT")
    assert "hello" in text
    assert text.rstrip().endswith("<<<END UNTRUSTED PROJECT TEXT>>>")
