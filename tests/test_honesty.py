"""Labels on checks, the blueprint sheet, and the mesh retry ladder."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from aicad import Document, kernel
from aicad.bolts import check_joint
from aicad.errors import GeometryError
from aicad.honesty import attach_sheet, sheet_lines
from aicad.modal import demote_if_simplified, fatigue_check
from aicad.requirements import evaluate

BOX = "part = Box(10, 20, 30)\n"


def test_measurement_is_a_verified_check_and_wall_thickness_is_an_estimate(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("box", BOX)
    measured = document.measure("box").to_dict()
    assert measured["result_kind"] == "verified_check"
    assert measured["result_label"] == "Verified check"
    assert measured["limits"]
    assert measured["assumptions"]

    wall = document.check_wall_thickness("box", 1.0).to_dict()
    assert wall["result_kind"] == "rough_estimate"
    assert wall["result_label"] == "Rough estimate"
    assert wall["limits"]


def test_clearance_is_verified_and_a_section_names_both_kinds(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("a", "part = Pos(0, 0, 0) * Box(10, 10, 10)\n")
    document.create_part_from_script("b", "part = Pos(30, 0, 0) * Box(10, 10, 10)\n")
    gap = document.check_clearance("a", "b").to_dict()
    assert gap["result_kind"] == "verified_check"
    assert gap["result_label"] == "Verified check"

    section = document.section("a", {"axis": "z", "offset": 0}).to_dict()
    labels = {item["result_name"]: item["result_label"] for item in section["results"]}
    assert labels["Cut area"] == "Verified check"
    assert labels["Section thickness"] == "Rough estimate"
    assert section["result_kind"] == "rough_estimate"
    assert "Verified check" in " ".join(sheet_lines(section))
    assert "Rough estimate" in " ".join(sheet_lines(section))


def test_bolts_fatigue_and_requirements_say_what_they_are(tmp_path):
    joint = check_joint({"size": "M8", "grip_mm": 20, "axial_n": 1000, "shear_n": 500})
    assert joint["result_kind"] == "rough_estimate"
    assert joint["result_label"] == "Rough estimate"
    assert joint["limits"]

    life = fatigue_check({"stress_amplitude_mpa": 40, "material": "steel"})
    assert life["result_kind"] == "rough_estimate"
    assert "handbook" in life["result_what"].lower() or "Goodman" in " ".join(life["assumptions"])

    document = Document(tmp_path)
    document.create_part_from_script("box", BOX)
    checks = {
        item["kind"]: item
        for item in evaluate(
            document,
            [
                {"id": "light", "kind": "max_mass", "part": "box", "material": "pla", "max_grams": 500},
                {"id": "bed", "kind": "fits_build_volume", "part": "box", "size_mm": [50, 50, 50]},
            ],
        )
    }
    assert checks["max_mass"]["result_kind"] == "rough_estimate"
    assert checks["fits_build_volume"]["result_kind"] == "verified_check"


def test_blueprint_sheet_is_blue_and_the_cli_json_carries_it(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("box", BOX)
    payload = attach_sheet(
        document.measure("box").to_dict(),
        tmp_path / "renders" / "sheets",
        "measure",
        root=tmp_path,
    )
    image = Image.open(tmp_path / payload["sheet"]["path"])
    assert image.getpixel((20, 20)) == (10, 48, 96)
    assert "Verified check" in " ".join(sheet_lines(payload))

    completed = subprocess.run(
        [sys.executable, "-P", "-m", "aicad", "measure", "box", "--workspace", str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    reported = json.loads(completed.stdout)
    assert reported["result_label"] == "Verified check"
    assert reported["result_kind"] == "verified_check"
    assert (tmp_path / reported["sheet"]["path"]).is_file()


def test_a_simplified_modal_mesh_is_not_clear_or_resonant():
    for status in ("resonance", "clear"):
        updated = demote_if_simplified({"status": status, "suggestions": ["keep this"]}, True)
        assert updated["status"] == "inconclusive"
        assert updated["reliable"] is False
        assert updated["computed_status"] == status
        assert updated["suggestions"][-1] == "keep this"
    untouched = demote_if_simplified({"status": "clear", "suggestions": []}, False)
    assert untouched["status"] == "clear"
    assert "reliable" not in untouched


def test_mesh_ladder_retries_finer_and_does_not_call_that_degraded(monkeypatch):
    from aicad import fea

    shape = kernel.make_from_ops([{"op": "add", "shape": "box", "size": [20, 20, 20]}])
    calls = []

    def fake(solid, mesh_size, order, *, algorithm, optimize, occ_fix):
        calls.append(mesh_size)
        if len(calls) < 3:
            raise GeometryError("PLC Error: A segment and a facet intersect")
        return {"points": None, "order": order, "element_count": 10, "mesh_size_mm": mesh_size}

    monkeypatch.setattr(fea, "_mesh_attempt", fake)
    _prepared, mesh = fea._mesh_ladder(shape, 8, 2)
    assert calls[2] < 8
    assert mesh["degraded"] is False
    notes = " ".join(mesh["mesh_notes"])
    assert "PLC" in notes
    assert "8" in notes


def test_mesh_ladder_reports_a_linear_fallback(monkeypatch):
    from aicad import fea

    shape = kernel.make_from_ops([{"op": "add", "shape": "box", "size": [20, 20, 20]}])

    def fake(solid, mesh_size, order, *, algorithm, optimize, occ_fix):
        if order != 1:
            raise GeometryError("PLC Error: A segment and a facet intersect")
        return {"points": None, "order": order, "element_count": 4, "mesh_size_mm": mesh_size}

    monkeypatch.setattr(fea, "_mesh_attempt", fake)
    _prepared, mesh = fea._mesh_ladder(shape, 8, 2)
    assert mesh["order"] == 1
    assert mesh["degraded"] is True
    assert any("linear" in line.lower() for line in mesh["simplifications"])


def test_mesh_ladder_fails_in_our_words_when_every_attempt_fails(monkeypatch):
    from aicad import fea

    shape = kernel.make_from_ops([{"op": "add", "shape": "box", "size": [20, 20, 20]}])

    def fake(solid, mesh_size, order, *, algorithm, optimize, occ_fix):
        raise GeometryError("PLC Error: A segment and a facet intersect")

    monkeypatch.setattr(fea, "_mesh_attempt", fake)
    with pytest.raises(GeometryError, match="could not mesh"):
        fea._mesh_ladder(shape, 8, 2)


def test_a_thin_wall_does_not_crash_the_mesher():
    pytest.importorskip("gmsh")
    from aicad.fea import _mesh_solid

    shape = kernel.make_from_ops(
        [
            {"op": "add", "shape": "cylinder", "radius": 8, "height": 12},
            {"op": "cut", "shape": "cylinder", "radius": 7.2, "height": 16, "center": [0, 0, -2]},
        ]
    )
    try:
        mesh = _mesh_solid(shape, 6, 2)
    except GeometryError as exc:
        assert "could not mesh" in str(exc).lower()
    else:
        assert mesh["element_count"] > 0
        if float(mesh.get("mesh_size_mm") or 6) < 6 or mesh.get("degraded"):
            assert mesh.get("mesh_notes")


def test_calculix_stays_a_separate_process():
    root = Path(__file__).resolve().parents[1] / "src" / "aicad"
    for name in ("fea.py", "modal.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "subprocess.run" in text
        assert '["ccx"' in text
        lowered = text.lower()
        assert "import calculix" not in lowered
        assert "import ccx" not in lowered
