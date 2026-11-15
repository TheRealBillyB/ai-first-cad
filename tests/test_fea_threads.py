"""CalculiX runs on more than one core.

Every solve ran on one thread: CalculiX was given no thread count. It now gets
up to eight, and the helper's CPU cap, which counts every thread, grows with
them. The direct solver keeps one thread for the factorisation itself.
"""

from __future__ import annotations

import sys

import numpy as np
import pytest

from aicad import fea


def _one_tet_mesh() -> dict:
    return {
        "points": np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]),
        "elements": np.array([[0, 1, 2, 3]]),
        "order": 1,
    }


def _solve(monkeypatch, method: str) -> dict:
    from aicad.sandbox import _HelperResult

    seen: dict = {}

    def helper(command, **kwargs):
        seen.update(kwargs)
        return _HelperResult(201, "*ERROR stop here\n", "")

    monkeypatch.setattr("aicad.sandbox.run_confined_helper", helper)
    monkeypatch.setattr("aicad.prereqs.find_program", lambda _name, project=None: "ccx")
    monkeypatch.setattr(fea.os, "cpu_count", lambda: 24)
    forces = np.zeros((4, 3))
    forces[1, 0] = 1.0
    fea._run_ccx(
        _one_tet_mesh(),
        np.array([0], dtype=np.int32),
        forces,
        {"youngs_modulus_mpa": 2000.0, "poisson": 0.3},
        method=method,
    )
    return seen


def test_calculix_is_told_its_thread_count_and_the_cap_grows_with_it(monkeypatch):
    seen = _solve(monkeypatch, "iterative")
    assert seen["threads"] == 8
    env = seen["env"]
    for name in ("OMP_NUM_THREADS", "NUMBER_OF_CPUS", "CCX_NPROC_STIFFNESS", "CCX_NPROC_RESULTS", "CCX_NPROC_EQUATION_SOLVER"):
        assert env[name] == "8", name


def test_the_direct_solver_factorises_on_one_thread(monkeypatch):
    env = _solve(monkeypatch, "direct")["env"]
    assert env["CCX_NPROC_EQUATION_SOLVER"] == "1"
    assert env["CCX_NPROC_STIFFNESS"] == "8"


def test_a_small_computer_uses_the_cores_it_has(monkeypatch):
    monkeypatch.setattr(fea.os, "cpu_count", lambda: 2)
    assert fea.ccx_threads() == 2
    monkeypatch.setattr(fea.os, "cpu_count", lambda: None)
    assert fea.ccx_threads() == 1


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows helper")
def test_the_windows_helper_cpu_cap_is_the_timeout_for_each_thread(monkeypatch, tmp_path):
    from aicad import sandbox

    seen: dict = {}

    def spawn(_command, **kwargs):
        seen.update(kwargs)
        return (0, "", "", False, False, "appcontainer")

    monkeypatch.setattr("aicad.sandbox_windows.spawn", spawn)
    sandbox._run_confined_helper_windows(["ccx"], env={}, timeout=180, cwd=tmp_path, reads=[], threads=8)
    assert seen["cpu_seconds"] == 180 * 8
    assert seen["timeout_seconds"] == 180.0
    sandbox._run_confined_helper_windows(["ccx"], env={}, timeout=180, cwd=tmp_path, reads=[])
    assert seen["cpu_seconds"] == 180
