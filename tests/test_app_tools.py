"""Workspace buttons call the same commands the assistant uses.

The page posts to /api/command. These tests follow that path: the static
files name the button and the command, and the server runs that command.
"""

import inspect
import json
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from aicad.app import make_server
from aicad.errors import GeometryError
from aicad.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]

# A hypoid in the model view can take longer than two minutes on a busy runner.
# A request that never returns is still stopped by the suite limit.
_REQUEST_TIMEOUT_S = 240

_BUTTONS = {
    "open-library": "search_parts",
    "library-insert": "insert_standard_part",
    "gear-create": "create_gear",
    "assembly-mesh": "check_gears",
    "assembly-train": "solve_gear_train",
    "assembly-kinematics": "solve_kinematics",
    "assembly-package": "check_packaging",
    "assembly-drawing": "export_drawing",
    "assembly-bom": "bill_of_materials",
    "assembly-exploded": "exploded_view",
    "assembly-animate": "render_animation",
}


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


def _exchange(server, method, path, body=None, token="page", origin=True, timeout=_REQUEST_TIMEOUT_S):
    host, port = server.server_address
    connection = HTTPConnection(host, port, timeout=timeout)
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


def _state(server):
    status, payload = _exchange(server, "GET", "/api/state")
    body = json.loads(payload.decode("utf-8"))
    assert status == 200
    assert body["ok"] is True
    return body["result"]


def _scene(server):
    status, payload = _exchange(server, "GET", "/api/mesh?scene=1")
    body = json.loads(payload.decode("utf-8"))
    assert status == 200, body
    assert body["ok"] is True
    return body["result"]


def test_the_page_wires_each_new_button_to_the_existing_command(server):
    status, page = _exchange(server, "GET", "/", token=None, origin=None)
    html = page.decode("utf-8")
    assert status == 200
    assert "/static/tools.js" in html
    for button in (
        "open-library",
        "open-gear",
        "assembly-mesh",
        "assembly-train",
        "assembly-kinematics",
        "assembly-package",
        "assembly-drawing",
        "assembly-bom",
        "assembly-exploded",
        "assembly-animate",
    ):
        assert f'id="{button}"' in html
    assert 'id="app-notice"' in html
    assert 'role="alert"' in html
    assert "Select an assembly first." not in html

    status, script = _exchange(server, "GET", "/static/tools.js", token=None, origin=None)
    tools = script.decode("utf-8")
    assert status == 200
    status, app = _exchange(server, "GET", "/static/app.js", token=None, origin=None)
    app_js = app.decode("utf-8")
    assert status == 200
    combined = tools + app_js
    for button, command in _BUTTONS.items():
        assert button in combined
        assert f'command("{command}"' in tools
    assert 'command("run_motion_study"' in tools
    assert "data-study" in app_js
    assert "runMotionStudy" in app_js
    power_button = app_js.split("assembly-power")[1].split("assembly-modes")[0]
    assert "current.studies" in power_button
    assert "http://" not in tools
    assert "https://" not in tools
    assert "hypoid" in tools
    assert "offset_mm" in tools
    assert "ring_diameter_mm" in tools
    assert "Nothing in the catalog matches that." in tools
    assert "Name the parts to pack, or pass an assembly." in tools


def test_library_search_and_insert_show_up_in_the_model(server):
    found = _command(server, "search_parts", {"query": "608"})
    match = next(item for item in found["matches"] if item["kind"] == "ball_bearing" and item["size"] == "608")
    assert match["kind"] == "ball_bearing"

    inserted = _command(
        server,
        "insert_standard_part",
        {"name": "bearing", "kind": match["kind"], "size": match["size"]},
    )
    assert inserted["part"]["name"] == "bearing"
    scene = _scene(server)
    assert "bearing" in scene["parts"]
    assert scene["positions"]


def test_a_model_view_request_waits_past_two_minutes():
    timeout = inspect.signature(_exchange).parameters["timeout"].default
    assert timeout == _REQUEST_TIMEOUT_S
    assert timeout >= 240


def test_spur_and_hypoid_gears_reach_the_model_view(server):
    spur = _command(
        server,
        "create_gear",
        {
            "name": "pinion",
            "spec": {
                "type": "spur",
                "module_mm": 2,
                "teeth": 18,
                "pressure_angle_deg": 20,
                "face_width_mm": 4,
            },
        },
    )
    assert spur["gear"]["type"] == "spur"
    assert spur["gear"]["teeth"] == 18
    assert spur["watertight"] is True
    scene = _scene(server)
    assert "pinion" in scene["parts"]
    assert scene["positions"]

    hypoid = _command(
        server,
        "create_gear",
        {
            "name": "hypoid_pinion",
            "spec": {
                "type": "hypoid",
                "role": "pinion",
                "teeth": 8,
                "mate_teeth": 16,
                "offset_mm": 12,
                "shaft_angle_deg": 90,
                "ring_diameter_mm": 80,
                "face_width_mm": 10,
                "spiral_angle_deg": 35,
                "pressure_angle_deg": 20,
            },
        },
    )
    assert hypoid["gear"]["type"] == "hypoid"
    assert hypoid["gear"]["teeth"] == 8
    assert hypoid["watertight"] is True
    scene = _scene(server)
    assert "hypoid_pinion" in scene["parts"]


def test_gear_mesh_check_uses_the_command_panel(server):
    spec = {"type": "spur", "module_mm": 2, "pressure_angle_deg": 20, "face_width_mm": 4}
    _command(server, "create_gear", {"name": "pinion", "spec": {**spec, "teeth": 20}})
    _command(server, "create_gear", {"name": "wheel", "spec": {**spec, "teeth": 40}})
    report = _command(
        server,
        "check_gears",
        {
            "pinion": "pinion",
            "gear": "wheel",
            "centre_distance_mm": 60,
            "backlash_mm": {"min": 0, "max": 0.05},
            "steps": 8,
        },
    )
    assert report["status"] == "pass"
    assert report["interference"] is False
    assert report["backlash"]["circumferential_mm"] == pytest.approx(0, abs=1e-4)
    assert report["total_contact_ratio"] > 1
    assert _state(server)["panels"]["gears"]["status"] == "pass"


def _box(server, name, size, center):
    _command(
        server,
        "create_part_from_ops",
        {"name": name, "ops": [{"op": "add", "shape": "box", "size": size, "center": center}]},
    )


def test_assembly_tools_run_the_same_commands(server):
    _box(server, "base", [20, 20, 10], [0, 0, 5])
    _box(server, "link1", [20, 10, 10], [25, 0, 5])
    _box(server, "link2", [10, 10, 10], [0, 0, 5])
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
                        "parent_anchor": [50, 0, 0],
                    },
                ],
                "studies": {
                    "swing": {
                        "duration_s": 1,
                        "steps": 3,
                        "drivers": [{"joint": "shoulder", "expression": "10 * t"}],
                    }
                },
                "gear_train": {
                    "shafts": [
                        {"name": "input", "gears": [{"name": "pinion", "teeth": 20}]},
                        {"name": "output", "gears": [{"name": "wheel", "teeth": 40}]},
                    ],
                    "meshes": [{"name": "mesh", "a": "pinion", "b": "wheel"}],
                },
            }
        },
    )

    study = _command(
        server,
        "run_motion_study",
        {"name": "arm", "sequence": "swing", "steps": 3, "clearance_mm": 0},
    )
    assert study["status"] in {"clear", "collision", "limit", "collision_and_limit"}
    assert isinstance(study["collision_count"], int)
    assert _state(server)["panels"]["motion_study"]["assembly"] == "arm"

    pose = _command(server, "solve_kinematics", {"name": "arm", "tool": "link2", "at": [0, 0, 0]})
    assert pose["task"] == "forward"
    assert len(pose["forward"]["position_mm"]) == 3
    assert _state(server)["panels"]["kinematics"]["assembly"] == "arm"

    train = _command(
        server,
        "solve_gear_train",
        {"name": "arm", "drive": {"inputs": [{"shaft": "input", "speed_rpm": 100, "torque_nm": 2}]}},
    )
    shafts = {row["name"]: row for row in train["shafts"]}
    assert train["status"] == "determined"
    assert shafts["output"]["speed_rpm"] == pytest.approx(-50)
    assert _state(server)["panels"]["gear_train"]["assembly"] == "arm"

    packed = _command(
        server,
        "check_packaging",
        {
            "envelope": {"shape": "box", "size": [400, 400, 400]},
            "assembly": "arm",
            "min_clearance_mm": 0,
        },
    )
    assert packed["passed"] is True
    assert {row["name"] for row in packed["parts"]} == {"base", "link1", "link2"}
    assert _state(server)["panels"]["packaging"]["passed"] is True

    drawing = _command(server, "export_drawing", {"name": "arm", "scale": "1:2"})
    assert drawing["scale"] == "1:2"
    assert Path(drawing["files"]["pdf"]).read_bytes().startswith(b"%PDF")
    assert Path(drawing["files"]["png"]).is_file()
    assert _state(server)["panels"]["drawing"]["scale"] == "1:2"

    bom = _command(server, "bill_of_materials", {"name": "arm"})
    assert sum(row["quantity"] for row in bom["rows"]) == 3
    assert Path(bom["csv"]).is_file()
    assert bom["views"][0]["path"]
    assert _state(server)["panels"]["bom"]["rows"]

    exploded = _command(server, "exploded_view", {"name": "arm", "gap_mm": 10})
    assert exploded["gap_mm"] == pytest.approx(10)
    picture = next(view["path"] for view in exploded["views"] if view["name"] == "exploded")
    assert Path(picture).is_file()
    assert _state(server)["panels"]["exploded"]["views"]

    clip = _command(
        server,
        "render_animation",
        {"name": "arm", "kind": "orbit", "frames": 2, "fps": 2, "video": "gif"},
    )
    assert clip["kind"] == "orbit"
    assert clip["frames"] == 2
    assert Path(clip["gif"]).is_file()
    assert Path(clip["gif"]).read_bytes().startswith(b"GIF")
    assert _state(server)["panels"]["animation"]["gif"]


def test_a_refused_tool_command_is_a_plain_error(server):
    status, payload = _exchange(
        server,
        "POST",
        "/api/command",
        body=json.dumps({"name": "search_parts", "arguments": {"query": "608"}}),
        token=None,
    )
    assert status == 403
    assert "session" in json.loads(payload.decode("utf-8"))["error"]

    status, payload = _exchange(
        server,
        "POST",
        "/api/command",
        body=json.dumps(
            {
                "name": "create_gear",
                "arguments": {
                    "name": "bad",
                    "spec": {
                        "type": "hypoid",
                        "role": "pinion",
                        "teeth": 8,
                        "mate_teeth": 16,
                        "offset_mm": 0,
                        "ring_diameter_mm": 80,
                        "face_width_mm": 10,
                        "spiral_angle_deg": 35,
                    },
                },
            }
        ),
    )
    body = json.loads(payload.decode("utf-8"))
    assert status == 400
    assert body["ok"] is False
    assert "offset" in body["error"].lower()
    assert "Traceback" not in payload.decode("utf-8")

    status, payload = _exchange(
        server,
        "POST",
        "/api/command",
        body=json.dumps({"name": "bill_of_materials", "arguments": {"name": "missing"}}),
    )
    body = json.loads(payload.decode("utf-8"))
    assert status == 400
    assert body["ok"] is False
    assert "Traceback" not in payload.decode("utf-8")
    assert body["error"]


def test_a_command_missing_its_fields_names_them(tmp_path, server):
    workspace = Workspace(tmp_path / "direct")
    with pytest.raises(GeometryError, match="create_gear needs name, spec"):
        workspace.execute("create_gear", {})
    status, payload = _exchange(
        server,
        "POST",
        "/api/command",
        body=json.dumps({"name": "create_gear", "arguments": {}}),
    )
    body = json.loads(payload.decode("utf-8"))
    assert status == 400
    assert body["ok"] is False
    assert "create_gear needs name, spec" in body["error"]
    assert "Traceback" not in payload.decode("utf-8")
