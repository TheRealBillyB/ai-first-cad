"""One approval builds once, and a failure to record it keeps the old model."""

import threading
from pathlib import Path

import pytest

from aicad.errors import PlanError
from aicad.workspace import Workspace


@pytest.fixture(autouse=True)
def approvals_stay_outside_the_project(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_APPROVALS", str(tmp_path / "approvals.json"))


def _draft(root: Path, script: str | None) -> Workspace:
    bundle = {
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
    }
    if script is not None:
        bundle["build"] = [{"part": "block", "script": script}]
    workspace = Workspace(root)
    workspace.execute(
        "start_plan",
        {"name": "bracket", "title": "Bracket", "bundle": bundle},
        source="ui",
    )
    workspace.execute(
        "create_part_from_ops",
        {"name": "keep", "ops": [{"op": "add", "shape": "box", "size": [1, 1, 1]}]},
        source="ui",
    )
    workspace.execute("submit_plan", {"name": "bracket"}, source="ui")
    return workspace


def _volume(workspace: Workspace, name: str):
    for part in workspace.document.list_parts():
        if part.name == name:
            return part.volume_mm3
    return None


def test_two_approvals_at_once_run_the_scripts_once(tmp_path, monkeypatch):
    import aicad.sandbox as sandbox

    seen = []
    original = sandbox._spawn

    def wrapped(workspace, kind, script, network, helper=False, **kwargs):
        seen.append(kind)
        return original(workspace, kind, script, network, helper=helper, **kwargs)

    monkeypatch.setattr(sandbox, "_spawn", wrapped)
    workspace = _draft(tmp_path, "part = Box(2, 3, 4)\n")
    errors = []

    def approve():
        try:
            workspace.execute("approve_plan", {"name": "bracket"}, source="ui")
        except PlanError as exc:
            errors.append(str(exc))

    first = threading.Thread(target=approve)
    second = threading.Thread(target=approve)
    first.start()
    second.start()
    first.join()
    second.join()
    assert seen == ["part"]
    assert len(errors) == 1
    assert "already approved" in errors[0]
    assert _volume(workspace, "block") == pytest.approx(24)
    assert _volume(workspace, "keep") == pytest.approx(1)


def test_a_failed_build_record_does_not_keep_the_new_solids(tmp_path, monkeypatch):
    from aicad.plan import PlanStore

    original = PlanStore.write_build_result

    def boom(self, plan, progress, parts):
        raise OSError("full")

    monkeypatch.setattr(PlanStore, "write_build_result", boom)
    workspace = _draft(tmp_path, "part = Box(2, 3, 4)\n")
    with pytest.raises(OSError, match="full"):
        workspace.execute("approve_plan", {"name": "bracket"}, source="ui")
    assert _volume(workspace, "block") is None
    assert _volume(workspace, "keep") == pytest.approx(1)
    monkeypatch.setattr(PlanStore, "write_build_result", original)
    again = workspace.execute("start_model_from_plan", {"name": "bracket"}, source="ui")
    assert _volume(workspace, "block") == pytest.approx(24)
    assert "not run again" not in again["note"]
