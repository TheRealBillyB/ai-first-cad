"""The position-error button calls check_position_error.

The page posts to /api/command. The two-link arm is the same chain the
command tests use: a 100 mm first link and a tool 80 mm out on the second.
"""

import json
import math
import threading
from http.client import HTTPConnection

import pytest

from aicad.app import make_server


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
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


def _exchange(server, method, path, body=None, token="page", origin=True):
    host, port = server.server_address
    connection = HTTPConnection(host, port, timeout=120)
    headers = {}
    if origin is True:
        headers["Origin"] = f"http://127.0.0.1:{port}"
    elif origin:
        headers["Origin"] = origin
    if token == "page":
        headers["X-Aicad-Token"] = server.page_token
    elif token:
        headers["X-Aicad-Token"] = token
    if body is not None:
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    payload = response.read()
    status = response.status
    connection.close()
    return status, payload


def _command(server, name, arguments):
    status, payload = _exchange(
        server,
        "POST",
        "/api/command",
        body=json.dumps({"name": name, "arguments": arguments}),
    )
    body = json.loads(payload.decode("utf-8"))
    assert status == 200, body
    assert body["ok"] is True
    assert "Traceback" not in payload.decode("utf-8")
    return body["result"]


def _refused(server, name, arguments):
    status, payload = _exchange(
        server,
        "POST",
        "/api/command",
        body=json.dumps({"name": name, "arguments": arguments}),
    )
    text = payload.decode("utf-8")
    body = json.loads(text)
    assert status == 400, body
    assert body["ok"] is False
    assert body["error"]
    assert "Traceback" not in text
    return body["error"]


def _state(server):
    status, payload = _exchange(server, "GET", "/api/state")
    body = json.loads(payload.decode("utf-8"))
    assert status == 200
    assert body["ok"] is True
    return body["result"]


def _arm(server):
    for name in ("base", "link1", "link2"):
        _command(
            server,
            "create_part_from_ops",
            {"name": name, "ops": [{"op": "add", "shape": "box", "size": [4, 4, 4]}]},
        )
    _command(
        server,
        "create_assembly",
        {
            "spec": {
                "name": "arm",
                "instances": [
                    {"name": "base", "part": "base"},
                    {"name": "link1", "part": "link1"},
                    {"name": "link2", "part": "link2"},
                ],
                "joints": [
                    {
                        "name": "shoulder",
                        "type": "revolute",
                        "parent": "base",
                        "child": "link1",
                        "axis": [0, 0, 1],
                    },
                    {
                        "name": "elbow",
                        "type": "revolute",
                        "parent": "link1",
                        "child": "link2",
                        "axis": [0, 0, 1],
                        "parent_anchor": [100, 0, 0],
                    },
                ],
                "poses": {
                    "stretch": {"shoulder": 0, "elbow": 0},
                    "bent": {"shoulder": 0, "elbow": 90},
                },
            },
        },
    )


def _budget(tolerance):
    return {
        "name": "arm",
        "tool": "link2",
        "at": [80, 0, 0],
        "tolerance_mm": tolerance,
        "poses": ["stretch", "bent"],
        "sources": {
            "shoulder": {"encoder": {"resolution_deg": 0.2}},
            "elbow": {"backlash": {"deg": 0.5}},
        },
    }


def test_the_page_wires_position_error_to_the_existing_command(server):
    status, page = _exchange(server, "GET", "/", token=None, origin=None)
    html = page.decode("utf-8")
    assert status == 200
    assert 'id="assembly-error"' in html
    assert "Select an assembly first." not in html

    status, script = _exchange(server, "GET", "/static/tools.js", token=None, origin=None)
    tools = script.decode("utf-8")
    assert status == 200
    assert 'id="assembly-error"' not in tools
    assert 'command("check_position_error"' in tools
    assert "Enter the tolerance in millimeters." in tools
    assert "http://" not in tools
    assert "https://" not in tools


def test_position_error_shows_in_the_panel_and_a_later_edit_clears_it(server):
    _arm(server)
    shoulder = 0.1 * math.pi
    elbow = 0.5 * 80.0 * math.pi / 180.0
    stretch_worst = shoulder + elbow

    report = _command(server, "check_position_error", _budget(0.9))
    assert report["status"] == "fail"
    assert report["worst_pose"] == "stretch"
    assert report["rss_peak_pose"] == "stretch"
    assert report["rss_mm"] < 0.9 < report["worst_case_mm"]
    assert report["worst_case_mm"] == pytest.approx(stretch_worst, abs=1e-6)
    assert report["assembly"] == "arm"
    panel = _state(server)["panels"]["position_error"]
    assert panel["assembly"] == "arm"
    assert panel["status"] == "fail"
    assert panel["worst_pose"] == "stretch"

    passing = _command(server, "check_position_error", _budget(2.0))
    assert passing["status"] == "pass"
    assert passing["worst_case_mm"] == pytest.approx(stretch_worst, abs=1e-6)
    assert "within" in passing["summary"]
    assert _state(server)["panels"]["position_error"]["status"] == "pass"

    error = _refused(
        server,
        "check_position_error",
        {"name": "arm", "tool": "link2", "tolerance_mm": 1.0},
    )
    assert "encoder" in error.lower()
    assert _state(server)["panels"]["position_error"]["status"] == "pass"

    status, payload = _exchange(
        server,
        "POST",
        "/api/command",
        body=json.dumps({"name": "check_position_error", "arguments": _budget(0.9)}),
        token=None,
    )
    assert status == 403
    assert "session" in json.loads(payload.decode("utf-8"))["error"]

    _command(
        server,
        "create_part_from_ops",
        {"name": "extra", "ops": [{"op": "add", "shape": "box", "size": [4, 4, 4]}]},
    )
    assert "position_error" not in _state(server)["panels"]
