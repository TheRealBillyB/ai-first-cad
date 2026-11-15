"""Linear static solves. The CalculiX binary is optional in CI; these skip without it."""

import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

import numpy as np

from aicad import kernel
from aicad.document import Document
from aicad.errors import GeometryError
from aicad.fea import fea_available, select_faces

ROOT = Path(__file__).resolve().parents[1]
_AVAILABLE, _REASON = fea_available()
needs_solver = pytest.mark.skipif(not _AVAILABLE, reason=_REASON)


def _png_stddev(path: Path) -> float:
    image = Image.open(path).convert("RGB")
    histogram = image.histogram()
    count = image.size[0] * image.size[1] * 3
    total = sum(index * count_at for index, count_at in enumerate(histogram))
    mean = total / count
    variance = sum(count_at * (index - mean) ** 2 for index, count_at in enumerate(histogram))
    return (variance / count) ** 0.5


def _beam():
    return kernel.make_from_ops(
        [{"op": "add", "shape": "box", "size": [20, 4, 4], "center": [10, 0, 0]}]
    )


def _cantilever(target: float, mesh: float = 1.5):
    return {
        "material": "steel",
        "mesh_size_mm": mesh,
        "target_safety_factor": target,
        "fixes": [{"name": "root", "select": {"kind": "plane", "normal": [-1, 0, 0]}}],
        "loads": [
            {
                "name": "tip",
                "type": "force",
                "vector": [0, 0, -100],
                "select": {"kind": "plane", "normal": [1, 0, 0]},
            }
        ],
    }


def test_face_selectors_name_planes_and_holes():
    shape = kernel.make_from_ops(
        [
            {"op": "add", "shape": "box", "size": [20, 20, 4], "center": [0, 0, 2]},
            {"op": "cut", "shape": "cylinder", "radius": 2, "height": 8, "center": [0, 0, 2]},
        ]
    )
    faces = kernel.describe_faces(shape)
    bottom = select_faces(faces, {"kind": "plane", "normal": [0, 0, -1]})
    assert len(bottom) == 1
    assert bottom[0]["center_mm"][2] == 0
    hole = select_faces(faces, {"kind": "cylinder", "radius": 2, "axis": [0, 0, 1]})
    assert len(hole) == 1
    assert abs(hole[0]["radius_mm"] - 2) < 0.05
    # A 1.7 mm request must not swallow a 2 mm hole at the default tolerance.
    assert select_faces(faces, {"kind": "cylinder", "radius": 1.7, "axis": [0, 0, 1]}) == []



def test_at_picks_the_face_nearest_the_point_among_faces_in_one_plane():
    """Two tongues share their top plane. Each ``at`` must land on its own tongue."""
    shape = kernel.make_from_ops(
        [
            {"op": "add", "shape": "box", "size": [60, 10, 4], "center": [0, 0, 2]},
            {"op": "add", "shape": "box", "size": [10, 10, 6], "center": [-24, 0, 7]},
            {"op": "add", "shape": "box", "size": [10, 10, 6], "center": [24, 0, 7]},
            {"op": "cut", "shape": "cylinder", "radius": 1.5, "height": 20, "center": [-24, 0, 5]},
            {"op": "cut", "shape": "cylinder", "radius": 1.5, "height": 20, "center": [24, 0, 5]},
        ]
    )
    faces = kernel.describe_faces(shape)
    tops = select_faces(faces, {"kind": "plane", "normal": [0, 0, 1]})
    assert len([face for face in tops if face["center_mm"][2] == 10]) == 2
    left = select_faces(faces, {"kind": "plane", "normal": [0, 0, 1], "at": [-22, 1, 10]})
    right = select_faces(faces, {"kind": "plane", "normal": [0, 0, 1], "at": [22, -1, 10]})
    assert left[0]["center_mm"][0] < 0 < right[0]["center_mm"][0]
    # A point above the tongues still takes the top plane, then the nearer tongue.
    above = select_faces(faces, {"kind": "plane", "normal": [0, 0, 1], "at": [20, 0, 30]})
    assert above[0]["center_mm"][2] == 10 and above[0]["center_mm"][0] > 0
    # The two holes are one radius on parallel axes; each point takes its own hole.
    hole = select_faces(faces, {"kind": "cylinder", "radius": 1.5, "axis": [0, 0, 1], "at": [24, 0, 3]})
    assert hole[0]["axis_origin_mm"][0] == 24
    # all: true keeps every match, with the named face first.
    every = select_faces(faces, {"kind": "plane", "normal": [0, 0, 1], "at": [22, 0, 10], "all": True})
    assert every[0]["index"] == right[0]["index"] and len(every) == len(tops)


def test_every_face_ref_names_its_own_face():
    """A viewport ref carries the face center, so coplanar faces must not resolve to the first one."""
    shape = kernel.make_from_ops(
        [
            {"op": "add", "shape": "box", "size": [60, 10, 4], "center": [0, 0, 2]},
            {"op": "add", "shape": "box", "size": [10, 10, 6], "center": [-24, 0, 7]},
            {"op": "add", "shape": "box", "size": [10, 10, 6], "center": [0, 0, 7]},
            {"op": "add", "shape": "box", "size": [10, 10, 6], "center": [24, 0, 7]},
        ]
    )
    faces = kernel.describe_faces(shape)
    for face in faces:
        assert [item["index"] for item in select_faces(faces, {"ref": face["ref"]})] == [face["index"]], face["ref"]

def test_bracket_frame_holes_are_one_selector():
    script = (ROOT / "examples" / "motor_mount.py").read_text(encoding="utf-8")
    faces = kernel.describe_faces(kernel.make_from_script(script))
    holes = select_faces(faces, {"kind": "cylinder", "radius": 1.7, "axis": [0, 0, 1]})
    assert len(holes) == 4
    assert all(abs(face["radius_mm"] - 1.7) < 0.05 for face in holes)
    bore = select_faces(faces, {"kind": "cylinder", "radius": 8, "axis": [0, 0, 1]})
    assert len(bore) >= 1
    boss_top = select_faces(faces, {"kind": "plane", "normal": [0, 0, 1], "at": [0, 0, 12]})
    assert abs(boss_top[0]["center_mm"][2] - 12) < 0.2


@needs_solver
def test_cantilever_matches_beam_theory_and_renders(tmp_path):
    from aicad.fea import solve_part

    # δ = F L^3 / (3 E I), I = 4*4^3/12, E = 210000 MPa, F = 100 N, L = 20 mm.
    analytical = 100 * 20**3 / (3 * 210_000 * (4 * 4**3 / 12))
    report = solve_part(_beam(), _cantilever(2.0), out_dir=tmp_path, stem="beam", part_name="beam")
    assert report.status == "solved"
    assert report.max_displacement_vector_mm.z < 0
    assert abs(abs(report.max_displacement_vector_mm.z) - analytical) / analytical < 0.20
    assert report.max_displacement_at_mm.x > 18
    assert 80 < report.max_von_mises_mpa < 400
    assert abs(report.min_safety_factor - 250 / report.max_von_mises_mpa) < 0.02
    assert report.review["status"] == "fail"
    text = " ".join(report.assumptions).lower()
    assert "linear" in text
    assert "isotropic" in text
    assert "mesh" in text
    assert "across layers" in text
    assert report.material["id"] == "steel"
    assert any(view.name == "stress_contact" for view in report.views)
    stress = next(view for view in report.views if view.name == "stress_contact")
    assert _png_stddev(Path(stress.path)) > 8


@needs_solver
def test_design_review_passes_when_the_target_is_easy(tmp_path):
    from aicad.fea import solve_part

    report = solve_part(_beam(), _cantilever(0.5), part_name="beam")
    assert report.status == "solved"
    assert report.min_safety_factor > 0.5
    assert report.review["status"] == "pass"


@needs_solver
def test_unconstrained_part_and_a_load_on_nothing_are_not_solved():
    from aicad.fea import solve_part

    floating = solve_part(
        _beam(),
        {
            "material": "steel",
            "mesh_size_mm": 2,
            "loads": [
                {
                    "name": "tip",
                    "type": "force",
                    "vector": [0, 0, -10],
                    "select": {"kind": "plane", "normal": [1, 0, 0]},
                }
            ],
        },
    )
    assert floating.status == "not_solved"
    assert any("float" in item.lower() or "fixed" in item.lower() for item in floating.warnings)

    missed = solve_part(
        _beam(),
        {
            "material": "steel",
            "mesh_size_mm": 2,
            "fixes": [{"name": "root", "select": {"kind": "plane", "normal": [-1, 0, 0]}}],
            "loads": [
                {
                    "name": "ghost",
                    "type": "force",
                    "vector": [0, 0, -10],
                    "select": {"kind": "cylinder", "radius": 50},
                }
            ],
        },
    )
    assert missed.status == "not_solved"
    assert any("no face" in item.lower() or "nothing" in item.lower() for item in missed.warnings)


@needs_solver
def test_coarse_mesh_is_called_out_and_cannot_pass():
    from aicad.fea import solve_part

    report = solve_part(_beam(), _cantilever(0.5, mesh=8), part_name="beam")
    assert any("coarse" in item.lower() for item in report.warnings)
    if report.status == "solved":
        assert report.review["status"] != "pass"


def _foil():
    return kernel.make_from_ops(
        [
            {"op": "add", "shape": "box", "size": [10, 10, 10]},
            {"op": "cut", "shape": "box", "size": [9.9, 9.9, 9.9]},
        ]
    )


def _foil_setup(mesh: float):
    return {
        "material": "pla",
        "mesh_size_mm": mesh,
        "target_safety_factor": 2.0,
        "fixes": [{"name": "root", "select": {"kind": "plane", "normal": [-1, 0, 0]}}],
        "loads": [
            {
                "name": "face",
                "type": "force",
                "vector": [0, 0, -20],
                "select": {"kind": "plane", "normal": [1, 0, 0]},
            }
        ],
    }


@needs_solver
def test_a_finer_foil_mesh_still_cannot_pass(tmp_path):
    from aicad.fea import solve_part

    report = solve_part(_foil(), _foil_setup(1.0), out_dir=tmp_path, part_name="foil")
    assert report.status == "solved"
    assert (report.min_safety_factor or 0) > 20
    assert report.review["status"] == "inconclusive"
    assert any("coarse mesh" in item.lower() for item in report.warnings)
    assert report.mesh.get("degraded") is False
    assert report.mesh.get("thin_feature") is True


@needs_solver
def test_a_thin_foil_under_the_target_still_fails(tmp_path):
    from aicad.fea import solve_part

    setup = _foil_setup(2.0)
    setup["loads"][0]["vector"] = [0, 0, -2000]
    report = solve_part(_foil(), setup, out_dir=tmp_path, part_name="foil")
    assert report.status == "solved"
    assert (report.min_safety_factor or 99) < 2
    assert report.review["status"] == "fail"


def test_example_bracket_meshes_are_not_flagged_as_thin_features():
    pytest.importorskip("gmsh")
    from aicad.fea import _mesh_solid, _thin_feature_mesh_note

    for name in ("motor_mount.py", "motor_mount_buried_ribs.py"):
        shape = kernel.make_from_script((ROOT / "examples" / name).read_text(encoding="utf-8"))
        mesh = _mesh_solid(shape, 1.5, 2)
        assert float(mesh["mesh_size_mm"]) == pytest.approx(1.5)
        assert _thin_feature_mesh_note(mesh) is None


def test_a_submillimetre_foil_mesh_is_still_too_coarse_for_the_wall():
    pytest.importorskip("gmsh")
    from aicad.fea import _mesh_solid, _thin_feature_mesh_note

    mesh = _mesh_solid(_foil(), 0.4, 2)
    note = _thin_feature_mesh_note(mesh)
    assert note is not None
    assert "coarse mesh" in note.lower()


@needs_solver
def test_a_thin_rib_on_a_thick_plate_cannot_pass(tmp_path):
    from aicad.fea import solve_part

    rib = kernel.make_from_ops(
        [
            {"op": "add", "shape": "box", "size": [30, 20, 4], "center": [0, 0, 2]},
            {"op": "add", "shape": "box", "size": [0.3, 16, 4], "center": [0, 0, 6]},
        ]
    )
    report = solve_part(
        rib,
        {
            "material": "pla",
            "mesh_size_mm": 2.0,
            "target_safety_factor": 2.0,
            "fixes": [{"name": "floor", "select": {"kind": "plane", "normal": [0, 0, -1]}}],
            "loads": [
                {
                    "name": "rib",
                    "type": "force",
                    "vector": [5, 0, 0],
                    "select": {"kind": "plane", "normal": [1, 0, 0], "at": [0.15, 0, 6]},
                }
            ],
        },
        out_dir=tmp_path,
        part_name="rib",
    )
    assert report.status == "solved"
    assert (report.min_safety_factor or 0) >= 2
    assert report.review["status"] == "inconclusive"
    assert any("coarse mesh" in item.lower() for item in report.warnings)


@needs_solver
def test_bolt_load_and_pressure_move_the_part(tmp_path):
    from aicad.fea import solve_part

    plate = kernel.make_from_ops(
        [
            {"op": "add", "shape": "box", "size": [16, 16, 4], "center": [0, 0, 2]},
            {"op": "cut", "shape": "cylinder", "radius": 2, "height": 8, "center": [0, 0, 2]},
        ]
    )
    bolted = solve_part(
        plate,
        {
            "material": "aluminum_6061",
            "mesh_size_mm": 2,
            "target_safety_factor": 2,
            "fixes": [{"name": "floor", "select": {"kind": "plane", "normal": [0, 0, -1]}}],
            "loads": [
                {
                    "name": "preload",
                    "type": "bolt",
                    "axial": 40,
                    "select": {"kind": "cylinder", "radius": 2, "axis": [0, 0, 1]},
                }
            ],
        },
        part_name="plate",
    )
    assert bolted.status == "solved"
    assert bolted.max_displacement_vector_mm.z > 0
    text = " ".join(bolted.assumptions).lower()
    assert "across layers" in text
    assert "aluminum" in text

    cube = kernel.make_from_ops(
        [{"op": "add", "shape": "box", "size": [10, 10, 10], "center": [0, 0, 5]}]
    )
    pressed = solve_part(
        cube,
        {
            "material": "pla",
            "mesh_size_mm": 2.5,
            "fixes": [{"name": "floor", "select": {"kind": "plane", "normal": [0, 0, -1]}}],
            "loads": [
                {
                    "name": "squeeze",
                    "type": "pressure",
                    "magnitude": 1,
                    "select": {"kind": "plane", "normal": [0, 0, 1]},
                }
            ],
        },
        part_name="cube",
    )
    assert pressed.status == "solved"
    assert pressed.max_displacement_vector_mm.z < 0
    assert 0.2 < pressed.max_von_mises_mpa < 5
    assert any("across layers" in line.lower() and "printed" in line.lower() for line in pressed.assumptions)


@needs_solver
def test_cli_materials_and_fea(tmp_path):
    ops = tmp_path / "beam.json"
    ops.write_text(
        json.dumps([{"op": "add", "shape": "box", "size": [20, 4, 4], "center": [10, 0, 0]}]),
        encoding="utf-8",
    )
    setup = tmp_path / "load.json"
    setup.write_text(json.dumps(_cantilever(2.0, mesh=2.0)), encoding="utf-8")
    workspace = tmp_path / "ws"
    ran = subprocess.run(
        [sys.executable, "-P", "-m", "aicad", "ops", str(ops), "--name", "beam", "--workspace", str(workspace)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert ran.returncode == 0, ran.stderr
    listed = subprocess.run(
        [sys.executable, "-P", "-m", "aicad", "materials", "--workspace", str(workspace)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert listed.returncode == 0, listed.stderr
    assert "pla" in listed.stdout
    solved = subprocess.run(
        [
            sys.executable,
            "-P",
            "-m",
            "aicad",
            "fea",
            "beam",
            "--file",
            str(setup),
            "--workspace",
            str(workspace),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert solved.returncode == 0, solved.stderr
    payload = json.loads(solved.stdout)
    assert payload["status"] == "solved"
    assert payload["max_displacement_mm"] > 0
    assert Path(payload["views"][0]["path"]).is_file()


def _bulged_tet10():
    from aicad.fea import _C3D10_EDGES

    corners = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    mids = np.array([0.5 * (corners[a] + corners[b]) for _mid, a, b in _C3D10_EDGES])
    mids[0] += [0.0, -0.4, 0.0]
    mids[5] += [0.3, 0.3, 0.0]
    return {"points": np.vstack([corners, mids]), "elements": np.array([list(range(10))]), "order": 2}


def _is_straight(points, elements):
    from aicad.fea import _C3D10_EDGES

    return all(
        np.allclose(points[elements[:, mid]], 0.5 * (points[elements[:, a]] + points[elements[:, b]]))
        for mid, a, b in _C3D10_EDGES
    )


def test_straight_sided_moves_only_the_mid_edge_nodes():
    from aicad.fea import straight_sided

    mesh = _bulged_tet10()
    out = straight_sided(mesh["points"], mesh["elements"])
    assert _is_straight(out, mesh["elements"])
    assert np.array_equal(out[:4], mesh["points"][:4])
    assert not _is_straight(mesh["points"], mesh["elements"])


def test_a_folded_quadratic_mesh_is_solved_straight_sided_before_linear(monkeypatch):
    from aicad import fea

    calls = []

    def ccx(mesh, _fixed, _forces, _material):
        calls.append(mesh["elements"].shape[1])
        if mesh["elements"].shape[1] == 10 and not _is_straight(mesh["points"], mesh["elements"]):
            return None, None, "nonpositive jacobian"
        return np.zeros((len(mesh["points"]), 3)), np.zeros((len(mesh["points"]), 6)), None

    monkeypatch.setattr(fea, "_run_ccx", ccx)
    mesh = _bulged_tet10()
    solved, _disp, _stress, warning, order = fea._execute(mesh, np.array([0]), np.zeros((10, 3)), {}, 2)
    assert (solved, warning, order) == (True, None, 2)
    assert calls == [10, 10]
    assert mesh["elements"].shape[1] == 10
    assert _is_straight(mesh["points"], mesh["elements"])
    assert fea.STRAIGHT_EDGE_NOTE in mesh["mesh_notes"]


def test_a_folded_quadratic_body_is_tried_straight_sided_in_a_modal_solve(monkeypatch):
    from aicad import modal

    calls = []

    def once(bodies, _fixed, _equations, _nmodes, _timeout):
        body = bodies[0]
        calls.append(np.asarray(body["elements"]).shape[1])
        if int(body["order"]) == 2 and not _is_straight(body["points"], np.asarray(body["elements"])):
            return None, None, "nonpositive jacobian"
        return [100.0], np.zeros((1, len(body["points"]), 3)), None

    monkeypatch.setattr(modal, "_frequency_once", once)
    body = _bulged_tet10()
    frequencies, _vectors, detail = modal._run_frequency([body], np.array([0]), [], 1, 5)
    assert frequencies == [100.0] and detail == ""
    assert calls == [10, 10]
    assert body["order"] == 2 and body["straight_sided"]


def test_missing_solver_message_names_ccx(monkeypatch):
    monkeypatch.setattr("aicad.prereqs.find_program", lambda _name, project=None: None)
    available, reason = fea_available()
    assert available is False
    assert "ccx" in reason


def test_pressure_on_a_round_hole_is_not_one_sideways_force():
    pytest.importorskip("gmsh")
    from aicad.fea import _apply_load, _mesh_solid

    plate = kernel.make_from_ops(
        [
            {"op": "add", "shape": "box", "size": [20, 20, 6], "center": [0, 0, 3]},
            {"op": "cut", "shape": "cylinder", "radius": 3, "height": 10, "center": [0, 0, 3]},
        ]
    )
    faces = kernel.describe_faces(plate)
    hole = [face for face in faces if face["kind"] == "cylinder"]
    assert len(hole) == 1
    area = float(hole[0]["area_mm2"])
    mesh = _mesh_solid(plate, 2.0, 1)
    forces = np.zeros((len(mesh["points"]), 3))
    _apply_load(
        mesh,
        {"kind": "pressure", "name": "bore", "spec": {"magnitude": 10}, "faces": hole},
        forces,
    )
    resultant = forces.sum(axis=0)
    # 10 MPa on the whole lateral area, applied along one normal, is about 1130 N.
    assert float(np.linalg.norm(resultant)) < 0.02 * 10 * area

    cube = kernel.make_from_ops([{"op": "add", "shape": "box", "size": [10, 10, 10], "center": [0, 0, 5]}])
    top = [face for face in kernel.describe_faces(cube) if face["kind"] == "plane" and face["normal"][2] > 0.9]
    mesh = _mesh_solid(cube, 2.5, 1)
    forces = np.zeros((len(mesh["points"]), 3))
    _apply_load(
        mesh,
        {"kind": "pressure", "name": "top", "spec": {"magnitude": 1}, "faces": top},
        forces,
    )
    planar = forces.sum(axis=0)
    assert planar[0] == pytest.approx(0, abs=1e-6)
    assert planar[1] == pytest.approx(0, abs=1e-6)
    assert planar[2] == pytest.approx(-100, rel=0.02)


@needs_solver
def test_a_load_on_the_fixed_face_is_not_a_pass():
    from aicad.fea import solve_part

    report = solve_part(
        _beam(),
        {
            "material": "steel",
            "mesh_size_mm": 3,
            "target_safety_factor": 2,
            "fixes": [{"name": "end", "select": {"kind": "plane", "normal": [1, 0, 0]}}],
            "loads": [
                {
                    "name": "same",
                    "type": "force",
                    "vector": [0, 0, -100],
                    "select": {"kind": "plane", "normal": [1, 0, 0]},
                }
            ],
        },
        part_name="beam",
    )
    assert report.status == "not_solved"
    assert report.min_safety_factor is None
    assert report.review["status"] != "pass"


@needs_solver
def test_a_disconnected_body_cannot_pass():
    from aicad.fea import solve_part

    shape = kernel.make_from_script(
        "part = Compound(children=[Box(10, 10, 10), Pos(40, 0, 0) * Box(10, 10, 10)])\n"
    )
    report = solve_part(
        shape,
        {
            "material": "steel",
            "mesh_size_mm": 3,
            "target_safety_factor": 2,
            "fixes": [{"name": "left", "select": {"kind": "plane", "normal": [-1, 0, 0], "at": [-5, 0, 0]}}],
            "loads": [
                {
                    "name": "right",
                    "type": "force",
                    "vector": [0, 0, -20],
                    "select": {"kind": "plane", "normal": [1, 0, 0], "at": [45, 0, 0]},
                }
            ],
        },
        part_name="two",
    )
    assert report.review["status"] != "pass"
    if report.status == "solved":
        assert report.max_displacement_mm > 1
        assert any("do not trust" in item.lower() for item in report.warnings)


def test_non_finite_mechanics_inputs_are_rejected():
    from aicad.fea import solve_part

    beam = _beam()
    base = {
        "material": "steel",
        "fixes": [{"name": "root", "select": {"kind": "plane", "normal": [-1, 0, 0]}}],
        "loads": [
            {
                "name": "tip",
                "type": "force",
                "vector": [0, 0, -10],
                "select": {"kind": "plane", "normal": [1, 0, 0]},
            }
        ],
    }
    with pytest.raises(GeometryError, match="mesh_size_mm"):
        solve_part(beam, {**base, "mesh_size_mm": float("nan")})
    with pytest.raises(GeometryError, match="target_safety_factor"):
        solve_part(beam, {**base, "target_safety_factor": float("inf")})
    with pytest.raises(GeometryError, match="finite"):
        bad = {**base, "loads": [{**base["loads"][0], "vector": [float("nan"), 0, -10]}]}
        solve_part(beam, bad)
    with pytest.raises(GeometryError, match="finite"):
        solve_part(
            beam,
            {
                **base,
                "loads": [
                    {
                        "name": "squeeze",
                        "type": "pressure",
                        "magnitude": float("nan"),
                        "select": {"kind": "plane", "normal": [1, 0, 0]},
                    }
                ],
            },
        )


def test_a_calculix_timeout_is_reported_not_raised(monkeypatch):
    from aicad import fea, modal

    def slow(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout"))

    monkeypatch.setattr("aicad.sandbox.run_confined_helper", slow)
    monkeypatch.setattr("aicad.prereqs.find_program", lambda _name, project=None: "ccx")
    mesh = {
        "points": np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]),
        "elements": np.array([[0, 1, 2, 3]]),
        "order": 1,
    }
    forces = np.zeros((4, 3))
    forces[1, 0] = 1.0
    disp, stress, warning = fea._run_ccx(
        mesh, np.array([0], dtype=np.int32), forces, {"youngs_modulus_mpa": 2000.0, "poisson": 0.3}
    )
    assert disp is None and stress is None
    assert "did not finish" in warning and "mesh_size_mm" in warning

    monkeypatch.setattr(modal, "_frequency_deck", lambda *_args: "*HEADING\n")
    frequencies, vectors, warning = modal._frequency_once([], [], [], 1, 5)
    assert frequencies is None and vectors is None
    assert "did not finish in 5 s" in warning
    with pytest.raises(GeometryError, match="did not finish"):
        modal._run_frequency([], [], [], 1, 5)



def _one_tet_mesh(mesh_size: float = 2.0) -> dict:
    return {
        "points": np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]),
        "elements": np.array([[0, 1, 2, 3]]),
        "order": 1,
        "mesh_size_mm": mesh_size,
    }


def test_a_solver_stopped_by_its_limit_tries_the_iterative_solver_then_names_the_equations(monkeypatch):
    """The direct solver ran out of memory near 250,000 equations with only a truncated log."""
    from aicad import fea
    from aicad.sandbox import _HelperResult

    decks: list[str] = []
    real_deck = fea.solver_deck

    def deck(lines):
        text = real_deck(lines)
        decks.append(text)
        return text

    def stopped(_command, **_kwargs):
        return _HelperResult(-1, "Factoring the system of equations using the symmetric spooles solver", "", limit_hit=True)

    monkeypatch.setattr(fea, "solver_deck", deck)
    monkeypatch.setattr("aicad.sandbox.run_confined_helper", stopped)
    monkeypatch.setattr("aicad.prereqs.find_program", lambda _name, project=None: "ccx")
    mesh = _one_tet_mesh()
    forces = np.zeros((4, 3))
    forces[1, 0] = 1.0
    disp, stress, warning = fea._run_ccx(
        mesh, np.array([0], dtype=np.int32), forces, {"youngs_modulus_mpa": 2000.0, "poisson": 0.3}
    )
    assert disp is None and stress is None
    assert len(decks) == 2
    assert "\n*STATIC\n" in decks[0]
    assert "\n*STATIC, SOLVER=ITERATIVE CHOLESKY\n" in decks[1]
    assert "ran out of" not in warning and "180 s of CPU it may use" not in warning
    assert "was stopped at the memory or CPU limit (" in warning and "s of CPU time)" in warning
    assert "9 equations" in warning and "mesh_size_mm" in warning
    assert mesh["equations"] == 9 and mesh["linear_solver"] == "iterative"


def test_a_large_system_goes_to_the_iterative_solver(monkeypatch):
    from aicad import fea

    decks: list[str] = []
    monkeypatch.setattr(fea, "solver_deck", lambda lines: decks.append("\n".join(lines)) or "")
    monkeypatch.setattr("aicad.paths.publish_text", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("aicad.prereqs.find_program", lambda _name, project=None: None)
    forces = np.zeros((4, 3))
    forces[1, 0] = 1.0
    material = {"youngs_modulus_mpa": 2000.0, "poisson": 0.3}
    fea._run_ccx(_one_tet_mesh(), np.array([0], dtype=np.int32), forces, material)
    monkeypatch.setattr(fea, "_DIRECT_SOLVER_EQUATIONS", 8)
    fea._run_ccx(_one_tet_mesh(), np.array([0], dtype=np.int32), forces, material)
    assert "\n*STATIC\n" in decks[0] and "SOLVER=" not in decks[0]
    assert "*STATIC, SOLVER=ITERATIVE CHOLESKY" in decks[1]


def test_the_suggested_mesh_brings_the_solve_within_reach():
    from aicad import fea

    # Equations go as 1 / size cubed; the suggestion is never finer than the mesh that failed.
    assert fea._coarser_mesh_size(2.2, 367_740) == 2.8
    assert fea._coarser_mesh_size(1.7, 900_000) == 2.0
    assert fea._equation_count(10, np.array([0, 0, 1])) == 24
    text = fea._solve_too_big("did not finish in 180 s", 900_000, 300_000, 1.7)
    assert "900,000 equations" in text and "mesh_size_mm 2 or larger" in text


@needs_solver
def test_the_iterative_solver_agrees_with_the_direct_one(monkeypatch):
    from aicad import fea
    from aicad.fea import solve_part

    direct = solve_part(_beam(), _cantilever(2.0), part_name="beam")
    monkeypatch.setattr(fea, "_DIRECT_SOLVER_EQUATIONS", 0)
    iterative = solve_part(_beam(), _cantilever(2.0), part_name="beam")
    assert direct.status == iterative.status == "solved"
    assert direct.mesh["linear_solver"] == "direct" and iterative.mesh["linear_solver"] == "iterative"
    assert iterative.mesh["equations"] == direct.mesh["equations"] > 0
    assert any("iterative Cholesky" in line for line in iterative.assumptions)
    assert not any("iterative Cholesky" in line for line in direct.assumptions)
    assert abs(iterative.max_displacement_mm - direct.max_displacement_mm) <= 0.01 * direct.max_displacement_mm
    assert abs(iterative.max_von_mises_mpa - direct.max_von_mises_mpa) <= 0.01 * direct.max_von_mises_mpa

def test_calculix_is_started_with_stdin_closed(monkeypatch):
    import sys

    from aicad import fea, modal

    seen: list[dict] = []

    def run(_command, **kwargs):
        seen.append(kwargs)

        class Done:
            returncode = 1
            stdout = ""
            stderr = ""

        return Done()

    if sys.platform == "win32":
        def spawn(_command, **_kwargs):
            seen.append({"stdin": subprocess.DEVNULL})
            return (1, "", "", False, False, "appcontainer")

        monkeypatch.setattr("aicad.sandbox_windows.spawn", spawn)
        from aicad.sandbox_windows import _launch
        import inspect

        assert ' "NUL"' in inspect.getsource(_launch) or '"NUL"' in inspect.getsource(_launch)
    else:
        monkeypatch.setattr("subprocess.run", run)
    monkeypatch.setattr("aicad.prereqs.find_program", lambda _name, project=None: "ccx")
    mesh = {
        "points": np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]),
        "elements": np.array([[0, 1, 2, 3]]),
        "order": 1,
    }
    forces = np.zeros((4, 3))
    forces[1, 0] = 1.0
    fea._run_ccx(mesh, np.array([0], dtype=np.int32), forces, {"youngs_modulus_mpa": 2000.0, "poisson": 0.3})
    monkeypatch.setattr(modal, "_frequency_deck", lambda *_args: "*HEADING\n")
    modal._frequency_once([], [], [], 1, 5)
    assert len(seen) == 2
    assert all(item.get("stdin") is subprocess.DEVNULL for item in seen)


@needs_solver
def test_torque_twists_a_tube_like_the_closed_form(tmp_path):
    from aicad.fea import solve_part

    tube = kernel.make_from_ops(
        [
            {"op": "add", "shape": "cylinder", "radius": 10, "height": 20, "center": [0, 0, 10]},
            {"op": "cut", "shape": "cylinder", "radius": 7, "height": 30, "center": [0, 0, 10]},
        ]
    )
    setup = {
        "material": "aluminum_6061",
        "mesh_size_mm": 2,
        "fixes": [{"name": "floor", "select": {"kind": "plane", "normal": [0, 0, -1]}}],
        "loads": [
            {
                "name": "twist",
                "type": "torque",
                "torque_nm": 10,
                "select": {"kind": "plane", "normal": [0, 0, 1]},
                "axis": [0, 0, 1],
            }
        ],
    }
    report = solve_part(tube, setup, part_name="tube", probes_mm=[[10, 0, 20], [-10, 0, 20], [0, 10, 20]])
    assert report.status == "solved"
    east, west, north = (probe["displacement_mm"] for probe in report.probes)
    # Right-handed about +Z: +X moves toward +Y, -X toward -Y, +Y toward -X.
    assert east[1] > 0 and west[1] < 0 and north[0] < 0
    # A couple has no net force, so opposite points move by the same amount.
    assert abs(east[1] + west[1]) < 0.1 * abs(east[1])
    shear_modulus = 68900.0 / (2 * (1 + 0.33))
    polar = math.pi / 2 * (10**4 - 7**4)
    expected = 10_000 * 20 / (shear_modulus * polar) * 10
    assert 0.9 * expected < east[1] < 1.1 * expected
    assert any("couple" in line for line in report.assumptions)

    no_axis = dict(setup, loads=[dict(setup["loads"][0], axis=None)])
    with pytest.raises(GeometryError, match="needs an axis"):
        solve_part(tube, no_axis, part_name="tube")


def test_a_mesh_on_a_worker_thread_leaves_sigpipe_ignored():
    """Gmsh resets SIGPIPE to its default, which ends the server with status 141.

    The server meshes on a worker thread, then writes to its client. A write
    to a client that has gone away must raise BrokenPipeError, not stop the
    process. The SIGINT handler must also stay the one Python installed.
    """
    pytest.importorskip("gmsh")
    if sys.platform == "win32":
        pytest.skip("SIGPIPE is a Unix signal")
    code = (
        "import os, signal, threading\n"
        "from aicad import kernel\n"
        "from aicad.fea import _mesh_solid\n"
        "before = signal.getsignal(signal.SIGINT)\n"
        "cube = kernel.make_from_ops([{'op': 'add', 'shape': 'box', 'size': [10, 10, 10], 'center': [0, 0, 5]}])\n"
        "errors = []\n"
        "def mesh():\n"
        "    try:\n"
        "        _mesh_solid(cube, 2.5, 1)\n"
        "    except BaseException as exc:\n"
        "        errors.append(repr(exc))\n"
        "for _ in range(2):\n"
        "    worker = threading.Thread(target=mesh)\n"
        "    worker.start()\n"
        "    worker.join()\n"
        "assert not errors, errors\n"
        "assert signal.getsignal(signal.SIGINT) is before\n"
        "read_end, write_end = os.pipe()\n"
        "os.close(read_end)\n"
        "try:\n"
        "    os.write(write_end, b'x')\n"
        "except BrokenPipeError:\n"
        "    print('broken pipe raised')\n"
    )
    env = dict(os.environ, PYTHONFAULTHANDLER="1")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300, env=env)
    # A crash report ends with a long list of extension modules, so keep the
    # start of stderr, where the thread stacks are.
    assert proc.returncode == 0, (proc.returncode, proc.stderr[:3000])
    assert "broken pipe raised" in proc.stdout


def test_every_mesh_runs_on_one_gmsh_thread_whatever_thread_asks(monkeypatch):
    """Gmsh crashes on macOS when a new thread starts it after another has exited."""
    gmsh = pytest.importorskip("gmsh")
    import threading

    from aicad import fea

    started: list[int] = []
    real_initialize = gmsh.initialize

    def initialize(*args, **kwargs):
        started.append(threading.get_ident())
        return real_initialize(*args, **kwargs)

    monkeypatch.setattr(gmsh, "initialize", initialize)
    cube = kernel.make_from_ops([{"op": "add", "shape": "box", "size": [10, 10, 10], "center": [0, 0, 5]}])
    callers: list[int] = []
    failures: list[BaseException] = []

    def mesh_once():
        callers.append(threading.get_ident())
        try:
            fea._mesh_solid(cube, 2.5, 1)
        except BaseException as exc:  # pragma: no cover - reported below
            failures.append(exc)

    for _ in range(3):
        thread = threading.Thread(target=mesh_once)
        thread.start()
        thread.join()
    mesh_once()
    assert not failures, failures
    assert len(started) == 4
    assert len(set(started)) == 1
    assert started[0] not in callers


def _run_one_tet_with(monkeypatch, answers):
    """Run the one-tet solve with each helper answer in turn. The decks the solver was given, and the warning."""
    from aicad import fea
    from aicad.sandbox import _HelperResult

    decks: list[str] = []
    real_deck = fea.solver_deck

    def deck(lines):
        text = real_deck(lines)
        decks.append(text)
        return text

    queue = list(answers)

    def helper(_command, **_kwargs):
        return _HelperResult(*queue.pop(0))

    monkeypatch.setattr(fea, "solver_deck", deck)
    monkeypatch.setattr("aicad.sandbox.run_confined_helper", helper)
    monkeypatch.setattr("aicad.prereqs.find_program", lambda _name, project=None: "ccx")
    forces = np.zeros((4, 3))
    forces[1, 0] = 1.0
    disp, _stress, warning = fea._run_ccx(
        _one_tet_mesh(), np.array([0], dtype=np.int32), forces, {"youngs_modulus_mpa": 2000.0, "poisson": 0.3}
    )
    assert disp is None
    return decks, warning


def test_a_failed_allocation_tries_the_iterative_solver_where_no_limit_signal_is_seen(monkeypatch):
    """Out of memory on Linux can fail an allocation: the process exits with an error, not a kill."""
    decks, warning = _run_one_tet_with(
        monkeypatch,
        [
            (16, "*ERROR in u_malloc: error allocating memory\n", ""),
            (16, "*ERROR in u_malloc: error allocating memory\n", ""),
        ],
    )
    assert len(decks) == 2 and "SOLVER=ITERATIVE CHOLESKY" in decks[1]
    assert "9 equations" in warning and "mesh_size_mm" in warning


# How a crash comes back: a signal elsewhere, the access-violation status on Windows.
_CRASH_CODE = 0xC0000005 - 0x100000000 if sys.platform == "win32" else -11


def test_a_solver_that_crashes_without_an_error_tries_the_iterative_solver(monkeypatch):
    """A failed allocation in SPOOLES often ends in a crash with no error line."""
    decks, warning = _run_one_tet_with(
        monkeypatch,
        [
            (_CRASH_CODE, "Factoring the system of equations using the symmetric spooles solver\n", ""),
            (_CRASH_CODE, "", ""),
        ],
    )
    assert len(decks) == 2 and "SOLVER=ITERATIVE CHOLESKY" in decks[1]
    assert f"without a result file or an error of its own (exit code {_CRASH_CODE})" in warning
    assert "9 equations" in warning


@pytest.mark.parametrize("code", [127, 126, 1])
def test_a_run_that_never_started_is_not_retried(monkeypatch, code):
    """The sandbox refusing CalculiX is not the model's size, and another solver would not help."""
    decks, warning = _run_one_tet_with(monkeypatch, [(code, "", "")])
    assert len(decks) == 1
    assert warning == "CalculiX failed."
    assert "allocation" not in warning


def test_only_a_signal_or_a_windows_crash_code_is_a_crash(monkeypatch):
    from aicad import fea

    monkeypatch.setattr(fea.sys, "platform", "win32")
    for code in (0xC0000005, 0xC0000017, 0xC00000FD, 0xC0000005 - 0x100000000):
        assert fea._died_in_a_crash(code), hex(code & 0xFFFFFFFF)
    # A missing DLL: the program never ran.
    for code in (0xC0000135, 0xC0000135 - 0x100000000, 127, 126, 1, 0):
        assert not fea._died_in_a_crash(code), code
    monkeypatch.setattr(fea.sys, "platform", "linux")
    assert fea._died_in_a_crash(-11) and fea._died_in_a_crash(-6)
    for code in (127, 126, 1, 0):
        assert not fea._died_in_a_crash(code), code


def test_an_error_calculix_reports_is_not_retried(monkeypatch):
    decks, warning = _run_one_tet_with(
        monkeypatch,
        [(201, "*ERROR reading *MATERIAL: no elastic constants\n", "")],
    )
    assert len(decks) == 1
    assert "no elastic constants" in warning
