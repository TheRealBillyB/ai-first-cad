"""A print check takes a stated print pose, not only the six axis directions.

The vise frame prints on its side at an angle, and the check could only try
z_up, z_down, x_up, x_down, y_up, and y_down. A pose is now stated with up,
the modeled direction that points up, or with rotation_deg.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from aicad import kernel, printprep
from aicad.errors import GeometryError

_CHECK = {"render": False, "slice": False}


def _tee():
    return kernel.make_from_ops(
        [
            {"op": "add", "shape": "box", "size": [6, 6, 30], "center": [0, 0, 15]},
            {"op": "add", "shape": "box", "size": [60, 6, 4], "center": [0, 0, 32]},
        ]
    )


def _cube():
    return kernel.make_from_ops([{"op": "add", "shape": "box", "size": [20, 20, 20]}])


@pytest.mark.parametrize(
    ("named", "stated"),
    [
        ("z_up", {"up": [0, 0, 1]}),
        ("z_up", {"rotation_deg": [0, 0, 0]}),
        ("z_down", {"up": [0, 0, -1]}),
        ("z_down", {"rotation_deg": [180, 0, 0]}),
        ("x_up", {"up": [1, 0, 0]}),
    ],
)
def test_a_stated_pose_matches_the_named_one(named, stated):
    shape = _tee()
    by_name = printprep.analyze_print(shape, {**_CHECK, "orientation": named})
    by_pose = printprep.analyze_print(shape, {**_CHECK, **stated})
    assert by_pose["orientation"] == "custom" and by_pose["orientation_automatic"] is False
    assert by_pose["support_area_mm2"] == pytest.approx(by_name["support_area_mm2"], abs=0.5)
    assert by_pose["height_mm"] == pytest.approx(by_name["height_mm"], abs=1e-3)
    assert by_pose["pose"] == {key: [float(value) for value in values] for key, values in stated.items()}


def test_a_cube_laid_on_an_edge_has_two_faces_at_45_degrees():
    shape = _cube()
    steep = printprep.analyze_print(shape, {**_CHECK, "up": [0, 1, 1], "threshold_deg": 40})
    shallow = printprep.analyze_print(shape, {**_CHECK, "up": [0, 1, 1], "threshold_deg": 50})
    # The two lower faces lean 45 degrees from vertical: past a 40 degree limit, inside a 50 degree one.
    assert steep["support_area_mm2"] == pytest.approx(2 * 20 * 20, rel=0.02)
    assert shallow["support_area_mm2"] == pytest.approx(0.0, abs=1.0)
    assert steep["height_mm"] == pytest.approx(20 * math.sqrt(2), abs=0.01)
    assert "(0, 1, 1) points up" in steep["orientation_note"]
    rotation = np.array(steep["print_rotation"])
    up = rotation @ (np.array([0, 1, 1]) / math.sqrt(2))
    assert up == pytest.approx([0, 0, 1], abs=1e-9)


def test_a_rotation_turns_about_x_then_y_then_z():
    shape = _cube()
    report = printprep.analyze_print(shape, {**_CHECK, "rotation_deg": [45, 0, 0], "threshold_deg": 40})
    assert report["support_area_mm2"] == pytest.approx(2 * 20 * 20, rel=0.02)
    assert report["height_mm"] == pytest.approx(20 * math.sqrt(2), abs=0.01)


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        ({"up": [0, 0, 1], "rotation_deg": [0, 0, 0]}, "not both"),
        ({"up": [0, 0, 1], "orientation": "x_up"}, "Use one"),
        ({"up": [0, 0, 0]}, "not zero"),
        ({"rotation_deg": [0, "a", 0]}, "rotation_deg"),
    ],
)
def test_a_pose_that_does_not_make_sense_is_refused(setup, message):
    with pytest.raises(GeometryError, match=message):
        printprep.analyze_print(_cube(), {**_CHECK, **setup})


def test_the_slicer_gets_the_part_in_its_stated_pose(monkeypatch):
    seen = {}

    def fake_slice(vertices, triangles, out_dir, price_per_kg=None, layer_height_mm=None, **_kwargs):
        seen["height"] = float(vertices[:, 2].max() - vertices[:, 2].min())
        return {"status": "skipped"}

    monkeypatch.setattr(printprep, "slice_mesh", fake_slice)
    printprep.analyze_print(_cube(), {"render": False, "slice": True, "up": [0, 1, 1]})
    assert seen["height"] == pytest.approx(20 * math.sqrt(2), abs=0.01)


def test_the_command_line_states_a_pose():
    from aicad.cli import build_parser

    args = build_parser().parse_args(["print", "frame", "--up", "0", "1", "1"])
    assert args.up == [0.0, 1.0, 1.0]
    args = build_parser().parse_args(["print", "frame", "--rotation-deg", "90", "0", "30"])
    assert args.rotation_deg == [90.0, 0.0, 30.0]
