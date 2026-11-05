"""The app's permission rules and a real sandboxed script, through the worker process.

`aicad app` runs commands in a worker. A CI step also runs the other app security
test files this way, with AICAD_TEST_APP_WORKER set.
"""

from __future__ import annotations

import json
import os
import threading
from http.client import HTTPConnection

import pytest

from aicad.app import make_server
from aicad.app_worker import GeometryWorker


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
    monkeypatch.delenv("AICAD_SCRIPT_NETWORK", raising=False)
    running = make_server(tmp_path / "project", "127.0.0.1", 0, worker=True)
    thread = threading.Thread(target=running.serve_forever, daemon=True)
    thread.start()
    try:
        yield running
    finally:
        running.shutdown()
        running.server_close()


def _post(server, path, body, *, token, origin=True):
    host, port = server.server_address
    connection = HTTPConnection(host, port, timeout=240)
    headers = {"Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin if isinstance(origin, str) else f"http://127.0.0.1:{port}"
    if token:
        headers["X-Aicad-Token"] = token
    connection.request("POST", path, body=json.dumps(body), headers=headers)
    response = connection.getresponse()
    payload = json.loads(response.read())
    connection.close()
    return response.status, payload


def _command(server, name, arguments, *, token):
    return _post(server, "/api/command", {"name": name, "arguments": arguments}, token=token)


@pytest.mark.skipif(not os.environ.get("AICAD_TEST_APP_WORKER"), reason="only in the CI step that sets it")
def test_the_ci_step_really_uses_the_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
    running = make_server(tmp_path / "project", "127.0.0.1", 0)
    try:
        assert isinstance(running.workspace, GeometryWorker)
    finally:
        running.server_close()


@pytest.mark.parametrize("path", ["/api/allow-scripts", "/api/script-network-once"])
def test_the_assistant_token_cannot_grant_a_permission(server, path):
    status, _payload = _post(server, path, {"source": "ui", "actor": "user"}, token=server.agent_token)
    assert status == 403
    state = server.workspace.script_status()
    assert state["enabled"] is False
    assert state["network"] == "off"


def test_a_page_from_another_site_cannot_run_a_command(server):
    status, _payload = _post(
        server,
        "/api/command",
        {"name": "list_parts", "arguments": {}},
        token=server.page_token,
        origin="https://example.com",
    )
    assert status == 403


def test_a_command_with_no_token_is_refused(server):
    status, _payload = _post(server, "/api/command", {"name": "list_parts", "arguments": {}}, token=None)
    assert status == 403


def test_the_assistant_cannot_open_another_folder(server, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    status, payload = _command(server, "open_project", {"path": str(other)}, token=server.agent_token)
    assert status == 400
    assert "Only the person" in payload["error"]
    assert server.workspace.root.name == "project"


def test_the_body_cannot_choose_who_is_asking(server, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    status, _payload = _post(
        server,
        "/api/command",
        {"name": "open_project", "arguments": {"path": str(other)}, "source": "ui"},
        token=server.agent_token,
    )
    assert status == 400
    assert server.workspace.root.name == "project"


def test_a_script_does_not_run_until_the_person_allows_it(server):
    status, payload = _command(server, "run_design_script", {"script": "print('no')\n"}, token=server.agent_token)
    assert status == 400
    assert "allow-scripts" in payload["error"]


def test_a_real_sandboxed_script_runs_through_the_worker(server, tmp_path):
    """The process that runs it is confined, and the person's permission reaches the worker."""
    secret = "outside-the-project-1234"
    outside = tmp_path / "outside.txt"
    outside.write_text(secret, encoding="utf-8")
    status, _payload = _post(server, "/api/allow-scripts", {}, token=server.page_token)
    assert status == 200
    script = (
        "from pathlib import Path\n"
        "Path('made.txt').write_text('inside', encoding='utf-8')\n"
        "print('wrote inside')\n"
        "try:\n"
        f"    print(Path({str(outside)!r}).read_text(encoding='utf-8'))\n"
        "except Exception as exc:\n"
        "    print('refused', type(exc).__name__)\n"
    )
    status, payload = _command(server, "run_design_script", {"script": script}, token=server.agent_token)
    assert status == 200, payload
    assert (server.workspace.root / "made.txt").read_text(encoding="utf-8") == "inside"
    assert "wrote inside" in payload["result"]["stdout"]
    assert secret not in json.dumps(payload)


def test_a_one_time_network_grant_is_used_up_in_the_worker(server):
    _post(server, "/api/allow-scripts", {}, token=server.page_token)
    status, _payload = _post(server, "/api/script-network-once", {}, token=server.page_token)
    assert status == 200
    assert server.workspace.script_status()["network"] == "next run"
    status, payload = _command(server, "run_design_script", {"script": "print('hello')\n"}, token=server.agent_token)
    assert status == 200, payload
    assert server.workspace.script_status()["network"] == "off"
