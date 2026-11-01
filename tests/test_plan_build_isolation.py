"""A plan build script does not read or write the project."""

import json
from pathlib import Path

import pytest

from aicad.errors import PlanBuildError
from aicad.sandbox import design_scripts_enabled, set_project_wall_seconds
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


def _watch_spawn(monkeypatch):
    import aicad.sandbox as sandbox

    seen = []
    original = sandbox._spawn

    def wrapped(workspace, kind, script, network, helper=False, **kwargs):
        seen.append(
            {
                "kind": kind,
                "network": network,
                "workspace": Path(workspace).resolve(),
                "wall": kwargs.get("wall_seconds_limit"),
            }
        )
        return original(workspace, kind, script, network, helper=helper, **kwargs)

    monkeypatch.setattr(sandbox, "_spawn", wrapped)
    return seen


def test_a_plan_script_cannot_read_or_write_the_project(tmp_path, monkeypatch):
    project = tmp_path / "project"
    note = project / "documents" / "decisions.md"
    marker = project / "plan-build-wrote.txt"
    seen = _watch_spawn(monkeypatch)
    script = (
        "from pathlib import Path\n"
        f"Path('plan-build-wrote.txt').write_text('relative')\n"
        f"Path({str(note)!r}).read_text()\n"
        "part = Box(2, 3, 4)\n"
    )
    workspace = _draft(project, script)
    before = note.read_text(encoding="utf-8")
    session = (project / "session.json").read_bytes()
    with pytest.raises(PlanBuildError, match="Nothing from this build was saved"):
        workspace.execute("approve_plan", {"name": "bracket"}, source="ui")
    assert not marker.exists()
    assert note.read_text(encoding="utf-8") == before
    assert (project / "session.json").read_bytes() == session
    assert _volume(workspace, "keep") == pytest.approx(1)
    assert _volume(workspace, "block") is None
    assert seen and seen[0]["kind"] == "part" and seen[0]["network"] is False
    assert project.resolve() not in seen[0]["workspace"].parents
    assert seen[0]["workspace"] != project.resolve()
    assert design_scripts_enabled(project) is False


def test_a_plan_script_that_only_builds_a_solid_still_runs(tmp_path, monkeypatch):
    project = tmp_path / "project"
    seen = _watch_spawn(monkeypatch)
    workspace = _draft(project, "part = Box(2, 3, 4)\n")
    geometry = project / ".aicad" / "geometry"
    before = sorted(path.name for path in geometry.glob("*")) if geometry.exists() else []
    approved = workspace.execute("approve_plan", {"name": "bracket"}, source="ui")
    assert approved["parts"] == ["block"]
    assert _volume(workspace, "block") == pytest.approx(24)
    assert _volume(workspace, "keep") == pytest.approx(1)
    assert seen[0]["network"] is False
    assert seen[0]["workspace"] != project.resolve()
    after = sorted(path.name for path in (project / ".aicad" / "geometry").glob("*"))
    assert any(name.startswith("block-") for name in after)
    assert set(before) <= set(after)


def test_a_plan_script_stops_at_the_byte_limit(tmp_path, monkeypatch):
    project = tmp_path / "project"
    monkeypatch.setenv("AICAD_SCRIPT_DISK_BYTES", "2000000")
    script = (
        "from pathlib import Path\n"
        "Path('big.bin').write_bytes(b'x' * 3000000)\n"
        "part = Box(1, 1, 1)\n"
    )
    workspace = _draft(project, script)
    with pytest.raises(PlanBuildError, match="Nothing from this build was saved"):
        workspace.execute("approve_plan", {"name": "bracket"}, source="ui")
    assert _volume(workspace, "block") is None
    assert _volume(workspace, "keep") == pytest.approx(1)
    assert list(project.rglob("big.bin")) == []


def test_a_plan_script_stops_at_the_project_time_limit(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    set_project_wall_seconds(project, 1)
    seen = _watch_spawn(monkeypatch)
    script = "import time\ntime.sleep(30)\npart = Box(1, 1, 1)\n"
    workspace = _draft(project, script)
    with pytest.raises(PlanBuildError, match="longer than"):
        workspace.execute("approve_plan", {"name": "bracket"}, source="ui")
    assert _volume(workspace, "block") is None
    assert _volume(workspace, "keep") == pytest.approx(1)
    assert seen[0]["wall"] == 1
    payload = json.loads((project / "plans" / "bracket.json").read_text(encoding="utf-8"))
    assert payload["status"] == "approved"
