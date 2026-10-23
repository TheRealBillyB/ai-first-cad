"""Motion studies against closed-form joint values and interference."""

import json
import math
from pathlib import Path

import pytest

from aicad.cli import main as cli_main
from aicad.document import Document
from aicad.errors import GeometryError
from aicad.kinematics import normalize, transform_point
from aicad.kernel import aabb_gap_lower_bound, make_from_ops, overlap_volume, penetration_depth
from aicad.motion_study import SCHEMA, sample_drivers
from aicad.workspace import Workspace


def _box(document: Document, name: str, size: list[float], center: list[float]) -> None:
    document.create_part_from_ops(
        name,
        [{"op": "add", "shape": "box", "size": size, "center": center}],
    )


def _arm_spec() -> dict:
    return {
        "name": "rig",
        "instances": [
            {"name": "base", "part": "base"},
            {"name": "link", "part": "link"},
            {"name": "other", "part": "link"},
        ],
        "joints": [
            {"name": "hinge", "type": "revolute", "parent": "base", "child": "link", "axis": [0, 0, 1]},
            {"name": "rocker", "type": "revolute", "parent": "base", "child": "other", "axis": [0, 0, 1]},
        ],
    }


def test_expression_and_keyframes_sample_the_same_timeline():
    spec = _arm_spec()
    expression = sample_drivers(
        spec,
        {
            "name": "spin",
            "duration_s": 1,
            "steps": 5,
            "drivers": [{"joint": "hinge", "expression": "20 * t"}],
        },
    )
    keyframes = sample_drivers(
        spec,
        {
            "name": "spin",
            "duration_s": 1,
            "steps": 5,
            "drivers": [{"joint": "hinge", "keyframes": [{"time_s": 0, "value": 0}, {"time_s": 1, "value": 20}]}],
        },
    )
    assert [sample["drivers"]["hinge"] for sample in expression] == pytest.approx([0, 5, 10, 15, 20])
    assert [sample["drivers"]["hinge"] for sample in keyframes] == pytest.approx([0, 5, 10, 15, 20])
    assert expression[-1]["time_s"] == pytest.approx(1)


def test_keyframes_hold_outside_their_span_and_interpolate():
    samples = sample_drivers(
        _arm_spec(),
        {
            "name": "hold",
            "duration_s": 1,
            "steps": 5,
            "drivers": [
                {"joint": "hinge", "keyframes": [{"time_s": 0.25, "value": 0}, {"time_s": 0.75, "value": 10}]}
            ],
        },
    )
    assert [sample["drivers"]["hinge"] for sample in samples] == pytest.approx([0, 0, 5, 10, 10])


def test_a_coupling_cycle_and_a_bad_expression_are_refused():
    spec = _arm_spec()
    spec["instances"].append({"name": "idle", "part": "link"})
    spec["joints"].append(
        {"name": "idle", "type": "revolute", "parent": "base", "child": "idle", "axis": [0, 0, 1]}
    )
    spec["studies"] = {
        "loop": {
            "steps": 2,
            "drivers": [{"joint": "idle", "expression": "t"}],
            "couplings": [
                {"type": "gear", "driver": "rocker", "driven": "hinge", "ratio": 1},
                {"type": "gear", "driver": "hinge", "driven": "rocker", "ratio": 1},
            ],
        }
    }
    with pytest.raises(GeometryError, match="cycle"):
        normalize(spec)
    with pytest.raises(GeometryError, match="unknown name"):
        sample_drivers(
            _arm_spec(),
            {"name": "bad", "steps": 2, "drivers": [{"joint": "hinge", "expression": "t + nope"}]},
        )
    with pytest.raises(GeometryError, match="divides by zero"):
        sample_drivers(
            _arm_spec(),
            {"name": "bad", "duration_s": 1, "steps": 2, "drivers": [{"joint": "hinge", "expression": "1/(t - 1)"}]},
        )


def test_separating_axis_depth_of_two_boxes_matches_the_overlap():
    # Boxes of 20×10×10. The second is shifted 15 mm on X, so the overlap is 5 mm.
    # Volume is 5 × 10 × 10. The minimum translation is 5 mm along X.
    left = make_from_ops([{"op": "add", "shape": "box", "size": [20, 10, 10]}])
    right = make_from_ops([{"op": "add", "shape": "box", "size": [20, 10, 10], "center": [15, 0, 0]}])
    assert overlap_volume(left, right) == pytest.approx(500, abs=1e-3)
    depth = penetration_depth(left, right)
    assert depth["penetration_depth_mm"] == pytest.approx(5, abs=1e-4)
    assert depth["depth_method"] == "separating_axis"
    assert depth["direction"] == pytest.approx([1, 0, 0], abs=1e-6)
    assert aabb_gap_lower_bound(left, right) < 0


def test_a_hinge_is_held_at_a_known_stop(tmp_path: Path):
    # The arm's leading corner is local (100, 10). At angle θ its height is
    # 100 sin θ + 10 cos θ. The stop face is y = 70.
    # At the 30° limit the corner is at 58.66 mm, short of the wall.
    # At the commanded 40° it would be at 71.94 mm and the solids would intersect.
    document = Document(tmp_path)
    _box(document, "base", [10, 10, 10], [0, 0, 5])
    _box(document, "arm", [100, 20, 40], [50, 0, 20])
    _box(document, "wall", [400, 40, 80], [0, 90, 20])
    document.create_assembly(
        {
            "name": "hinge",
            "instances": [
                {"name": "base", "part": "base"},
                {"name": "link", "part": "arm"},
                {"name": "stop", "part": "wall"},
            ],
            "joints": [
                {
                    "name": "hinge",
                    "type": "revolute",
                    "parent": "base",
                    "child": "link",
                    "axis": [0, 0, 1],
                    "min": 0,
                    "max": 30,
                },
                {"name": "mount", "type": "fixed", "parent": "base", "child": "stop"},
            ],
            "studies": {
                "to_stop": {
                    "duration_s": 1,
                    "steps": 7,
                    "drivers": [{"joint": "hinge", "expression": "60 * t"}],
                }
            },
        }
    )
    report = document.run_motion_study("hinge", sequence="to_stop")
    assert report["schema"] == SCHEMA
    assert report["result_kind"] == "verified_check"
    assert report["status"] == "limit"
    assert report["collisions"] == []
    assert report["first_limit"]["index"] == 4
    assert report["first_limit"]["joint"] == "hinge"
    assert report["first_limit"]["commanded"] == pytest.approx(40)
    assert report["first_limit"]["enforced"] == pytest.approx(30)
    assert report["first_limit"]["limit"] == pytest.approx(30)
    assert report["first_limit"]["which"] == "max"
    assert report["first_limit"]["unit"] == "deg"
    assert [hit["index"] for hit in report["limit_hits"]] == [4, 5, 6]
    assert report["steps"][3]["limits"] == []
    assert report["steps"][3]["joints"][0]["value"] == pytest.approx(30)
    for step in report["steps"]:
        assert step["joints"][0]["value"] <= 30 + 1e-9
    limited = report["steps"][4]
    corner = transform_point(limited["poses"]["link"]["matrix"], [100, 10, 0])
    assert corner[1] < 70
    overshoot = 100 * math.sin(math.radians(40)) + 10 * math.cos(math.radians(40))
    assert overshoot > 70
    listed = document.list_assemblies()
    assert listed[0]["studies"] == ["to_stop"]
    workspace = Workspace(tmp_path)
    again = workspace.execute("run_motion_study", {"name": "hinge", "sequence": "to_stop"})
    assert again["first_limit"]["enforced"] == pytest.approx(30)
    assert again["collision_count"] == 0


def test_cli_motion_study_reports_the_same_stop(tmp_path: Path, capsys):
    document = Document(tmp_path)
    _box(document, "base", [8, 8, 8], [0, 0, 4])
    _box(document, "arm", [20, 8, 8], [10, 0, 4])
    document.create_assembly(
        {
            "name": "hinge",
            "instances": [{"name": "base", "part": "base"}, {"name": "link", "part": "arm"}],
            "joints": [
                {
                    "name": "hinge",
                    "type": "revolute",
                    "parent": "base",
                    "child": "link",
                    "axis": [0, 0, 1],
                    "max": 15,
                }
            ],
        }
    )
    study = {
        "name": "to_stop",
        "duration_s": 1,
        "steps": 4,
        "drivers": [{"joint": "hinge", "expression": "30 * t"}],
    }
    path = tmp_path / "study.json"
    path.write_text(json.dumps(study), encoding="utf-8")
    code = cli_main(
        ["motion-study", "hinge", "--file", str(path), "--workspace", str(tmp_path), "--steps", "4"]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == SCHEMA
    assert payload["first_limit"]["limit"] == pytest.approx(15)
    assert payload["first_limit"]["enforced"] == pytest.approx(15)
    # steps 0, 10, 20, 30. The first command past 15° is 20°.
    assert payload["first_limit"]["commanded"] == pytest.approx(20)
    assert payload["result_kind"] == "verified_check"


def test_two_links_collide_at_a_computable_angle(tmp_path: Path):
    # Arm local extent x [0, 100], y [-10, 10], z [0, 40], hinge at the origin.
    # The leading corner (100, 10) has height 100 sin θ + 10 cos θ.
    # The stop occupies y >= 92. Contact is the angle where that corner meets y = 92.
    # At 90° the arm is an axis-aligned box x [-10, 10], y [0, 100], z [0, 40].
    # The stop covers that in x and z and starts at y = 92, so the overlap is
    # 20 × 8 × 40 mm and the separating translation is 8 mm.
    length = 100.0
    half_width = 10.0
    thickness = 40.0
    stop_y = 92.0
    radius = math.hypot(length, half_width)
    contact = math.degrees(math.asin(stop_y / radius) - math.atan2(half_width, length))
    first_degree = math.ceil(contact - 1e-9)
    assert 60 < contact < 61

    document = Document(tmp_path)
    _box(document, "base", [10, 10, 10], [0, 0, 5])
    _box(document, "arm", [length, 2 * half_width, thickness], [length / 2, 0, thickness / 2])
    _box(document, "wall", [400, 80, 80], [0, stop_y + 40, thickness / 2])
    document.create_assembly(
        {
            "name": "sweep",
            "instances": [
                {"name": "base", "part": "base"},
                {"name": "link", "part": "arm"},
                {"name": "stop", "part": "wall"},
            ],
            "joints": [
                {"name": "hinge", "type": "revolute", "parent": "base", "child": "link", "axis": [0, 0, 1]},
                {"name": "mount", "type": "fixed", "parent": "base", "child": "stop"},
            ],
            "studies": {
                "quarter": {
                    "duration_s": 1,
                    "steps": 91,
                    "drivers": [{"joint": "hinge", "expression": "90 * t"}],
                }
            },
        }
    )
    report = document.run_motion_study("sweep", sequence="quarter")
    assert report["status"] == "collision"
    assert report["first_collision"]["index"] == first_degree
    assert report["first_collision"]["joints"]["hinge"] == pytest.approx(first_degree)
    pair = report["first_collision"]["pairs"][0]
    assert {pair["a"], pair["b"]} == {"link", "stop"}
    assert pair["kind"] == "interference"
    assert report["colliding_steps"] == list(range(first_degree, 91))
    assert report["steps"][first_degree - 1]["collisions"] == []
    assert all("base" not in {hit["a"], hit["b"]} for hit in report["collisions"])

    square = next(step for step in report["steps"] if step["joints"][0]["value"] == pytest.approx(90))
    hit = square["collisions"][0]
    assert hit["interference_volume_mm3"] == pytest.approx(20 * 8 * thickness, abs=1e-2)
    assert hit["penetration_depth_mm"] == pytest.approx(8, abs=1e-4)
    assert hit["gap_mm"] == pytest.approx(0)
    assert hit["depth_method"] == "separating_axis"
    assert abs(hit["direction"][1]) == pytest.approx(1, abs=1e-4)
    assert report["narrow_phase_pairs"] > 0
    assert report["broad_phase_rejected"] > report["narrow_phase_pairs"]


def test_a_gear_coupling_holds_its_ratio_on_revolute_prismatic_and_cylindrical(tmp_path: Path):
    document = Document(tmp_path)
    _box(document, "chip", [4, 4, 4], [0, 0, 2])
    document.create_assembly(
        {
            "name": "train",
            "instances": [
                {"name": "ground", "part": "chip"},
                {"name": "pinion", "part": "chip"},
                {"name": "wheel", "part": "chip"},
                {"name": "ram", "part": "chip"},
                {"name": "screw", "part": "chip"},
            ],
            "joints": [
                {"name": "pinion", "type": "revolute", "parent": "ground", "child": "pinion", "axis": [0, 0, 1]},
                {
                    "name": "wheel",
                    "type": "revolute",
                    "parent": "ground",
                    "child": "wheel",
                    "parent_anchor": [0, 40, 0],
                    "axis": [0, 0, 1],
                },
                {
                    "name": "ram",
                    "type": "prismatic",
                    "parent": "ground",
                    "child": "ram",
                    "parent_anchor": [0, 80, 0],
                    "axis": [1, 0, 0],
                },
                {
                    "name": "screw",
                    "type": "cylindrical",
                    "parent": "ground",
                    "child": "screw",
                    "parent_anchor": [0, 120, 0],
                    "axis": [0, 0, 1],
                },
            ],
            "studies": {
                "mesh": {
                    "duration_s": 1,
                    "steps": 5,
                    "drivers": [
                        {
                            "joint": "pinion",
                            "keyframes": [{"time_s": 0, "value": 0}, {"time_s": 1, "value": 20}],
                        },
                        {"joint": "ram", "expression": "8 * t"},
                        {"joint": "screw", "expression": "90 * t", "slide": "12 * t"},
                    ],
                    "couplings": [
                        {"name": "mesh", "type": "gear", "driver": "pinion", "driven": "wheel", "ratio": -1.5, "offset": 4}
                    ],
                }
            },
        }
    )
    report = document.run_motion_study("train", sequence="mesh")
    assert report["status"] == "clear"
    assert report["collision_count"] == 0
    expected_pinion = [0, 5, 10, 15, 20]
    for step, pinion in zip(report["steps"], expected_pinion, strict=True):
        values = {joint["name"]: joint for joint in step["joints"]}
        assert values["pinion"]["value"] == pytest.approx(pinion)
        assert values["wheel"]["value"] == pytest.approx(-1.5 * pinion + 4)
        assert values["wheel"]["driven"] is True
        assert values["ram"]["value"] == pytest.approx(8 * step["index"] / 4)
        assert values["ram"]["unit"] == "mm"
    last = report["steps"][-1]
    screw = next(joint for joint in last["joints"] if joint["name"] == "screw")
    assert screw["value"] == pytest.approx(90)
    assert screw["slide"] == pytest.approx(12)
    moved = transform_point(last["poses"]["screw"]["matrix"], [10, 0, 0])
    assert moved == pytest.approx([0, 130, 12], abs=1e-4)
    ram = last["poses"]["ram"]["origin_mm"]
    assert ram == pytest.approx([8, 80, 0], abs=1e-4)


def test_clearance_uses_the_solid_gap_when_boxes_overlap(tmp_path: Path):
    # A 5 mm hole and a 3 mm pin are concentric. The radial gap is 2 mm.
    # The pin's box sits inside the block's box, so a box test would call this a hit.
    document = Document(tmp_path)
    document.create_part_from_ops(
        "block",
        [
            {"op": "add", "shape": "box", "size": [30, 30, 20]},
            {"op": "cut", "shape": "cylinder", "radius": 5, "height": 40},
        ],
    )
    document.create_part_from_ops(
        "pin",
        [{"op": "add", "shape": "cylinder", "radius": 3, "height": 16}],
    )
    _box(document, "base", [10, 10, 10], [0, 0, 0])
    _box(document, "cover", [10, 10, 10], [0, 0, 0])
    document.create_assembly(
        {
            "name": "bore",
            "instances": [
                {"name": "base", "part": "base", "placement": {"position": [0, 0, 40], "rotation_deg": [0, 0, 0]}},
                {"name": "block", "part": "block"},
                {"name": "pin", "part": "pin"},
                {"name": "cover", "part": "cover"},
            ],
            "joints": [
                {"name": "seat", "type": "fixed", "parent": "base", "child": "block"},
                {"name": "slide", "type": "prismatic", "parent": "base", "child": "pin", "axis": [0, 0, 1]},
                {"name": "aside", "type": "fixed", "parent": "base", "child": "cover", "parent_anchor": [500, 0, 0]},
            ],
            "studies": {
                "rest": {
                    "duration_s": 1,
                    "steps": 2,
                    "drivers": [{"joint": "slide", "expression": "0"}],
                }
            },
        }
    )
    clear = document.run_motion_study("bore", sequence="rest")
    assert clear["status"] == "clear"
    assert clear["collisions"] == []
    assert clear["narrow_phase_pairs"] == 2
    assert clear["broad_phase_rejected"] == 4
    assert clear["steps"][0]["narrow_phase_pairs"] == 1
    assert clear["steps"][0]["broad_phase_rejected"] == 2

    tight = document.run_motion_study("bore", sequence="rest", clearance_mm=3)
    assert tight["status"] == "collision"
    assert tight["colliding_steps"] == [0, 1]
    hit = tight["first_collision"]["pairs"][0]
    assert {hit["a"], hit["b"]} == {"block", "pin"}
    assert hit["kind"] == "clearance"
    assert hit["interference_volume_mm3"] == pytest.approx(0)
    assert hit["gap_mm"] == pytest.approx(2, abs=1e-3)
    assert hit["penetration_depth_mm"] == pytest.approx(1, abs=1e-3)
    assert hit["depth_method"] == "clearance"
    assert tight["collision_count"] == 2
