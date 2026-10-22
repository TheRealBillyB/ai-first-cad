"""Open, save, solid sizes, point measure, and the live-mode notes."""

import json
from http.client import HTTPConnection
from pathlib import Path

import pytest

from aicad.app import make_server
from aicad.errors import GeometryError
from aicad.llm import tool_specs
from aicad.workspace import Workspace


def _box(workspace: Workspace, name: str = "block", size=(10, 20, 30)):
    return workspace.execute(
        "create_part_from_ops",
        {
            "name": name,
            "ops": [{"op": "add", "shape": "box", "size": list(size), "name": "block"}],
        },
        source="ui",
    )


def test_parameter_on_an_operation_rebuilds_and_undoes(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    _box(workspace)
    listed = workspace.execute("list_parameters", {"name": "block"})
    assert any(item["id"] == "op/0/size.x" and item["value"] == 10 for item in listed["parameters"])
    changed = workspace.execute(
        "set_parameter",
        {"name": "block", "parameter": "op/0/size.x", "value": 20},
        source="ui",
    )
    assert changed["value"] == 20
    assert workspace.document.measure("block").volume_mm3 == 12000
    workspace.execute("undo")
    assert workspace.document.measure("block").volume_mm3 == 6000
    with pytest.raises(GeometryError, match="greater than zero"):
        workspace.execute("set_parameter", {"name": "block", "parameter": "op/0/size.y", "value": -1})
    workspace.execute("undo")
    assert workspace.document.list_parts() == []


def test_parameter_keeps_a_placement(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    _box(workspace)
    workspace.execute("place_part", {"name": "block", "position": [5, 0, 0]})
    workspace.execute("set_parameter", {"name": "block", "parameter": "op/0/size.z", "value": 40})
    placed = workspace.document.get_source("block")["placement"]["position"]
    assert placed == [5.0, 0.0, 0.0]
    assert workspace.document.measure("block").bounding_box.min.x == pytest.approx(0.0, abs=1e-6)


def _plate(workspace: Workspace):
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
            "steps": [{"op": "extrude", "points": ["p0", "p1", "p2", "p3"], "height": 5, "name": "extrude"}],
        },
    )


def test_sketch_dimension_and_extrude_height_are_parameters(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    _plate(workspace)
    listed = workspace.execute("list_parameters", {"name": "plate"})
    ids = {item["id"] for item in listed["parameters"]}
    assert "dimension/width" in ids
    assert "step/0/height" in ids
    workspace.execute("set_parameter", {"name": "plate", "parameter": "step/0/height", "value": 8})
    assert workspace.document.measure("plate").volume_mm3 == 6400
    workspace.execute("set_parameter", {"name": "plate", "parameter": "dimension/width", "value": 50})
    assert workspace.document.measure("plate").volume_mm3 == 8000


def test_catalog_length_is_a_parameter(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    workspace.execute(
        "insert_standard_part",
        {"name": "screw", "kind": "socket_head_cap_screw", "size": "M3-0.5", "length": 8},
    )
    listed = workspace.execute("list_parameters", {"name": "screw"})
    assert listed["parameters"][0]["id"] == "library/length"
    short = workspace.document.measure("screw").bounding_box.size.z
    workspace.execute("set_parameter", {"name": "screw", "parameter": "library/length", "value": 16})
    long = workspace.document.measure("screw").bounding_box.size.z
    assert long > short + 5


def test_script_has_no_size_to_slide(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    workspace.execute("create_part_from_script", {"name": "box", "script": "part = Box(2, 3, 4)\n"})
    listed = workspace.execute("list_parameters", {"name": "box"})
    assert listed["parameters"] == []
    assert "script" in listed["note"]


def test_measure_points_distance_and_angle(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    distance = workspace.execute("measure_points", {"a": [0, 0, 0], "b": [3, 4, 0]})
    assert distance["distance_mm"] == 5
    assert distance["text"] == "5 mm"
    angle = workspace.execute("measure_points", {"a": [0, 0, 0], "b": [1, 0, 0], "c": [1, 1, 0]})
    assert angle["angle_deg"] == 90
    assert "90" in angle["text"]
    with pytest.raises(GeometryError, match="three numbers"):
        workspace.execute("measure_points", {"a": [0, 0], "b": [1, 0, 0]})


def test_open_save_as_and_recent_projects(tmp_path, monkeypatch):
    recent = tmp_path / "recent.json"
    monkeypatch.setenv("AICAD_RECENT", str(recent))
    original = tmp_path / "one"
    workspace = Workspace(original)
    _box(workspace)
    (original / "documents").mkdir(exist_ok=True)
    workspace.execute("write_document", {"name": "decisions.md", "markdown": "# Kept\n"})
    copy = tmp_path / "two"
    saved = workspace.execute("save_project_as", {"path": str(tmp_path), "name": "two"}, source="ui")
    assert Path(saved["path"]) == copy.resolve()
    assert workspace.root == copy.resolve()
    assert workspace.document.measure("block").volume_mm3 == 6000
    assert "Kept" in (copy / "documents" / "decisions.md").read_text(encoding="utf-8")
    assert (original / "session.json").is_file()
    with pytest.raises(GeometryError, match="Nothing to undo"):
        workspace.execute("undo")
    opened = workspace.execute("open_project", {"path": str(original)}, source="mcp")
    assert opened["parts"] == ["block"]
    assert workspace.root == original.resolve()
    names = [item["path"] for item in workspace.execute("list_recent_projects")["projects"]]
    assert str(original.resolve()) in names
    assert str(copy.resolve()) in names
    with pytest.raises(GeometryError, match="already has a project"):
        workspace.execute("save_project_as", {"path": str(copy)})
    with pytest.raises(GeometryError, match="No folder"):
        workspace.execute("open_project", {"path": str(tmp_path / "missing")})


def test_folder_list_marks_a_project(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    project = tmp_path / "job"
    workspace = Workspace(project)
    _box(workspace)
    (tmp_path / "empty").mkdir()
    listed = workspace.execute("list_folders", {"path": str(tmp_path)})
    by_name = {item["name"]: item for item in listed["folders"]}
    assert by_name["job"]["project"] is True
    assert by_name["empty"]["project"] is False


def test_scene_mesh_contains_every_part(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    _box(workspace, "left", (10, 10, 10))
    _box(workspace, "right", (10, 10, 10))
    workspace.execute("place_part", {"name": "right", "position": [30, 0, 0]})
    scene = workspace.scene_mesh()
    assert scene["parts"] == ["left", "right"]
    assert scene["triangle_part"].count("left") > 0
    assert scene["triangle_part"].count("right") > 0
    assert scene["bounding_box"]["max"][0] > 30
    assert len(scene["colors"]) == len(scene["positions"])


def test_live_note_names_the_part_and_skips_reads(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    events = []
    workspace.listen(lambda event: events.append(event))
    workspace.execute(
        "create_part_from_ops",
        {"name": "block", "ops": [{"op": "add", "shape": "box", "size": [2, 2, 2]}]},
        source="mcp",
    )
    workspace.execute("list_parts", source="mcp")
    note = events[0]
    assert note["source"] == "mcp"
    assert note["follow"] is True
    assert note["frame"] == "part"
    assert note["parts"] == ["block"]
    assert "block" in note["summary"]
    assert events[1]["follow"] is False
    activity = workspace.state()["activity"]
    assert [item["command"] for item in activity] == ["create_part_from_ops"]
    assert activity[0]["source"] == "mcp"
    assert workspace.state()["project"]["name"] == "project"


def test_commands_are_on_the_shared_list():
    names = {spec["name"] for spec in tool_specs()}
    assert {
        "open_project",
        "save_project",
        "save_project_as",
        "list_parameters",
        "set_parameter",
        "measure_points",
        "place_part",
    } <= names


def test_page_offers_open_measure_and_live(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    server = make_server(tmp_path / "project", "127.0.0.1", 0)
    host, port = server.server_address
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection(host, port, timeout=30)
        connection.request("GET", "/")
        page = connection.getresponse()
        html = page.read().decode("utf-8")
        connection.close()
        assert page.status == 200
        for marker in ("open-project", "save-as", 'id="live"', 'data-mode="measure"', 'id="plan-mount"', 'id="plan-body"'):
            assert marker in html
        connection = HTTPConnection(host, port, timeout=30)
        connection.request(
            "POST",
            "/api/command",
            body=json.dumps(
                {
                    "name": "create_part_from_ops",
                    "arguments": {"name": "block", "ops": [{"op": "add", "shape": "box", "size": [4, 4, 4]}]},
                    "source": "mcp",
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        created = json.loads(connection.getresponse().read().decode("utf-8"))
        connection.close()
        assert created["ok"] is True
        connection = HTTPConnection(host, port, timeout=30)
        connection.request("GET", "/api/mesh?scene=1")
        scene = json.loads(connection.getresponse().read().decode("utf-8"))
        connection.close()
        assert scene["result"]["parts"] == ["block"]
        assert scene["result"]["triangle_face"]
        connection = HTTPConnection(host, port, timeout=30)
        connection.request("GET", "/api/state")
        state = json.loads(connection.getresponse().read().decode("utf-8"))
        connection.close()
        assert state["result"]["activity"][0]["source"] == "mcp"
        assert state["result"]["parts"][0]["placement"]["position"] == [0.0, 0.0, 0.0]
    finally:
        server.shutdown()
        server.server_close()
