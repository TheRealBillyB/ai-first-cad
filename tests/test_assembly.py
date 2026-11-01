"""Assemblies, motion, cables, and quasi-static power."""

import math
from pathlib import Path

import pytest

from aicad.document import Document
from aicad.errors import GeometryError
from aicad.kinematics import expand_motion, solve, transform_point
from aicad.workspace import Workspace


def _box(document: Document, name: str, size: list[float], center: list[float]) -> None:
    document.create_part_from_ops(
        name,
        [{"op": "add", "shape": "box", "size": size, "center": center}],
    )


def test_revolute_prismatic_and_cylindrical_place_points():
    spec = {
        "name": "arm",
        "instances": [
            {"name": "base", "part": "base"},
            {"name": "spin", "part": "link"},
            {"name": "slide", "part": "link"},
            {"name": "screw", "part": "link"},
        ],
        "joints": [
            {
                "name": "hinge",
                "type": "revolute",
                "parent": "base",
                "child": "spin",
                "parent_anchor": [10, 0, 0],
                "child_anchor": [0, 0, 0],
                "axis": [0, 0, 1],
                "value": 90,
            },
            {
                "name": "lift",
                "type": "revolute",
                "parent": "base",
                "child": "slide",
                "parent_anchor": [0, 0, 0],
                "child_anchor": [0, 0, 0],
                "axis": [0, 1, 0],
                "value": 90,
            },
            {
                "name": "ram",
                "type": "prismatic",
                "parent": "base",
                "child": "slide",
                "parent_anchor": [0, 0, 0],
                "child_anchor": [0, 0, 0],
                "axis": [0, 0, 1],
                "value": 12,
            },
        ],
    }
    # slide cannot have two parents; use a dedicated child for the prismatic and the screw.
    spec["joints"] = [
        spec["joints"][0],
        {
            "name": "pitch",
            "type": "revolute",
            "parent": "base",
            "child": "slide",
            "parent_anchor": [0, 0, 0],
            "child_anchor": [0, 0, 0],
            "axis": [0, 1, 0],
            "value": 90,
        },
        {
            "name": "ram",
            "type": "cylindrical",
            "parent": "spin",
            "child": "screw",
            "parent_anchor": [0, 0, 0],
            "child_anchor": [0, 0, 0],
            "axis": [0, 0, 1],
            "value": 0,
            "slide": 15,
        },
    ]
    solved = solve(spec)
    frames = {item["name"]: item["matrix"] for item in solved["instances"]}
    assert transform_point(frames["spin"], [10, 0, 0]) == pytest.approx([10, 10, 0], abs=1e-3)
    length = 40.0
    pitched = transform_point(frames["slide"], [length, 0, 0])
    assert pitched == pytest.approx([0.0, 0.0, -length], abs=1e-3)
    assert transform_point(frames["screw"], [0, 0, 0]) == pytest.approx(
        transform_point(frames["spin"], [0, 0, 15]),
        abs=1e-3,
    )


def test_gear_ratio_and_limit_clamp():
    spec = {
        "name": "jaws",
        "instances": [
            {"name": "body", "part": "body"},
            {"name": "left", "part": "jaw"},
            {"name": "right", "part": "jaw"},
        ],
        "joints": [
            {
                "name": "left",
                "type": "revolute",
                "parent": "body",
                "child": "left",
                "axis": [0, 0, 1],
                "value": 20,
            },
            {
                "name": "right",
                "type": "revolute",
                "parent": "body",
                "child": "right",
                "axis": [0, 0, 1],
                "value": 0,
                "min": -5,
                "max": 5,
            },
        ],
        "relations": [{"name": "mesh", "type": "gear", "driver": "left", "driven": "right", "ratio": -1}],
    }
    solved = solve(spec)
    values = {joint["name"]: joint for joint in solved["joints"]}
    assert values["right"]["commanded"] == pytest.approx(-20)
    assert values["right"]["value"] == pytest.approx(-5)
    assert values["right"]["limit"] == "clamped"
    assert solved["warnings"]


def test_gear_follows_the_driver_stop():
    """A driver held at its stop must not leave the driven gear at the unclamped command."""
    spec = {
        "name": "jaws",
        "instances": [
            {"name": "body", "part": "body"},
            {"name": "left", "part": "jaw"},
            {"name": "right", "part": "jaw"},
        ],
        "joints": [
            {
                "name": "left",
                "type": "revolute",
                "parent": "body",
                "child": "left",
                "axis": [0, 0, 1],
                "value": 90,
                "max": 10,
            },
            {
                "name": "right",
                "type": "revolute",
                "parent": "body",
                "child": "right",
                "axis": [0, 0, 1],
                "value": 0,
            },
        ],
        "relations": [{"name": "mesh", "type": "gear", "driver": "left", "driven": "right", "ratio": -1}],
    }
    solved = solve(spec)
    values = {joint["name"]: joint for joint in solved["joints"]}
    assert values["left"]["commanded"] == pytest.approx(90)
    assert values["left"]["value"] == pytest.approx(10)
    assert values["right"]["commanded"] == pytest.approx(-10)
    assert values["right"]["value"] == pytest.approx(-10)
    frames = {item["name"]: item["matrix"] for item in solved["instances"]}
    angle = math.radians(-10)
    assert transform_point(frames["right"], [10, 0, 0]) == pytest.approx(
        [10 * math.cos(angle), 10 * math.sin(angle), 0.0],
        abs=1e-3,
    )


def test_cylindrical_slide_override_is_a_number_not_a_crash():
    spec = {
        "name": "screw",
        "instances": [
            {"name": "base", "part": "base"},
            {"name": "screw", "part": "link"},
        ],
        "joints": [
            {
                "name": "ram",
                "type": "cylindrical",
                "parent": "base",
                "child": "screw",
                "axis": [0, 0, 1],
                "value": 0,
                "slide": 0,
            }
        ],
    }
    solved = solve(spec, {"ram": {"value": 90, "slide": 25}})
    frames = {item["name"]: item["matrix"] for item in solved["instances"]}
    assert transform_point(frames["screw"], [10, 0, 0]) == pytest.approx([0.0, 10.0, 25.0], abs=1e-3)
    row = next(joint for joint in solved["joints"] if joint["name"] == "ram")
    assert row["value"] == pytest.approx(90)
    assert row["slide"] == pytest.approx(25)


def test_four_bar_open_branch_at_zero():
    spec = {
        "name": "bar",
        "instances": [
            {"name": "ground", "part": "ground"},
            {"name": "crank", "part": "link"},
            {"name": "rocker", "part": "link"},
        ],
        "joints": [
            {"name": "crank", "type": "revolute", "parent": "ground", "child": "crank", "axis": [0, 0, 1], "value": 0},
            {"name": "rocker", "type": "revolute", "parent": "ground", "child": "rocker", "axis": [0, 0, 1], "value": 0},
        ],
        "relations": [
            {
                "name": "loop",
                "type": "linkage",
                "driver": "crank",
                "driven": "rocker",
                "ground_mm": 100,
                "crank_mm": 40,
                "coupler_mm": 80,
                "rocker_mm": 60,
                "branch": "open",
            }
        ],
    }
    solved = solve(spec)
    rocker = next(joint["value"] for joint in solved["joints"] if joint["name"] == "rocker")
    assert rocker == pytest.approx(96.38, abs=0.05)


def test_motion_interpolates_keyframes():
    spec = {
        "name": "arm",
        "instances": [{"name": "base", "part": "base"}, {"name": "link", "part": "link"}],
        "joints": [
            {"name": "shoulder", "type": "revolute", "parent": "base", "child": "link", "axis": [0, 1, 0], "value": 0}
        ],
    }
    samples = expand_motion(
        spec,
        {"keyframes": [{"shoulder": 0}, {"shoulder": 10}], "samples": 3, "duration_s": 2},
    )
    assert [sample["joints"]["shoulder"] for sample in samples] == pytest.approx([0, 5, 10])
    assert samples[-1]["time_s"] == pytest.approx(2)
    assert samples[0]["duration_assumed"] is False


def test_prismatic_sweep_reports_when_the_slider_hits(tmp_path):
    document = Document(tmp_path)
    _box(document, "block", [10, 10, 10], [0, 0, 0])
    document.create_assembly(
        {
            "name": "press",
            "instances": [
                {"name": "base", "part": "block"},
                {"name": "slider", "part": "block"},
                {"name": "wall", "part": "block"},
            ],
            "joints": [
                {
                    "name": "slide",
                    "type": "prismatic",
                    "parent": "base",
                    "child": "slider",
                    "parent_anchor": [20, 0, 0],
                    "axis": [1, 0, 0],
                    "value": 0,
                },
                {
                    "name": "mount",
                    "type": "fixed",
                    "parent": "base",
                    "child": "wall",
                    "parent_anchor": [40, 0, 0],
                },
            ],
            "motions": {"advance": {"sweep": {"joint": "slide", "start": 0, "end": 25, "frames": 6}, "duration_s": 1}},
        }
    )
    report = document.run_motion("press", sequence="advance", render=False)
    assert report["frames"][0]["collisions"] == []
    assert report["status"] == "collision"
    assert report["colliding_frames"]
    hit = next(frame for frame in report["frames"] if frame["collisions"])
    pair = {hit["collisions"][0]["a"], hit["collisions"][0]["b"]}
    assert pair == {"slider", "wall"}
    assert hit["collisions"][0]["at_mm"] is not None
    assert hit["index"] > 0


def test_sharp_cable_fails_bend_and_a_route_through_a_solid_pinches(tmp_path):
    document = Document(tmp_path)
    _box(document, "block", [20, 20, 20], [0, 0, 0])
    document.create_assembly(
        {
            "name": "routed",
            "instances": [{"name": "base", "part": "block"}, {"name": "post", "part": "block"}],
            "joints": [
                {
                    "name": "post",
                    "type": "fixed",
                    "parent": "base",
                    "child": "post",
                    "parent_anchor": [0, 0, 40],
                }
            ],
            "cables": [
                {
                    "name": "kink",
                    "kind": "cable",
                    "diameter_mm": 4,
                    "min_bend_radius_mm": 20,
                    "waypoints": [
                        {"instance": "base", "at": [0, 40, 0], "clip": True},
                        {"instance": "base", "at": [5, 40, 0], "clip": True},
                        {"instance": "base", "at": [5, 45, 0], "clip": True},
                    ],
                },
                {
                    "name": "through",
                    "kind": "hose",
                    "diameter_mm": 6,
                    "min_bend_radius_mm": 1,
                    "waypoints": [
                        {"instance": "post", "at": [-30, 0, 0], "clip": True},
                        {"instance": "post", "at": [30, 0, 0], "clip": True},
                    ],
                },
            ],
        }
    )
    report = document.check_cables("routed")
    by_name = {cable["name"]: cable for cable in report["cables"]}
    assert by_name["kink"]["status"] == "fail"
    assert by_name["kink"]["min_bend_radius_mm"] < 20
    assert by_name["through"]["status"] == "fail"
    assert by_name["through"]["pinch_count"] >= 1
    assert report["assumptions"]


def test_a_crossing_route_is_a_self_pinch(tmp_path):
    document = Document(tmp_path)
    _box(document, "block", [10, 10, 10], [0, 0, 40])
    document.create_assembly(
        {
            "name": "routed",
            "instances": [{"name": "base", "part": "block"}],
            "joints": [],
            "cables": [
                {
                    "name": "cross",
                    "kind": "cable",
                    "diameter_mm": 6,
                    "min_bend_radius_mm": 20,
                    "waypoints": [
                        {"instance": "base", "at": [0, 0, 0], "clip": True},
                        {"instance": "base", "at": [70, 0, 0], "clip": True},
                        {"instance": "base", "at": [35, -35, 0], "clip": True},
                        {"instance": "base", "at": [35, 35, 0], "clip": True},
                    ],
                },
                {
                    "name": "parallel",
                    "kind": "cable",
                    "diameter_mm": 6,
                    "min_bend_radius_mm": 20,
                    "waypoints": [
                        {"instance": "base", "at": [0, 0, 0], "clip": True},
                        {"instance": "base", "at": [70, 0, 0], "clip": True},
                        {"instance": "base", "at": [70, 20, 0], "clip": True},
                        {"instance": "base", "at": [0, 20, 0], "clip": True},
                    ],
                },
            ],
        }
    )
    by_name = {cable["name"]: cable for cable in document.check_cables("routed")["cables"]}
    cross = by_name["cross"]["frames"][0]["self_pinch"]
    assert cross
    assert cross[0]["gap_mm"] == pytest.approx(0.0, abs=0.001)
    assert by_name["cross"]["status"] == "fail"
    assert by_name["parallel"]["frames"][0]["self_pinch"] == []
    assert by_name["parallel"]["status"] == "pass"


def test_a_repeated_waypoint_is_not_a_sharp_bend(tmp_path):
    document = Document(tmp_path)
    _box(document, "block", [10, 10, 10], [0, 0, 40])
    document.create_assembly(
        {
            "name": "routed",
            "instances": [{"name": "base", "part": "block"}],
            "joints": [],
            "cables": [
                {
                    "name": "repeat",
                    "kind": "cable",
                    "diameter_mm": 4,
                    "min_bend_radius_mm": 20,
                    "waypoints": [
                        {"instance": "base", "at": [0, 0, 0], "clip": True},
                        {"instance": "base", "at": [0, 0, 0], "clip": True},
                        {"instance": "base", "at": [100, 0, 0], "clip": True},
                    ],
                }
            ],
        }
    )
    cable = document.check_cables("routed")["cables"][0]
    assert cable["status"] == "pass"
    assert cable["min_bend_radius_mm"] is None
    assert cable["frames"][0]["bend_ok"] is True
    assert cable["frames"][0]["bends"][0]["ok"] is True
    assert cable["frames"][0]["bends"][0]["radius_mm"] is None


def test_holding_torque_and_hydraulic_pressure(tmp_path):
    document = Document(tmp_path)
    _box(document, "base", [20, 20, 20], [0, 0, 0])
    _box(document, "arm", [20, 20, 20], [50, 0, 0])
    document.create_assembly(
        {
            "name": "hoist",
            "instances": [
                {"name": "base", "part": "base", "material": "steel"},
                {"name": "arm", "part": "arm", "material": "steel", "payload_kg": 10, "payload_at": [100, 0, 0]},
            ],
            "joints": [
                {
                    "name": "shoulder",
                    "type": "revolute",
                    "parent": "base",
                    "child": "arm",
                    "axis": [0, 1, 0],
                    "value": 0,
                }
            ],
            "actuators": [
                {
                    "name": "cyl",
                    "kind": "hydraulic_cylinder",
                    "joint": "shoulder",
                    "bore_mm": 20,
                    "rod_mm": 10,
                    "moment_arm_mm": 50,
                    "efficiency": 1,
                }
            ],
        }
    )
    report = document.estimate_power("hoist")
    torque = report["frames"][0]["joints"]["shoulder"]["torque_nm"]
    # 10 kg at 100 mm, plus the arm's own mass at 50 mm, both about +Y.
    assert torque == pytest.approx(10 * 9.81 * 0.1, abs=0.2)
    assert torque > 0
    pressure = report["actuators"][0]["peak_pressure_mpa"]
    force = torque * 1000 / 50
    area = math.pi / 4 * 20**2
    assert pressure == pytest.approx(force / area, abs=0.05)
    assert 0.4 < pressure < 2
    assert any("quasi-static" in line.lower() or "Quasi-static" in line for line in report["assumptions"])


def test_a_motion_study_strokes_the_hydraulic_cylinder(tmp_path):
    document = Document(tmp_path)
    _box(document, "frame", [40, 40, 20], [0, 0, 10])
    _box(document, "ram", [30, 30, 40], [0, 0, 20])
    spec = {
        "name": "press",
        "instances": [
            {"name": "frame", "part": "frame", "material": "steel"},
            {"name": "ram", "part": "ram", "material": "steel", "payload_kg": 50, "payload_at": [0, 0, 40]},
        ],
        "joints": [
            {
                "name": "stroke",
                "type": "prismatic",
                "parent": "frame",
                "child": "ram",
                "axis": [0, 0, 1],
                "value": 0,
            }
        ],
        "actuators": [
            {
                "name": "pump",
                "kind": "hydraulic_cylinder",
                "joint": "stroke",
                "bore_mm": 40,
                "rod_mm": 20,
                "efficiency": 1,
            }
        ],
        "studies": {
            "lift": {
                "duration_s": 2,
                "steps": 2,
                "drivers": [
                    {
                        "joint": "stroke",
                        "keyframes": [{"time_s": 0, "value": 0}, {"time_s": 2, "value": 80}],
                    }
                ],
            }
        },
    }
    document.create_assembly(spec)
    held = document.estimate_power("press")
    assert held["duration_s"] == pytest.approx(1)
    assert held["pump"]["displaced_volume_mm3"] == pytest.approx(0)

    report = document.estimate_power("press", sequence="lift")
    weight = (36000 * 7.85e-6 + 50) * 9.81
    area = math.pi / 4 * 40**2
    actuator = report["actuators"][0]
    assert report["study"] == "lift"
    assert report["duration_s"] == pytest.approx(2)
    assert [frame["joints"]["stroke"]["value"] for frame in report["frames"]] == pytest.approx([0, 80])
    assert actuator["peak_force_n"] == pytest.approx(weight, abs=0.05)
    assert actuator["peak_pressure_mpa"] == pytest.approx(weight / area, abs=1e-4)
    assert actuator["displaced_volume_mm3"] == pytest.approx(area * 80, rel=1e-4)
    assert report["pump"]["displaced_volume_mm3"] == pytest.approx(area * 80, rel=1e-4)

    workspace = Workspace(tmp_path / "app")
    _box(workspace.document, "frame", [40, 40, 20], [0, 0, 10])
    _box(workspace.document, "ram", [30, 30, 40], [0, 0, 20])
    workspace.execute("create_assembly", {"spec": spec})
    panel = workspace.execute("estimate_power", {"name": "press", "sequence": "lift"})
    assert panel["pump"]["displaced_volume_mm3"] == pytest.approx(area * 80, rel=1e-4)

    stored = document.get_assembly("press")["assembly"]
    stored["motions"] = {"lift": {"keyframes": [{"stroke": 0}, {"stroke": 10}], "samples": 2, "duration_s": 1}}
    document.update_assembly(stored)
    with pytest.raises(GeometryError, match="both a motion and a motion study"):
        document.estimate_power("press", sequence="lift")


def test_electric_current_from_joint_torque(tmp_path):
    document = Document(tmp_path)
    _box(document, "base", [10, 10, 10], [0, 0, 0])
    _box(document, "arm", [10, 10, 10], [0, 0, 0])
    document.create_assembly(
        {
            "name": "motor",
            "instances": [
                {"name": "base", "part": "base"},
                {"name": "arm", "part": "arm", "payload_kg": 2, "payload_at": [200, 0, 0]},
            ],
            "joints": [
                {"name": "shoulder", "type": "revolute", "parent": "base", "child": "arm", "axis": [0, 1, 0], "value": 0}
            ],
            "actuators": [
                {
                    "kind": "electric",
                    "joint": "shoulder",
                    "gear_ratio": 10,
                    "efficiency": 1,
                    "voltage": 24,
                    "torque_constant_nm_per_a": 0.05,
                }
            ],
            "motions": {
                "nod": {
                    "keyframes": [{"shoulder": 0}, {"shoulder": 30}],
                    "samples": 2,
                    "duration_s": 2,
                }
            },
        }
    )
    report = document.estimate_power("motor", sequence="nod")
    actuator = report["actuators"][0]
    assert actuator["peak_current_a"] > 0
    assert actuator["peak_power_w"] > 0
    assert report["battery"]["voltage"] == 24
    assert report["battery"]["energy_wh"] > 0


def _geared_holding(document: Document, name: str, payloads: dict[str, float], relations: list[dict]) -> dict:
    _box(document, "base", [10, 10, 10], [0, 0, 0])
    _box(document, "link", [10, 10, 10], [0, 0, 0])
    instances: list[dict] = [{"name": "base", "part": "base", "material": "aluminum"}]
    joints = []
    for joint, payload in payloads.items():
        instances.append(
            {
                "name": f"c_{joint}",
                "part": "link",
                "material": "aluminum",
                "payload_kg": payload,
                "payload_at": [100, 0, 0],
            }
        )
        joints.append(
            {"name": joint, "type": "revolute", "parent": "base", "child": f"c_{joint}", "axis": [0, 1, 0]}
        )
    document.create_assembly(
        {
            "name": name,
            "instances": instances,
            "joints": joints,
            "relations": relations,
            "actuators": [
                {
                    "name": "motor",
                    "kind": "electric",
                    "joint": "drive",
                    "gear_ratio": 1,
                    "torque_constant_nm_per_a": 1,
                    "efficiency": 1,
                }
            ],
        }
    )
    report = document.estimate_power(name)
    return report["frames"][0]["joints"]


def test_a_reversing_gear_cancels_equal_arm_moments(tmp_path):
    """10 kg at 100 mm on each arm. Ratio −1, so the moments cancel on the driver."""
    moment = 10 * 9.81 * 0.1
    joints = _geared_holding(
        Document(tmp_path),
        "rev",
        {"drive": 10, "last": 10},
        [{"type": "gear", "driver": "drive", "driven": "last", "ratio": -1}],
    )
    assert joints["drive"]["torque_nm"] == pytest.approx(0, abs=1e-3)
    assert joints["last"]["torque_nm"] == pytest.approx(moment, abs=1e-3)


def test_a_same_direction_gear_adds_the_arm_moments(tmp_path):
    moment = 10 * 9.81 * 0.1
    joints = _geared_holding(
        Document(tmp_path),
        "belt",
        {"drive": 10, "last": 10},
        [{"type": "gear", "driver": "drive", "driven": "last", "ratio": 1}],
    )
    assert joints["drive"]["torque_nm"] == pytest.approx(2 * moment, abs=1e-3)


def test_a_reduction_scales_the_driven_moment(tmp_path):
    driven = 4 * 9.81 * 0.1
    driver = 10 * 9.81 * 0.1
    joints = _geared_holding(
        Document(tmp_path),
        "red",
        {"drive": 10, "last": 4},
        [{"type": "gear", "driver": "drive", "driven": "last", "ratio": -0.5}],
    )
    assert joints["drive"]["torque_nm"] == pytest.approx(driver + (-0.5) * driven, abs=1e-3)


def test_a_two_stage_gear_chain_does_not_depend_on_relation_order(tmp_path):
    """Ratio −1 then −1 is an overall ratio of +1. Each arm moment is 9.81 N·m."""
    moment = 10 * 9.81 * 0.1
    orders = (
        ("input_first", [
            {"type": "gear", "driver": "drive", "driven": "mid", "ratio": -1},
            {"type": "gear", "driver": "mid", "driven": "last", "ratio": -1},
        ]),
        ("downstream_first", [
            {"type": "gear", "driver": "mid", "driven": "last", "ratio": -1},
            {"type": "gear", "driver": "drive", "driven": "mid", "ratio": -1},
        ]),
    )
    for name, relations in orders:
        joints = _geared_holding(
            Document(tmp_path / name),
            name,
            {"drive": 10, "mid": 10, "last": 10},
            relations,
        )
        assert joints["last"]["torque_nm"] == pytest.approx(moment, abs=1e-3)
        assert joints["mid"]["torque_nm"] == pytest.approx(0, abs=1e-3)
        assert joints["drive"]["torque_nm"] == pytest.approx(moment, abs=1e-3)


def test_shared_and_mixed_relations_add_the_driven_loads():
    from aicad.kinematics import normalize
    from aicad.power import _instant_ratio, reflect

    def row(name, kind, torque, force):
        return {
            "joint": name,
            "type": kind,
            "value": 0,
            "slide": 0,
            "torque_nm": torque,
            "force_n": force,
            "supported": [],
        }

    def apply(loads, relations, actuators):
        solved = {"joints": [{"name": item["joint"], "value": 0, "slide": 0} for item in loads]}
        spec = {"relations": relations}
        return {item["joint"]: item for item in reflect(loads, solved, spec, actuators)}

    shared = apply(
        [row("drive", "revolute", 5, 0), row("left", "revolute", 3, 0), row("right", "revolute", 4, 0)],
        [
            {"type": "gear", "driver": "drive", "driven": "left", "ratio": -1},
            {"type": "gear", "driver": "drive", "driven": "right", "ratio": 2},
        ],
        [{"joint": "drive"}],
    )
    assert shared["drive"]["torque_nm"] == pytest.approx(5 + (-1) * 3 + 2 * 4, abs=1e-4)

    belt = apply(
        [row("drive", "prismatic", 0, 10), row("last", "prismatic", 0, 4)],
        [{"type": "gear", "driver": "drive", "driven": "last", "ratio": 1}],
        [{"joint": "drive"}],
    )
    assert belt["drive"]["force_n"] == pytest.approx(14, abs=1e-4)
    reverse = apply(
        [row("drive", "prismatic", 0, 10), row("last", "prismatic", 0, 4)],
        [{"type": "gear", "driver": "drive", "driven": "last", "ratio": -1}],
        [{"joint": "drive"}],
    )
    assert reverse["drive"]["force_n"] == pytest.approx(6, abs=1e-4)

    rack = apply(
        [row("drive", "revolute", 3, 0), row("last", "prismatic", 0, 20)],
        [{"type": "gear", "driver": "drive", "driven": "last", "ratio": 2}],
        [{"joint": "drive"}],
    )
    expected_torque = 3 - 20 * 2 / 1000.0 * (180.0 / math.pi)
    assert rack["drive"]["torque_nm"] == pytest.approx(expected_torque, abs=1e-4)

    pinion = apply(
        [row("drive", "prismatic", 0, 10), row("last", "revolute", 2, 0)],
        [{"type": "gear", "driver": "drive", "driven": "last", "ratio": 0.5}],
        [{"joint": "drive"}],
    )
    expected_force = 10 - 2 * 0.5 * (math.pi / 180.0) * 1000.0
    assert pinion["drive"]["force_n"] == pytest.approx(expected_force, abs=1e-4)

    held = apply(
        [row("drive", "revolute", 5, 0), row("last", "revolute", 9, 0)],
        [{"type": "gear", "driver": "drive", "driven": "last", "ratio": -1}],
        [{"joint": "drive"}, {"joint": "last"}],
    )
    assert held["drive"]["torque_nm"] == pytest.approx(5, abs=1e-4)

    loop = apply(
        [row("drive", "revolute", 4, 0), row("last", "revolute", 6, 0)],
        [
            {"type": "gear", "driver": "drive", "driven": "last", "ratio": -1},
            {"type": "gear", "driver": "last", "driven": "drive", "ratio": -1},
        ],
        [],
    )
    assert loop["drive"]["torque_nm"] == pytest.approx(4, abs=1e-4)
    assert loop["last"]["torque_nm"] == pytest.approx(6, abs=1e-4)

    linkage = {
        "name": "bar",
        "instances": [
            {"name": "ground", "part": "ground"},
            {"name": "crank", "part": "link"},
            {"name": "rocker", "part": "link"},
        ],
        "joints": [
            {"name": "crank", "type": "revolute", "parent": "ground", "child": "crank", "axis": [0, 0, 1]},
            {"name": "rocker", "type": "revolute", "parent": "ground", "child": "rocker", "axis": [0, 0, 1]},
        ],
        "relations": [
            {
                "name": "loop",
                "type": "linkage",
                "driver": "crank",
                "driven": "rocker",
                "ground_mm": 100,
                "crank_mm": 40,
                "coupler_mm": 80,
                "rocker_mm": 60,
                "branch": "open",
            }
        ],
    }
    spec = normalize(linkage)
    solved = solve(spec)
    ratio = _instant_ratio(spec["relations"][0], solved, spec)
    reflected = {
        item["joint"]: item
        for item in reflect(
            [row("crank", "revolute", 4, 0), row("rocker", "revolute", 6, 0)],
            solved,
            spec,
            [{"joint": "crank"}],
        )
    }
    assert ratio != 0
    assert reflected["crank"]["torque_nm"] == pytest.approx(4 + ratio * 6, abs=1e-4)


def test_workspace_undo_restores_a_joint(tmp_path):
    workspace = Workspace(tmp_path)
    _box(workspace.document, "base", [10, 10, 10], [0, 0, 0])
    _box(workspace.document, "link", [10, 10, 10], [0, 0, 0])
    workspace.execute(
        "create_assembly",
        {
            "spec": {
                "name": "hinge",
                "instances": [{"name": "base", "part": "base"}, {"name": "link", "part": "link"}],
                "joints": [
                    {
                        "name": "hinge",
                        "type": "revolute",
                        "parent": "base",
                        "child": "link",
                        "axis": [0, 0, 1],
                        "value": 0,
                    }
                ],
            }
        },
    )
    workspace.execute("set_joint", {"name": "hinge", "joint": "hinge", "value": 25})
    assert workspace.document.get_assembly("hinge")["solved"]["joints"][0]["value"] == pytest.approx(25)
    workspace.execute("undo")
    assert workspace.document.get_assembly("hinge")["solved"]["joints"][0]["value"] == pytest.approx(0)
    reloaded = Document(tmp_path)
    assert reloaded.get_assembly("hinge")["assembly"]["joints"][0]["value"] == pytest.approx(0)


def test_rod_arm_lifts_and_reports_cables_and_power(tmp_path):
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "examples" / "rod_arm.py"
    spec = importlib.util.spec_from_file_location("rod_arm", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    document = Document(tmp_path)
    module.assemble(document)
    document.set_pose("rod_arm", "reach")
    low = next(item["origin_mm"][2] for item in document.get_assembly("rod_arm")["solved"]["instances"] if item["name"] == "rod")
    document.set_pose("rod_arm", "lift")
    high = next(item["origin_mm"][2] for item in document.get_assembly("rod_arm")["solved"]["instances"] if item["name"] == "rod")
    assert high > low + 50
    jaws = {joint["name"]: joint["value"] for joint in document.get_assembly("rod_arm")["solved"]["joints"]}
    assert jaws["jaw_r"] == pytest.approx(-jaws["jaw"])
    motion = document.run_motion("rod_arm", sequence="lift", render=False)
    assert motion["frames"][-1]["origins_mm"]["rod"][2] > motion["frames"][0]["origins_mm"]["rod"][2]
    cables = document.check_cables("rod_arm", sequence="lift")
    assert cables["status"] == "pass"
    assert all("length_change_mm" in cable and cable["max_strain"] < cable["strain_limit"] for cable in cables["cables"])
    power = document.estimate_power("rod_arm", sequence="lift")
    shoulder = next(item for item in power["actuators"] if item["joint"] == "shoulder")
    assert shoulder["peak_pressure_mpa"] > 0.5
    assert shoulder["peak_torque_nm"] > 5
    assert power["pump"]["displaced_volume_mm3"] > 0
    walk = document.run_motion("test_leg", sequence="walk", render=False)
    assert walk["status"] == "collision"
    assert 0 in walk["colliding_frames"]


def _cylinder_spec(stroke_max: float) -> dict:
    return {
        "name": "cyl",
        "instances": [
            {"name": "base", "part": "base", "color": [10, 20, 30]},
            {"name": "arm", "part": "arm"},
            {"name": "barrel", "part": "barrel"},
            {"name": "rod", "part": "rod"},
        ],
        "joints": [
            {
                "name": "shoulder",
                "type": "revolute",
                "parent": "base",
                "child": "arm",
                "axis": [0, 1, 0],
                "value": 0,
            },
            {
                "name": "barrel",
                "type": "revolute",
                "parent": "base",
                "child": "barrel",
                "parent_anchor": [-50, 0, 0],
                "axis": [0, 1, 0],
                "value": 0,
            },
            {
                "name": "stroke",
                "type": "prismatic",
                "parent": "barrel",
                "child": "rod",
                "parent_anchor": [100, 0, 0],
                "axis": [1, 0, 0],
                "min": 0,
                "max": stroke_max,
            },
        ],
        "relations": [
            {
                "name": "lift",
                "type": "cylinder",
                "driver": "shoulder",
                "driven": "barrel",
                "stroke": "stroke",
                "moving_anchor": [100, 0, 0],
                "retracted_mm": 100,
            }
        ],
    }


def test_cylinder_relation_puts_the_rod_eye_on_the_clevis():
    solved = solve(_cylinder_spec(80), {"shoulder": 90})
    values = {joint["name"]: joint for joint in solved["joints"]}
    assert values["barrel"]["value"] == pytest.approx(63.435, abs=0.05)
    assert values["stroke"]["value"] == pytest.approx(11.803, abs=0.05)
    assert values["stroke"]["driven"] is True
    rod = next(instance for instance in solved["instances"] if instance["name"] == "rod")
    assert rod["origin_mm"] == pytest.approx([0.0, 0.0, -100.0], abs=0.05)
    relation = solved["relations"][0]
    assert relation["stroke_mm"] == pytest.approx(11.803, abs=0.05)


def _side_cylinder(y_offset: float) -> dict:
    return {
        "name": "cyl",
        "instances": [
            {"name": "base", "part": "base"},
            {"name": "arm", "part": "arm"},
            {"name": "barrel", "part": "barrel"},
            {"name": "rod", "part": "rod"},
        ],
        "joints": [
            {"name": "shoulder", "type": "revolute", "parent": "base", "child": "arm", "axis": [0, 1, 0]},
            {
                "name": "barrel",
                "type": "revolute",
                "parent": "base",
                "child": "barrel",
                "parent_anchor": [-50, y_offset, 40],
                "axis": [0, 1, 0],
            },
            {
                "name": "stroke",
                "type": "prismatic",
                "parent": "barrel",
                "child": "rod",
                "parent_anchor": [120, 0, 0],
                "axis": [1, 0, 0],
                "min": -80,
                "max": 400,
            },
        ],
        "relations": [
            {
                "name": "lift",
                "type": "cylinder",
                "driver": "shoulder",
                "driven": "barrel",
                "stroke": "stroke",
                "moving_anchor": [180, 0, 20],
                "retracted_mm": 120,
                "bore_axis": [1, 0, 0],
            }
        ],
    }


def test_a_side_offset_cylinder_uses_the_in_plane_length():
    """The barrel pin cannot tilt. The eye stops at the barrel plane and misses by the offset."""
    for angle in (0, 25, -20):
        solved = solve(_side_cylinder(30), {"shoulder": angle})
        theta = math.radians(angle)
        clevis = [
            math.cos(theta) * 180 + math.sin(theta) * 20,
            0.0,
            -math.sin(theta) * 180 + math.cos(theta) * 20,
        ]
        pin = [-50.0, 30.0, 40.0]
        span = math.dist(pin, clevis)
        offset = abs(clevis[1] - pin[1])
        in_plane = math.sqrt(span * span - offset * offset)
        stroke = next(joint for joint in solved["joints"] if joint["name"] == "stroke")
        eye = next(item["origin_mm"] for item in solved["instances"] if item["name"] == "rod")
        assert stroke["value"] == pytest.approx(in_plane - 120.0, abs=1e-3)
        assert math.dist(eye, clevis) == pytest.approx(offset, abs=0.02)
        assert any("in-plane length" in note for note in solved["warnings"])
    flat = solve(_side_cylinder(0), {"shoulder": 25})
    theta = math.radians(25)
    clevis = [
        math.cos(theta) * 180 + math.sin(theta) * 20,
        0.0,
        -math.sin(theta) * 180 + math.cos(theta) * 20,
    ]
    eye = next(item["origin_mm"] for item in flat["instances"] if item["name"] == "rod")
    assert math.dist(eye, [-50.0, 0.0, 40.0]) == pytest.approx(math.dist([-50.0, 0.0, 40.0], clevis), abs=0.02)
    assert math.dist(eye, clevis) == pytest.approx(0, abs=0.02)
    assert flat["warnings"] == []


def test_two_in_plane_cylinders_meet_both_clevises():
    spec = {
        "name": "ex",
        "instances": [
            {"name": "base", "part": "base"},
            {"name": "boom", "part": "boom"},
            {"name": "stick", "part": "stick"},
            {"name": "b_barrel", "part": "b_barrel"},
            {"name": "b_rod", "part": "b_rod"},
            {"name": "s_barrel", "part": "s_barrel"},
            {"name": "s_rod", "part": "s_rod"},
        ],
        "joints": [
            {"name": "boom", "type": "revolute", "parent": "base", "child": "boom", "axis": [0, 1, 0]},
            {
                "name": "stick",
                "type": "revolute",
                "parent": "boom",
                "child": "stick",
                "parent_anchor": [400, 0, 0],
                "axis": [0, 1, 0],
            },
            {
                "name": "boom_barrel",
                "type": "revolute",
                "parent": "base",
                "child": "b_barrel",
                "parent_anchor": [60, 0, 80],
                "axis": [0, 1, 0],
            },
            {
                "name": "boom_stroke",
                "type": "prismatic",
                "parent": "b_barrel",
                "child": "b_rod",
                "parent_anchor": [150, 0, 0],
                "axis": [1, 0, 0],
                "min": -100,
                "max": 400,
            },
            {
                "name": "stick_barrel",
                "type": "revolute",
                "parent": "boom",
                "child": "s_barrel",
                "parent_anchor": [220, 0, 70],
                "axis": [0, 1, 0],
            },
            {
                "name": "stick_stroke",
                "type": "prismatic",
                "parent": "s_barrel",
                "child": "s_rod",
                "parent_anchor": [140, 0, 0],
                "axis": [1, 0, 0],
                "min": -100,
                "max": 400,
            },
        ],
        "relations": [
            {
                "name": "boom_cyl",
                "type": "cylinder",
                "driver": "boom",
                "driven": "boom_barrel",
                "stroke": "boom_stroke",
                "moving_anchor": [160, 0, 30],
                "retracted_mm": 150,
            },
            {
                "name": "stick_cyl",
                "type": "cylinder",
                "driver": "stick",
                "driven": "stick_barrel",
                "stroke": "stick_stroke",
                "moving_anchor": [140, 0, -20],
                "retracted_mm": 140,
            },
        ],
    }

    def world_point(matrix, point):
        return [
            matrix[0][0] * point[0] + matrix[0][1] * point[1] + matrix[0][2] * point[2] + matrix[0][3],
            matrix[1][0] * point[0] + matrix[1][1] * point[1] + matrix[1][2] * point[2] + matrix[1][3],
            matrix[2][0] * point[0] + matrix[2][1] * point[1] + matrix[2][2] * point[2] + matrix[2][3],
        ]

    for boom, stick in ((0, 0), (20, -30), (35, 15), (-10, 40)):
        solved = solve(spec, {"boom": boom, "stick": stick})
        frames = {item["name"]: item for item in solved["instances"]}
        boom_clevis = world_point(frames["boom"]["matrix"], [160, 0, 30])
        stick_clevis = world_point(frames["stick"]["matrix"], [140, 0, -20])
        assert math.dist(frames["b_rod"]["origin_mm"], boom_clevis) == pytest.approx(0, abs=1e-3)
        assert math.dist(frames["s_rod"]["origin_mm"], stick_clevis) == pytest.approx(0, abs=1e-3)


def test_a_cylinder_on_a_cylindrical_joint_includes_the_slide():
    spec = _cylinder_spec(80)
    shoulder = spec["joints"][0]
    shoulder["type"] = "cylindrical"
    shoulder["axis"] = [0, 0, 1]
    parked = solve(spec)
    parked_values = {joint["name"]: joint for joint in parked["joints"]}
    assert parked_values["stroke"]["value"] == pytest.approx(50.0, abs=1e-3)
    assert parked_values["barrel"]["value"] == pytest.approx(0.0, abs=1e-3)
    slid = solve(spec, {"shoulder": {"value": 0, "slide": 50}})
    slid_values = {joint["name"]: joint for joint in slid["joints"]}
    length = math.hypot(150.0, 50.0)
    assert slid_values["stroke"]["value"] == pytest.approx(length - 100.0, abs=1e-3)
    assert slid_values["barrel"]["value"] == pytest.approx(math.degrees(math.atan2(-50.0, 150.0)), abs=1e-3)
    revolute = solve(_cylinder_spec(80), {"shoulder": {"value": 0, "slide": 50}})
    revolute_stroke = next(joint for joint in revolute["joints"] if joint["name"] == "stroke")
    assert revolute_stroke["value"] == pytest.approx(50.0, abs=1e-3)


def test_cylinder_power_ratio_uses_the_cylindrical_slide():
    from aicad.kinematics import normalize
    from aicad.power import _instant_ratio

    spec = _cylinder_spec(80)
    spec["joints"][0]["type"] = "cylindrical"
    spec["joints"][0]["axis"] = [0, 0, 1]
    cleaned = normalize(spec)
    relation = cleaned["relations"][0]
    solved = {"joints": [{"name": "shoulder", "value": 90.0, "slide": 0.0}]}
    flat = _instant_ratio(relation, solved, cleaned)
    solved["joints"][0]["slide"] = 50.0
    raised = _instant_ratio(relation, solved, cleaned)
    assert flat == pytest.approx(0.0, abs=1e-3)
    assert raised == pytest.approx(-1.0, abs=1e-2)


def test_a_stroke_actuator_carries_the_arm_moment(tmp_path):
    document = Document(tmp_path)
    _box(document, "base", [20, 20, 20], [0, 0, 0])
    _box(document, "arm", [20, 20, 20], [50, 0, 0])
    _box(document, "barrel", [40, 10, 10], [20, 0, 0])
    _box(document, "rod", [20, 6, 6], [10, 0, 0])
    spec = _cylinder_spec(80)
    for instance in spec["instances"]:
        instance["material"] = "steel"
    spec["instances"][1]["payload_kg"] = 10
    spec["instances"][1]["payload_at"] = [100, 0, 0]
    spec["joints"][0]["value"] = 30
    spec["actuators"] = [
        {
            "name": "lift",
            "kind": "hydraulic_cylinder",
            "joint": "stroke",
            "bore_mm": 25,
            "rod_mm": 12,
            "efficiency": 0.9,
        }
    ]
    document.create_assembly(spec)
    report = document.estimate_power("cyl")
    torque = report["frames"][0]["joints"]["shoulder"]["torque_nm"]
    actuator = report["actuators"][0]
    from aicad.kinematics import _cylinder
    from aicad.kinematics import normalize

    cleaned = normalize(document.get_assembly("cyl")["assembly"])
    relation = cleaned["relations"][0]
    joints = {item["name"]: item for item in cleaned["joints"]}
    driver = report["frames"][0]["joints"]["shoulder"]["value"]
    _angle, high, _offset = _cylinder(relation, joints, driver + 0.5, 0.0)
    _angle, low, _offset = _cylinder(relation, joints, driver - 0.5, 0.0)
    ratio = high - low
    expected = abs(torque * (math.pi / 180.0) * 1000.0 / ratio)
    assert actuator["peak_force_n"] == pytest.approx(expected, abs=1.0)
    assert actuator["peak_force_n"] > 20
    assert actuator["peak_pressure_mpa"] > 0.01
    assert any("stroke" in line for line in report["assumptions"])


def test_cylinder_stroke_clamps_at_the_joint_limit():
    solved = solve(_cylinder_spec(40))
    stroke = next(joint for joint in solved["joints"] if joint["name"] == "stroke")
    assert stroke["commanded"] == pytest.approx(50.0, abs=1e-3)
    assert stroke["value"] == pytest.approx(40.0, abs=1e-3)
    assert stroke["limit"] == "clamped"
    assert solved["warnings"]


def test_assembly_mesh_colors_each_part(tmp_path):
    document = Document(tmp_path)
    _box(document, "base", [10, 10, 10], [0, 0, 0])
    _box(document, "arm", [20, 10, 10], [10, 0, 0])
    document.create_assembly(
        {
            "name": "painted",
            "instances": [
                {"name": "base", "part": "base", "color": [10, 20, 30]},
                {"name": "arm", "part": "arm"},
            ],
            "joints": [
                {
                    "name": "hinge",
                    "type": "revolute",
                    "parent": "base",
                    "child": "arm",
                    "axis": [0, 1, 0],
                }
            ],
        }
    )
    mesh = document.assembly_mesh("painted")
    assert len(mesh["colors"]) == len(mesh["positions"])
    assert mesh["colors"][0:3] == [10.0, 20.0, 30.0]


def test_an_instance_color_can_be_written_as_hex(tmp_path):
    document = Document(tmp_path)
    _box(document, "base", [10, 10, 10], [0, 0, 0])
    _box(document, "arm", [20, 10, 10], [10, 0, 0])
    document.create_assembly(
        {
            "name": "painted",
            "instances": [
                {"name": "base", "part": "base", "color": "#0a141e"},
                {"name": "arm", "part": "arm", "color": "#fff"},
            ],
            "joints": [{"name": "glued", "type": "fixed", "parent": "base", "child": "arm"}],
        }
    )
    colors = {item["name"]: item["color"] for item in document.get_assembly("painted")["assembly"]["instances"]}
    assert colors == {"base": [10, 20, 30], "arm": [255, 255, 255]}
    mesh = document.assembly_mesh("painted")
    assert mesh["colors"][0:3] == [10.0, 20.0, 30.0]
    with pytest.raises(GeometryError, match="#rrggbb"):
        document.create_assembly(
            {"name": "smudged", "instances": [{"name": "base", "part": "base", "color": "#12345"}]}
        )


def test_a_child_must_meet_its_parent_and_the_viewport_uses_that_placement(tmp_path):
    from aicad.assembly import loose_mounts, posed_shapes

    document = Document(tmp_path)
    _box(document, "base", [20, 20, 20], [0, 0, 0])
    _box(document, "block", [10, 10, 10], [0, 0, 0])
    document.create_part_from_script(
        "tube",
        "part = feature.add('wall', Rot(0, -90, 0) * Cylinder(15, 40))\n"
        "part = feature.cut('bore', Rot(0, -90, 0) * Cylinder(8, 44))\n",
    )
    document.create_part_from_script(
        "shaft",
        "part = feature.add('shaft', Rot(0, -90, 0) * Cylinder(4, 24))\n",
    )
    shapes = {name: part.base for name, part in document._parts.items()}

    def placed(spec):
        solved, solids = posed_shapes(spec, shapes)
        return loose_mounts(solved, solids), solved, solids

    near, _solved, _solids = placed(
        {
            "name": "near",
            "instances": [{"name": "base", "part": "base"}, {"name": "block", "part": "block"}],
            "joints": [
                {
                    "name": "seat",
                    "type": "fixed",
                    "parent": "base",
                    "child": "block",
                    "parent_anchor": [15.5, 0, 0],
                }
            ],
        }
    )
    assert near == []
    far, _solved, _solids = placed(
        {
            "name": "far",
            "instances": [{"name": "base", "part": "base"}, {"name": "block", "part": "block"}],
            "joints": [
                {
                    "name": "seat",
                    "type": "fixed",
                    "parent": "base",
                    "child": "block",
                    "parent_anchor": [80, 0, 0],
                }
            ],
        }
    )
    assert [row["child"] for row in far] == ["block"]
    assert far[0]["gap_mm"] > 20
    engaged, _solved, _solids = placed(
        {
            "name": "bore",
            "instances": [{"name": "tube", "part": "tube"}, {"name": "shaft", "part": "shaft"}],
            "joints": [{"name": "slide", "type": "fixed", "parent": "tube", "child": "shaft"}],
        }
    )
    assert engaged == []

    document.create_assembly(
        {
            "name": "near",
            "instances": [{"name": "base", "part": "base"}, {"name": "block", "part": "block"}],
            "joints": [
                {
                    "name": "seat",
                    "type": "fixed",
                    "parent": "base",
                    "child": "block",
                    "parent_anchor": [15.5, 0, 0],
                }
            ],
        }
    )
    mesh = document.assembly_mesh("near")
    index = next(face["index"] for face in mesh["faces"] if face["ref"] == "instance/block")
    xs = [
        mesh["positions"][tri * 9 + corner * 3]
        for tri, face in enumerate(mesh["triangle_face"])
        if face == index
        for corner in range(3)
    ]
    assert max(xs) == pytest.approx(20.5, abs=0.5)
    assert min(xs) == pytest.approx(10.5, abs=0.5)


def test_rod_handler_parts_stay_on_their_parents_at_every_pose(tmp_path):
    import importlib.util

    from aicad.assembly import loose_mounts, posed_shapes

    path = Path(__file__).resolve().parents[1] / "examples" / "rod_handler.py"
    spec = importlib.util.spec_from_file_location("rod_handler", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    document = Document(tmp_path)
    built = module._build(document)
    document.create_assembly(built["spec"])
    shapes = {name: part.base for name, part in document._parts.items()}
    for pose in built["spec"]["poses"]:
        document.set_pose("rod_handler", pose)
        packed = document.get_assembly("rod_handler")
        solved, solids = posed_shapes(packed["assembly"], shapes)
        loose = loose_mounts(solved, solids)
        assert loose == [], pose
        mesh = document.assembly_mesh("rod_handler")
        jaw_index = next(face["index"] for face in mesh["faces"] if face["ref"] == "instance/jaw")
        mesh_z = [
            mesh["positions"][tri * 9 + corner * 3 + 2]
            for tri, face in enumerate(mesh["triangle_face"])
            if face == jaw_index
            for corner in range(3)
        ]
        box = solids["jaw"].impl.bounding_box()
        assert min(mesh_z) == pytest.approx(box.min.Z, abs=0.6)
        assert max(mesh_z) == pytest.approx(box.max.Z, abs=0.6)


def test_fixed_actuator_is_rejected(tmp_path):
    document = Document(tmp_path)
    _box(document, "base", [10, 10, 10], [0, 0, 0])
    _box(document, "link", [10, 10, 10], [0, 0, 0])
    with pytest.raises(GeometryError, match="fixed"):
        document.create_assembly(
            {
                "name": "bad",
                "instances": [{"name": "base", "part": "base"}, {"name": "link", "part": "link"}],
                "joints": [{"name": "weld", "type": "fixed", "parent": "base", "child": "link"}],
                "actuators": [{"kind": "electric", "joint": "weld", "torque_constant_nm_per_a": 0.1}],
            }
        )


def test_prismatic_limit_is_millimetres_and_a_hinge_can_pass_a_full_turn(tmp_path):
    document = Document(tmp_path)
    _box(document, "base", [10, 10, 10], [0, 0, 0])
    _box(document, "block", [10, 10, 10], [0, 0, 0])
    document.create_assembly(
        {
            "name": "ram",
            "instances": [{"name": "base", "part": "base"}, {"name": "block", "part": "block"}],
            "joints": [
                {
                    "name": "travel",
                    "type": "prismatic",
                    "parent": "base",
                    "child": "block",
                    "axis": [0, 0, 1],
                    "value": 0,
                }
            ],
        }
    )
    with pytest.raises(GeometryError, match="mm") as caught:
        document.set_joint("ram", "travel", 1_000_001)
    message = str(caught.value)
    assert "deg" not in message
    assert "1000001" in message
    document.create_assembly(
        {
            "name": "hinge",
            "instances": [{"name": "base", "part": "base"}, {"name": "block", "part": "block"}],
            "joints": [
                {
                    "name": "spin",
                    "type": "revolute",
                    "parent": "base",
                    "child": "block",
                    "axis": [0, 0, 1],
                    "value": 0,
                }
            ],
        }
    )
    moved = document.set_joint("hinge", "spin", 1_000_001)
    stored = next(joint for joint in moved["assembly"]["joints"] if joint["name"] == "spin")
    assert stored["value"] == pytest.approx(1_000_001)


def _gear_chain(count: int, reverse: bool) -> dict:
    instances = [{"name": "ground", "part": "ground"}]
    joints = []
    for index in range(count + 1):
        instances.append({"name": f"p{index}", "part": "part"})
        joints.append(
            {
                "name": f"j{index}",
                "type": "revolute",
                "parent": "ground",
                "child": f"p{index}",
                "axis": [0, 0, 1],
            }
        )
    relations = [
        {"name": f"r{index}", "type": "gear", "driver": f"j{index}", "driven": f"j{index + 1}", "ratio": -2}
        for index in range(count)
    ]
    if reverse:
        relations.reverse()
    return {"name": "chain", "instances": instances, "joints": joints, "relations": relations}


def test_a_reversed_gear_chain_reaches_the_last_shaft():
    """Nine meshes listed from the output back still turn the last shaft."""
    solved = solve(_gear_chain(9, True), {"j0": 1})
    values = {joint["name"]: joint["value"] for joint in solved["joints"]}
    assert values["j9"] == pytest.approx((-2) ** 9)
    assert solved["warnings"] == []


def test_a_clamped_idler_limits_the_next_gear():
    spec = _gear_chain(2, True)
    for joint in spec["joints"]:
        if joint["name"] == "j1":
            joint["min"] = -5
            joint["max"] = 5
    for relation in spec["relations"]:
        if relation["driven"] == "j2":
            relation["ratio"] = 3
    solved = solve(spec, {"j0": 10})
    values = {joint["name"]: joint for joint in solved["joints"]}
    assert values["j1"]["value"] == pytest.approx(-5)
    assert values["j2"]["value"] == pytest.approx(-15)


def test_two_relations_cannot_drive_one_joint():
    spec = {
        "name": "fight",
        "instances": [
            {"name": "g", "part": "g"},
            {"name": "a", "part": "a"},
            {"name": "b", "part": "b"},
            {"name": "c", "part": "c"},
        ],
        "joints": [
            {"name": "a", "type": "revolute", "parent": "g", "child": "a", "axis": [0, 0, 1]},
            {"name": "b", "type": "revolute", "parent": "g", "child": "b", "axis": [0, 0, 1]},
            {"name": "c", "type": "revolute", "parent": "g", "child": "c", "axis": [0, 0, 1]},
        ],
        "relations": [
            {"name": "ab", "type": "gear", "driver": "a", "driven": "b", "ratio": -2},
            {"name": "cb", "type": "gear", "driver": "c", "driven": "b", "ratio": -3},
        ],
    }
    with pytest.raises(GeometryError, match="already drives"):
        solve(spec, {"a": 10, "c": 10})


def test_a_relation_cycle_is_refused():
    spec = {
        "name": "cycle",
        "instances": [
            {"name": "g", "part": "g"},
            {"name": "a", "part": "a"},
            {"name": "b", "part": "b"},
        ],
        "joints": [
            {"name": "a", "type": "revolute", "parent": "g", "child": "a", "axis": [0, 0, 1]},
            {"name": "b", "type": "revolute", "parent": "g", "child": "b", "axis": [0, 0, 1]},
        ],
        "relations": [
            {"name": "ab", "type": "gear", "driver": "a", "driven": "b", "ratio": -2},
            {"name": "ba", "type": "gear", "driver": "b", "driven": "a", "ratio": -3},
        ],
    }
    with pytest.raises(GeometryError, match="cycle"):
        solve(spec, {"a": 10})


def test_a_linkage_does_not_drive_a_slide():
    spec = {
        "name": "slidebar",
        "instances": [
            {"name": "g", "part": "g"},
            {"name": "c", "part": "c"},
            {"name": "r", "part": "r"},
        ],
        "joints": [
            {"name": "crank", "type": "revolute", "parent": "g", "child": "c", "axis": [0, 0, 1]},
            {"name": "rocker", "type": "prismatic", "parent": "g", "child": "r", "axis": [1, 0, 0]},
        ],
        "relations": [
            {
                "name": "loop",
                "type": "linkage",
                "driver": "crank",
                "driven": "rocker",
                "ground_mm": 80,
                "crank_mm": 40,
                "coupler_mm": 90,
                "rocker_mm": 70,
            }
        ],
    }
    with pytest.raises(GeometryError, match="prismatic"):
        solve(spec, {"crank": 90})


def test_a_gear_does_not_turn_a_fixed_joint():
    spec = {
        "name": "fixedgear",
        "instances": [
            {"name": "g", "part": "g"},
            {"name": "a", "part": "a"},
            {"name": "b", "part": "b"},
        ],
        "joints": [
            {"name": "a", "type": "revolute", "parent": "g", "child": "a", "axis": [0, 0, 1]},
            {"name": "b", "type": "fixed", "parent": "g", "child": "b", "parent_anchor": [40, 0, 0]},
        ],
        "relations": [{"name": "mesh", "type": "gear", "driver": "a", "driven": "b", "ratio": -2}],
    }
    with pytest.raises(GeometryError, match="fixed"):
        solve(spec, {"a": 30})


def test_a_rack_maps_degrees_to_millimetres():
    spec = {
        "name": "rack",
        "instances": [
            {"name": "g", "part": "g"},
            {"name": "pinion", "part": "pinion"},
            {"name": "rack", "part": "rack"},
        ],
        "joints": [
            {"name": "pinion", "type": "revolute", "parent": "g", "child": "pinion", "axis": [0, 1, 0]},
            {"name": "rack", "type": "prismatic", "parent": "g", "child": "rack", "axis": [1, 0, 0]},
        ],
        "relations": [{"name": "mesh", "type": "gear", "driver": "pinion", "driven": "rack", "ratio": -2}],
    }
    solved = solve(spec, {"pinion": 10})
    values = {joint["name"]: joint["value"] for joint in solved["joints"]}
    assert values["rack"] == pytest.approx(-20)


def test_an_assembly_that_says_inches_is_refused():
    spec = {
        "name": "slide",
        "units": "inch",
        "instances": [{"name": "block", "part": "block"}],
        "joints": [],
    }
    with pytest.raises(GeometryError, match="millimeters"):
        solve(spec)
    spec["units"] = "mm"
    solved = solve(spec)
    assert solved["instances"][0]["name"] == "block"


def test_a_repeated_part_is_meshed_once(tmp_path, monkeypatch):
    from aicad import kernel

    document = Document(tmp_path)
    _box(document, "base", [20, 20, 10], [0, 0, 5])
    _box(document, "bolt", [4, 4, 4], [0, 0, 2])
    document.create_assembly(
        {
            "name": "plate",
            "instances": [
                {"name": "base", "part": "base"},
                {"name": "bolt_a", "part": "bolt"},
                {"name": "bolt_b", "part": "bolt"},
            ],
            "joints": [
                {
                    "name": "seat_a",
                    "type": "fixed",
                    "parent": "base",
                    "child": "bolt_a",
                    "parent_anchor": [6, 0, 7],
                },
                {
                    "name": "seat_b",
                    "type": "fixed",
                    "parent": "base",
                    "child": "bolt_b",
                    "parent_anchor": [-6, 0, 7],
                },
            ],
        }
    )
    calls = []
    real = kernel.tessellate

    def counting(shape):
        calls.append(shape)
        return real(shape)

    monkeypatch.setattr(kernel, "tessellate", counting)
    mesh = document.assembly_mesh("plate")
    assert len(calls) == 2
    assert {face["ref"] for face in mesh["faces"]} == {
        "instance/base",
        "instance/bolt_a",
        "instance/bolt_b",
    }

    def xs(ref):
        index = next(face["index"] for face in mesh["faces"] if face["ref"] == ref)
        return [
            mesh["positions"][tri * 9 + corner * 3]
            for tri, face in enumerate(mesh["triangle_face"])
            if face == index
            for corner in range(3)
        ]

    assert max(xs("instance/bolt_a")) > max(xs("instance/bolt_b"))
    document.assembly_mesh("plate")
    assert len(calls) == 2


def test_a_motion_hit_uses_one_boolean(monkeypatch):
    from aicad import kernel
    from aicad.assembly import collisions

    left = kernel.make_from_ops([{"op": "add", "shape": "box", "size": [10, 10, 10], "center": [0, 0, 0]}])
    right = kernel.make_from_ops([{"op": "add", "shape": "box", "size": [10, 10, 10], "center": [5, 0, 0]}])

    def boom(*_args, **_kwargs):
        raise AssertionError("the hit asked for a second boolean")

    monkeypatch.setattr(kernel, "clearance_between", boom)
    hits = collisions({"joints": []}, {"left": left, "right": right})
    assert len(hits) == 1
    assert hits[0]["interference_volume_mm3"] == pytest.approx(500, abs=1e-3)
    assert hits[0]["gap_mm"] == 0
    assert hits[0]["at_mm"] is not None


def test_separated_boxes_keep_the_surface_gap():
    from aicad import kernel

    left = kernel.make_from_ops([{"op": "add", "shape": "box", "size": [10, 10, 10], "center": [0, 0, 0]}])
    right = kernel.make_from_ops([{"op": "add", "shape": "box", "size": [10, 10, 10], "center": [30, 0, 0]}])
    overlap, gap, *_rest = kernel.clearance_between(left, right)
    assert overlap == 0
    assert gap == pytest.approx(20)
