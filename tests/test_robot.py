"""URDF and MJCF export, inertia, and a headless load when MuJoCo is installed."""

import math
import xml.etree.ElementTree as ET

import pytest

from aicad.document import Document
from aicad.kernel import solid_inertia
from aicad.robot import export_robot


def _box(document: Document, name: str, size: list[float], center: list[float]) -> None:
    document.create_part_from_ops(
        name,
        [{"op": "add", "shape": "box", "size": size, "center": center}],
    )


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
