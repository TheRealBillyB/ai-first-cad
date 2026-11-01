"""A plan build stops at the file count and at the STEP size limit."""

from pathlib import Path

import pytest

from aicad.errors import PlanBuildError
from aicad.sandbox import _DiskWatch, _snapshot_files
from aicad.workspace import Workspace


@pytest.fixture(autouse=True)
def approvals_stay_outside_the_project(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_APPROVALS", str(tmp_path / "approvals.json"))


def _draft(root: Path, script: str) -> Workspace:
    workspace = Workspace(root)
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


def test_too_many_new_files_are_removed(tmp_path):
    before = _snapshot_files(tmp_path)
    disk = _DiskWatch(tmp_path, before, file_cap=10**9, disk_cap=10**9, count_cap=5)
    for index in range(12):
        (tmp_path / f"flood-{index}.txt").write_text("x", encoding="utf-8")
    message = disk.settle()
    assert message is not None
    assert "more than 5 files" in message
    assert list(tmp_path.glob("flood-*.txt")) == []


def test_too_many_new_directories_are_removed(tmp_path):
    disk = _DiskWatch(tmp_path, _snapshot_files(tmp_path), file_cap=10**9, disk_cap=10**9, count_cap=4)
    for index in range(8):
        (tmp_path / f"flood-{index}").mkdir()
    message = disk.settle()
    assert message is not None
    assert "more than 4 files" in message
    assert list(tmp_path.glob("flood-*")) == []


def test_a_plan_script_stops_when_it_creates_too_many_files(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_SCRIPT_FILE_COUNT", "8")
    project = tmp_path / "project"
    script = (
        "from pathlib import Path\n"
        "for index in range(40):\n"
        "    Path(f'flood-{index}.txt').write_text('x', encoding='utf-8')\n"
        "part = Box(1, 1, 1)\n"
    )
    workspace = _draft(project, script)
    with pytest.raises(PlanBuildError, match="Nothing from this build was saved"):
        workspace.execute("approve_plan", {"name": "bracket"}, source="ui")
    assert list(project.glob("flood-*.txt")) == []
    assert _volume(workspace, "block") is None
    assert _volume(workspace, "keep") == pytest.approx(1)


def test_a_plan_script_result_past_the_step_limit_is_not_saved(tmp_path, monkeypatch):
    import aicad.kernel as kernel

    monkeypatch.setattr(kernel, "MAX_STEP_BYTES", 32)
    project = tmp_path / "project"
    workspace = _draft(project, "part = Box(2, 3, 4)\n")
    with pytest.raises(PlanBuildError, match="Nothing from this build was saved"):
        workspace.execute("approve_plan", {"name": "bracket"}, source="ui")
    assert _volume(workspace, "block") is None
    assert _volume(workspace, "keep") == pytest.approx(1)
