"""An assembly prints as one file, and the parts stay separate solids."""

import struct
import zipfile
from pathlib import Path

import pytest

from aicad.document import Document
from aicad.errors import PartNotFound
from aicad.workspace import Workspace


def _pair(document: Document) -> None:
    document.create_part_from_ops(
        "base",
        [{"op": "add", "shape": "box", "size": [10, 10, 4], "center": [0, 0, 2]}],
    )
    document.create_part_from_ops(
        "lid",
        [{"op": "add", "shape": "box", "size": [20, 16, 3], "center": [0, 0, 6]}],
    )
    document.create_assembly(
        {
            "name": "stack",
            "instances": [{"name": "base", "part": "base"}, {"name": "lid", "part": "lid"}],
            "joints": [{"name": "sit", "type": "fixed", "parent": "base", "child": "lid"}],
        }
    )


def test_an_assembly_is_one_print_with_separate_bodies(tmp_path: Path):
    document = Document(tmp_path)
    _pair(document)
    report = document.check_print("stack", {"orientation": "z_up", "render": False, "slice": False})
    # Base top is z=4. Lid bottom is z=4.5, so the underside is a 20 by 16 overhang.
    assert report["height_mm"] == pytest.approx(7.5, abs=0.2)
    assert report["support_area_mm2"] == pytest.approx(320, abs=30)

    stl = document.export("stack", tmp_path / "stack.stl", "stl")
    payload = stl.read_bytes()
    count = struct.unpack_from("<I", payload, 80)[0]
    assert count > 12

    path = document.export("stack", tmp_path / "stack.3mf", "3mf")
    with zipfile.ZipFile(path) as archive:
        model = archive.read("3D/3dmodel.model")
    assert b"base" in model
    assert b"lid" in model
    assert model.count(b"<object") >= 2

    step = document.export("stack", tmp_path / "stack.step", "step")
    assert step.stat().st_size > 200

    part = document.export("base", tmp_path / "base.3mf", "3mf")
    with zipfile.ZipFile(part) as archive:
        alone = archive.read("3D/3dmodel.model")
    assert b"base" in alone
    workspace = Workspace(tmp_path)
    written = workspace.execute("export_part", {"name": "stack", "format": "3mf"})
    assert written["bytes"] > 200
    assert Path(written["path"]).suffix == ".3mf"


def test_a_missing_name_names_parts_and_assemblies(tmp_path: Path):
    document = Document(tmp_path)
    _pair(document)
    with pytest.raises(PartNotFound, match="Assemblies"):
        document.check_print("missing", {"render": False, "slice": False})
