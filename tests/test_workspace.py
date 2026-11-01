"""The command layer the UI, the chat, and MCP all call."""

import errno
import json
import os
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from aicad.app import HttpCommandClient, make_server
from aicad.errors import AICADError, GeometryError
from aicad.fea import select_faces
from aicad.kernel import describe_faces
from aicad.llm import public_settings, run_turn, save_settings, tool_specs
from aicad.refs import ref_to_selector
from aicad.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]


def _rectangle(width=40, height=20):
    return {
        "plane": "xy",
        "entities": [
            {"id": "p0", "type": "point", "at": [0, 0]},
            {"id": "p1", "type": "point", "at": [width, 0]},
            {"id": "p2", "type": "point", "at": [width, height]},
            {"id": "p3", "type": "point", "at": [0, height]},
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
            {"type": "distance", "a": "p0", "b": "p1", "value": width, "name": "width"},
            {"type": "distance", "a": "p0", "b": "p3", "value": height, "name": "height"},
        ],
    }


def _extrude(workspace: Workspace, width=40, height=20, depth=5):
    report = workspace.execute("create_sketch", {"name": "plate", "spec": _rectangle(width, height)}, source="ui")
    assert report["status"] == "fully_constrained"
    assert report["dof"] == 0
    return workspace.execute(
        "create_part_from_sketch",
        {
            "name": "plate",
            "sketch": "plate",
            "steps": [{"op": "extrude", "points": ["p0", "p1", "p2", "p3"], "height": depth, "name": "extrude"}],
        },
        source="ui",
    )


def test_sketch_dimension_regenerates_and_undo_restores(tmp_path):
    workspace = Workspace(tmp_path)
    events = []
    workspace.listen(lambda event: events.append(event["source"]))
    created = _extrude(workspace)
    assert created["part"]["volume_mm3"] == 4000
    changed = workspace.execute(
        "set_dimension",
        {"sketch": "plate", "dimension": "width", "value": 50},
        source="ui",
    )
    assert changed["status"] == "fully_constrained"
    assert any(part["volume_mm3"] == 5000 for part in changed["rebuilt_parts"])
    workspace.execute("undo", source="ui")
    assert workspace.document.measure("plate").volume_mm3 == 4000
    workspace.execute("redo", source="chat")
    assert workspace.document.measure("plate").volume_mm3 == 5000
    assert events == ["ui", "ui", "ui", "ui", "chat"]
    state = workspace.state()
    assert state["sketches"][0]["geometry"]["points"]["p1"]["x"] == 50
    assert state["can_undo"] is True


def test_failed_edit_does_not_consume_undo(tmp_path):
    workspace = Workspace(tmp_path)
    _extrude(workspace)
    with pytest.raises(AICADError):
        workspace.execute("update_part_script", {"name": "plate", "script": "part = None\n"}, source="ui")
    assert workspace.document.measure("plate").volume_mm3 == 4000
    workspace.execute("undo")
    assert workspace.document.list_parts() == []


def test_face_ref_round_trips_into_a_selector_and_the_selection(tmp_path):
    workspace = Workspace(tmp_path)
    _extrude(workspace)
    faces = workspace.execute("describe_faces", {"name": "plate"})["faces"]
    top = next(face for face in faces if face["ref"].startswith("plane/z+"))
    picked = select_faces(describe_faces(workspace.document._get("plate").shape), {"ref": top["ref"]})
    assert len(picked) == 1
    assert picked[0]["ref"] == top["ref"]
    assert ref_to_selector(top["ref"])["kind"] == "plane"
    edges = workspace.execute("describe_edges", {"name": "plate"})["edges"]
    workspace.execute("set_selection", {"part": "plate", "faces": [top["ref"]], "edges": [edges[0]["ref"]]}, source="chat")
    selection = workspace.state()["selection"]
    assert selection["faces"] == [top["ref"]]
    assert selection["edges"] == [edges[0]["ref"]]
    with pytest.raises(GeometryError, match="not on part"):
        workspace.execute("set_selection", {"part": "plate", "faces": ["plane/z+/0,0,99"]})


def test_documents_and_requirements_undo_together(tmp_path):
    workspace = Workspace(tmp_path)
    assert "requirements.md" in workspace.execute("list_documents")["documents"]
    workspace.execute("write_document", {"name": "decisions.md", "markdown": "# Keep the plate thin\n"}, source="chat")
    _extrude(workspace)
    workspace.execute(
        "set_requirements",
        {"requirements": [{"id": "light", "kind": "max_mass", "part": "plate", "material": "pla", "max_grams": 10}]},
    )
    assert workspace.execute("check_requirements", {"run": False})["checks"][0]["status"] == "pass"
    workspace.execute("undo")
    assert workspace.execute("get_requirements")["requirements"] == []
    workspace.execute("undo")
    assert workspace.document.list_parts() == []
    text = workspace.execute("read_document", {"name": "decisions.md"})["markdown"]
    assert "Keep the plate thin" in text


def test_http_command_and_mesh_share_the_document(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
    server = make_server(tmp_path, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        connection = HTTPConnection(host, port, timeout=30)
        connection.request("GET", "/")
        page = connection.getresponse()
        html = page.read().decode("utf-8")
        assert page.status == 200
        assert "aicad workspace" in html
        assert "0.9.0" in html
        assert "__AICAD_VERSION__" not in html
        connection.close()

        connection = HTTPConnection(host, port, timeout=30)
        connection.request("GET", "/static/app.js")
        script = connection.getresponse()
        assert script.status == 200
        assert b"function command" in script.read()
        connection.close()

        client = HttpCommandClient(f"http://{host}:{port}")
        report = client.execute("create_sketch", {"name": "plate", "spec": _rectangle()}, source="mcp")
        assert report["status"] == "fully_constrained"
        client.execute(
            "create_part_from_sketch",
            {
                "name": "plate",
                "sketch": "plate",
                "steps": [{"op": "extrude", "points": ["p0", "p1", "p2", "p3"], "height": 5, "name": "extrude"}],
            },
            source="mcp",
        )
        connection = HTTPConnection(host, port, timeout=30)
        connection.request(
            "GET",
            "/api/mesh?part=plate",
            headers={"Origin": f"http://127.0.0.1:{port}", "X-Aicad-Token": server.page_token},
        )
        body = json.loads(connection.getresponse().read().decode("utf-8"))
        connection.close()
        assert body["ok"] is True
        assert body["result"]["triangle_face"]
        assert any(face["ref"].startswith("plane/") for face in body["result"]["faces"])
        state = client.execute("get_state")
        assert state["parts"][0]["name"] == "plate"
        assert state["activity"]
        assert {item["source"] for item in state["activity"]} == {"agent"}
        # The browser and an attached assistant see the same revision.
        assert server.workspace.state()["revision"] == state["revision"]
    finally:
        server.shutdown()
        server.server_close()


def test_chat_turn_runs_a_tool_against_the_workspace(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path)
    seen = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            body = {
                "choices": [
                    {
                        "message": {
                            "content": "Made a box.",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {
                                        "name": "create_part_from_ops",
                                        "arguments": json.dumps(
                                            {
                                                "name": "box",
                                                "ops": [{"op": "add", "shape": "box", "size": [2, 3, 4]}],
                                            }
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
            if seen.get("calls"):
                body = {"choices": [{"message": {"content": "Volume is 24 mm³.", "tool_calls": []}}]}
            seen["calls"] = seen.get("calls", 0) + 1
            return json.dumps(body).encode("utf-8")

    def fake_urlopen(request, timeout=120):
        seen["url"] = request.full_url
        seen["auth"] = request.headers.get("Authorization")
        return _Response()

    monkeypatch.setattr("aicad.llm.urllib.request.urlopen", fake_urlopen)
    events = []
    run_turn(
        workspace,
        [{"role": "user", "content": "Make a 2 by 3 by 4 box named box."}],
        lambda kind, payload: events.append((kind, payload)),
        settings={"provider": "openai", "base_url": "https://example.test/v1", "model": "demo", "api_key": "sk-test"},
    )
    assert workspace.document.measure("box").volume_mm3 == 24
    assert ("tool", True) in [(kind, payload.get("ok")) for kind, payload in events if kind == "tool"]
    assert events[-1] == ("text", {"text": "Volume is 24 mm³."})
    assert "example.test" in seen["url"]
    assert seen["auth"] == "Bearer sk-test"
    assert any(spec["name"] == "create_sketch" for spec in tool_specs())
    assert all(spec["name"] != "mesh_part" for spec in tool_specs())


def test_settings_file_keeps_the_key_out_of_the_public_view(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setenv("AICAD_SETTINGS", str(path))
    saved = save_settings({"provider": "anthropic", "base_url": "https://api.anthropic.com", "model": "claude", "api_key": "sk-secret-1234"})
    assert saved["has_key"] is True
    assert saved["key_hint"] == "…1234"
    assert "sk-secret" not in json.dumps(saved)
    stored = json.loads(path.read_text(encoding="utf-8"))
    from aicad.paths import file_is_user_private

    assert stored["api_key"] == "sk-secret-1234"
    assert file_is_user_private(path)
    assert public_settings()["provider"] == "anthropic"


def test_get_sketch_names_a_sketch():
    from aicad.workspace import COMMANDS

    description = COMMANDS["get_sketch"].parameters["properties"]["name"]["description"]
    assert description == "Sketch name."
    sketch = next(spec for spec in tool_specs() if spec["name"] == "get_sketch")
    assert sketch["parameters"]["properties"]["name"]["description"] == "Sketch name."


def test_cli_app_help_lists_the_workspace(tmp_path):
    import subprocess
    import sys

    completed = subprocess.run([sys.executable, "-P", "-m", "aicad", "app", "--help"], check=False, capture_output=True, text=True)
    assert completed.returncode == 0
    assert "--no-browser" in completed.stdout
    assert (ROOT / "src" / "aicad" / "app_static" / "index.html").is_file()


def _refuse_chmod(*_args, **_kwargs):
    raise PermissionError(errno.EPERM, "Operation not permitted")


def _posix_chmod() -> bool:
    """True where ``os.fchmod`` exists and ``stat`` mode bits are the access check.

    Windows has no ``os.fchmod``. A directory there reports mode ``0o777`` even
    when the access list is not world-writable, so these POSIX checks do not
    apply.
    """

    return hasattr(os, "fchmod")


def test_a_new_project_directory_is_not_left_world_writable(tmp_path):
    if not _posix_chmod():
        pytest.skip("POSIX mode bits; Windows has no os.fchmod and stat mode stays writable")
    workspace = Workspace(tmp_path / "proj")
    documents = workspace.root / "documents"
    mode = documents.stat().st_mode
    assert mode & 0o700 == 0o700
    assert mode & 0o022 == 0
    note = documents / "requirements.md"
    assert note.is_file()
    assert note.stat().st_mode & 0o022 == 0


def test_a_project_folder_is_created_when_chmod_is_refused(tmp_path, monkeypatch):
    if not _posix_chmod():
        pytest.skip("POSIX chmod refusal; Windows has no os.fchmod")
    monkeypatch.setattr(os, "chmod", _refuse_chmod)
    monkeypatch.setattr(os, "fchmod", _refuse_chmod)
    workspace = Workspace(tmp_path / "proj")
    documents = workspace.root / "documents"
    assert documents.is_dir()
    assert (documents / "requirements.md").is_file()
    assert (workspace.root / "requirements.json").is_file()
    mode = documents.stat().st_mode
    assert mode & 0o700 == 0o700
    assert mode & 0o022 == 0
    assert (documents / "requirements.md").stat().st_mode & 0o022 == 0


def test_a_bad_tool_number_is_a_geometry_error(tmp_path):
    workspace = Workspace(tmp_path)
    _extrude(workspace)
    workspace.execute(
        "create_part_from_ops",
        {"name": "rib", "ops": [{"op": "add", "shape": "box", "size": [8, 8, 8]}]},
        source="agent",
    )
    cases = [
        ("check_wall_thickness", {"name": "plate", "min_wall_mm": "thick"}, "finite number"),
        ("check_wall_thickness", {"name": "plate", "min_wall_mm": True}, "finite number"),
        ("check_wall_thickness", {"name": "plate", "min_wall_mm": 1, "samples": "many"}, "integer from 1 to 8"),
        ("check_wall_thickness", {"name": "plate", "min_wall_mm": 1, "samples": 0}, "integer from 1 to 8"),
        ("check_wall_thickness", {"name": "plate", "min_wall_mm": 1, "samples": True}, "integer from 1 to 8"),
        ("check_clearance", {"part_a": "plate", "part_b": "rib", "min_clearance_mm": "wide"}, "finite number"),
        ("check_clearance", {"part_a": "plate", "part_b": "rib", "min_clearance_mm": True}, "finite number"),
        ("check_packaging", {"envelope": "plate", "parts": ["rib"], "min_clearance_mm": True}, "finite number"),
        ("inspect_part", {"name": "plate", "min_wall_mm": "thick"}, "finite number"),
        ("section_part", {"name": "plate", "axis": "z", "offset": "mid"}, "finite number"),
        ("export_part", {"name": "plate", "format": 3}, "Unknown export format"),
        ("set_dimension", {"sketch": "plate", "dimension": "width", "value": "80mm"}, "finite number"),
        ("set_dimension", {"sketch": "plate", "dimension": "width", "value": True}, "finite number"),
        (
            "solve_mechanics",
            {
                "name": "plate",
                "setup": {
                    "material": "aluminum_6061",
                    "mesh_size_mm": True,
                    "fixes": [],
                    "loads": [],
                },
            },
            "Expected a number",
        ),
    ]
    for name, arguments, match in cases:
        with pytest.raises(GeometryError, match=match):
            workspace.execute(name, arguments, source="agent")
    wall = workspace.execute(
        "check_wall_thickness",
        {"name": "plate", "min_wall_mm": 1, "samples": 4.0},
        source="agent",
    )
    assert wall["passed"] is True
    assert workspace.document.measure("plate").volume_mm3 == 4000


def test_a_refused_chmod_does_not_accept_a_world_writable_directory(tmp_path, monkeypatch):
    if not _posix_chmod():
        pytest.skip("POSIX chmod refusal; Windows has no os.fchmod")
    from aicad.paths import _chmod_new_directory, open_directory

    parent = tmp_path / "parent"
    parent.mkdir()
    loose = parent / "loose"
    loose.mkdir()
    loose.chmod(0o777)
    monkeypatch.setattr(os, "chmod", _refuse_chmod)
    monkeypatch.setattr(os, "fchmod", _refuse_chmod)
    descriptor = open_directory(parent)
    try:
        with pytest.raises(PermissionError):
            _chmod_new_directory(descriptor, "loose")
    finally:
        os.close(descriptor)
    assert loose.stat().st_mode & 0o022 != 0
