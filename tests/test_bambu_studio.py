"""Bambu Studio slices a print check, as OrcaSlicer and PrusaSlicer do.

Slicer detection looked only for OrcaSlicer and PrusaSlicer on PATH. The vise
was sliced with Bambu Studio by a script outside the app. Bambu Studio is
found on PATH or in its usual install folder, its profiles are folded with
their parents, and its result.json gives time, grams, and its warnings.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aicad import printprep
from aicad.workspace import Workspace

_BAMBU = printprep._bambu_installs()


def test_bambu_result_gives_time_grams_and_the_warning():
    data = {
        "return_code": 0,
        "sliced_plates": [
            {
                "total_predication": 3725.4,
                "filaments": [{"total_used_g": 10.25}, {"total_used_g": 1.5}],
                "warning_message": "It seems object part.stl has floating cantilever.",
            }
        ],
    }
    parsed = printprep._bambu_result(data)
    assert parsed["seconds"] == 3725
    assert parsed["time"] == "1h 2m 5s"
    assert parsed["grams"] == pytest.approx(11.75)
    assert "floating cantilever" in parsed["warning"]
    assert printprep._needs_supports(parsed["warning"])
    empty = printprep._bambu_result({"error_string": "Failed"})
    assert empty["seconds"] is None and empty["grams"] is None


def test_a_profile_is_folded_with_its_parents(tmp_path):
    root = tmp_path / "BBL"
    (root / "process").mkdir(parents=True)
    (root / "process" / "base.json").write_text(json.dumps({"name": "base", "wall_loops": "2", "layer_height": "0.2"}), encoding="utf-8")
    (root / "process" / "mid.json").write_text(json.dumps({"name": "mid", "inherits": "base", "wall_loops": "3"}), encoding="utf-8")
    (root / "process" / "top.json").write_text(json.dumps({"name": "top", "inherits": "mid", "sparse_infill_density": "15%"}), encoding="utf-8")
    folded = printprep._bambu_profile(root, "process", "top", {"enable_support": "0"})
    assert folded == {
        "name": "top",
        "wall_loops": "3",
        "layer_height": "0.2",
        "sparse_infill_density": "15%",
        "from": "system",
        "enable_support": "0",
    }


def test_bambu_studio_is_found_in_its_install_folder(tmp_path, monkeypatch):
    program = tmp_path / "Bambu Studio" / "bambu-studio.exe"
    program.parent.mkdir()
    program.write_bytes(b"")
    program.chmod(0o755)
    monkeypatch.setattr(printprep, "_BAMBU_INSTALLS", ((str(program),),))
    monkeypatch.setattr("aicad.prereqs.find_program", lambda _name, project=None: None)
    assert printprep.find_slicers() == [str(program.resolve())]
    assert printprep.is_bambu(printprep.find_slicers()[0])


def test_a_warning_about_supports_slices_again_with_them(monkeypatch):
    calls = []

    def fake(executable, work, printer_id, layer_height_mm, supports):
        calls.append(supports)
        warning = None if supports else "It seems object oriented.stl has floating cantilever."
        parsed = {"seconds": 600 if supports else 500, "time": "10m 0s", "grams": 4.0, "cost": None, "cost_source": None, "warning": warning}
        parsed["profiles"] = {"printer": "bambu_a1", "machine": "m", "process": "p", "filament": "f"}
        parsed["printer_matched"] = printer_id == "bambu_a1"
        return parsed, ""

    monkeypatch.setattr(printprep, "_slice_bambu", fake)
    report = printprep._bambu_slice_report("C:/x/bambu-studio.exe", None, None, None, 25)
    assert calls == [False, True]
    assert report["supports"] == "on" and report["seconds"] == 600
    assert "floating cantilever" in report["slicer_warning"]
    assert report["cost"] == pytest.approx(0.1)
    assert "No printer was named" in report["assumptions"][0]


@pytest.mark.skipif(not _BAMBU, reason="Bambu Studio is not installed")
def test_a_real_print_check_is_sliced_by_bambu_studio(tmp_path):
    workspace = Workspace(tmp_path / "project")
    workspace.execute("create_part_from_ops", {"name": "box", "ops": [{"op": "add", "shape": "box", "size": [20, 20, 20], "center": [0, 0, 10]}]}, source="agent")
    setup = {"orientation": "z_up", "slice": True, "render": False, "printer": "bambu_x1"}
    slicer = workspace.execute("check_print", {"name": "box", "setup": setup}, source="agent")["slicer"]
    if "bambu" not in str(slicer.get("slicer")).lower():
        pytest.skip("Another slicer on PATH sliced first.")
    assert slicer["status"] == "sliced"
    assert slicer["seconds"] > 0 and slicer["grams"] > 0
    assert slicer["printer_matched"] is True
    assert Path(slicer["slicer"]).name.lower().startswith("bambu")
