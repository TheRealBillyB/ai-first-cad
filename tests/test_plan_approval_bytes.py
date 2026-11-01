"""Approval runs the revision that was approved, not a file that replaced it."""

import json
from pathlib import Path

import pytest

from aicad.errors import PlanError
from aicad.workspace import Workspace


@pytest.fixture(autouse=True)
def approvals_stay_outside_the_project(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_APPROVALS", str(tmp_path / "approvals.json"))


def _volume(workspace: Workspace, name: str):
    for part in workspace.document.list_parts():
        if part.name == name:
            return part.volume_mm3
    return None


def _draft(tmp_path: Path) -> Workspace:
    workspace = Workspace(tmp_path)
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
    workspace.execute("submit_plan", {"name": "bracket"}, source="ui")
    return workspace


def test_a_replaced_plan_file_is_not_the_build(tmp_path, monkeypatch):
    import aicad.paths as paths

    workspace = _draft(tmp_path)
    original = paths.publish_text
    once = {"done": False}

    def wrapped(path, text, *args, **kwargs):
        original(path, text, *args, **kwargs)
        if once["done"] or Path(path).name != "bracket.json":
            return
        if "Box(2, 3, 4)" not in text or '"status": "approved"' not in text:
            return
        once["done"] = True
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        payload["revisions"][-1]["build"][0]["script"] = "part = Box(9, 9, 9)\n"
        Path(path).write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(paths, "publish_text", wrapped)
    approved = workspace.execute("approve_plan", {"name": "bracket"}, source="ui")
    assert once["done"] is True
    assert _volume(workspace, "block") == pytest.approx(24)
    assert "Box(2, 3, 4)" in approved["revision"]["build"][0]["script"]
    assert "Box(9, 9, 9)" not in approved["revision"]["build"][0]["script"]
    path = tmp_path / "plans" / "bracket.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["revisions"][-1]["build"][0]["script"] = "part = Box(9, 9, 9)\n"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PlanError, match="does not match an approval"):
        workspace.execute("start_model_from_plan", {"name": "bracket"}, source="ui")
    assert _volume(workspace, "block") == pytest.approx(24)


def test_a_replaced_plan_file_is_not_the_approval_result(tmp_path, monkeypatch):
    import aicad.paths as paths

    workspace = _draft(tmp_path)
    original = paths.publish_text

    def wrapped(path, text, *args, **kwargs):
        original(path, text, *args, **kwargs)
        if Path(path).name != "bracket.json":
            return
        if "Box(2, 3, 4)" not in text or '"status": "approved"' not in text:
            return
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        payload["revisions"][-1]["build"][0]["script"] = "part = Box(9, 9, 9)\n"
        Path(path).write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(paths, "publish_text", wrapped)
    approved = workspace.execute("approve_plan", {"name": "bracket"}, source="ui")
    assert _volume(workspace, "block") == pytest.approx(24)
    assert "Box(2, 3, 4)" in approved["revision"]["build"][0]["script"]
    assert "Box(9, 9, 9)" not in approved["revision"]["build"][0]["script"]
    with pytest.raises(PlanError, match="does not match an approval"):
        workspace.execute("start_model_from_plan", {"name": "bracket"}, source="ui")
    assert _volume(workspace, "block") == pytest.approx(24)
