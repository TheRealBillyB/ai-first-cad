"""A joint can be swept from its lower stop to its upper stop."""

from pathlib import Path

import pytest

from aicad.document import Document
from aicad.errors import GeometryError
from aicad.motion_study import sample_drivers
from aicad.workspace import Workspace


def _hinge(document: Document, minimum, maximum) -> None:
    document.create_part_from_ops(
        "base",
        [{"op": "add", "shape": "box", "size": [10, 10, 10], "center": [0, 0, 5]}],
    )
    document.create_part_from_ops(
        "arm",
        [{"op": "add", "shape": "box", "size": [40, 8, 8], "center": [20, 0, 4]}],
    )
    joint = {
        "name": "hinge",
        "type": "revolute",
        "parent": "base",
        "child": "link",
        "axis": [0, 0, 1],
    }
    if minimum is not None:
        joint["min"] = minimum
    if maximum is not None:
        joint["max"] = maximum
    document.create_assembly(
        {
            "name": "hinge",
            "instances": [{"name": "base", "part": "base"}, {"name": "link", "part": "arm"}],
            "joints": [joint],
        }
    )


def test_a_study_sweeps_from_the_lower_stop_to_the_upper_stop(tmp_path: Path):
    document = Document(tmp_path)
    _hinge(document, 0, 90)
    report = document.run_motion_study(
        "hinge",
        study={"name": "range", "duration_s": 1, "steps": 5, "drivers": [{"joint": "hinge", "range": True}]},
    )
    assert report["status"] == "clear"
    assert report["limit_hit_count"] == 0
    assert [step["joints"][0]["value"] for step in report["steps"]] == pytest.approx([0, 22.5, 45, 67.5, 90])
    assert [step["joints"][0]["limit"] for step in report["steps"]] == ["ok"] * 5
    samples = sample_drivers(
        document.get_assembly("hinge")["assembly"],
        {"name": "range", "duration_s": 1, "steps": 5, "drivers": [{"joint": "hinge", "range": True}]},
    )
    assert samples[0]["drivers"]["hinge"] == pytest.approx(0)
    assert samples[-1]["drivers"]["hinge"] == pytest.approx(90)
    workspace = Workspace(tmp_path)
    again = workspace.execute(
        "run_motion_study",
        {"name": "hinge", "study": {"name": "range", "steps": 3, "drivers": [{"joint": "hinge", "range": {"start": 10, "end": 40}}]}},
    )
    assert [step["joints"][0]["value"] for step in again["steps"]] == pytest.approx([10, 25, 40])
    assert again["limit_hit_count"] == 0


def test_a_motion_sweep_uses_the_stops_when_start_and_end_are_omitted(tmp_path: Path):
    document = Document(tmp_path)
    _hinge(document, -30, 30)
    report = document.run_motion(
        "hinge",
        motion={"name": "span", "sweep": {"joint": "hinge", "frames": 3}},
        render=False,
    )
    assert [frame["joints"]["hinge"] for frame in report["frames"]] == pytest.approx([-30, 0, 30])
    explicit = document.run_motion(
        "hinge",
        motion={"name": "span", "sweep": {"joint": "hinge", "start": 0, "end": 15, "frames": 2}},
        render=False,
    )
    assert [frame["joints"]["hinge"] for frame in explicit["frames"]] == pytest.approx([0, 15])


def test_a_joint_without_stops_cannot_be_swept_through_its_range(tmp_path: Path):
    document = Document(tmp_path)
    _hinge(document, None, 90)
    with pytest.raises(GeometryError, match="min and max"):
        document.run_motion_study(
            "hinge",
            study={"name": "range", "steps": 2, "drivers": [{"joint": "hinge", "range": True}]},
        )
    with pytest.raises(GeometryError, match="start"):
        document.run_motion("hinge", motion={"sweep": {"joint": "hinge", "frames": 2}}, render=False)
    with pytest.raises(GeometryError, match="another way"):
        document.run_motion_study(
            "hinge",
            study={
                "name": "both",
                "steps": 2,
                "drivers": [{"joint": "hinge", "range": True, "expression": "t"}],
            },
        )
