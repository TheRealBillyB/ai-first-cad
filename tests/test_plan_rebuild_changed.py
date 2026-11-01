"""Modeling from a plan rebuilds a part that is no longer the approved script."""

from pathlib import Path

import pytest

from aicad.workspace import Workspace


@pytest.fixture(autouse=True)
def approvals_stay_outside_the_project(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_APPROVALS", str(tmp_path / "approvals.json"))


def _volume(workspace: Workspace, name: str):
    for part in workspace.document.list_parts():
        if part.name == name:
            return part.volume_mm3
    return None


def _approve(tmp_path: Path, script: str) -> Workspace:
    workspace = Workspace(tmp_path)
    workspace.execute(
        "start_plan",
        {
            "name": "bracket",
            "title": "Bracket",
            "bundle": {
                "build": [{"part": "block", "script": script}],
                "research": [{"note": "A box stands in for the part.", "source": "This test."}],
                "assumptions": [{"text": "The solid is a box."}],
                "sheets": [
                    {
                        "id": "01_top",
                        "title": "Top",
                        "panels": [
                            {
                                "title": "Top",
                                "blocks": [{"x": 0, "y": 0, "w": 10, "h": 10, "label": "block", "fill": "yellow"}],
                            }
                        ],
                    }
                ],
            },
        },
        source="ui",
    )
    workspace.execute("submit_plan", {"name": "bracket"}, source="ui")
    workspace.execute("approve_plan", {"name": "bracket"}, source="ui")
    return workspace


def test_a_missing_part_is_built_again_and_can_be_undone(tmp_path):
    workspace = _approve(tmp_path, "part = Box(2, 3, 4)\n")
    assert _volume(workspace, "block") == pytest.approx(24)
    workspace.execute("delete_part", {"name": "block"}, source="ui")
    assert _volume(workspace, "block") is None
    again = workspace.execute("start_model_from_plan", {"name": "bracket"}, source="ui")
    assert "not run again" not in again["note"]
    assert _volume(workspace, "block") == pytest.approx(24)
    workspace.execute("undo", source="ui")
    assert _volume(workspace, "block") is None
    assert workspace.execute("get_plan", {"name": "bracket"}, source="ui")["status"] == "approved"


def test_a_changed_script_is_built_again_from_the_approval(tmp_path):
    script = "width = 2\npart = Box(width, 3, 4)\n"
    workspace = _approve(tmp_path, script)
    assert _volume(workspace, "block") == pytest.approx(24)
    workspace.execute(
        "set_parameter",
        {"name": "block", "parameter": "script/width", "value": 5},
        source="ui",
    )
    assert _volume(workspace, "block") == pytest.approx(60)
    again = workspace.execute("start_model_from_plan", {"name": "bracket"}, source="ui")
    assert _volume(workspace, "block") == pytest.approx(24)
    assert "not run again" not in again["note"]
    workspace.execute("undo", source="ui")
    assert _volume(workspace, "block") == pytest.approx(60)
    kept = workspace.execute("start_model_from_plan", {"name": "bracket"}, source="mcp")
    # The undo restored the edited solid, so modeling builds the approved script again.
    assert _volume(workspace, "block") == pytest.approx(24)
    assert "not run again" not in kept["note"]


def test_an_unchanged_part_is_not_built_twice(tmp_path):
    workspace = _approve(tmp_path, "part = Box(2, 3, 4)\n")
    again = workspace.execute("start_model_from_plan", {"name": "bracket"}, source="ui")
    assert "not run again" in again["note"]
    workspace.execute("undo", source="ui")
    assert _volume(workspace, "block") is None
    assert workspace.execute("get_plan", {"name": "bracket"}, source="ui")["status"] == "in_review"
