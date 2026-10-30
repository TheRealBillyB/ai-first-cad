"""Only the person can open a project folder or save a copy.

The marker is CANARY_HIT. A session file and an assistant command do not
switch the open folder, and that marker stays where it was written.
"""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection

import pytest

from aicad.app import make_server
from aicad.errors import GeometryError
from aicad.workspace import Workspace

_MARKER = "CANARY_HIT"


def test_the_assistant_cannot_open_or_copy_a_project(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    project = tmp_path / "project"
    other = tmp_path / "other"
    other.mkdir()
    marker = other / "SANDBOX_CANARY.txt"
    marker.write_text(_MARKER + "\n", encoding="utf-8")
    workspace = Workspace(project)
    root = workspace.root
    for source in ("agent", "chat", "mcp"):
        for name, arguments in (
            ("open_project", {"path": str(other)}),
            ("save_project_as", {"path": str(tmp_path), "name": "copy-" + source}),
        ):
            try:
                workspace.execute(name, arguments, source=source)
            except GeometryError as exc:
                assert "person" in str(exc)
            else:
                raise AssertionError(f"{source} {name} changed the folder")
            assert workspace.root == root
    opened = workspace.execute("open_project", {"path": str(other)}, source="ui")
    assert opened["path"] == str(other.resolve()) or workspace.root == other.resolve()
    assert workspace.root == other.resolve()
    assert marker.read_text(encoding="utf-8") == _MARKER + "\n"
    workspace.execute("open_project", {"path": str(project)}, source="cli")
    assert workspace.root == project.resolve()


def test_a_session_file_does_not_switch_the_open_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    project = tmp_path / "project"
    project.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    marker = other / "SANDBOX_CANARY.txt"
    marker.write_text(_MARKER + "\n", encoding="utf-8")
    (project / "session.json").write_text(
        json.dumps(
            {
                "workspace": str(other),
                "open_project": str(other),
                "parts": [],
                "assemblies": [],
                "sketches": [],
            }
        ),
        encoding="utf-8",
    )
    workspace = Workspace(project)
    assert workspace.root == project.resolve()
    assert marker.read_text(encoding="utf-8") == _MARKER + "\n"
    assert not (other / "session.json").exists()


def test_a_design_script_cannot_open_another_folder(tmp_path, monkeypatch):
    from aicad.sandbox import allow_design_scripts, run_design_script, sandbox_supported

    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.delenv("AICAD_SCRIPT_NETWORK", raising=False)
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    project = tmp_path / "project"
    project.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    marker = other / "SANDBOX_CANARY.txt"
    marker.write_text(_MARKER + "\n", encoding="utf-8")
    allow_design_scripts(project)
    home = Workspace(project)
    payload = run_design_script(
        project,
        "from aicad.workspace import Workspace\n"
        f"Workspace({str(project)!r}).execute('open_project', {{'path': {str(other)!r}}}, source='ui')\n",
    )
    assert payload.get("ok") is False
    assert "person" in str(payload.get("error") or "")
    assert home.root == project.resolve()
    assert marker.read_text(encoding="utf-8") == _MARKER + "\n"
    assert _MARKER not in json.dumps(payload)


def test_the_assistant_token_cannot_open_a_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("AICAD_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
    project = tmp_path / "project"
    other = tmp_path / "other"
    other.mkdir()
    marker = other / "SANDBOX_CANARY.txt"
    marker.write_text(_MARKER + "\n", encoding="utf-8")
    server = make_server(project, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        connection = HTTPConnection(host, port, timeout=30)
        body = json.dumps({"name": "open_project", "arguments": {"path": str(other)}, "source": "ui"}).encode()
        connection.request(
            "POST",
            "/api/command",
            body=body,
            headers={
                "Origin": f"http://127.0.0.1:{port}",
                "X-Aicad-Token": server.agent_token,
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        raw = response.read()
        status = response.status
        connection.close()
        assert status == 400
        assert b"person" in raw
        assert server.workspace.root == project.resolve()
        assert marker.read_text(encoding="utf-8") == _MARKER + "\n"
        assert server.page_token.encode() not in raw
        assert server.agent_token.encode() not in raw
    finally:
        server.shutdown()
        server.server_close()
