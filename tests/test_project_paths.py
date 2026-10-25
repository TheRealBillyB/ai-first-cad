"""Status, errors, and the script log do not repeat the project's absolute path."""

import asyncio
import json

import pytest

from aicad.errors import GeometryError
from aicad.paths import hide_project_path
from aicad.sandbox import _append_log, design_script_status, require_in_project, script_log_path
from aicad.server import build_server
from aicad.workspace import Workspace


def test_a_longer_folder_name_is_left_alone(tmp_path):
    project = tmp_path / "job"
    project.mkdir()
    other = str(tmp_path / "job2" / "notes.txt")
    assert hide_project_path(other, project) == other
    assert hide_project_path(str(project / "session.json"), project) == "session.json"


def test_script_status_hides_the_project_folder(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    status = design_script_status(project)
    folder = str(project.resolve())
    assert folder not in status["log"]
    assert folder not in status["enable"]
    assert status["log"].endswith(".aicad/script_log.jsonl") or status["log"].endswith(".aicad\\script_log.jsonl")
    assert status["enable"].endswith("--workspace .")


def test_the_open_project_path_stays_in_the_workspace_state(tmp_path):
    project = tmp_path / "project"
    workspace = Workspace(project)
    assert workspace.state()["project"]["path"] == str(project.resolve())


def test_an_outside_path_error_hides_the_project_folder(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr("aicad.sandbox_state.in_sandbox", lambda: True)
    with pytest.raises(Exception, match="outside the project") as raised:
        require_in_project(tmp_path / "outside.txt", project, writing=True)
    assert str(project.resolve()) not in str(raised.value)


def test_the_script_log_hides_the_project_folder_and_keeps_the_script(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    script = "part = Box(1, 1, 1)\n"
    _append_log(project, script, False, f"could not write {project.resolve()}/out.txt")
    text = script_log_path(project).read_text(encoding="utf-8")
    assert str(project.resolve()) not in text
    stored = json.loads(text)
    assert stored["script"] == script
    assert "out.txt" in stored["error"]


def test_a_tool_error_hides_the_project_folder(tmp_path, monkeypatch):
    from mcp.server.mcpserver.exceptions import ToolError

    project = tmp_path / "project"
    project.mkdir()

    def boom(self, name, arguments=None, source="agent"):
        raise GeometryError(f"could not read {project.resolve()}/session.json")

    monkeypatch.setattr("aicad.workspace.Workspace.execute", boom)
    server = build_server(project)
    with pytest.raises(ToolError, match="session.json") as raised:
        asyncio.run(server.call_tool("list_parts", {}))
    assert str(project.resolve()) not in str(raised.value)


def test_a_resource_error_hides_the_project_folder(tmp_path, monkeypatch):
    from mcp.server.mcpserver.exceptions import ResourceError

    project = tmp_path / "project"
    project.mkdir()

    def boom(self, name, arguments=None, source="agent"):
        raise GeometryError(f"could not read {project.resolve()}/session.json")

    monkeypatch.setattr("aicad.workspace.Workspace.execute", boom)
    server = build_server(project)
    with pytest.raises(ResourceError, match="session.json") as raised:
        asyncio.run(server.read_resource("aicad://parts"))
    assert type(raised.value) is ResourceError
    assert str(project.resolve()) not in str(raised.value)
