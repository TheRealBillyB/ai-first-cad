"""A script cannot approve a plan or start the build that approval runs."""

from pathlib import Path

import pytest

from aicad.document import Document
from aicad.errors import PlanError
from aicad.sandbox import allow_design_scripts, run_design_script, sandbox_supported
from aicad.sandbox_state import design_call
from aicad.workspace import Workspace


@pytest.fixture(autouse=True)
def approvals_stay_outside_the_project(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_APPROVALS", str(tmp_path / "approvals.json"))


def _draft(root: Path) -> Workspace:
    workspace = Workspace(root)
    workspace.execute(
        "start_plan",
        {
            "name": "bracket",
            "title": "Bracket",
            "bundle": {
                "build": [{"part": "block", "script": "part = Box(2, 3, 4)\n"}],
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
    workspace.execute(
        "create_part_from_ops",
        {"name": "keep", "ops": [{"op": "add", "shape": "box", "size": [1, 1, 1]}]},
        source="ui",
    )
    workspace.execute("submit_plan", {"name": "bracket"}, source="ui")
    return workspace


def _names(root: Path) -> list[str]:
    return [part.name for part in Document(root).list_parts()]


def test_a_person_call_marked_as_a_design_call_cannot_approve(tmp_path):
    workspace = _draft(tmp_path)
    with design_call():
        with pytest.raises(PlanError, match="cannot approve"):
            workspace.execute("approve_plan", {"name": "bracket"}, source="ui")
    assert workspace.execute("get_plan", {"name": "bracket"}, source="ui")["status"] == "in_review"
    assert "block" not in _names(tmp_path)


def test_a_confined_process_cannot_approve(tmp_path, monkeypatch):
    workspace = _draft(tmp_path)
    monkeypatch.setattr("aicad.sandbox_state.in_sandbox", lambda: True)
    with pytest.raises(PlanError, match="cannot approve"):
        workspace.execute("approve_plan", {"name": "bracket"}, source="ui")
    assert workspace.execute("get_plan", {"name": "bracket"}, source="ui")["status"] == "in_review"


def test_a_design_call_cannot_start_the_build(tmp_path):
    workspace = _draft(tmp_path)
    workspace.execute("approve_plan", {"name": "bracket"}, source="ui")
    workspace.execute("delete_part", {"name": "block"}, source="ui")
    with design_call():
        with pytest.raises(PlanError, match="cannot start"):
            workspace.execute("start_model_from_plan", {"name": "bracket"}, source="ui")
    assert "block" not in _names(tmp_path)


def test_a_design_script_cannot_approve_or_build(tmp_path):
    if not sandbox_supported()[0]:
        pytest.skip(sandbox_supported()[1])
    project = tmp_path / "project"
    workspace = _draft(project)
    allow_design_scripts(project)
    script = "import aicad.document\naicad.document.Document('.').approve_plan('bracket')\n"
    run_design_script(project, script)
    shown = workspace.execute("get_plan", {"name": "bracket"}, source="ui")
    assert shown["status"] == "in_review"
    assert shown["modeling"] == "closed"
    assert _names(project) == ["keep"]
    workspace.execute("approve_plan", {"name": "bracket"}, source="ui")
    assert "block" in _names(project)
    # The approved solid is removed, then a script asks to build it again.
    workspace.execute("delete_part", {"name": "block"}, source="ui")
    again = "import aicad.document\naicad.document.Document('.').start_model_from_plan('bracket')\n"
    run_design_script(project, again)
    assert "block" not in _names(project)
