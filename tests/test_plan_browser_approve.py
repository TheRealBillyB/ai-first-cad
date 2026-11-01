"""Another website cannot approve a plan. The assistant token cannot either."""

import json
import threading
from http.client import HTTPConnection

import pytest

from aicad.app import make_server


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
    monkeypatch.setenv("AICAD_APPROVALS", str(tmp_path / "approvals.json"))
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


def _exchange(server, method, path, body=None, token="page", origin=True):
    host, port = server.server_address
    connection = HTTPConnection(host, port, timeout=30)
    headers = {}
    if origin is True:
        headers["Origin"] = f"http://127.0.0.1:{port}"
    elif origin:
        headers["Origin"] = origin
    if token == "page":
        headers["X-Aicad-Token"] = server.page_token
    elif token == "agent":
        headers["X-Aicad-Token"] = server.agent_token
    if body is not None:
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    payload = response.read()
    status = response.status
    connection.close()
    return status, payload


def _prepare(server):
    server.workspace.execute(
        "start_plan",
        {
            "name": "bracket",
            "title": "Bracket",
            "bundle": {
                "research": [{"note": "A note.", "source": "This test."}],
                "assumptions": [{"text": "The size is 10 mm."}],
                "sheets": [
                    {
                        "id": "01_top",
                        "title": "Top",
                        "panels": [
                            {
                                "title": "Top",
                                "blocks": [{"x": 0, "y": 0, "w": 10, "h": 10, "label": "block", "fill": "yellow"}],
                            }
                        ],
                    }
                ],
            },
        },
        source="ui",
    )
    server.workspace.execute("submit_plan", {"name": "bracket"}, source="ui")


def _status(server) -> str:
    return server.workspace.execute("get_plan", {"name": "bracket"}, source="ui")["status"]


def test_another_page_and_the_assistant_cannot_approve(server):
    _prepare(server)
    body = json.dumps({"name": "approve_plan", "arguments": {"name": "bracket"}, "source": "ui"})
    port = int(server.server_address[1])
    status, payload = _exchange(server, "POST", "/api/command", body=body, origin=f"http://evil.example:{port}")
    assert status == 403
    assert "another site" in json.loads(payload.decode("utf-8"))["error"]
    assert _status(server) == "in_review"

    status, payload = _exchange(server, "POST", "/api/command", body=body, origin=None)
    assert status == 403
    assert "another site" in json.loads(payload.decode("utf-8"))["error"]
    assert _status(server) == "in_review"

    status, payload = _exchange(server, "POST", "/api/command", body=body, token="agent")
    assert status == 400
    assert "cannot approve" in json.loads(payload.decode("utf-8"))["error"]
    assert _status(server) == "in_review"

    status, payload = _exchange(server, "POST", "/api/command", body=body, token="page")
    assert status == 200
    assert json.loads(payload.decode("utf-8"))["ok"] is True
    assert _status(server) == "approved"
