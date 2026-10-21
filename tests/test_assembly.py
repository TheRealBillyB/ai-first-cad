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
