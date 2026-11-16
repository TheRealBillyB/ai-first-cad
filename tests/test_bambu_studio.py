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


def _fake_bambu(monkeypatch, tmp_path, second):
    """Bambu Studio that slices once with a support warning, then does ``second`` on the supported run."""
    from aicad.sandbox import _HelperResult

    monkeypatch.setattr(printprep, "_bambu_profile_root", lambda _executable: tmp_path)
    monkeypatch.setattr(printprep, "_bambu_profile", lambda *_args, **_kwargs: {})
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        folder = Path(kwargs["cwd"])
        if len(calls) == 1:
            result = {
                "return_code": 0,
                "sliced_plates": [
                    {"total_predication": 600, "filaments": [{"total_used_g": 4.0}], "warning_message": "It seems object oriented.stl has floating cantilever."}
                ],
            }
            (folder / "result.json").write_text(json.dumps(result), encoding="utf-8")
            return _HelperResult(0, "", "")
        if second == "crash":
            return _HelperResult(-1073741819, "", "")
        (folder / "result.json").write_text(json.dumps({"return_code": -13, "error_string": "Failed exporting 3mf files"}), encoding="utf-8")
        return _HelperResult(0, "", "")

    monkeypatch.setattr("aicad.sandbox.run_confined_helper", run)
    return calls


@pytest.mark.parametrize("second", ["crash", "error"])
def test_a_supported_slice_that_fails_is_not_reported_as_done(monkeypatch, tmp_path, second):
    """The supported run used the same folder, so the first run's result.json was read as its own."""
    from aicad.tool_directory import held_subprocess_directory

    calls = _fake_bambu(monkeypatch, tmp_path, second)
    with held_subprocess_directory() as work:
        report = printprep._bambu_slice_report("C:/x/bambu-studio.exe", work, None, None, None)
    assert len(calls) == 2
    assert report["supports"] == "warned"
    assert report["seconds"] == 600 and report["grams"] == pytest.approx(4.0)
    assert "floating cantilever" in report["slicer_warning"]


def test_a_failed_return_code_is_a_failed_slice(monkeypatch, tmp_path):
    from aicad.sandbox import _HelperResult
    from aicad.tool_directory import held_subprocess_directory

    monkeypatch.setattr(printprep, "_bambu_profile_root", lambda _executable: tmp_path)
    monkeypatch.setattr(printprep, "_bambu_profile", lambda *_args, **_kwargs: {})

    def run(command, **kwargs):
        result = {"return_code": -13, "error_string": "Failed", "sliced_plates": [{"total_predication": 1, "filaments": []}]}
        (Path(kwargs["cwd"]) / "result.json").write_text(json.dumps(result), encoding="utf-8")
        return _HelperResult(0, "", "")

    monkeypatch.setattr("aicad.sandbox.run_confined_helper", run)
    with held_subprocess_directory() as work:
        parsed, log = printprep._slice_bambu("C:/x/bambu-studio.exe", work, None, None, supports=False)
    assert parsed is None and "returned -13" in log


def test_a_p1s_slices_with_the_p1s_profile():
    assert printprep.lookup_printer("p1s")["id"] == "bambu_p1"
    assert printprep._BAMBU_PROFILES["bambu_p1"][0] == "Bambu Lab P1S 0.4 nozzle"
    assert printprep.lookup_printer("p1p")["id"] == "bambu_p1p"
    assert printprep._BAMBU_PROFILES["bambu_p1p"][0] == "Bambu Lab P1P 0.4 nozzle"
