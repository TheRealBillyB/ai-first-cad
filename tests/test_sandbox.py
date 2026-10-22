"""Design scripts stay in the project, and opening a project does not run them."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from aicad.document import Document
from aicad.errors import ScriptError
from aicad.sandbox import allow_design_scripts, os_probe, run_design_script, run_part_script, script_log_path
from aicad.server import build_server
from aicad.workspace import COMMANDS, Workspace


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.delenv("AICAD_SCRIPT_NETWORK", raising=False)
    return tmp_path


def test_opening_a_session_does_not_run_its_script(tmp_path, config_home):
    session = {
        "units": "mm",
        "sketches": [],
        "assemblies": [],
        "parts": [
            {
                "name": "evil",
                "source": "script",
                "script": "open('marker.txt', 'w').write('ran')\npart = Box(1, 1, 1)\n",
            }
        ],
    }
    (tmp_path / "session.json").write_text(json.dumps(session), encoding="utf-8")
    document = Document(tmp_path)
    assert not (tmp_path / "marker.txt").exists()
    info = document.list_parts()[0]
    assert info.name == "evil"
    assert info.built is False
    source = document.get_source("evil")
    assert source["script_pending"] is True
    assert "Box" in source["script"]
    with pytest.raises(Exception, match="has not been run"):
        document.measure("evil")


def test_script_geometry_reopens_without_running_again(tmp_path, config_home):
    document = Document(tmp_path)
    script = 'block = feature.add("base", Box(4, 4, 4))\npart = block\n'
    document.create_part_from_script("block", script)
    assert document.feature_report("block").tracked is True
    assert document.feature_report("block").steps[0].name == "base"
    reopened = Document(tmp_path)
    assert not (tmp_path / "marker.txt").exists()
    report = reopened.feature_report("block")
    assert report.tracked is True
    assert report.steps[0].name == "base"
    assert reopened.measure("block").volume_mm3 == pytest.approx(64)
    assert reopened.get_source("block")["script_pending"] is False


def test_open_project_loads_script_text_and_does_not_run_it(tmp_path, config_home):
    other = tmp_path / "other"
    other.mkdir()
    session = {
        "units": "mm",
        "sketches": [],
        "assemblies": [],
        "parts": [
            {
                "name": "evil",
                "source": "script",
                "script": "open('marker.txt', 'w').write('ran')\npart = Box(1, 1, 1)\n",
            }
        ],
    }
    (other / "session.json").write_text(json.dumps(session), encoding="utf-8")
    workspace = Workspace(tmp_path / "here")
    opened = workspace.execute("open_project", {"path": str(other)})
    assert opened["parts"][0]["built"] is False
    assert not (other / "marker.txt").exists()
    saved = workspace.execute("save_project", {})
    assert Path(saved["path"]).is_file()
    assert workspace.execute("design_script_status", {})["enabled"] is False


def test_design_script_requires_opt_in(tmp_path, config_home):
    with pytest.raises(ScriptError, match="allow-scripts"):
        run_design_script(tmp_path, "print('no')\n")
    assert not script_log_path(tmp_path).exists()


def test_import_and_paths_are_refused(tmp_path, config_home):
    allow_design_scripts(tmp_path)
    script = """
errors = []
try:
    import os
except Exception as exc:
    errors.append(str(exc))
try:
    open("/etc/passwd", encoding="utf-8")
except Exception as exc:
    errors.append(str(exc))
try:
    open("/tmp/aicad-sandbox-outside.txt", "w", encoding="utf-8")
except Exception as exc:
    errors.append(str(exc))
open("inside.txt", "w", encoding="utf-8").write("ok")
print(" | ".join(errors))
"""
    payload = run_design_script(tmp_path, script)
    assert payload["ok"] is True, payload
    text = payload["stdout"]
    assert "not allowed" in text
    assert "outside the project" in text
    assert "/etc/passwd" in text
    assert "/tmp/aicad-sandbox-outside.txt" in text
    assert (tmp_path / "inside.txt").read_text(encoding="utf-8") == "ok"
    assert not Path("/tmp/aicad-sandbox-outside.txt").exists()
    log = script_log_path(tmp_path).read_text(encoding="utf-8")
    assert "import os" in log
    assert '"ok": true' in log


def test_os_probe_blocks_network_and_outside_files(tmp_path, config_home):
    probe = os_probe(tmp_path)
    assert probe["read_outside"].startswith("denied")
    assert probe["write_outside"].startswith("denied")
    assert probe["write_inside"] == "ok"
    assert probe["tcp"].startswith("denied")
    assert probe["udp"].startswith("denied")


def test_wall_clock_limit_stops_the_script(tmp_path, config_home, monkeypatch):
    monkeypatch.setenv("AICAD_SCRIPT_WALL_SECONDS", "2")
    monkeypatch.setenv("AICAD_SCRIPT_CPU_SECONDS", "30")
    with pytest.raises(ScriptError, match="longer than 2 seconds"):
        run_part_script("while True:\n    pass\n", tmp_path)


def test_cpu_or_memory_limit_stops_the_script(tmp_path, config_home, monkeypatch):
    monkeypatch.setenv("AICAD_SCRIPT_CPU_SECONDS", "1")
    monkeypatch.setenv("AICAD_SCRIPT_WALL_SECONDS", "30")
    with pytest.raises(ScriptError, match="memory or CPU"):
        run_part_script("while True:\n    pass\n", tmp_path)


def test_memory_limit_stops_a_huge_allocation(tmp_path, config_home, monkeypatch):
    monkeypatch.setenv("AICAD_SCRIPT_MEMORY_MB", "256")
    monkeypatch.setenv("AICAD_SCRIPT_WALL_SECONDS", "60")
    monkeypatch.setenv("AICAD_SCRIPT_CPU_SECONDS", "30")
    with pytest.raises(ScriptError, match="MemoryError|memory or CPU"):
        run_part_script("blob = bytearray(800 * 1024 * 1024)\npart = Box(1, 1, 1)\n", tmp_path)


def test_design_script_reports_the_change_and_the_check(tmp_path, config_home):
    allow_design_scripts(tmp_path)
    script = """
document.create_part_from_script("plate", "part = Box(20, 10, 1)\\n")
wall = document.check_wall_thickness("plate", 2)
print(wall.passed)
document.update_part_script("plate", "part = Box(20, 10, 4)\\n")
wall = document.check_wall_thickness("plate", 2)
print(wall.passed)
"""
    payload = run_design_script(tmp_path, script)
    assert payload["ok"] is True, payload.get("error")
    assert payload["changed_parts"]
    assert payload["changed_parts"][0]["name"] == "plate"
    assert payload["changed_parts"][0]["volume_mm3"] == pytest.approx(800)
    methods = [item["method"] for item in payload["checks"]]
    assert methods.count("check_wall_thickness") == 2
    assert "False" in payload["stdout"]
    assert "True" in payload["stdout"]
    assert payload["views"]
    assert Path(payload["views"][0]["path"]).is_file()
    reopened = Document(tmp_path)
    assert reopened.measure("plate").volume_mm3 == pytest.approx(800)


def test_network_switch_is_not_an_assistant_command(tmp_path, config_home, monkeypatch):
    workspace = Workspace(tmp_path)
    assert workspace.script_status()["network"] == "off"
    workspace.allow_script_network_once()
    assert workspace.script_status()["network"] == "next run"
    assert workspace.consume_script_network() is True
    assert workspace.consume_script_network() is False
    monkeypatch.setenv("AICAD_SCRIPT_NETWORK", "1")
    always = Workspace(tmp_path)
    assert always.script_status()["network"] == "on"
    assert "allow_script_network_once" not in COMMANDS


def test_unexpected_errors_become_tool_errors(tmp_path, config_home):
    from mcp.server.mcpserver.exceptions import ToolError

    saved = COMMANDS["list_parts"]
    COMMANDS["list_parts"] = replace(saved, handler=lambda _workspace, _arguments: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        server = build_server(tmp_path)
        with pytest.raises(ToolError, match="RuntimeError: boom"):
            asyncio.run(server.call_tool("list_parts", {}))
    finally:
        COMMANDS["list_parts"] = saved
