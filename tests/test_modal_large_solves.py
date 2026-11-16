"""A frequency solve that is too big says so, as a static solve does.

A frequency solve stopped by the helper's memory or CPU limit came back as
"CalculiX failed" with a cut-off log, a timeout suggested only "a larger
mesh_size_mm", and a fine mesh's mode shapes were refused by a fixed 64 MiB
read cap.
"""

from __future__ import annotations

import subprocess
import sys

import numpy as np

from aicad import modal
from aicad.sandbox import _HelperResult


def _body(nodes: int = 4) -> dict:
    points = np.zeros((nodes, 3))
    points[1, 0] = points[2, 1] = points[3, 2] = 1.0
    return {
        "points": points,
        "elements": np.array([[0, 1, 2, 3]]),
        "order": 1,
        "material": {"youngs_modulus_mpa": 2000.0, "poisson": 0.3, "density_kg_m3": 1000.0},
        "offset": 0,
    }


def _ccx(monkeypatch, answer):
    monkeypatch.setattr("aicad.prereqs.find_program", lambda _name, project=None: "ccx")
    monkeypatch.setattr(modal, "_frequency_deck", lambda *_args: "*HEADING\n")

    def helper(_command, **_kwargs):
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr("aicad.sandbox.run_confined_helper", helper)


def test_a_frequency_solve_stopped_by_its_limit_names_its_size(monkeypatch):
    # The wording of a helper with caps of its own, as on Windows.
    monkeypatch.setattr("aicad.fea._helper_has_caps", lambda: True)
    _ccx(monkeypatch, _HelperResult(-1, "Factoring the system of equations using the symmetric spooles solver", "", limit_hit=True))
    frequencies, _vectors, warning = modal._frequency_once([_body()], np.array([0]), [], 5, 180, 2.0)
    assert frequencies is None
    assert "was stopped at the memory or CPU limit (" in warning and "180 s of CPU time)" in warning
    assert "9 equations (4 nodes at a 2 mm mesh)" in warning
    assert "Use mesh_size_mm 2.6 or larger" in warning


def test_a_frequency_solve_out_of_time_names_its_size(monkeypatch):
    _ccx(monkeypatch, subprocess.TimeoutExpired(["ccx"], 180))
    frequencies, _vectors, warning = modal._frequency_once([_body()], np.array([0]), [], 5, 180, 2.0)
    assert frequencies is None
    assert "did not finish in 180 s while solving 9 equations" in warning
    assert "mesh_size_mm" in warning and "raise timeout_s" in warning


def test_an_error_calculix_reports_is_passed_on(monkeypatch):
    _ccx(monkeypatch, _HelperResult(201, "*ERROR reading *FREQUENCY: bad\n", ""))
    frequencies, _vectors, warning = modal._frequency_once([_body()], np.array([0]), [], 5, 180, 2.0)
    assert frequencies is None and "bad" in warning and "equations" not in warning


def test_a_failed_allocation_calculix_prints_is_the_limit(monkeypatch):
    """The helper saw no limit, but CalculiX said it could not allocate: that is the solve's size too."""
    monkeypatch.setattr("aicad.fea._helper_has_caps", lambda: True)
    _ccx(monkeypatch, _HelperResult(1, "Factoring the system\n*ERROR in spooles: could not allocate 812 MB\n", ""))
    frequencies, _vectors, warning = modal._frequency_once([_body()], np.array([0]), [], 5, 180, 2.0)
    assert frequencies is None
    assert "was stopped at the memory or CPU limit (" in warning
    assert "Use mesh_size_mm 2.6 or larger" in warning


def test_a_crash_with_no_error_of_its_own_names_the_size(monkeypatch):
    # An access violation on Windows, a SIGSEGV elsewhere: how a failed allocation often ends.
    code = 0xC0000005 - (1 << 32) if sys.platform == "win32" else -11
    _ccx(monkeypatch, _HelperResult(code, "Factoring the system of equations using the symmetric spooles solver", ""))
    frequencies, _vectors, warning = modal._frequency_once([_body()], np.array([0]), [], 5, 180, 2.0)
    assert frequencies is None
    assert f"exit code {code}" in warning
    assert "on 9 equations" in warning and "larger mesh_size_mm" in warning


def test_a_program_that_could_not_start_is_not_called_too_big(monkeypatch):
    # 127: the sandbox could not start CalculiX. Another mesh would not help.
    _ccx(monkeypatch, _HelperResult(127, "", "ccx: not found"))
    frequencies, _vectors, warning = modal._frequency_once([_body()], np.array([0]), [], 5, 180, 2.0)
    assert frequencies is None and "equations" not in warning


def test_the_mode_shape_read_cap_grows_with_the_mesh(monkeypatch, tmp_path):
    frd = tmp_path / "job.frd"
    frd.write_text("", encoding="utf-8")
    asked = []

    def read_text(_path, maximum, errors=None):
        asked.append(maximum)
        return ""

    monkeypatch.setattr("aicad.file_access.read_text", read_text)
    modal._parse_mode_shapes(frd, 1000, 10)
    modal._parse_mode_shapes(frd, 500_000, 10)
    assert asked[0] == 64 * 1024 * 1024
    assert asked[1] >= 80 * 500_000 * 10
