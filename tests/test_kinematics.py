"""Forward and inverse kinematics against the joint solver."""

import json
import math
from pathlib import Path

import pytest

from aicad.chain import SCHEMA, solve_kinematics
from aicad.cli import main as cli_main
from aicad.document import Document
from aicad.errors import GeometryError
from aicad.kinematics import solve, transform_point
from aicad.motion_study import SCHEMA as STUDY_SCHEMA
from aicad.workspace import Workspace


def _instance(name: str) -> dict:
    return {"name": name, "part": name}


def _revolute(name, parent, child, axis, parent_anchor=None, limits=None) -> dict:
    joint = {
        "name": name,
        "type": "revolute",
        "parent": parent,
        "child": child,
        "axis": axis,
    }
    if parent_anchor is not None:
        joint["parent_anchor"] = parent_anchor
    if limits:
        joint.update(limits)
    return joint


def _planar(axis, limits=None) -> dict:
    return {
        "name": "arm",
        "instances": [_instance("base"), _instance("link1"), _instance("link2")],
        "joints": [
            _revolute("shoulder", "base", "link1", axis, limits=None if limits is None else limits.get("shoulder")),
            _revolute("elbow", "link1", "link2", axis, [100, 0, 0], None if limits is None else limits.get("elbow")),
        ],
    }


def _arm6(limits=None) -> dict:
    names = ["base", "l1", "l2", "l3", "l4", "l5", "l6"]
    axes = [[0, 0, 1], [0, 1, 0], [0, 1, 0], [1, 0, 0], [0, 1, 0], [1, 0, 0]]
    anchors = [None, None, [120, 0, 0], [100, 0, 0], None, None]
    joints = []
    for index in range(6):
        extra = None if not limits else limits
        joints.append(_revolute(f"j{index + 1}", names[index], names[index + 1], axes[index], anchors[index], extra))
    return {"name": "arm6", "instances": [_instance(name) for name in names], "joints": joints}


def _tool_points(spec, joints, tool, at):
    solved = solve(spec, joints)
    matrix = next(item["matrix"] for item in solved["instances"] if item["name"] == tool)
    origin = transform_point(matrix, at)
    nudged = transform_point(matrix, [at[0] + 10.0, at[1], at[2]])
    return origin, nudged


def _angle_error(left, right) -> float:
    return abs((left - right + 180.0) % 360.0 - 180.0)


def _assert_round_trip(spec, solutions, tool, at, origin, nudged=None):
    assert solutions
    for item in solutions:
        back, back_nudged = _tool_points(spec, item["joints"], tool, at)
        assert math.dist(back, origin) <= 1e-6
        if nudged is not None:
            assert math.dist(back_nudged, nudged) <= 1e-6
        assert item["position_error_mm"] <= 1e-6


def test_planar_forward_matches_the_joint_solver_and_the_link_formula():
    spec = _planar([0, 0, 1])
    at = [80, 0, 0]
    joints = {"shoulder": 30, "elbow": 40}
    report = solve_kinematics(spec, None, {"tool": "link2", "at": at, "joints": joints})
    origin, _nudged = _tool_points(spec, joints, "link2", at)
    expect = [
        100 * math.cos(math.radians(30)) + 80 * math.cos(math.radians(70)),
        100 * math.sin(math.radians(30)) + 80 * math.sin(math.radians(70)),
        0.0,
    ]
    assert report["schema"] == SCHEMA
    assert report["task"] == "forward"
    assert report["result_kind"] == "verified_check"
    assert math.dist(report["forward"]["position_mm"], origin) <= 1e-6
    assert math.dist(report["forward"]["position_mm"], expect) <= 1e-6
    assert report["forward"]["rotation_deg"][2] == pytest.approx(70)


def test_planar_position_returns_both_elbows_and_the_pose_keeps_the_branch():
    spec = _planar([0, 0, 1])
    at = [80, 0, 0]
    joints = {"shoulder": 30, "elbow": 40}
    origin, nudged = _tool_points(spec, joints, "link2", at)
    position = solve_kinematics(spec, None, {"tool": "link2", "at": at, "target": {"position": origin}})
    inverse = position["inverse"]
    assert inverse["status"] == "solved"
    assert inverse["method"] == "closed_form"
    assert {item["name"] for item in inverse["solutions"]} == {"elbow_up", "elbow_down"}
    _assert_round_trip(spec, inverse["solutions"], "link2", at, origin)
    up = next(item for item in inverse["solutions"] if item["name"] == "elbow_up")
    assert up["joints"]["shoulder"] == pytest.approx(30)
    assert up["joints"]["elbow"] == pytest.approx(40)

    forward = solve_kinematics(spec, None, {"tool": "link2", "at": at, "joints": joints})
    pose = solve_kinematics(
        spec,
        None,
        {
            "tool": "link2",
            "at": at,
            "joints": {"shoulder": 5, "elbow": -15},
            "target": {"matrix": forward["forward"]["matrix"]},
        },
    )
    assert pose["inverse"]["status"] == "solved"
    assert pose["inverse"]["method"] == "closed_form"
    assert [item["name"] for item in pose["inverse"]["solutions"]] == ["elbow_up"]
    _assert_round_trip(spec, pose["inverse"]["solutions"], "link2", at, origin, nudged)
    assert pose["inverse"]["rejected"][0]["reason"] == "orientation"


def test_a_planar_arm_about_y_round_trips_the_same_way():
    spec = _planar([0, 1, 0])
    at = [80, 0, 0]
    joints = {"shoulder": 30, "elbow": 40}
    forward = solve_kinematics(spec, None, {"tool": "link2", "at": at, "joints": joints})
    origin, _nudged = _tool_points(spec, joints, "link2", at)
    assert math.dist(forward["forward"]["position_mm"], origin) <= 1e-6
    pose = solve_kinematics(
        spec,
        None,
        {"tool": "link2", "at": at, "target": {"matrix": forward["forward"]["matrix"]}},
    )
    assert pose["inverse"]["method"] == "closed_form"
    assert pose["inverse"]["solutions"][0]["joints"]["shoulder"] == pytest.approx(30)
    assert pose["inverse"]["solutions"][0]["joints"]["elbow"] == pytest.approx(40)
    back, _other = _tool_points(spec, pose["inverse"]["solutions"][0]["joints"], "link2", at)
    assert math.dist(back, origin) <= 1e-6


def test_a_stretched_arm_is_singular_and_a_far_target_is_unreachable():
    spec = _planar([0, 0, 1])
    at = [80, 0, 0]
    stretch = solve_kinematics(spec, None, {"tool": "link2", "at": at, "target": {"position": [180, 0, 0]}})
    assert stretch["inverse"]["status"] == "singular"
    assert stretch["inverse"]["reached"] is True
    assert stretch["inverse"]["position_error_mm"] <= 1e-6
    assert "singular" in stretch["inverse"]["message"].lower()
    assert stretch["inverse"]["solutions"][0]["singular"] is True

    far = solve_kinematics(spec, None, {"tool": "link2", "at": at, "target": {"position": [1000, 0, 0]}})
    assert far["inverse"]["status"] == "unreachable"
    assert far["inverse"]["reached"] is False
    assert far["inverse"]["solutions"] == []
    assert "unreachable" in far["inverse"]["message"].lower()


def test_joint_limits_drop_the_elbow_that_cannot_reach():
    spec = _planar([0, 0, 1], {"elbow": {"min": 0, "max": 180}})
    at = [80, 0, 0]
    origin, _nudged = _tool_points(_planar([0, 0, 1]), {"shoulder": 30, "elbow": 40}, "link2", at)
    report = solve_kinematics(spec, None, {"tool": "link2", "at": at, "target": {"position": origin}})
    inverse = report["inverse"]
    assert inverse["status"] == "solved"
    assert [item["name"] for item in inverse["solutions"]] == ["elbow_up"]
    for item in inverse["solutions"]:
        assert 0 <= item["joints"]["elbow"] <= 180
    rejected = inverse["rejected"]
    assert rejected[0]["reason"] == "limit"
    assert rejected[0]["violations"][0]["joint"] == "elbow"

    tight = _planar([0, 0, 1], {"shoulder": {"min": -1, "max": 1}, "elbow": {"min": -1, "max": 1}})
    blocked = solve_kinematics(tight, None, {"tool": "link2", "at": at, "target": {"position": origin}})
    assert blocked["inverse"]["status"] == "limit"
    assert blocked["inverse"]["solutions"] == []
    assert blocked["inverse"]["rejected"]
    assert "limit" in blocked["inverse"]["message"].lower()


def test_six_axis_spherical_wrist_round_trips_within_a_nanometer():
    spec = _arm6()
    at = [40, 0, 0]
    joints = {"j1": 20, "j2": 30, "j3": 40, "j4": 25, "j5": 50, "j6": 15}
    forward = solve_kinematics(spec, None, {"tool": "l6", "at": at, "joints": joints})
    origin, nudged = _tool_points(spec, joints, "l6", at)
    assert math.dist(forward["forward"]["position_mm"], origin) <= 1e-6
    pose = solve_kinematics(
        spec,
        None,
        {
            "tool": "l6",
            "at": at,
            "joints": {name: 0.0 for name in joints},
            "target": {"matrix": forward["forward"]["matrix"]},
        },
    )
    inverse = pose["inverse"]
    assert inverse["status"] == "solved"
    assert inverse["method"] == "closed_form"
    assert len(inverse["solutions"]) >= 2
    assert all(item["singular"] is False for item in inverse["solutions"])
    _assert_round_trip(spec, inverse["solutions"], "l6", at, origin, nudged)
    matched = [
        item
        for item in inverse["solutions"]
        if all(_angle_error(item["joints"][name], joints[name]) <= 1e-5 for name in joints)
    ]
    assert len(matched) == 1


def test_six_axis_unreachable_and_limit_targets_are_not_solutions():
    spec = _arm6()
    at = [40, 0, 0]
    far = solve_kinematics(
        spec,
        None,
        {"tool": "l6", "at": at, "target": {"position": [5000, 0, 0], "rotation_deg": [0, 0, 0]}},
    )
    assert far["inverse"]["status"] == "unreachable"
    assert far["inverse"]["solutions"] == []
    assert "unreachable" in far["inverse"]["message"].lower()

    joints = {"j1": 20, "j2": 30, "j3": 40, "j4": 25, "j5": 50, "j6": 15}
    forward = solve_kinematics(spec, None, {"tool": "l6", "at": at, "joints": joints})
    limited = _arm6({"min": -1, "max": 1})
    blocked = solve_kinematics(
        limited,
        None,
        {"tool": "l6", "at": at, "target": {"matrix": forward["forward"]["matrix"]}},
    )
    assert blocked["inverse"]["status"] == "limit"
    assert blocked["inverse"]["solutions"] == []
    assert blocked["inverse"]["rejected"]
    for item in blocked["inverse"]["rejected"]:
        assert item["reason"] == "limit"
        assert any(abs(value) > 1 for value in item["joints"].values())


def test_a_prismatic_revolute_chain_round_trips_from_the_jacobian():
    spec = {
        "name": "mixed",
        "instances": [_instance("base"), _instance("link"), _instance("tip")],
        "joints": [
            _revolute("spin", "base", "link", [0, 0, 1]),
            {
                "name": "reach",
                "type": "prismatic",
                "parent": "link",
                "child": "tip",
                "axis": [1, 0, 0],
                "parent_anchor": [20, 0, 0],
            },
        ],
    }
    joints = {"spin": 20, "reach": 40}
    origin, _nudged = _tool_points(spec, joints, "tip", [0, 0, 0])
    report = solve_kinematics(
        spec,
        None,
        {"tool": "tip", "joints": {"spin": 5, "reach": 10}, "target": {"position": origin}},
    )
    assert report["inverse"]["status"] == "solved"
    assert report["inverse"]["method"] == "jacobian"
    assert len(report["inverse"]["solutions"]) == 1
    back, _other = _tool_points(spec, report["inverse"]["solutions"][0]["joints"], "tip", [0, 0, 0])
    assert math.dist(back, origin) <= 1e-6


def test_a_line_and_waypoints_report_a_joint_value_at_every_step():
    spec = _planar([0, 0, 1])
    at = [80, 0, 0]
    start = solve_kinematics(spec, None, {"tool": "link2", "at": at, "joints": {"shoulder": 20, "elbow": 30}})
    end = solve_kinematics(spec, None, {"tool": "link2", "at": at, "joints": {"shoulder": 35, "elbow": 20}})
    mid = solve_kinematics(spec, None, {"tool": "link2", "at": at, "joints": {"shoulder": 28, "elbow": 24}})
    traced = solve_kinematics(
        spec,
        None,
        {
            "tool": "link2",
            "at": at,
            "joints": {"shoulder": 20, "elbow": 30},
            "path": {"kind": "line", "steps": 5, "target": {"position": end["forward"]["position_mm"]}},
        },
    )
    assert traced["path"]["status"] == "solved"
    assert traced["path"]["collisions_checked"] is False
    start_point = start["forward"]["position_mm"]
    end_point = end["forward"]["position_mm"]
    for step in traced["path"]["steps"]:
        blend = step["index"] / 4
        expect = [start_point[i] + (end_point[i] - start_point[i]) * blend for i in range(3)]
        assert math.dist(step["position_mm"], expect) <= 1e-6
        assert set(step["joints"]) == {"shoulder", "elbow"}

    waypoints = solve_kinematics(
        spec,
        None,
        {
            "tool": "link2",
            "at": at,
            "joints": {"shoulder": 20, "elbow": 30},
            "path": {
                "kind": "waypoints",
                "steps": 3,
                "waypoints": [
                    {"position": start_point},
                    {"position": mid["forward"]["position_mm"]},
                    {"position": end_point},
                ],
            },
        },
    )
    assert waypoints["path"]["status"] == "solved"
    assert waypoints["path"]["step_count"] == 5
    assert math.dist(waypoints["path"]["steps"][0]["position_mm"], start_point) <= 1e-6
    assert math.dist(waypoints["path"]["steps"][-1]["position_mm"], end_point) <= 1e-6


def test_a_driven_joint_is_not_an_inverse_variable():
    spec = _planar([0, 0, 1])
    spec["relations"] = [
        {"name": "mesh", "type": "gear", "driver": "shoulder", "driven": "elbow", "ratio": -1}
    ]
    with pytest.raises(GeometryError, match="driven"):
        solve_kinematics(spec, None, {"tool": "link2", "target": {"position": [100, 0, 0]}})


def _box(document: Document, name: str, size: list[float], center: list[float]) -> None:
    document.create_part_from_ops(name, [{"op": "add", "shape": "box", "size": size, "center": center}])


def test_a_tool_path_reuses_the_motion_study_for_collisions_and_stops(tmp_path: Path):
    document = Document(tmp_path)
    _box(document, "base", [10, 10, 10], [0, 0, 5])
    _box(document, "carriage", [40, 40, 40], [0, 0, 20])
    _box(document, "wall", [20, 40, 40], [50, 0, 20])
    document.create_assembly(
        {
            "name": "slide",
            "instances": [
                {"name": "base", "part": "base"},
                {"name": "carriage", "part": "carriage"},
                {"name": "wall", "part": "wall"},
            ],
            "joints": [
                {
                    "name": "rail",
                    "type": "prismatic",
                    "parent": "base",
                    "child": "carriage",
                    "axis": [1, 0, 0],
                },
                {"name": "mount", "type": "fixed", "parent": "base", "child": "wall"},
            ],
        }
    )
    clear = document.solve_kinematics(
        "slide",
        "carriage",
        path={"kind": "line", "steps": 3, "target": {"position": [10, 0, 0]}},
    )
    assert clear["path"]["status"] == "solved"
    assert clear["path"]["motion_study"]["schema"] == STUDY_SCHEMA
    assert clear["path"]["motion_study"]["collision_count"] == 0

    hit = document.solve_kinematics(
        "slide",
        "carriage",
        path={"kind": "line", "steps": 5, "target": {"position": [100, 0, 0]}},
    )
    assert hit["path"]["status"] == "collision"
    assert hit["path"]["motion_study"]["collision_count"] > 0
    pairs = {(item["a"], item["b"]) for item in hit["path"]["motion_study"]["collisions"]}
    assert ("carriage", "wall") in pairs

    stored = document.get_assembly("slide")["assembly"]
    for joint in stored["joints"]:
        if joint["name"] == "rail":
            joint["max"] = 40
    document.update_assembly(stored)
    stopped = document.solve_kinematics(
        "slide",
        "carriage",
        joints={"rail": 0},
        path={"kind": "line", "steps": 5, "target": {"position": [100, 0, 0]}},
    )
    assert stopped["path"]["steps"][2]["status"] == "limit"
    assert stopped["path"]["steps"][2]["commanded"]["joints"]["rail"] == pytest.approx(50)
    assert stopped["path"]["steps"][2]["joints"]["rail"] == pytest.approx(40)
    assert stopped["path"]["steps"][3]["status"] == "not_solved"
    assert stopped["path"]["steps"][4]["status"] == "not_solved"
    names = {item["joint"] for item in stopped["path"]["motion_study"]["limit_hits"]}
    assert "rail" in names


def test_cli_and_workspace_return_the_same_inverse(tmp_path: Path, capsys):
    document = Document(tmp_path)
    _box(document, "base", [4, 4, 4], [0, 0, 2])
    _box(document, "link1", [4, 4, 4], [0, 0, 2])
    _box(document, "link2", [4, 4, 4], [0, 0, 2])
    document.create_assembly(_planar([0, 0, 1]))
    request = {"tool": "link2", "at": [80, 0, 0], "target": {"position": [120, 40, 0]}}
    path = tmp_path / "target.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    code = cli_main(["kinematics", "arm", "--file", str(path), "--workspace", str(tmp_path)])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == SCHEMA
    assert payload["inverse"]["status"] == "solved"
    assert payload["result_kind"] == "verified_check"
    workspace = Workspace(tmp_path)
    again = workspace.execute("solve_kinematics", {"name": "arm", **request})
    assert again["inverse"]["solutions"][0]["joints"] == payload["inverse"]["solutions"][0]["joints"]
    assert again["assembly"] == "arm"
