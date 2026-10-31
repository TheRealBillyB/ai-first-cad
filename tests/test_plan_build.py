"""Approving a plan runs its build steps. Nothing runs before that approval."""

import json
from pathlib import Path

import pytest

from aicad.document import Document
from aicad.errors import PlanBuildError, PlanError
from aicad.sandbox import design_scripts_enabled
from aicad.workspace import Workspace


@pytest.fixture(autouse=True)
def approvals_stay_outside_the_project(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_APPROVALS", str(tmp_path / "approvals.json"))


def _steps(*rows):
    return [{"part": part, "script": script} for part, script in rows]


def _volume(workspace: Workspace, name: str):
    for part in workspace.document.list_parts():
        if part.name == name:
            return part.volume_mm3
    return None


def _watch_spawn(monkeypatch):
    import aicad.sandbox as sandbox

    seen = []
    original = sandbox._spawn

    def wrapped(workspace, kind, script, network, helper=False):
        seen.append({"kind": kind, "network": network})
        return original(workspace, kind, script, network, helper=helper)

    monkeypatch.setattr(sandbox, "_spawn", wrapped)
    return seen


def _draft(tmp_path: Path, steps: list[dict]) -> Workspace:
    workspace = Workspace(tmp_path)
    workspace.execute(
        "start_plan",
        {
            "name": "bracket",
            "title": "Bracket",
            "bundle": {
                "build": steps,
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
    return workspace


def test_build_steps_stay_data_until_the_person_approves(tmp_path, monkeypatch):
    marker = tmp_path / "should-not-run"
    script = f"open({str(marker)!r}, 'w').write('ran')\npart = Box(1, 1, 1)\n"
    seen = _watch_spawn(monkeypatch)
    workspace = _draft(tmp_path, _steps(("block", script)))
    stored = workspace.execute("get_plan", {"name": "bracket"}, source="mcp")
    assert stored["revision"]["build"][0]["script"].startswith("open(")
    assert stored["modeling"] == "closed"
    assert "do not run" in stored["modeling_note"]
    assert not marker.exists()
    Document(tmp_path)
    assert not marker.exists()
    workspace.execute("submit_plan", {"name": "bracket"}, source="ui")
    with pytest.raises(PlanError, match="in review"):
        workspace.execute(
            "set_plan_build",
            {"name": "bracket", "steps": _steps(("block", "part = Box(1, 1, 1)\n"))},
            source="mcp",
        )
    with pytest.raises(PlanError, match="cannot approve"):
        workspace.execute("approve_plan", {"name": "bracket"}, source="mcp")
    with pytest.raises(PlanError, match="Nothing was modeled"):
        workspace.execute("start_model_from_plan", {"name": "bracket"}, source="mcp")
    path = tmp_path / "plans" / "bracket.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["status"] = "approved"
    payload["approved_revision"] = payload["current_revision"]
    payload["revisions"][-1]["build"][0]["script"] = "part = Box(9, 9, 9)\n"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PlanError, match="does not match an approval"):
        workspace.execute("start_model_from_plan", {"name": "bracket"}, source="mcp")
    assert seen == []
    assert not marker.exists()
    assert design_scripts_enabled(tmp_path) is False
    fresh = Workspace(tmp_path / "other")
    fresh.execute("start_plan", {"name": "bracket", "title": "Bracket"}, source="ui")
    with pytest.raises(PlanError, match="listed twice"):
        fresh.execute(
            "set_plan_build",
            {"name": "bracket", "steps": _steps(("block", "part = Box(1, 1, 1)\n"), ("block", "part = Box(2, 2, 2)\n"))},
            source="ui",
        )
    with pytest.raises(PlanError, match="reserved on Windows"):
        fresh.execute(
            "set_plan_build",
            {"name": "bracket", "steps": [{"part": "CON", "script": "part = Box(1, 1, 1)\n"}]},
            source="ui",
        )
    assert not marker.exists()


def test_approval_builds_the_parts_and_leaves_the_network_off(tmp_path, monkeypatch):
    seen = _watch_spawn(monkeypatch)
    workspace = _draft(
        tmp_path,
        _steps(("block", "part = Box(2, 3, 4)\n"), ("rib", "part = Box(4, 5, 6)\n")),
    )
    workspace.execute(
        "create_part_from_ops",
        {"name": "keep", "ops": [{"op": "add", "shape": "box", "size": [1, 1, 1]}]},
        source="ui",
    )
    workspace.execute("submit_plan", {"name": "bracket"}, source="ui")
    assert workspace.allow_script_network_once()["network"] == "next run"
    approved = workspace.execute("approve_plan", {"name": "bracket"}, source="ui")
    assert [row["status"] for row in approved["progress"]] == ["built", "built"]
    assert approved["parts"] == ["block", "rib"]
    assert "network off" in approved["note"]
    assert _volume(workspace, "block") == pytest.approx(24)
    assert _volume(workspace, "rib") == pytest.approx(120)
    assert _volume(workspace, "keep") == pytest.approx(1)
    assert seen == [{"kind": "part", "network": False}, {"kind": "part", "network": False}]
    assert workspace.script_status()["network"] == "next run"
    assert design_scripts_enabled(tmp_path) is False
    simulated = workspace.execute("start_simulation_from_plan", {"name": "bracket"}, source="mcp")
    assert simulated["released"] == "simulation"
    assert "parts" not in simulated
    assert len(seen) == 2
    again = workspace.execute("start_model_from_plan", {"name": "bracket"}, source="mcp")
    assert again["parts"] == ["block", "rib"]
    assert "not run again" in again["note"]
    assert len(seen) == 2
    workspace.execute("undo", source="ui")
    assert _volume(workspace, "block") is None
    assert workspace.execute("get_plan", {"name": "bracket"})["status"] == "in_review"
    workspace.execute("redo", source="ui")
    assert _volume(workspace, "block") == pytest.approx(24)
    assert workspace.execute("get_plan", {"name": "bracket"})["status"] == "approved"
    assert len(seen) == 2
    path = tmp_path / "plans" / "bracket.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["revisions"][-1]["build"][0]["script"] = "part = Box(9, 9, 9)\n"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PlanError, match="does not match an approval"):
        workspace.execute("start_model_from_plan", {"name": "bracket"}, source="ui")
    assert _volume(workspace, "block") == pytest.approx(24)
    assert len(seen) == 2


def test_a_failed_step_leaves_the_previous_model(tmp_path):
    workspace = _draft(
        tmp_path,
        [
            {
                "id": "first",
                "part": "keep",
                "script": (
                    "import json\n"
                    "from pathlib import Path\n"
                    "Path('session.json').write_text("
                    "json.dumps({'units': 'mm', 'parts': [{'name': 'intruder', 'source': 'ops', 'ops': []}]}))\n"
                    "part = Box(2, 3, 4)\n"
                ),
            },
            {"id": "second", "part": "bad", "script": "part = 1\n"},
        ],
    )
    workspace.execute(
        "create_part_from_ops",
        {"name": "keep", "ops": [{"op": "add", "shape": "box", "size": [1, 1, 1]}]},
        source="ui",
    )
    workspace.execute("submit_plan", {"name": "bracket"}, source="ui")
    with pytest.raises(PlanBuildError, match="left in place") as caught:
        workspace.execute("approve_plan", {"name": "bracket"}, source="ui")
    error = caught.value
    assert "Nothing from this build was saved" in str(error)
    assert error.step_id == "second"
    assert error.part == "bad"
    assert error.index == 2
    assert error.count == 2
    assert [row["status"] for row in error.progress] == ["passed", "failed"]
    assert "Expected a 3D solid" in error.progress[1]["error"]
    assert workspace.document.get_source("keep")["source"] == "ops"
    assert _volume(workspace, "keep") == pytest.approx(1)
    assert _volume(workspace, "bad") is None
    assert _volume(workspace, "intruder") is None
    reloaded = Document(tmp_path)
    assert [part.name for part in reloaded.list_parts()] == ["keep"]
    assert reloaded.measure("keep").volume_mm3 == pytest.approx(1)
    shown = workspace.execute("get_plan", {"name": "bracket"}, source="ui")
    assert [row["status"] for row in shown["build_progress"]] == ["passed", "failed"]
    with pytest.raises(PlanBuildError, match="Build step 'second'"):
        workspace.execute("start_model_from_plan", {"name": "bracket"}, source="ui")
    assert _volume(workspace, "keep") == pytest.approx(1)
    assert _volume(workspace, "bad") is None


def test_a_step_cannot_write_outside_the_project(tmp_path):
    canary = tmp_path / "plan-build-canary.txt"
    project = tmp_path / "project"
    workspace = _draft(
        project,
        _steps(("block", f"from pathlib import Path\nPath({str(canary)!r}).write_text('plan-build-canary')\npart = Box(1, 1, 1)\n")),
    )
    workspace.execute(
        "create_part_from_ops",
        {"name": "keep", "ops": [{"op": "add", "shape": "box", "size": [1, 1, 1]}]},
        source="ui",
    )
    workspace.execute("submit_plan", {"name": "bracket"}, source="ui")
    with pytest.raises(PlanBuildError, match="Nothing from this build was saved"):
        workspace.execute("approve_plan", {"name": "bracket"}, source="ui")
    assert not canary.exists()
    assert _volume(workspace, "keep") == pytest.approx(1)
    assert _volume(workspace, "block") is None
    assert design_scripts_enabled(project) is False
