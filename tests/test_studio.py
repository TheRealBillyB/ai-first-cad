"""Open, save, solid sizes, point measure, and the live-mode notes."""

import json
import os
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


def _inset_frame(workspace: Workspace) -> None:
    workspace.execute(
        "create_sketch",
        {
            "name": "frame",
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
                    {"id": "mark", "type": "point", "at": [6, 10]},
                ],
                "constraints": [
                    {"type": "fix", "entity": "p0"},
                    {"type": "horizontal", "entity": "bottom"},
                    {"type": "horizontal", "entity": "top"},
                    {"type": "vertical", "entity": "right"},
                    {"type": "vertical", "entity": "left"},
                    {"type": "distance", "a": "p0", "b": "p1", "value": 40, "name": "width"},
                    {"type": "distance", "a": "p0", "b": "p3", "value": 20, "name": "height"},
                    {"type": "distance", "a": "mark", "b": "left", "value": -6, "name": "inset"},
                    {"type": "distance", "a": "mark", "b": "bottom", "value": -10, "name": "rise"},
                ],
            },
        },
    )
    workspace.execute(
        "create_part_from_sketch",
        {
            "name": "frame",
            "sketch": "frame",
            "steps": [{"op": "extrude", "points": ["p0", "p1", "p2", "p3"], "height": 5, "name": "extrude"}],
        },
    )


def _mark(workspace: Workspace) -> dict:
    return workspace.execute("get_sketch", {"name": "frame"})["geometry"]["points"]["mark"]


def test_point_to_line_distance_stays_signed(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    _inset_frame(workspace)
    listed = workspace.execute("list_parameters", {"name": "frame"})
    by_id = {}
    for item in listed["parameters"]:
        assert item["id"] not in by_id
        by_id[item["id"]] = item
    assert by_id["dimension/inset"]["kind"] == "signed"
    assert by_id["dimension/inset"]["value"] == -6
    assert by_id["dimension/rise"]["kind"] == "signed"
    assert by_id["dimension/width"]["kind"] == "length"
    assert workspace.execute("get_sketch", {"name": "frame"})["status"] == "fully_constrained"
    assert _mark(workspace)["x"] == pytest.approx(6)
    workspace.execute("set_parameter", {"name": "frame", "parameter": "dimension/inset", "value": -10})
    assert _mark(workspace)["x"] == pytest.approx(10)
    assert workspace.execute("get_sketch", {"name": "frame"})["status"] == "fully_constrained"
    workspace.execute("set_parameter", {"name": "frame", "parameter": "dimension/inset", "value": 6})
    assert _mark(workspace)["x"] == pytest.approx(-6)
    with pytest.raises(GeometryError, match="greater than zero"):
        workspace.execute("set_parameter", {"name": "frame", "parameter": "dimension/width", "value": -5})
    assert workspace.document.measure("frame").volume_mm3 == pytest.approx(4000)
    assert _mark(workspace)["x"] == pytest.approx(-6)
    workspace.execute("undo")
    assert _mark(workspace)["x"] == pytest.approx(10)
    workspace.execute("undo")
    assert _mark(workspace)["x"] == pytest.approx(6)


def test_shared_inset_name_is_one_signed_parameter():
    from aicad.studio import _dimension_parameters

    spec = {
        "entities": [
            {"id": "hsw", "type": "point"},
            {"id": "left", "type": "line"},
            {"id": "bottom", "type": "line"},
            {"id": "a", "type": "point"},
            {"id": "b", "type": "point"},
            {"id": "hole", "type": "circle"},
            {"id": "edge", "type": "line"},
        ],
        "constraints": [
            {"type": "distance", "a": "hsw", "b": "left", "value": -6, "name": "frame_inset"},
            {"type": "distance", "a": "hsw", "b": "bottom", "value": -6, "name": "frame_inset"},
            {"type": "distance", "a": "a", "b": "b", "value": 40, "name": "width"},
            {"type": "radius", "entity": "hole", "value": 3, "name": "hole"},
            {"type": "angle", "a": "bottom", "b": "edge", "value": 90, "name": "corner"},
        ],
    }

    class _Holder:
        def __init__(self, stored):
            self.spec = stored

    class _Document:
        def _get_sketch(self, _name):
            return _Holder(spec)

    class _Workspace:
        document = _Document()

    listed = _dimension_parameters(_Workspace(), "frame")
    by_id = {item["id"]: item for item in listed}
    assert len(listed) == len(by_id)
    assert by_id["dimension/frame_inset"]["kind"] == "signed"
    assert by_id["dimension/frame_inset"]["value"] == -6
    assert by_id["dimension/width"]["kind"] == "length"
    assert by_id["dimension/hole"]["kind"] == "length"
    assert by_id["dimension/corner"]["kind"] == "angle"


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
    opened = workspace.execute("open_project", {"path": str(original)}, source="ui")
    assert opened["parts"] == ["block"]
    assert workspace.root == original.resolve()
    names = [item["path"] for item in workspace.execute("list_recent_projects")["projects"]]
    assert str(original.resolve()) in names
    assert str(copy.resolve()) in names
    with pytest.raises(GeometryError, match="already has a project"):
        workspace.execute("save_project_as", {"path": str(copy)})
    with pytest.raises(GeometryError, match="No folder"):
        workspace.execute("open_project", {"path": str(tmp_path / "missing")})


def test_save_as_keeps_a_space_and_a_unicode_name(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    _box(workspace)
    empty = tmp_path / "already here"
    empty.mkdir()
    (empty / "notes.txt").write_text("keep", encoding="utf-8")
    saved = workspace.execute("save_project_as", {"path": str(empty)})
    assert Path(saved["path"]) == empty.resolve()
    assert (empty / "notes.txt").read_text(encoding="utf-8") == "keep"
    assert (empty / "session.json").is_file()
    named = workspace.execute("save_project_as", {"path": str(tmp_path), "name": "my project"})
    assert Path(named["path"]) == (tmp_path / "my project").resolve()
    unicode_name = workspace.execute("save_project_as", {"path": str(tmp_path), "name": "零件 α"})
    assert (tmp_path / "零件 α" / "session.json").is_file()
    assert workspace.root == Path(unicode_name["path"])


def test_save_as_refuses_names_windows_cannot_create(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "Project" / "job")
    _box(workspace)
    refused = (
        ("", "folder name"),
        ("   ", "folder name"),
        ("trailing ", "space or a dot"),
        ("my project.", "space or a dot"),
        ("CON", "reserved"),
        ("aux.txt", "reserved"),
        ("COM1", "reserved"),
        ("lpt9", "reserved"),
        ("bad:name", "cannot contain"),
        ("nested/child", "cannot contain"),
    )
    for name, message in refused:
        with pytest.raises(GeometryError, match=message):
            workspace.execute("save_project_as", {"path": str(tmp_path), "name": name})
    assert workspace.root == (tmp_path / "Project" / "job").resolve()
    real_normcase = os.path.normcase
    monkeypatch.setattr(
        "aicad.studio.os.path.normcase",
        lambda value: real_normcase(str(value)).casefold(),
    )
    with pytest.raises(GeometryError, match="different folder"):
        workspace.execute("save_project_as", {"path": str(tmp_path / "Project"), "name": "JOB"})
    with pytest.raises(GeometryError, match="beside this project"):
        workspace.execute("save_project_as", {"path": str(tmp_path), "name": "project"})


def test_save_as_keeps_the_script_solid(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    _box(workspace, "block", (10, 10, 10))
    workspace.execute("create_part_from_script", {"name": "box", "script": "part = Box(2, 3, 4)\n"})
    saved = workspace.execute("save_project_as", {"path": str(tmp_path), "name": "copy"})
    copy = Workspace(saved["path"])
    scene = copy.scene_mesh()
    assert scene["unbuilt"] == []
    assert "box" in scene["parts"]
    assert scene["positions"]
    for file in (Path(saved["path"]) / ".aicad" / "geometry").glob("*.step"):
        file.unlink()
    reopened = Workspace(saved["path"])
    scene = reopened.scene_mesh()
    assert scene["unbuilt"] == ["box"]
    assert "block" in scene["parts"]
    assert scene["positions"]


def test_save_as_reports_a_folder_it_cannot_write(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    _box(workspace)
    locked = tmp_path / "locked"
    if os.name == "nt":
        # An access-list deny does not stop this token from creating a folder.
        # A file in that place cannot become the parent of the copy.
        locked.write_bytes(b"x")
        with pytest.raises(GeometryError, match="Could not save"):
            workspace.execute("save_project_as", {"path": str(locked), "name": "copy"})
        assert workspace.root == (tmp_path / "project").resolve()
        assert locked.read_bytes() == b"x"
        return
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("the root user ignores a read-only folder")
    from aicad.paths import allow_directory_write, deny_directory_write

    locked.mkdir()
    deny_directory_write(locked)
    try:
        with pytest.raises(GeometryError, match="Could not save"):
            workspace.execute("save_project_as", {"path": str(locked), "name": "copy"})
        assert workspace.root == (tmp_path / "project").resolve()
        assert not (locked / "copy" / "session.json").exists()
    finally:
        allow_directory_write(locked)


def test_a_huge_size_or_move_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    _box(workspace)
    before = workspace.document.measure("block").bounding_box.size.x
    for value, message in (
        ("abc", "has to be a number"),
        (-1, "greater than zero"),
        (0, "greater than zero"),
        (1e12, "1000000"),
        (float("inf"), "finite"),
    ):
        with pytest.raises(GeometryError, match=message):
            workspace.execute("set_parameter", {"name": "block", "parameter": "op/0/size.x", "value": value})
    assert workspace.document.measure("block").bounding_box.size.x == pytest.approx(before)
    with pytest.raises(GeometryError, match="1000000"):
        workspace.execute("place_part", {"name": "block", "position": [1e15, 0, 0]})
    with pytest.raises(GeometryError, match="finite"):
        workspace.execute("place_part", {"name": "block", "position": [float("nan"), 0, 0]})
    with pytest.raises(GeometryError, match="3600"):
        workspace.execute("place_part", {"name": "block", "position": [0, 0, 0], "rotation_deg": [0, 0, 9000]})
    assert workspace.document.get_source("block")["placement"]["position"] == [0.0, 0.0, 0.0]
    workspace.execute("set_parameter", {"name": "block", "parameter": "op/0/size.x", "value": 1000000})
    assert workspace.document.measure("block").bounding_box.size.x == pytest.approx(1000000)


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
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
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
        assert page.getheader("Cache-Control") == "no-store"
        assert server.page_token in html
        assert "__AICAD_TOKEN__" not in html
        for marker in ("open-project", "save-as", 'id="live"', 'data-mode="measure"', 'id="plan-mount"', 'id="plan-body"', 'rel="icon"'):
            assert marker in html
        connection = HTTPConnection(host, port, timeout=30)
        connection.request("GET", "/favicon.ico")
        icon = connection.getresponse()
        icon_bytes = icon.read()
        connection.close()
        assert icon.status == 200
        assert icon.getheader("Content-Type") == "image/png"
        assert icon_bytes.startswith(b"\x89PNG")
        session = {
            "Origin": f"http://127.0.0.1:{port}",
            "X-Aicad-Token": server.agent_token,
        }
        connection = HTTPConnection(host, port, timeout=30)
        connection.request(
            "POST",
            "/api/command",
            body=json.dumps(
                {
                    "name": "create_part_from_ops",
                    "arguments": {"name": "block", "ops": [{"op": "add", "shape": "box", "size": [4, 4, 4]}]},
                    "source": "ui",
                }
            ),
            headers={"Content-Type": "application/json", **session},
        )
        created = json.loads(connection.getresponse().read().decode("utf-8"))
        connection.close()
        assert created["ok"] is True
        connection = HTTPConnection(host, port, timeout=30)
        connection.request("GET", "/api/mesh?scene=1", headers=session)
        scene = json.loads(connection.getresponse().read().decode("utf-8"))
        connection.close()
        assert scene["result"]["parts"] == ["block"]
        assert scene["result"]["triangle_face"]
        connection = HTTPConnection(host, port, timeout=30)
        connection.request("GET", "/api/state", headers=session)
        state = json.loads(connection.getresponse().read().decode("utf-8"))
        connection.close()
        assert state["result"]["activity"][0]["source"] == "agent"
        assert state["result"]["parts"][0]["placement"]["position"] == [0.0, 0.0, 0.0]
    finally:
        server.shutdown()
        server.server_close()
