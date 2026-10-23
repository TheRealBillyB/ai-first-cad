"""Linear static solves. The CalculiX binary is optional in CI; these skip without it."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from aicad import kernel
from aicad.document import Document
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
        [sys.executable, "-m", "aicad", "ops", str(ops), "--name", "beam", "--workspace", str(workspace)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert ran.returncode == 0, ran.stderr
    listed = subprocess.run(
        [sys.executable, "-m", "aicad", "materials", "--workspace", str(workspace)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert listed.returncode == 0, listed.stderr
    assert "pla" in listed.stdout
    solved = subprocess.run(
        [
            sys.executable,
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


def test_missing_solver_message_names_ccx(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    available, reason = fea_available()
    assert available is False
    assert "ccx" in reason
