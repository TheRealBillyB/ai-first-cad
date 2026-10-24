"""Tip error is the positional Jacobian times each stated bound.

The two-link arm is short enough to differentiate by hand. Link 1 is 100 mm.
The tool sits 80 mm out on link 2. Both joints turn about Z. A column is
millimeters of tool motion per degree: axis × (tool − origin) × π/180.
"""

import json
import math
from pathlib import Path

import numpy as np
import pytest

from aicad.chain import solve_kinematics
from aicad.cli import main as cli_main
from aicad.document import Document
from aicad.error_budget import SCHEMA, _fit_rotation, _worst, check_position_error
from aicad.errors import GeometryError
from aicad.fea import fea_available
from aicad.gears.geometry import cylindrical_mount, gear_data, virtual_spur_at
from aicad.server import build_server
from aicad.workspace import Workspace

_AVAILABLE, _REASON = fea_available()
needs_solver = pytest.mark.skipif(not _AVAILABLE, reason=_REASON)

AT = [80.0, 0.0, 0.0]
_ABS = 1e-6


def _instance(name: str) -> dict:
    return {"name": name, "part": name}


def _revolute(name, parent, child, axis, parent_anchor=None) -> dict:
    joint = {
        "name": name,
        "type": "revolute",
        "parent": parent,
        "child": child,
        "axis": axis,
    }
    if parent_anchor is not None:
        joint["parent_anchor"] = parent_anchor
    return joint


def _arm() -> dict:
    return {
        "name": "arm",
        "instances": [_instance("base"), _instance("link1"), _instance("link2")],
        "joints": [
            _revolute("shoulder", "base", "link1", [0, 0, 1]),
            _revolute("elbow", "link1", "link2", [0, 0, 1], [100, 0, 0]),
        ],
        "poses": {
            "stretch": {"shoulder": 0, "elbow": 0},
            "bent": {"shoulder": 0, "elbow": 90},
        },
    }


def _request(tolerance: float, **extra) -> dict:
    request = {
        "tool": "link2",
        "at": AT,
        "tolerance_mm": tolerance,
        "poses": ["stretch", "bent"],
        "sources": {
            "shoulder": {"encoder": {"resolution_deg": 0.2}},
            "elbow": {"backlash": {"deg": 0.5}},
        },
    }
    request.update(extra)
    return request


def _pose(report: dict, name: str) -> dict:
    return next(item for item in report["poses"] if item["name"] == name)


def _by_id(pose: dict, ident: str) -> dict:
    return next(item for item in pose["contributions"] if item["id"] == ident)


def _signed_total(pose: dict) -> list[float]:
    signs = {item["id"]: item["sign"] for item in pose["signs"]}
    total = [0.0, 0.0, 0.0]
    for item in pose["contributions"]:
        if not item["used"]:
            continue
        sign = signs[item["id"]]
        for index in range(3):
            total[index] += sign * item["vector_mm"][index]
    return total


def _tip(spec, joints) -> list[float]:
    report = solve_kinematics(spec, None, {"tool": "link2", "at": AT, "joints": joints})
    return report["forward"]["position_mm"]


def _column_from_nudge(spec, joints, name: str, step: float = 0.001) -> list[float]:
    base = _tip(spec, joints)
    nudged = dict(joints)
    nudged[name] = joints[name] + step
    moved = _tip(spec, nudged)
    return [(moved[index] - base[index]) / step for index in range(3)]


def test_a_two_link_arm_matches_the_hand_solution_and_fails_between_rss_and_worst_case():
    spec = _arm()
    # Stretch, tool at [180, 0, 0]. Shoulder lever 180 mm, elbow lever 80 mm.
    shoulder_column = [0.0, math.pi, 0.0]
    elbow_column = [0.0, 80.0 * math.pi / 180.0, 0.0]
    shoulder_bound = 0.1
    elbow_bound = 0.5
    shoulder_error = shoulder_bound * math.pi
    elbow_error = elbow_bound * 80.0 * math.pi / 180.0
    stretch_worst = shoulder_error + elbow_error
    stretch_rss = math.hypot(shoulder_error, elbow_error)
    # Bent 90°, tool at [100, 80, 0]. Columns in mm per degree, then times the bounds.
    bent_shoulder = [component * shoulder_bound * math.pi / 180.0 for component in (-80.0, 100.0, 0.0)]
    bent_elbow = [component * elbow_bound * math.pi / 180.0 for component in (-80.0, 0.0, 0.0)]
    bent_worst = (math.pi / 180.0) * math.sqrt(48.0**2 + 10.0**2)

    report = check_position_error(spec, None, _request(0.9))
    assert report["schema"] == SCHEMA
    assert report["result_kind"] == "rough_estimate"
    assert report["status"] == "fail"
    assert report["worst_pose"] == "stretch"
    assert report["rss_peak_pose"] == "stretch"
    assert report["worst_case_mm"] == pytest.approx(stretch_worst, abs=_ABS)
    assert report["rss_mm"] == pytest.approx(stretch_rss, abs=_ABS)
    assert report["rss_mm"] < 0.9 < report["worst_case_mm"]
    assert "past" in report["summary"]
    assert "root-sum-square" in " ".join(report["limits"]).lower()
    assert "thermal" in " ".join(report["limits"]).lower()
    assert "offset" in " ".join(report["limits"]).lower()
    assert "jacobian" in " ".join(report["assumptions"]).lower()
    assert type(report["worst_case_mm"]) is float
    json.dumps(report)

    stretch = _pose(report, "stretch")
    assert stretch["status"] == "fail"
    assert stretch["position_mm"] == pytest.approx([180.0, 0.0, 0.0], abs=_ABS)
    assert stretch["rank"] == 1
    assert any("share one line" in note for note in stretch["notes"])
    shoulder = _by_id(stretch, "shoulder.encoder")
    elbow = _by_id(stretch, "elbow.backlash")
    assert shoulder["kind"] == "encoder"
    assert elbow["kind"] == "backlash"
    assert shoulder["bound"] == pytest.approx(shoulder_bound, abs=_ABS)
    assert elbow["bound"] == pytest.approx(elbow_bound, abs=_ABS)
    assert shoulder["column_mm"] == pytest.approx(shoulder_column, abs=_ABS)
    assert elbow["column_mm"] == pytest.approx(elbow_column, abs=_ABS)
    assert shoulder["vector_mm"] == pytest.approx([0.0, shoulder_error, 0.0], abs=_ABS)
    assert elbow["vector_mm"] == pytest.approx([0.0, elbow_error, 0.0], abs=_ABS)
    # Equal bounds would scale with the levers 180:80. These bounds scale 18:40.
    assert shoulder["magnitude_mm"] / elbow["magnitude_mm"] == pytest.approx(18.0 / 40.0, abs=_ABS)
    assert _signed_total(stretch) == pytest.approx(stretch["worst_case_vector_mm"], abs=_ABS)
    assert math.dist(_signed_total(stretch), [0.0, 0.0, 0.0]) == pytest.approx(stretch_worst, abs=_ABS)
    assert stretch["combination"] == "signs"
    finite_shoulder = _column_from_nudge(spec, {"shoulder": 0.0, "elbow": 0.0}, "shoulder")
    finite_elbow = _column_from_nudge(spec, {"shoulder": 0.0, "elbow": 0.0}, "elbow")
    assert finite_shoulder == pytest.approx(shoulder["column_mm"], abs=1e-4)
    assert finite_elbow == pytest.approx(elbow["column_mm"], abs=1e-4)

    bent = _pose(report, "bent")
    assert bent["position_mm"] == pytest.approx([100.0, 80.0, 0.0], abs=_ABS)
    assert bent["rank"] == 2
    assert not any("share one line" in note for note in bent["notes"])
    assert _by_id(bent, "shoulder.encoder")["vector_mm"] == pytest.approx(bent_shoulder, abs=_ABS)
    assert _by_id(bent, "elbow.backlash")["vector_mm"] == pytest.approx(bent_elbow, abs=_ABS)
    assert bent["worst_case_mm"] == pytest.approx(bent_worst, abs=_ABS)
    assert bent["worst_case_mm"] < stretch["worst_case_mm"]
    assert _signed_total(bent) == pytest.approx(bent["worst_case_vector_mm"], abs=_ABS)

    passing = check_position_error(spec, None, _request(2.0))
    assert passing["status"] == "pass"
    assert passing["worst_case_mm"] == pytest.approx(stretch_worst, abs=_ABS)
    assert "within" in passing["summary"]
    assert _pose(passing, "stretch")["status"] == "pass"


def test_joint_stiffness_and_encoder_counts_are_half_or_full_steps_on_the_same_column():
    spec = _arm()
    stiffness = check_position_error(
        spec,
        None,
        {
            "tool": "link2",
            "at": AT,
            "tolerance_mm": 1.0,
            "joints": {"shoulder": 0, "elbow": 0},
            "sources": {"shoulder": {"stiffness": {"nmm_per_rad": 1.0e6, "torque_nmm": 1000}}},
        },
    )
    # 1000 N·mm / 1e6 N·mm/rad = 0.001 rad. The 180 mm arm moves 0.18 mm.
    pose = stiffness["poses"][0]
    row = _by_id(pose, "shoulder.stiffness")
    assert row["bound"] == pytest.approx(math.degrees(0.001), abs=_ABS)
    assert row["vector_mm"] == pytest.approx([0.0, 0.18, 0.0], abs=_ABS)
    assert stiffness["worst_case_mm"] == pytest.approx(0.18, abs=_ABS)
    assert stiffness["status"] == "pass"

    counts = check_position_error(
        spec,
        None,
        {
            "tool": "link2",
            "at": AT,
            "tolerance_mm": 1.0,
            "joints": {"shoulder": 0, "elbow": 0},
            "sources": {"shoulder": {"encoder": {"counts": 3600, "ratio": 1}}},
        },
    )
    counted = _by_id(counts["poses"][0], "shoulder.encoder")
    assert counted["bound"] == pytest.approx(0.05, abs=_ABS)
    assert counted["magnitude_mm"] == pytest.approx(0.05 * math.pi, abs=_ABS)


def test_a_stated_link_screw_rotates_with_the_link():
    spec = _arm()
    screw = {"translation_mm": [0.0, 0.1, 0.0], "rotation_rad": [0.0, 0.0, 0.001]}
    straight = check_position_error(
        spec,
        None,
        {
            "tool": "link2",
            "at": AT,
            "tolerance_mm": 1.0,
            "joints": {"shoulder": 0, "elbow": 0},
            "links": {"link1": screw},
        },
    )
    # Probe at the elbow, [100, 0, 0]. Translation 0.1 mm plus 0.001 rad × 80 mm.
    straight_row = straight["poses"][0]["contributions"][0]
    assert straight_row["link"] == "link1"
    assert straight_row["origin_mm"] == pytest.approx([100.0, 0.0, 0.0], abs=_ABS)
    assert straight_row["vector_mm"] == pytest.approx([0.0, 0.18, 0.0], abs=_ABS)
    assert straight["status"] == "pass"

    turned = check_position_error(
        spec,
        None,
        {
            "tool": "link2",
            "at": AT,
            "tolerance_mm": 1.0,
            "joints": {"shoulder": 90, "elbow": 0},
            "links": {"link1": screw},
        },
    )
    # Link 1 is +90° about Z, so local +Y is world −X.
    assert turned["poses"][0]["position_mm"] == pytest.approx([0.0, 180.0, 0.0], abs=_ABS)
    assert turned["poses"][0]["contributions"][0]["vector_mm"] == pytest.approx([-0.18, 0.0, 0.0], abs=_ABS)


def test_a_prismatic_gap_and_a_cylindrical_pair_use_their_own_columns():
    slide = {
        "name": "slide",
        "instances": [_instance("base"), _instance("tip")],
        "joints": [
            {
                "name": "rail",
                "type": "prismatic",
                "parent": "base",
                "child": "tip",
                "axis": [1, 0, 0],
            }
        ],
    }
    report = check_position_error(
        slide,
        None,
        {
            "tool": "tip",
            "tolerance_mm": 1.0,
            "sources": {
                "rail": {
                    "encoder": {"resolution_mm": 0.02},
                    "backlash": {"mm": 0.2},
                }
            },
        },
    )
    pose = report["poses"][0]
    assert _by_id(pose, "rail.encoder")["vector_mm"] == pytest.approx([0.01, 0.0, 0.0], abs=_ABS)
    assert _by_id(pose, "rail.backlash")["vector_mm"] == pytest.approx([0.2, 0.0, 0.0], abs=_ABS)
    assert report["worst_case_mm"] == pytest.approx(0.21, abs=_ABS)
    assert report["rss_mm"] == pytest.approx(math.hypot(0.2, 0.01), abs=_ABS)

    barrel = {
        "name": "barrel",
        "instances": [_instance("base"), _instance("tip")],
        "joints": [
            {
                "name": "spin",
                "type": "cylindrical",
                "parent": "base",
                "child": "tip",
                "axis": [0, 0, 1],
            }
        ],
    }
    mixed = check_position_error(
        barrel,
        None,
        {
            "tool": "tip",
            "at": [100, 0, 0],
            "tolerance_mm": 1.0,
            "sources": {
                "spin": {
                    "angle": {"encoder": {"resolution_deg": 0.2}},
                    "slide": {"backlash": {"mm": 0.4}},
                }
            },
        },
    )
    angular = 0.1 * 100.0 * math.pi / 180.0
    row = mixed["poses"][0]
    assert _by_id(row, "spin.encoder")["vector_mm"] == pytest.approx([0.0, angular, 0.0], abs=_ABS)
    assert _by_id(row, "spin.slide.backlash")["vector_mm"] == pytest.approx([0.0, 0.0, 0.4], abs=_ABS)
    assert mixed["worst_case_mm"] == pytest.approx(math.hypot(angular, 0.4), abs=_ABS)
    with pytest.raises(GeometryError, match="angle and slide"):
        check_position_error(
            barrel,
            None,
            {
                "tool": "tip",
                "at": [100, 0, 0],
                "tolerance_mm": 1.0,
                "sources": {"spin": {"encoder": {"resolution_deg": 0.2}}},
            },
        )


def test_a_gear_mesh_arc_divided_by_the_operating_radius_is_the_joint_bound():
    spec = {
        "name": "reducer",
        "instances": [_instance("base"), _instance("arm")],
        "joints": [_revolute("shoulder", "base", "arm", [0, 0, 1])],
    }
    pinion = {"type": "spur", "module_mm": 2, "teeth": 20, "pressure_angle_deg": 20, "face_width_mm": 8}
    gear = {"type": "spur", "module_mm": 2, "teeth": 40, "pressure_angle_deg": 20, "face_width_mm": 8}
    centre = 60.2
    report = check_position_error(
        spec,
        None,
        {
            "tool": "arm",
            "at": [100, 0, 0],
            "tolerance_mm": 5.0,
            "sources": {
                "shoulder": {
                    "backlash": {
                        "mesh": {
                            "pinion": pinion,
                            "gear": gear,
                            "member": "pinion",
                            "centre_distance_mm": centre,
                            "steps": 8,
                        }
                    }
                }
            },
        },
    )
    alpha = math.radians(20.0)
    ideal = 60.0
    working = math.acos(math.cos(alpha) * ideal / centre)
    circumferential = 2.0 * centre * ((math.tan(working) - working) - (math.tan(alpha) - alpha))
    radius = centre * 20.0 / 60.0
    tip = circumferential / radius * 100.0
    mesh = _by_id(report["poses"][0], "shoulder.backlash")
    assert mesh["mesh"]["circumferential_mm"] == pytest.approx(circumferential, abs=1e-4)
    assert mesh["mesh"]["radius_mm"] == pytest.approx(radius, abs=_ABS)
    assert mesh["magnitude_mm"] == pytest.approx(tip, abs=1e-4)
    assert report["status"] == "pass"

    # One millimeter of arc at the tool radius is one millimeter at the tool.
    arc = check_position_error(
        spec,
        None,
        {
            "tool": "arm",
            "at": [100, 0, 0],
            "tolerance_mm": 2.0,
            "sources": {"shoulder": {"backlash": {"mm": 1.0, "radius_mm": 100.0}}},
        },
    )
    assert arc["worst_case_mm"] == pytest.approx(1.0, abs=_ABS)


def test_a_bevel_uses_the_mid_face_virtual_spur_and_an_offset_pair_is_refused():
    spec = {
        "name": "bevel-arm",
        "instances": [_instance("base"), _instance("arm")],
        "joints": [_revolute("shoulder", "base", "arm", [0, 0, 1])],
    }
    straight = {"module_mm": 3, "pressure_angle_deg": 20, "face_width_mm": 12, "shaft_angle_deg": 90}
    pinion = {"type": "straight_bevel", "teeth": 20, "mate_teeth": 40, **straight}
    gear = {"type": "straight_bevel", "teeth": 40, "mate_teeth": 20, **straight}
    report = check_position_error(
        spec,
        None,
        {
            "tool": "arm",
            "at": [100, 0, 0],
            "tolerance_mm": 5.0,
            "sources": {
                "shoulder": {
                    "backlash": {
                        "mesh": {
                            "pinion": pinion,
                            "gear": gear,
                            "member": "pinion",
                            "centre_distance_mm": 0.3,
                            "steps": 8,
                        }
                    }
                }
            },
        },
    )
    left = gear_data(pinion)
    right = gear_data(gear)
    outer = min(left["outer_cone_distance_mm"], right["outer_cone_distance_mm"])
    face = min(left["face_width_mm"], right["face_width_mm"])
    mid = outer - face / 2.0
    section_p = virtual_spur_at(left, mid)
    section_g = virtual_spur_at(right, mid)
    mount_p = _virtual_record(section_p, left)
    mount_g = _virtual_record(section_g, right)
    ideal = cylindrical_mount(mount_p, mount_g)["centre_distance_mm"]
    opened = cylindrical_mount(mount_p, mount_g, ideal + 0.3)
    radius = opened["centre_distance_mm"] * section_p["z"] / (section_p["z"] + section_g["z"])
    mesh = _by_id(report["poses"][0], "shoulder.backlash")["mesh"]
    assert mesh["radius_mm"] == pytest.approx(radius, abs=1e-6)
    assert mesh["circumferential_mm"] == pytest.approx(opened["circumferential_backlash_mm"], abs=1e-4)
    assert mesh["centre_distance_mm"] == pytest.approx(0.3, abs=1e-6)
    tip = mesh["circumferential_mm"] / mesh["radius_mm"] * 100.0
    assert report["worst_case_mm"] == pytest.approx(tip, abs=1e-4)

    with pytest.raises(GeometryError, match="along the tooth"):
        check_position_error(
            spec,
            None,
            {
                "tool": "arm",
                "at": [100, 0, 0],
                "tolerance_mm": 1.0,
                "sources": {
                    "shoulder": {
                        "backlash": {
                            "mesh": {
                                "pinion": _hypoid("pinion"),
                                "gear": _hypoid("ring"),
                                "member": "pinion",
                                "steps": 8,
                            }
                        }
                    }
                },
            },
        )


def test_a_path_reports_the_sample_with_the_longest_lever():
    spec = _arm()
    bent = solve_kinematics(spec, None, {"tool": "link2", "at": AT, "joints": {"shoulder": 0, "elbow": 90}})
    stretched = [180.0, 0.0, 0.0]
    report = check_position_error(
        spec,
        None,
        {
            "tool": "link2",
            "at": AT,
            "tolerance_mm": 2.0,
            "joints": {"shoulder": 0, "elbow": 90},
            "path": {"kind": "line", "steps": 3, "target": {"position": stretched}},
            "sources": {"shoulder": {"encoder": {"resolution_deg": 0.2}}},
        },
    )
    assert report["path_status"] in ("solved", "singular")
    assert report["pose_count"] == 3
    assert report["worst_pose"] == "path-2"
    assert report["status"] == "pass"
    start = _pose(report, "path-0")
    end = _pose(report, "path-2")
    assert start["position_mm"] == pytest.approx(bent["forward"]["position_mm"], abs=1e-4)
    assert end["position_mm"] == pytest.approx(stretched, abs=1e-3)
    # Shoulder error is the lever arm times half a 0.2° step. Full reach is the longest.
    assert end["worst_case_mm"] == pytest.approx(0.1 * math.pi, abs=1e-3)
    assert end["worst_case_mm"] > start["worst_case_mm"]

    missed = check_position_error(
        spec,
        None,
        {
            "tool": "link2",
            "at": AT,
            "tolerance_mm": 10.0,
            "joints": {"shoulder": 0, "elbow": 0},
            "path": {"kind": "line", "steps": 3, "target": {"position": [1000, 0, 0]}},
            "sources": {"shoulder": {"encoder": {"resolution_deg": 0.2}}},
        },
    )
    assert missed["status"] == "inconclusive"
    assert any(pose["status"] == "inconclusive" for pose in missed["poses"])


def test_a_driven_joint_keeps_its_column_at_the_relation_value():
    spec = _arm()
    spec["relations"] = [{"name": "mesh", "type": "gear", "driver": "shoulder", "driven": "elbow", "ratio": -1}]
    report = check_position_error(
        spec,
        None,
        {
            "tool": "link2",
            "at": AT,
            "tolerance_mm": 5.0,
            "joints": {"shoulder": 10, "elbow": 40},
            "sources": {"elbow": {"backlash": {"deg": 0.5}}},
        },
    )
    pose = report["poses"][0]
    assert pose["joints"]["elbow"] == pytest.approx(-10.0, abs=1e-6)
    assert any("driven" in note and "-10" in note for note in pose["notes"])
    column = next(item for item in pose["jacobian"] if item["joint"] == "elbow")
    finite = _column_from_nudge(spec, {"shoulder": 10.0, "elbow": -10.0}, "elbow")
    assert column["driven"] is True
    assert column["vector_mm"] == pytest.approx(finite, abs=1e-4)
    assert _by_id(pose, "elbow.backlash")["vector_mm"] == pytest.approx(
        [component * 0.5 for component in column["vector_mm"]],
        abs=_ABS,
    )


def test_more_than_sixteen_contributions_use_the_sum_of_the_magnitudes():
    count = 17
    names = ["base"] + [f"link{index}" for index in range(count)]
    joints = []
    for index in range(count):
        joints.append(
            _revolute(
                f"j{index + 1}",
                names[index],
                names[index + 1],
                [0, 0, 1],
                None if index == 0 else [10, 0, 0],
            )
        )
    spec = {
        "name": "long",
        "instances": [_instance(name) for name in names],
        "joints": joints,
    }
    sources = {f"j{index + 1}": {"encoder": {"resolution_deg": 0.2}} for index in range(count)}
    report = check_position_error(
        spec,
        None,
        {"tool": names[-1], "at": [10, 0, 0], "tolerance_mm": 100.0, "sources": sources},
    )
    pose = report["poses"][0]
    assert pose["combination"] == "sum_of_magnitudes"
    assert pose["signs"] is None
    total = sum(item["magnitude_mm"] for item in pose["contributions"])
    assert report["worst_case_mm"] == pytest.approx(total, abs=_ABS)
    assert report["rss_mm"] < report["worst_case_mm"]
    assert report["status"] == "pass"
    assert any("sum of the magnitudes" in note for note in pose["notes"])
    magnitudes, _vector, signs, kind = _worst(
        [np.array([float(index + 1), 0.0, 0.0]) for index in range(17)]
    )
    assert kind == "sum_of_magnitudes"
    assert signs is None
    assert magnitudes == pytest.approx(sum(range(1, 18)), abs=_ABS)


def test_rotation_fit_recovers_a_known_screw_and_refuses_a_flat_probe():
    omega = (0.001, -0.002, 0.003)
    origin = (0.01, -0.02, 0.03)

    def moved(point):
        return [
            origin[0] + omega[1] * point[2] - omega[2] * point[1],
            origin[1] + omega[2] * point[0] - omega[0] * point[2],
            origin[2] + omega[0] * point[1] - omega[1] * point[0],
        ]

    probes = [
        {"node_mm": [0.0, 0.0, 0.0], "displacement_mm": list(origin)},
        {"node_mm": [10.0, 0.0, 0.0], "displacement_mm": moved([10.0, 0.0, 0.0])},
        {"node_mm": [0.0, 12.0, 1.0], "displacement_mm": moved([0.0, 12.0, 1.0])},
    ]
    fitted, problem = _fit_rotation(probes)
    assert problem is None
    assert fitted == pytest.approx(omega, abs=1e-12)

    flat = [
        {"node_mm": [0.0, 0.0, 0.0], "displacement_mm": [0.0, 0.0, 0.0]},
        {"node_mm": [10.0, 0.0, 0.0], "displacement_mm": [0.0, 0.0, 0.0]},
        {"node_mm": [20.0, 0.0, 0.0], "displacement_mm": [0.0, 0.0, 0.0]},
    ]
    missed, reason = _fit_rotation(flat)
    assert missed is None
    assert reason


def test_unknown_fields_and_an_empty_budget_are_refused():
    spec = _arm()
    with pytest.raises(GeometryError, match="unknown fields"):
        check_position_error(spec, None, {"tool": "link2", "tolerance_mm": 1.0, "fudge": 1})
    with pytest.raises(GeometryError, match="encoder, a backlash"):
        check_position_error(spec, None, {"tool": "link2", "at": AT, "tolerance_mm": 1.0})
    with pytest.raises(GeometryError, match="not both"):
        check_position_error(spec, None, {**_request(1.0), "path": {"kind": "line", "steps": 2, "target": {"position": [1, 0, 0]}}})
    with pytest.raises(GeometryError, match="finite number"):
        check_position_error(
            spec,
            None,
            {
                "tool": "link2",
                "at": AT,
                "tolerance_mm": 1.0,
                "sources": {"shoulder": {"encoder": {"counts": True}}},
            },
        )
    with pytest.raises(GeometryError, match="negative"):
        check_position_error(
            spec,
            None,
            {
                "tool": "link2",
                "at": AT,
                "tolerance_mm": 1.0,
                "sources": {"elbow": {"backlash": {"deg": -0.2}}},
            },
        )
    fixed = _arm()
    fixed["joints"][1]["type"] = "fixed"
    with pytest.raises(GeometryError, match="fixed"):
        check_position_error(fixed, None, _request(1.0))
    side = _arm()
    side["instances"].append(_instance("other"))
    side["joints"].append(_revolute("side", "base", "other", [0, 1, 0]))
    with pytest.raises(GeometryError, match="not on the chain"):
        check_position_error(
            side,
            None,
            {
                "tool": "link2",
                "at": AT,
                "tolerance_mm": 1.0,
                "sources": {"side": {"encoder": {"resolution_deg": 0.2}}},
            },
        )


def test_cli_workspace_and_mcp_return_the_same_budget(tmp_path: Path, capsys):
    document = Document(tmp_path)
    for name in ("base", "link1", "link2"):
        document.create_part_from_ops(name, [{"op": "add", "shape": "box", "size": [4, 4, 4], "center": [0, 0, 2]}])
    document.create_assembly(_arm())
    request = _request(0.9)
    path = tmp_path / "budget.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    code = cli_main(["error-budget", "arm", "--file", str(path), "--workspace", str(tmp_path)])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == SCHEMA
    assert payload["status"] == "fail"
    assert payload["result_kind"] == "rough_estimate"

    workspace = Workspace(tmp_path)
    again = workspace.execute("check_position_error", {"name": "arm", **request})
    assert again["status"] == payload["status"]
    assert again["worst_case_mm"] == payload["worst_case_mm"]
    assert again["worst_pose"] == payload["worst_pose"]
    assert again["rss_mm"] == payload["rss_mm"]
    assert again["poses"][0]["contributions"][0]["vector_mm"] == payload["poses"][0]["contributions"][0]["vector_mm"]
    assert again["sheet"]["path"]

    import asyncio

    server = build_server(tmp_path)
    called = asyncio.run(server.call_tool("check_position_error", {"name": "arm", **request}))
    body = json.loads(called.content[0].text)
    assert body["status"] == payload["status"]
    assert body["worst_case_mm"] == payload["worst_case_mm"]
    assert body["schema"] == SCHEMA


def test_a_named_gear_part_supplies_the_mesh(tmp_path: Path):
    document = Document(tmp_path)
    pinion = {"type": "spur", "module_mm": 2, "teeth": 20, "pressure_angle_deg": 20, "face_width_mm": 8}
    gear = {"type": "spur", "module_mm": 2, "teeth": 40, "pressure_angle_deg": 20, "face_width_mm": 8}
    document.create_gear("pinion", pinion)
    document.create_gear("wheel", gear)
    document.create_part_from_ops("base", [{"op": "add", "shape": "box", "size": [4, 4, 4]}])
    document.create_part_from_ops("arm", [{"op": "add", "shape": "box", "size": [4, 4, 4]}])
    document.create_assembly(
        {
            "name": "reducer",
            "instances": [_instance("base"), _instance("arm")],
            "joints": [_revolute("shoulder", "base", "arm", [0, 0, 1])],
        }
    )
    request = {
        "tool": "arm",
        "at": [100, 0, 0],
        "tolerance_mm": 5.0,
        "sources": {
            "shoulder": {
                "backlash": {
                    "mesh": {
                        "pinion": "pinion",
                        "gear": "wheel",
                        "member": "pinion",
                        "centre_distance_mm": 60.2,
                        "steps": 8,
                    }
                }
            }
        },
    }
    report = document.check_position_error("reducer", request)
    mesh = report["poses"][0]["contributions"][0]["mesh"]
    assert mesh["radius_mm"] == pytest.approx(60.2 * 20.0 / 60.0, abs=_ABS)
    assert mesh["circumferential_mm"] > 0
    assert report["status"] == "pass"


@needs_solver
def test_link_deflection_comes_from_the_strength_check_and_a_tight_tolerance_fails(tmp_path: Path):
    from aicad import kernel

    shape = kernel.make_from_ops([{"op": "add", "shape": "box", "size": [20, 4, 4], "center": [10, 0, 0]}])
    spec = {
        "name": "cantilever",
        "instances": [_instance("base"), _instance("beam")],
        "joints": [_revolute("root", "base", "beam", [0, 0, 1])],
    }
    # δ = F L^3 / (3 E I). The probe is the tip. One probe has no fitted rotation.
    analytical = 100.0 * 20.0**3 / (3.0 * 210_000.0 * (4.0 * 4.0**3 / 12.0))
    report = check_position_error(
        spec,
        {"beam": shape},
        {
            "tool": "beam",
            "at": [20, 0, 0],
            "tolerance_mm": 0.001,
            "links": {
                "beam": {
                    "fea": {
                        "material": "steel",
                        "mesh_size_mm": 1.5,
                        "target_safety_factor": 2.0,
                        "fixes": [{"name": "root", "select": {"kind": "plane", "normal": [-1, 0, 0]}}],
                        "loads": [
                            {
                                "name": "tip",
                                "type": "force",
                                "vector": [0, 0, -100],
                                "select": {"kind": "plane", "normal": [1, 0, 0]},
                            }
                        ],
                    },
                    "probes_mm": [[20, 0, 0]],
                }
            },
        },
        out_dir=tmp_path,
    )
    row = report["poses"][0]["contributions"][0]
    assert row["kind"] == "link"
    assert row["used"] is True
    assert row["fea"]["status"] == "solved"
    assert row["vector_mm"][2] < 0
    assert abs(row["vector_mm"][2]) == pytest.approx(analytical, rel=0.20)
    assert abs(row["vector_mm"][0]) < abs(row["vector_mm"][2])
    assert abs(row["vector_mm"][1]) < abs(row["vector_mm"][2])
    assert report["status"] == "fail"
    assert report["worst_case_mm"] > 0.001
    assert any("linear" in line.lower() for line in report["assumptions"])


def _hypoid(role: str) -> dict:
    teeth, mate = (8, 16) if role == "pinion" else (16, 8)
    return {
        "type": "hypoid",
        "role": role,
        "teeth": teeth,
        "mate_teeth": mate,
        "offset_mm": 12,
        "shaft_angle_deg": 90,
        "ring_diameter_mm": 80,
        "face_width_mm": 10,
        "spiral_angle_deg": 35,
        "pressure_angle_deg": 20,
    }


def _virtual_record(section: dict, data: dict) -> dict:
    return {
        "normal_module_mm": section["m"],
        "normal_pressure_angle_deg": section["alpha_t_deg"],
        "helix_angle_deg": 0.0,
        "profile_shift": section["x"],
        "teeth": section["z"],
        "transverse_pressure_angle_deg": section["alpha_t_deg"],
        "reference_diameter_mm": section["d"],
        "base_diameter_mm": section["db"],
        "tip_diameter_mm": section["da"],
        "transverse_module_mm": section["mt"],
        "face_width_mm": data["face_width_mm"],
        "form_radius_mm": section["form_radius"],
        "hand": data.get("hand"),
    }
