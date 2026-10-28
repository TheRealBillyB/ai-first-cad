"""Print orientation, overhangs, printer fit, and the slicer skip path."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from aicad.document import Document
from aicad.errors import GeometryError
from aicad.printprep import _score, parse_gcode, slice_mesh
from aicad.workspace import Workspace


def _plate(document: Document) -> None:
    document.create_part_from_ops(
        "plate",
        [{"op": "add", "shape": "box", "size": [40, 20, 5], "center": [0, 0, 2.5]}],
    )


def _shelf(document: Document) -> None:
    document.create_part_from_ops(
        "shelf",
        [
            {"op": "add", "shape": "box", "size": [8, 30, 40], "center": [4, 0, 20]},
            {"op": "add", "shape": "box", "size": [36, 30, 4], "center": [22, 0, 38]},
        ],
    )


def test_a_flat_plate_on_the_bed_needs_no_support(tmp_path):
    document = Document(tmp_path)
    _plate(document)
    report = document.check_print("plate", {"orientation": "z_up", "slice": False})
    assert report["support_area_mm2"] == pytest.approx(0, abs=1)
    assert report["height_mm"] == pytest.approx(5, abs=0.2)
    assert report["views"]
    ender = next(row for row in report["printers"] if row["id"] == "ender_3")
    assert ender["fits"] is True


def test_a_shelf_overhangs_until_it_is_turned(tmp_path):
    document = Document(tmp_path)
    _shelf(document)
    upright = document.check_print("shelf", {"orientation": "z_up", "render": False, "slice": False})
    assert upright["support_area_mm2"] > 500
    best = document.check_print("shelf", {"orientation": "best", "render": False, "slice": False})
    assert best["support_area_mm2"] < upright["support_area_mm2"]
    assert best["orientation_automatic"] is True


def test_a_long_part_fails_the_ender_and_a_plate_passes(tmp_path):
    workspace = Workspace(tmp_path)
    workspace.execute(
        "create_part_from_ops",
        {"name": "rod", "ops": [{"op": "add", "shape": "box", "size": [300, 10, 10], "center": [0, 0, 5]}]},
    )
    workspace.execute(
        "create_part_from_ops",
        {"name": "plate", "ops": [{"op": "add", "shape": "box", "size": [40, 20, 5], "center": [0, 0, 2.5]}]},
    )
    workspace.execute(
        "set_requirements",
        {
            "requirements": [
                {"id": "rod", "kind": "fits_printer", "part": "rod", "printer": "ender_3", "orientation": "best", "title": "Rod on the Ender"},
                {"id": "plate", "kind": "fits_printer", "part": "plate", "printer": "prusa_mk4", "orientation": "z_up", "title": "Plate on the MK4"},
                {"id": "alias", "kind": "fits_printer", "part": "plate", "printer": "a1", "orientation": "best", "title": "Plate on the A1"},
            ]
        },
    )
    checks = {item["id"]: item for item in workspace.execute("check_requirements", {"run": False})["checks"]}
    assert checks["rod"]["status"] == "fail"
    assert checks["plate"]["status"] == "pass"
    assert checks["alias"]["status"] == "pass"
    assert "Prusa MK4" in checks["plate"]["detail"]


def test_a_bar_stands_on_end_when_that_is_what_fits_the_ender(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_ops(
        "bar",
        [{"op": "add", "shape": "box", "size": [240, 10, 10], "center": [0, 0, 5]}],
    )
    plain = document.check_print("bar", {"orientation": "best", "render": False, "slice": False})
    assert plain["height_mm"] == pytest.approx(10, abs=0.5)
    ender = next(row for row in plain["printers"] if row["id"] == "ender_3")
    assert ender["fits"] is False
    assert "x_up" in ender["note"]
    fitted = document.check_print(
        "bar",
        {"orientation": "best", "printer": "ender_3", "render": False, "slice": False},
    )
    assert fitted["status"] == "pass"
    assert fitted["orientation"] in ("x_up", "x_down")
    assert fitted["height_mm"] == pytest.approx(240, abs=0.5)
    assert fitted["printer"]["fits"] is True
    assert fitted["printer"]["note"].startswith("Fits in this orientation")
    workspace = Workspace(tmp_path / "req")
    workspace.execute(
        "create_part_from_ops",
        {"name": "bar", "ops": [{"op": "add", "shape": "box", "size": [240, 10, 10], "center": [0, 0, 5]}]},
    )
    workspace.execute(
        "set_requirements",
        {
            "requirements": [
                {
                    "id": "bar",
                    "kind": "fits_printer",
                    "part": "bar",
                    "printer": "ender_3",
                    "orientation": "best",
                    "title": "Bar on the Ender",
                }
            ]
        },
    )
    check = workspace.execute("check_requirements", {"run": False})["checks"][0]
    assert check["status"] == "pass"
    assert check["detail"].split("orientation ", 1)[1].startswith(("x_up", "x_down"))


def test_unknown_orientation_and_printer_are_rejected(tmp_path):
    document = Document(tmp_path)
    _plate(document)
    with pytest.raises(GeometryError, match="Orientation"):
        document.check_print("plate", {"orientation": "diagonal"})
    with pytest.raises(GeometryError, match="printer"):
        document.check_print("plate", {"printer": "not-a-printer"})


def test_gcode_comments_become_time_grams_and_cost():
    parsed = parse_gcode(
        "\n".join(
            [
                "; generated by PrusaSlicer",
                "; estimated printing time (normal mode) = 1h 2m 3s",
                "; filament used [g] = 12.5",
                "; total filament cost = 0.40",
                "G1 X0 Y0",
            ]
        )
    )
    assert parsed["seconds"] == 3723
    assert parsed["grams"] == pytest.approx(12.5)
    assert parsed["cost"] == pytest.approx(0.4)
    assert parsed["cost_source"] == "slicer"


def test_comments_at_the_end_of_a_file_still_count():
    body = "\n".join([f"G1 X{index}" for index in range(100)])
    body += "\n; estimated printing time (normal mode) = 45m 10s\n; filament used [g] = 3.25\n"
    parsed = parse_gcode(body)
    assert parsed["seconds"] == 45 * 60 + 10
    assert parsed["grams"] == pytest.approx(3.25)


def test_support_follows_the_angle_from_vertical():
    # Bed triangle faces up. The other face leans 45 degrees from vertical
    # and points down, so it needs support below that angle and not above it.
    vertices = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 10.0],
            [0.0, 1.0, 10.0],
            [1.0, 0.0, 9.0],
        ]
    )
    triangles = np.array([[0, 1, 2], [3, 4, 5]])
    mesh = SimpleNamespace(vertices=vertices, triangles=triangles)
    needs = _score(mesh, "z_up", 30)["support_area_mm2"]
    clear = _score(mesh, "z_up", 60)["support_area_mm2"]
    assert needs > 0
    assert clear == pytest.approx(0)


def _span(document: Document, name: str, span: float) -> None:
    document.create_part_from_ops(
        name,
        [
            {"op": "add", "shape": "box", "size": [4, 10, 8], "center": [2, 0, 4]},
            {"op": "add", "shape": "box", "size": [span, 10, 2], "center": [4 + span / 2, 0, 7]},
            {"op": "add", "shape": "box", "size": [4, 10, 8], "center": [6 + span, 0, 4]},
        ],
    )


def test_a_short_bridge_is_not_support_and_a_longer_span_is(tmp_path):
    short = Document(tmp_path / "short")
    _span(short, "deck", 4)
    report = short.check_print("deck", {"orientation": "z_up", "render": False, "slice": False})
    assert report["bridge_mm"] == pytest.approx(5)
    assert report["bed_band_mm"] == pytest.approx(0.05)
    assert report["bridge_area_mm2"] == pytest.approx(40, abs=8)
    assert report["support_area_mm2"] == pytest.approx(0, abs=5)
    counted = short.check_print(
        "deck", {"orientation": "z_up", "bridge_mm": 0, "render": False, "slice": False}
    )
    assert counted["support_area_mm2"] == pytest.approx(40, abs=8)
    assert counted["bridge_area_mm2"] == pytest.approx(0, abs=1)

    long = Document(tmp_path / "long")
    _span(long, "deck", 6)
    wide = long.check_print("deck", {"orientation": "z_up", "render": False, "slice": False})
    assert wide["support_area_mm2"] == pytest.approx(60, abs=12)
    assert wide["bridge_area_mm2"] == pytest.approx(0, abs=1)


def test_a_floating_face_and_a_one_ended_overhang_need_support(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_ops(
        "island",
        [
            {"op": "add", "shape": "box", "size": [10, 10, 2], "center": [0, 0, 1]},
            {"op": "add", "shape": "box", "size": [10, 10, 2], "center": [0, 0, 3.2]},
        ],
    )
    near = document.check_print("island", {"orientation": "z_up", "render": False, "slice": False})
    assert near["support_area_mm2"] == pytest.approx(100, abs=15)
    document.create_part_from_ops(
        "higher",
        [
            {"op": "add", "shape": "box", "size": [10, 10, 2], "center": [0, 0, 1]},
            {"op": "add", "shape": "box", "size": [10, 10, 2], "center": [0, 0, 3.5]},
        ],
    )
    far = document.check_print("higher", {"orientation": "z_up", "render": False, "slice": False})
    assert far["support_area_mm2"] == pytest.approx(100, abs=15)

    document.create_part_from_ops(
        "tongue",
        [
            {"op": "add", "shape": "box", "size": [6, 8, 10], "center": [3, 0, 5]},
            {"op": "add", "shape": "box", "size": [20, 4, 2], "center": [16, 0, 9]},
        ],
    )
    lip = document.check_print("tongue", {"orientation": "z_up", "render": False, "slice": False})
    assert lip["support_area_mm2"] > 60
    with pytest.raises(GeometryError, match="bridge_mm"):
        document.check_print("tongue", {"bridge_mm": -1, "render": False, "slice": False})


def test_a_missing_slicer_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr("aicad.printprep.find_slicers", lambda: [])
    report = slice_mesh(__import__("numpy").zeros((1, 3)), __import__("numpy").zeros((0, 3), dtype=int), tmp_path)
    assert report["status"] == "skipped"
    assert "not on PATH" in report["note"]


def test_a_reparse_that_is_not_a_symlink_is_removed_before_slicing(tmp_path, monkeypatch):
    from aicad import paths
    from aicad.printprep import _drop_link

    path = tmp_path / "print.gcode"
    path.write_text("keep\n", encoding="utf-8")
    removed = []
    monkeypatch.setattr(paths, "is_reparse", lambda candidate: Path(candidate) == path)

    def unlink(candidate: Path) -> None:
        removed.append(Path(candidate))
        Path(candidate).unlink()

    monkeypatch.setattr(paths, "_unlink_reparse", unlink)
    _drop_link(path)
    assert removed == [path]
    assert not path.exists()


def test_a_support_warning_slices_again_with_supports(tmp_path, monkeypatch):
    script = tmp_path / "prusa-slicer"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "supported = '--support-material' in args\n"
        "for arg in args:\n"
        "    if str(arg).endswith('.gcode'):\n"
        "        text = '; estimated printing time (normal mode) = 2m\\n'\n"
        "        text += '; filament used [g] = 1.5\\n'\n"
        "        text += '; supports\\n' if supported else '; plain\\n'\n"
        "        Path(arg).write_text(text, encoding='utf-8')\n"
        "if not supported:\n"
        "    print('The print has floating regions. Consider enabling supports.', file=sys.stderr)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setattr("aicad.printprep.find_slicers", lambda: [str(script)])
    if os.name == "nt":
        # Windows will not start a shebang script. The interpreter runs the same file.
        from aicad.printprep import _slice_command

        def command(executable, gcode, stl, price_per_kg, export, supports=False):
            built = _slice_command(executable, gcode, stl, price_per_kg, export, supports)
            return [sys.executable, *built]

        monkeypatch.setattr("aicad.printprep._slice_command", command)
    report = slice_mesh(np.zeros((1, 3)), np.zeros((0, 3), dtype=int), tmp_path / "out")
    assert report["status"] == "sliced"
    assert report["supports"] == "on"
    assert "; supports" in Path(report["gcode"]).read_text(encoding="utf-8")
    from aicad.printprep import _support_flags

    assert _support_flags("OrcaSlicer") == ["--enable-support=1"]
    assert _support_flags("prusa-slicer") == ["--support-material"]


def test_a_slicer_link_still_unlinks_a_symlink_and_leaves_the_target(tmp_path):
    from aicad.printprep import _drop_link

    target = tmp_path / "target.gcode"
    target.write_text("CANARY_HIT\n", encoding="utf-8")
    link = tmp_path / "print.gcode"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("this machine cannot create a symlink")
    _drop_link(link)
    assert not link.exists()
    assert target.read_text(encoding="utf-8") == "CANARY_HIT\n"
