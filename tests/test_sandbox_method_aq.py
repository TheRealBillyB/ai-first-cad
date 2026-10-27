"""Opening a plan draws each sheet again from the stored blocks.

The marker is CANARY_HIT. A picture left on disk is replaced when the plan
is opened, and a list of plans does not draw it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from aicad.errors import PlanError
from aicad.workspace import Workspace

_MARKER = b"CANARY_HIT\n"


def _sheet(label: str) -> dict:
    return {
        "id": "01_top",
        "title": "Top",
        "panels": [
            {
                "title": "Top",
                "blocks": [{"x": 0, "y": 0, "w": 200, "h": 80, "label": label, "fill": "yellow"}],
                "dimensions": [{"x0": 0, "y0": 0, "x1": 200, "y1": 0, "text": "200 mm", "offset": -30}],
                "notes": ["Sketch only."],
            }
        ],
    }


def _swap(path: Path) -> None:
    image = Image.new("RGB", (1, 1), (255, 0, 0))
    image.save(path, "PNG")
    path.write_bytes(path.read_bytes() + _MARKER)


@pytest.fixture(autouse=True)
def approvals_stay_outside_the_project(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_APPROVALS", str(tmp_path / "approvals.json"))


def test_opening_a_plan_redraws_a_swapped_sheet(tmp_path):
    project = tmp_path / "project"
    workspace = Workspace(project)
    workspace.execute("start_plan", {"name": "shelf", "title": "Shelf"}, source="ui")
    workspace.execute("put_plan_sheet", {"name": "shelf", "spec": _sheet("first")}, source="ui")
    workspace.execute(
        "revise_plan",
        {"name": "shelf", "changed": "A second sheet.", "bundle": {"sheets": [_sheet("second")]}},
        source="ui",
    )
    opened = workspace.execute("get_plan", {"name": "shelf"})
    current = Path(opened["views"][0]["path"])
    previous = Path(opened["revisions"][0]["sheets"][0]["path"])
    assert current.is_file() and previous.is_file()
    assert current != previous
    _swap(current)
    _swap(previous)
    listed = workspace.execute("list_plans", {})
    assert listed["plans"][0]["id"] == "shelf"
    assert _MARKER in current.read_bytes()
    assert _MARKER in previous.read_bytes()
    record = (project / "plans" / "shelf.json").read_bytes()
    again = workspace.execute("get_plan", {"name": "shelf"})
    assert (project / "plans" / "shelf.json").read_bytes() == record
    for item in (
        Path(again["views"][0]["path"]),
        Path(again["revisions"][0]["sheets"][0]["path"]),
    ):
        raw = item.read_bytes()
        assert _MARKER not in raw
        assert raw.startswith(b"\x89PNG")
        with Image.open(item) as image:
            assert image.size[0] > 1000


def test_a_failed_redraw_does_not_return_the_picture(tmp_path, monkeypatch):
    project = tmp_path / "project"
    workspace = Workspace(project)
    workspace.execute("start_plan", {"name": "shelf", "title": "Shelf"}, source="ui")
    opened = workspace.execute("put_plan_sheet", {"name": "shelf", "spec": _sheet("first")}, source="ui")
    picture = Path(opened["views"][0]["path"])
    _swap(picture)

    def stop(*_args, **_kwargs):
        raise PlanError("The sheet could not be drawn.")

    monkeypatch.setattr("aicad.plan.render_plan_sheet", stop)
    with pytest.raises(PlanError, match="could not be drawn"):
        workspace.execute("get_plan", {"name": "shelf"})
    assert _MARKER in picture.read_bytes()
