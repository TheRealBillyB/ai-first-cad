"""A result panel is shown again after the project is opened again.

Panels lived only in the open workspace, so closing the app, a worker
restart, or opening the project in a new window lost every strength result
and inspection. They are kept in .aicad/panels.json with a hash of the model
they are about, and come back only while the model is the same.
"""

from __future__ import annotations

import json

import pytest

from aicad.fea import fea_available
from aicad.workspace import Workspace

_AVAILABLE, _REASON = fea_available()
_BEAM = [{"op": "add", "shape": "box", "size": [60, 10, 10], "center": [30, 0, 0]}]


def _inspected(tmp_path) -> Workspace:
    workspace = Workspace(tmp_path / "project")
    workspace.execute("create_part_from_ops", {"name": "beam", "ops": _BEAM}, source="agent")
    workspace.execute("inspect_part", {"name": "beam", "render": False}, source="agent")
    return workspace


def test_an_inspection_is_shown_again_when_the_project_is_opened_again(tmp_path):
    workspace = _inspected(tmp_path)
    shown = workspace.state()["panels"]["inspect"]
    again = Workspace(workspace.root).state()["panels"]
    assert again["inspect"] == json.loads(json.dumps(shown))


def test_a_changed_model_does_not_bring_back_its_old_panels(tmp_path):
    workspace = _inspected(tmp_path)
    workspace.execute("add_step", {"name": "beam", "step": {"op": "cut", "shape": "box", "size": [5, 20, 20], "center": [30, 0, 0]}}, source="agent")
    assert "inspect" not in workspace.state()["panels"]
    assert "inspect" not in Workspace(workspace.root).state()["panels"]


def test_a_model_changed_by_another_program_does_not_show_old_panels(tmp_path):
    workspace = _inspected(tmp_path)
    other = Workspace(workspace.root)
    other.execute("create_part_from_ops", {"name": "extra", "ops": _BEAM}, source="agent")
    assert "inspect" not in Workspace(workspace.root).state()["panels"]


def test_a_broken_panels_file_is_ignored(tmp_path):
    workspace = _inspected(tmp_path)
    (workspace.root / ".aicad" / "panels.json").write_text("{not json", encoding="utf-8")
    assert Workspace(workspace.root).state()["panels"] == {}


@pytest.mark.skipif(not _AVAILABLE, reason=_REASON)
def test_a_strength_result_survives_a_reload(tmp_path):
    workspace = Workspace(tmp_path / "project")
    workspace.execute("create_part_from_ops", {"name": "beam", "ops": _BEAM}, source="agent")
    setup = {
        "material": "steel",
        "mesh_size_mm": 4.0,
        "target_safety_factor": 1.0,
        "fixes": [{"name": "root", "select": {"kind": "plane", "normal": [-1, 0, 0]}}],
        "loads": [{"name": "tip", "type": "force", "vector": [0, 0, -100], "select": {"kind": "plane", "normal": [1, 0, 0]}}],
    }
    result = workspace.execute("solve_mechanics", {"name": "beam", "setup": setup}, source="agent")
    again = Workspace(workspace.root).state()["panels"]
    assert again["fea"]["max_von_mises_mpa"] == pytest.approx(result["max_von_mises_mpa"])


def test_a_panel_made_before_another_copy_changed_the_model_is_not_kept(tmp_path):
    first = _inspected(tmp_path)
    second = Workspace(first.root)
    second.execute("add_step", {"name": "beam", "step": {"op": "cut", "shape": "box", "size": [5, 20, 20], "center": [30, 0, 0]}}, source="agent")
    # The first copy picks up the change, and its inspection is now about an old model.
    first.execute("feature_report", {"name": "beam"}, source="agent")
    reopened = Workspace(first.root).state()
    assert "inspect" not in reopened["panels"]
    assert "features" in reopened["panels"]
    assert reopened["panel_revision"]["features"] == reopened["revision"]
