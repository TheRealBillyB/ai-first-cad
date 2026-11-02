"""URDF and MJCF export, inertia, and a headless load when MuJoCo is installed."""

import math
import xml.etree.ElementTree as ET

import pytest

from aicad.document import Document
from aicad.kinematics import solve
from aicad.kernel import place, solid_inertia
from aicad.robot import export_robot


def _box(document: Document, name: str, size: list[float], center: list[float]) -> None:
    document.create_part_from_ops(
        name,
        [{"op": "add", "shape": "box", "size": size, "center": center}],
    )


def _urdf_rotation(roll: float, pitch: float, yaw: float) -> list[list[float]]:
    """URDF rpy is radians: R = Rz(yaw) @ Ry(pitch) @ Rx(roll)."""
    cx, sx = math.cos(roll), math.sin(roll)
    cy, sy = math.cos(pitch), math.sin(pitch)
    cz, sz = math.cos(yaw), math.sin(yaw)
    rx = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]
    ry = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    rz = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]
    ry_rx = [[sum(ry[row][k] * rx[k][col] for k in range(3)) for col in range(3)] for row in range(3)]
    return [[sum(rz[row][k] * ry_rx[k][col] for k in range(3)) for col in range(3)] for row in range(3)]


def test_a_tilted_base_keeps_the_placed_orientation(tmp_path):
    document = Document(tmp_path)
    _box(document, "base", [20, 10, 6], [4, 1, 2])
    position = [12.0, -4.0, 8.0]
    rotation = [30.0, 50.0, 70.0]
    document.create_assembly(
        {
            "name": "tilted",
            "instances": [
                {
                    "name": "base",
                    "part": "base",
                    "placement": {"position": position, "rotation_deg": rotation},
                }
            ],
            "joints": [],
        }
    )
    report = document.export_robot("tilted", tmp_path / "robot")
    root = ET.parse(report["urdf"]).getroot()
    origin = root.find("joint[@name='world_to_base']/origin")
    xyz = [float(value) for value in origin.get("xyz").split()]
    rpy = [float(value) for value in origin.get("rpy").split()]
    assert xyz == pytest.approx([value / 1000.0 for value in position], abs=1e-9)
    rotation_m = _urdf_rotation(*rpy)
    shape = document._get("base").shape  # noqa: SLF001
    placed = place(shape, tuple(position), tuple(rotation))
    expect = solid_inertia(placed)["center_mm"]
    unplaced = solid_inertia(shape)["center_mm"]
    moved = [
        sum(rotation_m[row][col] * unplaced[col] for col in range(3)) + position[row]
        for row in range(3)
    ]
    assert moved == pytest.approx(expect, abs=1e-3)
    assert unplaced == pytest.approx([4, 1, 2], abs=1e-6)


def test_a_yawed_base_is_ninety_degrees_in_mjcf(tmp_path):
    document = Document(tmp_path)
    _box(document, "base", [20, 10, 6], [10, 0, 0])
    document.create_assembly(
        {
            "name": "yawed",
            "instances": [
                {
                    "name": "base",
                    "part": "base",
                    "placement": {"position": [0, 0, 0], "rotation_deg": [0, 0, 90]},
                }
            ],
            "joints": [],
        }
    )
    report = document.export_robot("yawed", tmp_path / "robot")
    mjcf = ET.parse(report["mjcf"]).getroot()
    assert mjcf.find("compiler").get("angle") == "degree"
    euler = [float(value) for value in mjcf.find(".//body[@name='base']").get("euler").split()]
    assert euler == pytest.approx([90.0, 0.0, 0.0], abs=1e-4)
    urdf = ET.parse(report["urdf"]).getroot()
    rpy = [float(value) for value in urdf.find("joint[@name='world_to_base']/origin").get("rpy").split()]
    assert rpy == pytest.approx([0.0, 0.0, math.pi / 2], abs=1e-6)
    if report["validation"]["mujoco"]["status"] != "loaded":
        return
    import mujoco

    model = mujoco.MjModel.from_xml_path(report["mjcf"])
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    body = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "base")
    axis = data.xmat[body].reshape(3, 3)[:, 0]
    assert list(axis) == pytest.approx([0.0, 1.0, 0.0], abs=1e-6)


def test_box_inertia_matches_the_handbook(tmp_path):
    document = Document(tmp_path)
    _box(document, "block", [10, 20, 30], [0, 0, 0])
    props = solid_inertia(document._get("block").shape)  # noqa: SLF001
    assert props["volume_mm3"] == pytest.approx(6000, rel=1e-6)
    assert props["center_mm"] == pytest.approx([0, 0, 0], abs=1e-6)
    tensor = props["inertia_mm5"]
    assert tensor[0][0] == pytest.approx(650000, rel=1e-4)
    assert tensor[1][1] == pytest.approx(500000, rel=1e-4)
    assert tensor[2][2] == pytest.approx(250000, rel=1e-4)
    assert tensor[0][1] == pytest.approx(0, abs=1)


def test_revolute_export_writes_limits_mass_and_meshes(tmp_path):
    document = Document(tmp_path)
    _box(document, "base", [20, 20, 10], [0, 0, 5])
    _box(document, "link", [40, 10, 10], [20, 0, 0])
    document.create_assembly(
        {
            "name": "arm",
            "instances": [
                {"name": "base", "part": "base", "material": "aluminum"},
                {"name": "link", "part": "link", "material": "steel", "payload_kg": 0.1, "payload_at": [40, 0, 0]},
            ],
            "joints": [
                {
                    "name": "shoulder",
                    "type": "revolute",
                    "parent": "base",
                    "child": "link",
                    "parent_anchor": [10, 0, 10],
                    "child_anchor": [0, 0, 5],
                    "axis": [0, 1, 0],
                    "min": -40,
                    "max": 90,
                }
            ],
            "relations": [],
        }
    )
    report = document.export_robot("arm", tmp_path / "robot")
    root = ET.parse(report["urdf"]).getroot()
    assert root.tag == "robot"
    names = {joint.get("name"): joint.get("type") for joint in root.findall("joint")}
    assert names["shoulder"] == "revolute"
    assert names["world_to_base"] == "fixed"
    limit = root.find("joint[@name='shoulder']/limit")
    assert float(limit.get("lower")) == pytest.approx(math.radians(-40))
    assert float(limit.get("upper")) == pytest.approx(math.radians(90))
    axis = root.find("joint[@name='shoulder']/axis").get("xyz").split()
    assert [float(value) for value in axis] == pytest.approx([0, 1, 0])
    masses = {link.get("name"): float(link.find("inertial/mass").get("value")) for link in root.findall("link") if link.find("inertial/mass") is not None}
    assert masses["base"] > 0
    assert masses["link"] > 0.1
    assert (tmp_path / "robot" / "meshes" / "base.stl").is_file()
    assert (tmp_path / "robot" / "meshes" / "link.stl").is_file()
    mjcf = ET.parse(report["mjcf"]).getroot()
    hinge = mjcf.find(".//joint[@name='shoulder']")
    assert hinge.get("type") == "hinge"
    lower, upper = (float(value) for value in hinge.get("range").split())
    assert lower == pytest.approx(-40)
    assert upper == pytest.approx(90)
    assert report["validation"]["mujoco"]["status"] in ("loaded", "skipped")
    if report["validation"]["mujoco"]["status"] == "loaded":
        assert report["validation"]["mujoco"]["steps"] == 5


def test_a_second_joint_keeps_the_span_between_pins(tmp_path):
    """The parent link frame sits on the incoming pin, so the next origin includes that shift."""
    document = Document(tmp_path)
    _box(document, "base", [20, 20, 10], [0, 0, 5])
    _box(document, "link1", [100, 10, 10], [0, 0, 0])
    _box(document, "link2", [60, 10, 10], [0, 0, 0])
    document.create_assembly(
        {
            "name": "arm",
            "instances": [
                {"name": "base", "part": "base", "material": "aluminum"},
                {"name": "link1", "part": "link1", "material": "aluminum"},
                {"name": "link2", "part": "link2", "material": "aluminum"},
            ],
            "joints": [
                {
                    "name": "shoulder",
                    "type": "revolute",
                    "parent": "base",
                    "child": "link1",
                    "parent_anchor": [0, 0, 10],
                    "child_anchor": [-50, 0, 0],
                    "axis": [0, 0, 1],
                    "min": -180,
                    "max": 180,
                },
                {
                    "name": "elbow",
                    "type": "revolute",
                    "parent": "link1",
                    "child": "link2",
                    "parent_anchor": [50, 0, 0],
                    "child_anchor": [-30, 0, 0],
                    "axis": [0, 0, 1],
                    "min": -180,
                    "max": 180,
                },
            ],
            "relations": [],
        }
    )
    report = document.export_robot("arm", tmp_path / "robot")
    root = ET.parse(report["urdf"]).getroot()
    shoulder = [float(value) for value in root.find("joint[@name='shoulder']/origin").get("xyz").split()]
    elbow = [float(value) for value in root.find("joint[@name='elbow']/origin").get("xyz").split()]
    assert shoulder == pytest.approx([0.0, 0.0, 0.01], abs=1e-6)
    assert elbow == pytest.approx([0.1, 0.0, 0.0], abs=1e-6)
    mjcf = ET.parse(report["mjcf"]).getroot()
    link2 = [float(value) for value in mjcf.find(".//body[@name='link2']").get("pos").split()]
    assert link2 == pytest.approx([0.1, 0.0, 0.0], abs=1e-6)
    turned = _urdf_rotation(0.0, 0.0, math.pi / 2)
    world = [
        shoulder[row] + sum(turned[row][col] * elbow[col] for col in range(3))
        for row in range(3)
    ]
    pin = next(
        joint["origin_mm"]
        for joint in solve(document.get_assembly("arm")["assembly"], {"shoulder": 90})["joints"]
        if joint["name"] == "elbow"
    )
    assert pin == pytest.approx([0.0, 100.0, 10.0], abs=0.05)
    assert [value * 1000.0 for value in world] == pytest.approx(pin, abs=0.05)
    if report["validation"]["mujoco"]["status"] != "loaded":
        return
    import mujoco

    model = mujoco.MjModel.from_xml_path(report["mjcf"])
    data = mujoco.MjData(model)
    joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "shoulder")
    data.qpos[model.jnt_qposadr[joint_id]] = math.pi / 2
    mujoco.mj_forward(model, data)
    body = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "link2")
    assert list(data.xpos[body]) == pytest.approx([0.0, 0.1, 0.01], abs=1e-4)


def test_a_turned_fixed_child_keeps_its_turn_in_urdf(tmp_path):
    document = Document(tmp_path)
    _box(document, "frame", [20, 10, 6], [0, 0, 0])
    _box(document, "cup", [4, 6, 2], [0, 1, 0])
    rotation = [0.0, 30.0, 90.0]
    document.create_assembly(
        {
            "name": "turned",
            "instances": [{"name": "frame", "part": "frame"}, {"name": "cup", "part": "cup"}],
            "joints": [
                {
                    "name": "seat",
                    "type": "fixed",
                    "parent": "frame",
                    "child": "cup",
                    "parent_anchor": [5, 0, 3],
                    "child_anchor": [0, 0, 0],
                    "rotation_deg": rotation,
                }
            ],
        }
    )
    report = document.export_robot("turned", tmp_path / "robot")
    origin = ET.parse(report["urdf"]).getroot().find("joint[@name='seat']/origin")
    rpy = [float(value) for value in origin.get("rpy").split()]
    solved = solve(document.get_assembly("turned")["assembly"])
    cup = next(item for item in solved["instances"] if item["name"] == "cup")
    expected = [value for row in cup["matrix"][:3] for value in row[:3]]
    exported = [value for row in _urdf_rotation(*rpy) for value in row]
    assert exported == pytest.approx(expected, abs=1e-6)


def test_a_cylindrical_joint_becomes_two_joints(tmp_path):
    document = Document(tmp_path)
    _box(document, "base", [10, 10, 10], [0, 0, 0])
    _box(document, "nut", [8, 8, 8], [0, 0, 0])
    spec_shapes = {
        "base": document._get("base").base,  # noqa: SLF001
        "nut": document._get("nut").base,  # noqa: SLF001
    }
    spec = {
        "name": "screw",
        "instances": [
            {"name": "base", "part": "base", "material": "steel", "placement": {"position": [0, 0, 0], "rotation_deg": [0, 0, 0]}},
            {"name": "nut", "part": "nut", "material": "steel"},
        ],
        "joints": [
            {
                "name": "thread",
                "type": "cylindrical",
                "parent": "base",
                "child": "nut",
                "parent_anchor": [0, 0, 10],
                "child_anchor": [0, 0, 0],
                "axis": [0, 0, 1],
                "min": -180,
                "max": 180,
            }
        ],
        "relations": [
            {"type": "gear", "driver": "thread", "driven": "other", "ratio": -1, "offset": 0}
        ],
    }
    report = export_robot(spec, spec_shapes, tmp_path / "cyl")
    root = ET.parse(report["urdf"]).getroot()
    types = {joint.get("name"): joint.get("type") for joint in root.findall("joint")}
    assert types["thread_revolute"] == "revolute"
    assert types["thread_prismatic"] == "prismatic"
    assert root.find("link[@name='thread_spin']/inertial/mass") is not None
    text = (tmp_path / "cyl" / "robot.xml").read_text(encoding="utf-8")
    assert 'type="slide"' in text
    assert "Not enforced" in (tmp_path / "cyl" / "robot.urdf").read_text(encoding="utf-8")
    assert report["validation"]["mujoco"]["status"] in ("loaded", "skipped")
