"""A catalog part snaps onto a face or an axis and keeps that mount."""

import json
import math

import pytest

from aicad import Document, MountError
from aicad.kinematics import matrix_from_placement, transform_point
from aicad.mount import frame_pose, snap_rotation
from aicad.workspace import Workspace


def test_a_spin_about_the_mount_axis_is_a_right_handed_turn():
    position, rotation = frame_pose([0, 0, 0], [0, 0, 1], [10, 0, 0], [0, 0, 1], 90, False, "axis")
    moved = transform_point(
        matrix_from_placement({"position": list(position), "rotation_deg": list(rotation)}),
        [1, 0, 0],
    )
    assert moved[0] == pytest.approx(10, abs=1e-6)
    assert moved[1] == pytest.approx(1, abs=1e-6)
    assert moved[2] == pytest.approx(0, abs=1e-6)


def test_a_face_mate_points_into_the_face_until_it_is_flipped():
    position, rotation = frame_pose([0, 0, 0], [0, 0, 1], [0, 0, 5], [0, 0, 1], 0, False, "face")
    moved = transform_point(
        matrix_from_placement({"position": list(position), "rotation_deg": list(rotation)}),
        [0, 0, 1],
    )
    assert moved == pytest.approx((0, 0, 4), abs=1e-6)
    flipped_at, flipped_turn = frame_pose([0, 0, 0], [0, 0, 1], [0, 0, 5], [0, 0, 1], 0, True, "face")
    flipped = transform_point(
        matrix_from_placement({"position": list(flipped_at), "rotation_deg": list(flipped_turn)}),
        [0, 0, 1],
    )
    assert flipped == pytest.approx((0, 0, 6), abs=1e-6)


def test_rotation_snaps_to_fifteen_degree_steps():
    assert snap_rotation(0) == 0
    assert snap_rotation(7) == 0
    assert snap_rotation(7.5) == 15
    assert snap_rotation(22) == 15
    assert snap_rotation(23) == 30
    assert snap_rotation(-10) == -15
    assert snap_rotation(180) == 180
    assert snap_rotation(360) == 0


def test_a_bearing_bore_snaps_onto_a_shaft_and_survives_reload(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("shaft", "part = Pos(25, 0, 0) * Cylinder(4, 40)\n")
    shaft = _cylinder(document, "shaft", 4)
    document.insert_standard_part(
        "bearing",
        "bearing",
        "608",
        mount={"part": "shaft", "ref": shaft["ref"], "rotation_deg": 22},
    )
    bore = _cylinder(document, "bearing", 4)
    assert _parallel(shaft["axis_direction"], bore["axis_direction"])
    assert _axis_gap(shaft, bore) == pytest.approx(0, abs=0.05)
    assert _along(shaft, bore["center_mm"]) == pytest.approx(_along(shaft, shaft["center_mm"]), abs=0.05)
    stored = document.get_source("bearing")["placement"]
    assert stored["mount"]["mate"] == "bore"
    assert stored["mount"]["rotation_deg"] == 15
    assert stored["mount"]["flip"] is False
    payload = json.loads((tmp_path / "session.json").read_text())
    saved = next(part for part in payload["parts"] if part["name"] == "bearing")
    assert saved["placement"]["mount"]["mate"] == "bore"

    reopened = Document(tmp_path)
    again = _cylinder(reopened, "bearing", 4)
    shaft_again = _cylinder(reopened, "shaft", 4)
    assert _axis_gap(shaft_again, again) == pytest.approx(0, abs=0.05)
    assert reopened.get_source("bearing")["placement"]["mount"]["mate"] == "bore"
    assert reopened.problems == []


def test_a_face_on_a_placed_part_is_stored_on_the_unplaced_solid(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("shaft", "part = Cylinder(4, 40)\n")
    document.place_part("shaft", (0, 12, 0))
    world = _cylinder(document, "shaft", 4)
    assert world["center_mm"][1] == pytest.approx(12, abs=0.05)
    document.insert_standard_part("bearing", "bearing", "608", mount={"part": "shaft", "ref": world["ref"]})
    shaft = _cylinder(document, "shaft", 4)
    bore = _cylinder(document, "bearing", 4)
    assert _axis_gap(shaft, bore) == pytest.approx(0, abs=0.05)
    stored_ref = document.get_source("bearing")["placement"]["mount"]["ref"]
    assert "12.000" not in stored_ref


def test_moving_the_shaft_keeps_the_bearing_on_its_axis(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("shaft", "part = Cylinder(4, 40)\n")
    ref = _cylinder(document, "shaft", 4)["ref"]
    document.insert_standard_part("bearing", "bearing", "608", mount={"part": "shaft", "ref": ref})
    document.place_part("shaft", (0, 12, 0))
    shaft = _cylinder(document, "shaft", 4)
    bore = _cylinder(document, "bearing", 4)
    assert shaft["center_mm"][1] == pytest.approx(12, abs=0.05)
    assert _axis_gap(shaft, bore) == pytest.approx(0, abs=0.05)
    assert document.get_source("bearing")["placement"]["mount"]["part"] == "shaft"


def test_rebuilding_the_shaft_keeps_the_mount_and_a_lost_axis_does_not_move_the_bearing(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("shaft", "part = Pos(25, 0, 0) * Cylinder(4, 40)\n")
    ref = _cylinder(document, "shaft", 4)["ref"]
    document.insert_standard_part("bearing", "bearing", "608", mount={"part": "shaft", "ref": ref})
    before = document.measure("bearing").center_of_mass_mm
    document.update_part_script("shaft", "part = Pos(25, 0, 18) * Cylinder(4, 40)\n")
    shaft = _cylinder(document, "shaft", 4)
    bore = _cylinder(document, "bearing", 4)
    assert shaft["center_mm"][2] == pytest.approx(18, abs=0.05)
    assert _axis_gap(shaft, bore) == pytest.approx(0, abs=0.05)
    assert document.get_source("bearing")["placement"]["mount"]["ref"]

    stayed = document.measure("bearing").center_of_mass_mm
    with pytest.raises(MountError, match="is gone"):
        document.update_part_script("shaft", "part = Box(10, 10, 10)\n")
    assert document.measure("bearing").center_of_mass_mm.x == pytest.approx(stayed.x)
    assert document.measure("bearing").center_of_mass_mm.z == pytest.approx(stayed.z)
    assert document.get_source("shaft")["source"] == "script"
    assert "Cylinder" in document.get_source("shaft")["script"]
    assert before.x == pytest.approx(25, abs=0.5)


def test_an_unknown_face_is_refused_before_the_part_moves(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("shaft", "part = Cylinder(4, 40)\n")
    document.insert_standard_part("bearing", "bearing", "608")
    before = document.measure("bearing").center_of_mass_mm
    placement = document.get_source("bearing")["placement"]
    with pytest.raises(MountError, match="was not moved"):
        document.place_part("bearing", mount={"part": "shaft", "ref": "cylinder/r1/z+/0,0,0"})
    after = document.measure("bearing").center_of_mass_mm
    assert after.x == pytest.approx(before.x)
    assert after.z == pytest.approx(before.z)
    assert document.get_source("bearing")["placement"] == placement
    with pytest.raises(MountError, match="was not moved"):
        document.insert_standard_part(
            "other",
            "bearing",
            "608",
            mount={"part": "shaft", "ref": "not-a-face"},
        )
    assert [part.name for part in document.list_parts()] == ["shaft", "bearing"]


def test_a_motor_flange_sits_flush_and_flip_turns_it_around(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("plate", "part = Pos(0, 0, 5) * Box(80, 80, 10)\n")
    face = _plane(document, "plate", (0, 0, 1))
    document.insert_standard_part(
        "motor",
        "motor",
        "NEMA17",
        mount={"part": "plate", "ref": face["ref"]},
    )
    stored = document.get_source("motor")["placement"]["mount"]
    assert stored["mate"] == "mount"
    assert stored["flip"] is False
    flange, gap = _closest_plane(document, "motor", face)
    assert gap == pytest.approx(0, abs=0.05)
    assert _dot(flange["normal"], face["normal"]) == pytest.approx(-1, abs=1e-6)
    assert document.measure("motor").center_of_mass_mm.z > face["center_mm"][2]

    document.place_part("motor", mount={"part": "plate", "ref": face["ref"], "flip": True, "rotation_deg": 90})
    flipped = document.get_source("motor")["placement"]["mount"]
    assert flipped["flip"] is True
    assert flipped["rotation_deg"] == 90
    assert document.measure("motor").center_of_mass_mm.z < face["center_mm"][2]


def test_a_plain_position_still_places_and_clears_a_mount(tmp_path):
    document = Document(tmp_path)
    document.insert_standard_part("nut", "hex_nut", "M3-0.5", position=(4, 0, 1), rotation_deg=(0, 0, 30))
    assert "mount" not in document.get_source("nut")["placement"]
    reopened = Document(tmp_path)
    placement = reopened.get_source("nut")["placement"]
    assert placement["position"] == pytest.approx([4, 0, 1])
    assert placement["rotation_deg"] == pytest.approx([0, 0, 30])
    assert reopened.measure("nut").center_of_mass_mm.x == pytest.approx(
        document.measure("nut").center_of_mass_mm.x
    )

    document.create_part_from_script("shaft", "part = Cylinder(4, 20)\n")
    ref = _cylinder(document, "shaft", 4)["ref"]
    document.place_part("nut", mount={"part": "shaft", "ref": ref})
    assert "mount" in document.get_source("nut")["placement"]
    document.place_part("nut", (3, 0, 0))
    assert "mount" not in document.get_source("nut")["placement"]
    assert document.get_source("nut")["placement"]["position"] == pytest.approx([3, 0, 0])


def test_a_missing_mount_in_a_saved_project_is_reported_and_the_part_stays(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("shaft", "part = Cylinder(4, 20)\n")
    ref = _cylinder(document, "shaft", 4)["ref"]
    document.insert_standard_part("bearing", "bearing", "608", mount={"part": "shaft", "ref": ref})
    path = tmp_path / "session.json"
    payload = json.loads(path.read_text())
    bearing = next(part for part in payload["parts"] if part["name"] == "bearing")
    bearing["placement"]["mount"]["ref"] = "cylinder/r1/z+/0,0,0"
    path.write_text(json.dumps(payload))
    reopened = Document(tmp_path)
    assert any("is gone" in problem for problem in reopened.problems)
    assert reopened.get_source("bearing")["placement"]["mount"]["ref"] == "cylinder/r1/z+/0,0,0"
    assert reopened.measure("bearing").volume_mm3 > 0


def test_the_workspace_mounts_a_catalog_part(tmp_path):
    workspace = Workspace(tmp_path)
    workspace.execute(
        "create_part_from_script",
        {"name": "shaft", "script": "part = Cylinder(4, 30)\n"},
    )
    faces = workspace.execute("describe_faces", {"name": "shaft"})
    ref = next(face["ref"] for face in faces["faces"] if face["kind"] == "cylinder")
    workspace.execute(
        "insert_standard_part",
        {"name": "bearing", "kind": "bearing", "size": "608", "mount": {"part": "shaft", "ref": ref}},
    )
    bore = next(
        face
        for face in workspace.execute("describe_faces", {"name": "bearing"})["faces"]
        if face["kind"] == "cylinder" and abs(face["radius_mm"] - 4) < 0.1
    )
    shaft = next(face for face in faces["faces"] if face["kind"] == "cylinder")
    assert _axis_gap(shaft, bore) == pytest.approx(0, abs=0.05)
    with pytest.raises(MountError, match="not both"):
        workspace.execute(
            "place_part",
            {"name": "bearing", "position": [1, 0, 0], "mount": {"part": "shaft", "ref": ref}},
        )


def test_a_catalog_length_change_keeps_the_mount(tmp_path):
    workspace = Workspace(tmp_path)
    document = workspace.document
    document.create_part_from_script("plate", "part = Pos(0, 0, 4) * Box(20, 20, 8) - Cylinder(3, 30)\n")
    hole = _cylinder(document, "plate", 3)
    document.insert_standard_part(
        "screw",
        "screw",
        "M5-0.8",
        length=12,
        mount={"part": "plate", "ref": hole["ref"]},
    )
    workspace.execute("set_parameter", {"name": "screw", "parameter": "library/length", "value": 20})
    hole = _cylinder(document, "plate", 3)
    center = document.measure("screw").center_of_mass_mm
    assert document.get_source("screw")["placement"]["mount"]["part"] == "plate"
    assert document.get_source("screw")["library"]["length"] == 20
    assert _point_axis_gap(hole, (center.x, center.y, center.z)) == pytest.approx(0, abs=0.2)


def _cylinder(document: Document, name: str, radius: float) -> dict:
    faces = [
        face
        for face in document.describe_faces(name)["faces"]
        if face["kind"] == "cylinder" and abs(float(face["radius_mm"]) - radius) < 0.15
    ]
    assert faces, name
    return faces[0]


def _closest_plane(document: Document, name: str, target: dict) -> tuple[dict, float]:
    best = None
    best_gap = None
    for face in document.describe_faces(name)["faces"]:
        if face["kind"] != "plane" or not face.get("normal"):
            continue
        gap = abs(_dot(_sub(face["center_mm"], target["center_mm"]), target["normal"]))
        if best_gap is None or gap < best_gap:
            best = face
            best_gap = gap
    assert best is not None
    return best, best_gap


def _plane(document: Document, name: str, normal) -> dict:
    faces = [
        face
        for face in document.describe_faces(name)["faces"]
        if face["kind"] == "plane" and _dot(face["normal"], normal) > 0.9
    ]
    assert faces, name
    return max(faces, key=lambda face: abs(face["center_mm"][2]))


def _parallel(left, right) -> bool:
    return abs(_dot(left, right)) > 0.99


def _axis_gap(left: dict, right: dict) -> float:
    delta = _sub(right["axis_origin_mm"], left["axis_origin_mm"])
    cross = (
        delta[1] * left["axis_direction"][2] - delta[2] * left["axis_direction"][1],
        delta[2] * left["axis_direction"][0] - delta[0] * left["axis_direction"][2],
        delta[0] * left["axis_direction"][1] - delta[1] * left["axis_direction"][0],
    )
    return math.sqrt(sum(component * component for component in cross))


def _point_axis_gap(face: dict, point) -> float:
    delta = _sub(point, face["axis_origin_mm"])
    cross = (
        delta[1] * face["axis_direction"][2] - delta[2] * face["axis_direction"][1],
        delta[2] * face["axis_direction"][0] - delta[0] * face["axis_direction"][2],
        delta[0] * face["axis_direction"][1] - delta[1] * face["axis_direction"][0],
    )
    return math.sqrt(sum(component * component for component in cross))


def _along(face: dict, point) -> float:
    delta = _sub(point, face["axis_origin_mm"])
    return _dot(delta, face["axis_direction"])


def _dot(left, right) -> float:
    return sum(float(left[index]) * float(right[index]) for index in range(3))


def _sub(left, right):
    return [float(left[index]) - float(right[index]) for index in range(3)]
