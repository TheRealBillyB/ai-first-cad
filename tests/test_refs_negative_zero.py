"""A reference prints a coordinate that rounds to zero as 0.000, whatever its sign.

A face center a turn leaves at -0.0, or at -0.0000001, printed as -0.000. The
same face read another way printed 0.000, and the two strings did not match:
``set_selection`` refused a face the part has.
"""

from __future__ import annotations

import pytest

from aicad import kernel, refs
from aicad.refs import edge_ref, face_ref, ref_to_selector
from aicad.workspace import Workspace

_PLATE = [{"op": "add", "shape": "box", "size": [10, 6, 4], "center": [0, 0, 2]}]


@pytest.mark.parametrize("value", [-0.0, -1e-12, -0.0004, 0.0, 0.0004])
def test_a_coordinate_that_rounds_to_zero_prints_without_a_sign(value):
    face = {"kind": "plane", "normal": [0, 0, 1], "center_mm": [value, 1.25, value]}
    assert face_ref(face) == "plane/z+/0.000,1.250,0.000"
    assert edge_ref("line", [value, value, 3]) == "edge/line/0.000,0.000,3.000"


def test_a_small_negative_coordinate_keeps_its_sign():
    face = {"kind": "plane", "normal": [0, 0, -1], "center_mm": [-0.002, -1.5, -10]}
    assert face_ref(face) == "plane/z-/-0.002,-1.500,-10.000"


def test_an_older_reference_with_minus_zero_reads_as_this_one():
    canonical_ref = refs.canonical_ref
    old = "cylinder/r1.7/z+/-0.000,0.000,-0.000"
    assert canonical_ref(old) == "cylinder/r1.7/z+/0.000,0.000,0.000"
    assert ref_to_selector(canonical_ref(old)) == ref_to_selector(old)
    # Only a coordinate that is exactly -0.000 changes.
    assert canonical_ref("edge/line/-0.002,1.000,-0.000") == "edge/line/-0.002,1.000,0.000"
    assert canonical_ref("not a reference") == "not a reference"


def _turned_plate(tmp_path, monkeypatch) -> Workspace:
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_MESH_CACHE", str(tmp_path / "meshes"))
    workspace = Workspace(tmp_path / "project")
    workspace.execute("create_part_from_ops", {"name": "plate", "ops": _PLATE}, source="ui")
    # A half turn about Z leaves the centers on the axis at -0.0 and at rounding noise.
    workspace.execute("place_part", {"name": "plate", "position": [0, 0, 0], "rotation_deg": [0, 0, 180]}, source="ui")
    return workspace


def test_no_reference_on_a_turned_part_prints_minus_zero(tmp_path, monkeypatch):
    workspace = _turned_plate(tmp_path, monkeypatch)
    shape = workspace.document._get("plate").shape  # noqa: SLF001
    faces = workspace.document.describe_faces("plate")["faces"]
    edges = kernel.describe_edges(shape)
    mesh = workspace.mesh("plate")
    refs = [face["ref"] for face in faces] + [edge["ref"] for edge in edges]
    refs += [face["ref"] for face in mesh["faces"]] + [edge["ref"] for edge in mesh["edges"]]
    assert refs
    assert not [ref for ref in refs if "-0.000" in ref]
    # The view's references are the ones the part describes, so a click selects.
    assert sorted(face["ref"] for face in mesh["faces"]) == sorted(face["ref"] for face in faces)


def test_selecting_each_face_of_a_turned_part_works(tmp_path, monkeypatch):
    workspace = _turned_plate(tmp_path, monkeypatch)
    for face in workspace.mesh("plate")["faces"]:
        answer = workspace.execute("set_selection", {"part": "plate", "faces": [face["ref"]]}, source="ui")
        assert answer["selection"]["faces"] == [face["ref"]]


def test_a_selection_written_with_minus_zero_is_accepted_as_this_version_prints_it(tmp_path, monkeypatch):
    workspace = _turned_plate(tmp_path, monkeypatch)
    top = next(face["ref"] for face in workspace.document.describe_faces("plate")["faces"] if face["ref"].startswith("plane/z+/"))
    assert top == "plane/z+/0.000,0.000,4.000"
    answer = workspace.execute("set_selection", {"part": "plate", "faces": ["plane/z+/-0.000,0.000,4.000"]}, source="ui")
    assert answer["selection"]["faces"] == [top]
    # A face the part does not have is still refused.
    with pytest.raises(Exception, match="not on part"):
        workspace.execute("set_selection", {"part": "plate", "faces": ["plane/z+/0.000,0.000,9.000"]}, source="ui")
