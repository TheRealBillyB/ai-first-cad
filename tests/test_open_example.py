"""An empty project can open the worked motor-mount example."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from aicad.errors import GeometryError
from aicad.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "motor_mount_workspace" / "session.json"
PAGE = ROOT / "src" / "aicad" / "app_static" / "index.html"
SCRIPT = ROOT / "src" / "aicad" / "app_static" / "app.js"


def _motor_mount():
    path = ROOT / "examples" / "motor_mount.py"
    spec = importlib.util.spec_from_file_location("motor_mount_example", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_the_empty_canvas_offers_the_example():
    page = PAGE.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    assert 'id="open-example"' in page
    assert "Open the motor-mount example" in page
    assert "open_example" in script
    assert "function showEmpty" in script
    session = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    sketch = next(item for item in session["sketches"] if item["name"] == "bracket_base")
    assert sketch["spec"] == _motor_mount().bracket_sketch().to_spec()
    part = next(item for item in session["parts"] if item["name"] == "motor_mount")
    assert part["source"] == "sketch"
    assert part["sketch"] == "bracket_base"


def test_open_example_switches_to_the_motor_mount(tmp_path):
    workspace = Workspace(tmp_path / "empty")
    assert workspace.document.list_parts() == []
    opened = workspace.execute("open_example", {"name": "motor_mount"}, source="ui")
    assert opened["example"] == "motor_mount"
    assert opened["name"].startswith("motor-mount-")
    assert "motor_mount" in opened["parts"]
    sketch = workspace.document.get_sketch("bracket_base")
    assert sketch.status == "fully_constrained"
    assert workspace.document.measure("motor_mount").volume_mm3 == pytest.approx(20426.094423, rel=1e-6)
    again = workspace.execute("open_example", {}, source="ui")
    assert again["path"] == opened["path"]
    with pytest.raises(GeometryError, match="person at this workspace"):
        workspace.execute("open_example", {"name": "motor_mount"}, source="agent")
    with pytest.raises(GeometryError, match="motor_mount"):
        Workspace(tmp_path / "other").execute("open_example", {"name": "other"}, source="ui")
