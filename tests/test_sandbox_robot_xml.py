"""URDF and MJCF names stay inside their attributes.

A quote, an angle bracket, or a comment closer in a part or joint name must
not add an attribute or an element. Both files parse as one document. A name
that mentions a marker file outside the project does not create that file.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from aicad.document import Document
from aicad.errors import GeometryError
from aicad.robot import export_robot

_ATTRS = {
    "robot": {"name"},
    "link": {"name"},
    "joint": {"name", "type", "axis", "range", "limited"},
    "parent": {"link"},
    "child": {"link"},
    "origin": {"xyz", "rpy"},
    "axis": {"xyz"},
    "limit": {"lower", "upper", "effort", "velocity"},
    "visual": set(),
    "collision": set(),
    "geometry": set(),
    "inertial": {"pos", "mass", "fullinertia", "diaginertia"},
    "mass": {"value"},
    "inertia": {"ixx", "ixy", "ixz", "iyy", "iyz", "izz"},
    "mesh": {"filename", "scale", "name", "file"},
    "mujoco": {"model"},
    "compiler": {"angle", "eulerseq", "meshdir"},
    "option": {"gravity", "timestep"},
    "asset": set(),
    "worldbody": set(),
    "body": {"name", "pos", "euler"},
    "geom": {"type", "mesh", "pos"},
}


def _box(document: Document, name: str) -> None:
    document.create_part_from_ops(
        name,
        [{"op": "add", "shape": "box", "size": [10, 10, 10], "center": [0, 0, 0]}],
    )


def _local_mesh(value: str) -> bool:
    name = value.removeprefix("meshes/")
    return bool(name) and name.endswith(".stl") and "/" not in name and "\\" not in name and ":" not in name and ".." not in name


def _assert_document(path: Path, canary: Path) -> ET.Element:
    text = path.read_text(encoding="utf-8")
    root = ET.fromstring(text)
    assert canary.name not in text or "&quot;" in text or "&lt;" in text
    for element in root.iter():
        assert element.tag in _ATTRS
        assert set(element.attrib) <= _ATTRS[element.tag]
        for key in ("filename", "file"):
            if key in element.attrib:
                assert _local_mesh(element.attrib[key])
                assert str(canary) not in element.attrib[key]
        if "mesh" in element.attrib:
            mesh_name = element.attrib["mesh"]
            assert "/" not in mesh_name and "\\" not in mesh_name and ":" not in mesh_name and ".." not in mesh_name
    return root


def test_a_quote_in_a_name_stays_inside_its_attribute(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "SANDBOX_CANARY.txt"
    quoted = f'base" file="{canary.as_posix()}" <mesh>&'
    child = f"link'-->{canary.as_posix()}"
    part = f'part" file="{canary.as_posix()}"'
    joint = f'shoulder" type="fixed" file="{canary.as_posix()}"'
    assembly = f'arm" model="{canary.as_posix()}"'
    document = Document(tmp_path / "project")
    _box(document, "base")
    _box(document, "link")
    shapes = {
        "base": document._get("base").base,  # noqa: SLF001
        part: document._get("link").base,  # noqa: SLF001
    }
    spec = {
        "name": assembly,
        "instances": [
            {"name": quoted, "part": "base", "material": "steel"},
            {"name": child, "part": part, "material": "aluminum"},
        ],
        "joints": [
            {
                "name": joint,
                "type": "revolute",
                "parent": quoted,
                "child": child,
                "parent_anchor": [0, 0, 10],
                "child_anchor": [0, 0, 0],
                "axis": [0, 0, 1],
                "min": -20,
                "max": 20,
            }
        ],
        "relations": [
            {
                "type": 'gear"--><robot name="x"',
                "driver": joint,
                "driven": f'driven--><mesh file="{canary.as_posix()}">',
                "ratio": -1,
                "offset": 0,
            }
        ],
    }
    report = export_robot(spec, shapes, tmp_path / "project" / "robot")
    urdf = _assert_document(Path(report["urdf"]), canary)
    mjcf = _assert_document(Path(report["mjcf"]), canary)
    assert urdf.attrib == {"name": assembly}
    assert mjcf.attrib == {"model": assembly}
    assert quoted in {link.get("name") for link in urdf.findall("link")}
    assert child in {link.get("name") for link in urdf.findall("link")}
    assert joint in {item.get("name") for item in urdf.findall("joint")}
    quoted_joint = next(item for item in urdf.findall("joint") if item.get("name") == joint)
    assert quoted_joint.attrib == {"name": joint, "type": "revolute"}
    assert f"world_to_{quoted}" in {item.get("name") for item in urdf.findall("joint")}
    assert joint in {item.get("name") for item in mjcf.iter("joint")}
    assert child in {item.get("name") for item in mjcf.iter("body")}
    for mesh in (tmp_path / "project" / "robot" / "meshes").iterdir():
        assert mesh.is_file()
        assert mesh.suffix == ".stl"
        assert mesh.resolve().is_relative_to((tmp_path / "project").resolve())
    assert not canary.exists()
    raw = Path(report["urdf"]).read_text(encoding="utf-8")
    assert "&quot;" in raw
    assert "&lt;" in raw
    assert "&amp;" in raw
    assert f'<robot name="{assembly}"' not in raw


def test_a_name_xml_cannot_store_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "SANDBOX_CANARY.txt"
    document = Document(tmp_path / "project")
    _box(document, "base")
    _box(document, "link")
    shapes = {
        "base": document._get("base").base,  # noqa: SLF001
        "link": document._get("link").base,  # noqa: SLF001
    }
    spec = {
        "name": "arm\x01",
        "instances": [
            {"name": "base", "part": "base", "material": "steel"},
            {"name": "link", "part": "link", "material": "steel"},
        ],
        "joints": [
            {
                "name": "shoulder",
                "type": "revolute",
                "parent": "base",
                "child": "link",
                "parent_anchor": [0, 0, 10],
                "child_anchor": [0, 0, 0],
                "axis": [0, 0, 1],
            }
        ],
    }
    with pytest.raises(GeometryError, match="XML cannot store"):
        export_robot(spec, shapes, tmp_path / "project" / "robot")
    assert not canary.exists()
