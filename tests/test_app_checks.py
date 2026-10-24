"""Fatigue, clearance, and STEP import buttons call the existing commands.

The page posts to /api/command. A STEP file is read by import_step, including
the refusal of a file that names another file outside its folder.
"""

import json
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest
from build123d import Box, export_step
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPConstruct import STEPConstruct_ExternRefs
from OCP.STEPControl import STEPControl_Reader
from OCP.StepBasic import StepBasic_ProductDefinition

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


def _scene(server):
    status, payload = _exchange(server, "GET", "/api/mesh?scene=1")
    body = json.loads(payload.decode("utf-8"))
    assert status == 200, body
    assert body["ok"] is True
    return body["result"]


def _box(server, name, size, center=None):
    op = {"op": "add", "shape": "box", "size": size}
    if center is not None:
        op["center"] = center
    _command(server, "create_part_from_ops", {"name": name, "ops": [op]})


def _craft_step(base: Path, dest: Path, reference: str) -> None:
    reader = STEPControl_Reader()
    assert reader.ReadFile(str(base)) == IFSelect_RetDone
    model = reader.WS().Model()
    product = next(
        model.Value(index)
        for index in range(1, model.NbEntities() + 1)
        if isinstance(model.Value(index), StepBasic_ProductDefinition)
    )
    refs = STEPConstruct_ExternRefs(reader.WS())
    refs.Init(reader.WS())
    refs.AddExternRef(reference, product, "STEP AP214")
    refs.WriteExternRefs(214)
    assert reader.WS().WriteFile(str(dest)) == IFSelect_RetDone


def test_the_page_wires_fatigue_clearance_and_step_to_the_existing_commands(server):
    status, page = _exchange(server, "GET", "/", token=None, origin=None)
    html = page.decode("utf-8")
    assert status == 200
    assert 'id="open-step"' in html
    assert 'data-tab="fatigue"' in html
    assert 'data-tab="clearance"' in html
    assert 'data-tab="step"' in html

    status, script = _exchange(server, "GET", "/static/tools.js", token=None, origin=None)
    tools = script.decode("utf-8")
    assert status == 200
    for button in ("fatigue-run", "clearance-run", "step-import"):
        assert f'id="{button}"' in tools
    for command in ("check_fatigue", "check_clearance", "import_step"):
        assert f'command("{command}"' in tools
    assert "FileReader" not in tools
    assert "STEPControl" not in tools
    assert "http://" not in tools
    assert "https://" not in tools
    assert "Enter the stress amplitude in MPa." in tools
    assert "Choose two different parts." in tools
    assert "Name the STEP file on this computer." in tools
    assert "Add two parts, then check the gap between them." in tools


def test_fatigue_and_clearance_show_in_the_panels(server):
    report = _command(
        server,
        "check_fatigue",
        {
            "spec": {
                "material": "steel",
                "stress_amplitude_mpa": 90,
                "mean_stress_mpa": 40,
                "surface": "machined",
                "section_mm": 12,
            }
        },
    )
    assert report["status"] == "fail"
    assert report["safety_factor"] == pytest.approx(1.378, abs=0.02)
    assert report["endurance_mpa"] == pytest.approx(142.8, abs=0.1)
    assert _state(server)["panels"]["fatigue"]["status"] == "fail"

    _box(server, "left", [10, 10, 10])
    _box(server, "overlap", [10, 10, 10], [5, 0, 0])
    _box(server, "apart", [10, 10, 10], [30, 0, 0])
    hit = _command(
        server,
        "check_clearance",
        {"part_a": "left", "part_b": "overlap", "min_clearance_mm": 0.2},
    )
    assert hit["intersecting"] is True
    assert hit["passed"] is False
    assert hit["interference_volume_mm3"] == pytest.approx(500, rel=1e-3)
    assert _state(server)["panels"]["clearance"]["part_a"] == "left"

    gap = _command(server, "check_clearance", {"part_a": "left", "part_b": "apart", "min_clearance_mm": 0})
    assert gap["intersecting"] is False
    assert gap["min_distance_mm"] == pytest.approx(20)
    assert gap["passed"] is True
    assert _state(server)["panels"]["clearance"]["passed"] is True

    error = _refused(server, "check_clearance", {"part_a": "left", "part_b": "left"})
    assert "two different" in error.lower()
    error = _refused(server, "check_fatigue", {"spec": {"stress_amplitude_mpa": 0, "material": "steel"}})
    assert "stress_amplitude_mpa" in error


def test_step_import_uses_the_command_and_refuses_a_file_that_names_another(server, tmp_path):
    project = tmp_path / "project"
    clean = project / "incoming.step"
    export_step(Box(4, 4, 4), clean)
    imported = _command(server, "import_step", {"name": "plate", "path": "incoming.step"})
    part = imported["part"]
    assert part["name"] == "plate"
    assert part["source"] == "step"
    assert part["watertight"] is True
    scene = _scene(server)
    assert "plate" in scene["parts"]
    assert (project / "imports" / "plate.step").is_file()

    outside = tmp_path / "outside"
    outside.mkdir()
    hidden = outside / "hidden.step"
    export_step(Box(10, 10, 10), hidden)
    shared = project / "shared.step"
    _craft_step(clean, shared, str(hidden))
    before = hidden.read_bytes()
    error = _refused(server, "import_step", {"name": "shared", "path": str(shared)})
    assert "outside" in error.lower()
    assert hidden.read_bytes() == before
    assert "shared" not in _scene(server)["parts"]
    assert not (project / "imports" / "shared.step").exists()

    error = _refused(server, "import_step", {"name": "remote", "path": "http://127.0.0.1/part.step"})
    assert error
    error = _refused(server, "import_step", {"name": "escape", "path": "../outside/hidden.step"})
    assert "outside" in error.lower()
    assert hidden.read_bytes() == before

    status, payload = _exchange(
        server,
        "POST",
        "/api/command",
        body=json.dumps({"name": "import_step", "arguments": {"name": "again", "path": "incoming.step"}}),
        token=None,
    )
    assert status == 403
    assert "session" in json.loads(payload.decode("utf-8"))["error"]
