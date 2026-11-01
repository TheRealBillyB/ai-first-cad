"""Appending a step, and script sizes, leave the rest of the part in place."""

import ast
import json
import socket
import threading
from http.client import HTTPConnection

import pytest

from aicad.app import make_server
from aicad.errors import GeometryError, ScriptError
from aicad.llm import _SYSTEM, tool_specs
from aicad.sandbox import CPU_SECONDS, MEMORY_BYTES, WALL_SECONDS, cpu_seconds, memory_bytes, wall_seconds
from aicad.script_params import rewrite_script_size, script_sizes
from aicad.workspace import Workspace


def _history(workspace: Workspace, name: str) -> list[dict]:
    part = next(item for item in workspace.state()["parts"] if item["name"] == name)
    return part["history"]


def test_a_script_size_is_a_top_level_number():
    script = "width = 10\nheight = 20.5\npart = Box(width, height, 4)\n"
    sizes = script_sizes(script)
    assert [item["name"] for item in sizes] == ["width", "height"]
    assert sizes[0]["value"] == 10
    assert rewrite_script_size(script, "width", 16) == "width = 16\nheight = 20.5\npart = Box(width, height, 4)\n"
    text = "width = 1\nwidth = 4  # mm\npart = Box(width, 2, 2)\n"
    assert rewrite_script_size(text, "width", 8) == "width = 1\nwidth = 8  # mm\npart = Box(width, 2, 2)\n"
    assert rewrite_script_size("gap = -2\npart = Box(1, 1, 1)\n", "gap", 3) == "gap = 3\npart = Box(1, 1, 1)\n"
    assert script_sizes("part = 5\n") == []
    with pytest.raises(GeometryError, match="No size"):
        rewrite_script_size(script, "depth", 1)


def test_add_step_appends_a_cut_and_a_fillet(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    workspace.execute(
        "create_part_from_ops",
        {"name": "block", "ops": [{"op": "add", "shape": "box", "size": [10, 20, 30], "name": "base"}]},
        source="ui",
    )
    workspace.execute("place_part", {"name": "block", "position": [4, 0, 0]})
    plain = workspace.document.measure("block").volume_mm3
    workspace.execute(
        "add_step",
        {"name": "block", "step": {"op": "cut", "shape": "box", "size": [4, 4, 40], "name": "slot"}},
        source="ui",
    )
    assert workspace.document.measure("block").volume_mm3 < plain
    assert [row["name"] for row in _history(workspace, "block")] == ["base", "slot"]
    assert workspace.document.get_source("block")["placement"]["position"] == [4.0, 0.0, 0.0]
    workspace.execute(
        "add_step",
        {"name": "block", "step": {"op": "fillet", "radius": 1, "edges": [0], "name": "round"}},
        source="ui",
    )
    assert _history(workspace, "block")[-1]["kind"] == "fillet"
    assert workspace.document.measure("block").volume_mm3 < plain
    workspace.execute("undo")
    assert [row["name"] for row in _history(workspace, "block")] == ["base", "slot"]
    workspace.execute("undo")
    assert [row["name"] for row in _history(workspace, "block")] == ["base"]
    assert workspace.document.measure("block").volume_mm3 == pytest.approx(plain)
    workspace.execute(
        "insert_standard_part",
        {"name": "screw", "kind": "socket_head_cap_screw", "size": "M3-0.5", "length": 8},
    )
    with pytest.raises(GeometryError, match="named sizes"):
        workspace.execute("add_step", {"name": "screw", "step": {"op": "cut", "shape": "box", "size": [1, 1, 1]}})


def test_add_step_appends_a_sketch_cut(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    workspace.execute(
        "create_sketch",
        {
            "name": "plate",
            "spec": {
                "plane": "xy",
                "entities": [
                    {"id": "p0", "type": "point", "at": [0, 0]},
                    {"id": "p1", "type": "point", "at": [40, 0]},
                    {"id": "p2", "type": "point", "at": [40, 20]},
                    {"id": "p3", "type": "point", "at": [0, 20]},
                    {"id": "bottom", "type": "line", "start": "p0", "end": "p1"},
                    {"id": "right", "type": "line", "start": "p1", "end": "p2"},
                    {"id": "top", "type": "line", "start": "p2", "end": "p3"},
                    {"id": "left", "type": "line", "start": "p3", "end": "p0"},
                ],
                "constraints": [
                    {"type": "fix", "entity": "p0"},
                    {"type": "horizontal", "entity": "bottom"},
                    {"type": "horizontal", "entity": "top"},
                    {"type": "vertical", "entity": "right"},
                    {"type": "vertical", "entity": "left"},
                    {"type": "distance", "a": "p0", "b": "p1", "value": 40, "name": "width"},
                    {"type": "distance", "a": "p0", "b": "p3", "value": 20, "name": "height"},
                ],
            },
        },
    )
    workspace.execute(
        "create_part_from_sketch",
        {
            "name": "plate",
            "sketch": "plate",
            "steps": [{"op": "extrude", "points": ["p0", "p1", "p2", "p3"], "height": 5, "name": "slab"}],
        },
    )
    before = workspace.document.measure("plate").volume_mm3
    workspace.execute(
        "add_step",
        {
            "name": "plate",
            "step": {"op": "cut_box", "center": [20, 10, 2.5], "size": [4, 4, 8], "name": "slot"},
        },
    )
    assert workspace.document.measure("plate").volume_mm3 < before
    assert [row["id"] for row in _history(workspace, "plate")] == ["sketch", "step/0", "step/1"]
    assert workspace.document.get_source("plate")["source"] == "sketch"


def test_a_script_size_rebuilds_through_the_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    workspace.execute(
        "create_part_from_script",
        {"name": "block", "script": "width = 10\nheight = 20\npart = Box(width, height, 5)\n"},
        source="ui",
    )
    listed = workspace.execute("list_parameters", {"name": "block"})
    width = next(item for item in listed["parameters"] if item["id"] == "script/width")
    assert width["value"] == 10
    assert width["step"] == "script"
    assert _history(workspace, "block")[0]["id"] == "script"
    workspace.execute("place_part", {"name": "block", "position": [3, 0, 0]})
    workspace.execute("set_parameter", {"name": "block", "parameter": "script/width", "value": 16}, source="ui")
    assert workspace.document.measure("block").volume_mm3 == pytest.approx(1600)
    assert "width = 16" in workspace.document.get_source("block")["script"]
    assert workspace.document.get_source("block")["placement"]["position"] == [3.0, 0.0, 0.0]
    workspace.execute("undo")
    assert workspace.document.measure("block").volume_mm3 == pytest.approx(1000)
    with pytest.raises(ScriptError):
        workspace.execute("set_parameter", {"name": "block", "parameter": "script/width", "value": 0})
    assert workspace.document.measure("block").volume_mm3 == pytest.approx(1000)


def test_a_script_size_on_a_line_with_other_text_changes_only_the_number():
    script = "宽 = 10 #;marker = 1\npart = Box(宽, 2, 2)\n"
    updated = rewrite_script_size(script, "宽", 16)
    assert updated == "宽 = 16 #;marker = 1\npart = Box(宽, 2, 2)\n"
    names = [
        node.targets[0].id
        for node in ast.parse(updated).body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
    ]
    assert names == ["宽", "part"]
    same_line = "café = 10; height = 20 # keep\npart = Box(café, height, 1)\n"
    assert rewrite_script_size(same_line, "café", 4) == "café = 4; height = 20 # keep\npart = Box(café, height, 1)\n"
    windows = "宽 = -10 #;marker = 1\r\npart = Box(1, 1, 1)\r\n"
    assert rewrite_script_size(windows, "宽", 3) == "宽 = 3 #;marker = 1\r\npart = Box(1, 1, 1)\r\n"
    assert rewrite_script_size(windows, "宽", -2) == "宽 = -2 #;marker = 1\r\npart = Box(1, 1, 1)\r\n"
    paged = "width = 10\f#;marker = 1\npart = Box(width, 1, 1)\n"
    assert rewrite_script_size(paged, "width", 12) == "width = 12\f#;marker = 1\npart = Box(width, 1, 1)\n"
    old_mac = "width = 10 # keep\rheight = 4\rpart = Box(width, height, 1)\r"
    assert rewrite_script_size(old_mac, "width", 6) == "width = 6 # keep\rheight = 4\rpart = Box(width, height, 1)\r"
    assert rewrite_script_size("width = 10", "width", 4) == "width = 4"
    assert rewrite_script_size("宽 = (10) #;marker = 1\n", "宽", 16) == "宽 = (16) #;marker = 1\n"


def test_a_script_size_with_other_text_on_the_line_rebuilds_that_number(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    script = "宽 = 10 #;marker = 1\npart = Box(宽, 2, 2)\n"
    workspace.execute("create_part_from_script", {"name": "block", "script": script}, source="ui")
    assert workspace.document.measure("block").volume_mm3 == pytest.approx(40)
    workspace.execute("set_parameter", {"name": "block", "parameter": "script/宽", "value": 16}, source="ui")
    stored = workspace.document.get_source("block")["script"]
    assert stored == "宽 = 16 #;marker = 1\npart = Box(宽, 2, 2)\n"
    assert workspace.document.measure("block").volume_mm3 == pytest.approx(64)
    workspace.execute("undo")
    assert workspace.document.measure("block").volume_mm3 == pytest.approx(40)
    assert "#;marker = 1" in workspace.document.get_source("block")["script"]


def test_a_script_size_rebuild_stays_inside_the_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_SCRIPT_NETWORK", "1")
    monkeypatch.setenv("AICAD_SCRIPT_WALL_SECONDS", "999999")
    monkeypatch.setenv("AICAD_SCRIPT_CPU_SECONDS", "999999")
    monkeypatch.setenv("AICAD_SCRIPT_MEMORY_MB", "999999")
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "SANDBOX_CANARY.txt"
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(0.2)
    port = listener.getsockname()[1]
    script = (
        "width = 10\n"
        "def _touch():\n"
        "    try:\n"
        "        import socket\n"
        "        from pathlib import Path\n"
        f"        sock = socket.create_connection(('127.0.0.1', {port}), 1)\n"
        "        sock.close()\n"
        "        Path('connected.txt').write_text('CONNECTED', encoding='utf-8')\n"
        "    except Exception:\n"
        "        pass\n"
        "    try:\n"
        "        from pathlib import Path\n"
        f"        Path({str(canary)!r}).write_text('CANARY_HIT', encoding='utf-8')\n"
        "    except Exception:\n"
        "        pass\n"
        "_touch()\n"
        "part = Box(width, 2, 2)\n"
    )
    dot = project / ".aicad"
    try:
        workspace = Workspace(project)
        dot.mkdir(exist_ok=True)
        claims = {
            "network": True,
            "AICAD_SCRIPT_NETWORK": "1",
            "AICAD_SCRIPT_WALL_SECONDS": 999999,
            "AICAD_SCRIPT_CPU_SECONDS": 999999,
            "AICAD_SCRIPT_MEMORY_MB": 999999,
        }
        (dot / "config.json").write_text(json.dumps(claims) + "\n", encoding="utf-8")
        (project / ".env").write_text("AICAD_SCRIPT_NETWORK=1\n", encoding="utf-8")
        assert wall_seconds(project) == WALL_SECONDS
        assert cpu_seconds() == CPU_SECONDS
        assert memory_bytes() == MEMORY_BYTES
        workspace.execute("create_part_from_script", {"name": "block", "script": script}, source="ui")
        workspace.execute(
            "set_parameter",
            {"name": "block", "parameter": "script/width", "value": 16},
            source="ui",
        )
        assert workspace.document.measure("block").volume_mm3 == pytest.approx(64)
        assert "width = 16" in workspace.document.get_source("block")["script"]
        assert not canary.exists()
        assert not (project / "connected.txt").exists()
        assert list(outside.iterdir()) == []
        try:
            accepted, _address = listener.accept()
        except TimeoutError:
            accepted = None
        assert accepted is None
    finally:
        listener.close()


def test_a_page_from_another_site_cannot_change_a_size(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
    server = make_server(tmp_path / "project", "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        workspace = server.workspace
        workspace.execute(
            "create_part_from_ops",
            {"name": "block", "ops": [{"op": "add", "shape": "box", "size": [10, 10, 10], "name": "base"}]},
            source="ui",
        )
        body = json.dumps(
            {
                "name": "set_parameter",
                "arguments": {"name": "block", "parameter": "op/0/size.x", "value": 30},
                "source": "ui",
            }
        )

        def post(origin, token):
            connection = HTTPConnection(host, port, timeout=30)
            headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
            if origin is not None:
                headers["Origin"] = origin
            if token is not None:
                headers["X-Aicad-Token"] = token
            connection.request("POST", "/api/command", body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            connection.close()
            return response.status, payload

        status, payload = post("http://evil.example", server.page_token)
        assert status == 403
        assert b"another site" in payload
        status, payload = post(None, server.page_token)
        assert status == 403
        assert b"another site" in payload
        append = json.dumps(
            {
                "name": "add_step",
                "arguments": {
                    "name": "block",
                    "step": {"op": "cut", "shape": "box", "size": [4, 4, 20], "name": "slot"},
                },
                "source": "ui",
            }
        )
        connection = HTTPConnection(host, port, timeout=30)
        connection.request(
            "POST",
            "/api/command",
            body=append,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(append)),
                "Origin": "http://evil.example",
                "X-Aicad-Token": server.page_token,
            },
        )
        response = connection.getresponse()
        assert response.status == 403
        response.read()
        connection.close()
        assert workspace.document.measure("block").volume_mm3 == pytest.approx(1000)
        assert [row["name"] for row in workspace.document.get_source("block")["ops"]] == ["base"]

        status, payload = post(f"http://127.0.0.1:{port}", server.agent_token)
        assert status == 200
        assert json.loads(payload)["ok"] is True
        assert workspace.document.measure("block").volume_mm3 == pytest.approx(3000)
        assert workspace.state()["activity"][-1]["source"] == "agent"
    finally:
        server.shutdown()


def test_the_assistant_is_told_to_edit_a_step():
    names = {spec["name"] for spec in tool_specs()}
    assert "add_step" in names
    assert "set_parameter" in names
    assert "add_step" in _SYSTEM
    assert "update_part_ops" in _SYSTEM
    assert "width = 40" in _SYSTEM
