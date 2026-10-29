"""CalculiX output is read as UTF-8.

A byte that is not UTF-8 does not stop the report. The marker file outside
the project stays as it was written. Nothing is sent to another machine.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np


def _solver(tools: Path) -> Path:
    tools.mkdir()
    script = tools / "ccx.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stdout.buffer.write(b'\\xffERROR line\\n')\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _run_python_on_windows(monkeypatch, module: str, script: Path) -> None:
    if os.name != "nt":
        return
    real_run = subprocess.run

    def run(command, **kwargs):
        if command and command[0] == str(script):
            command = [sys.executable, *command]
        return real_run(command, **kwargs)

    monkeypatch.setattr(f"{module}.subprocess.run", run)


def _mesh():
    return {
        "points": np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]),
        "elements": np.array([[0, 1, 2, 3]]),
        "order": 1,
    }


def test_calculix_output_that_is_not_utf8_is_still_reported(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "SANDBOX_CANARY.txt"
    marker.write_text("untouched\n", encoding="utf-8")
    script = _solver(tmp_path / "tools")
    monkeypatch.setattr("aicad.prereqs.find_program", lambda name, project=None: str(script))
    _run_python_on_windows(monkeypatch, "aicad.fea", script)
    _run_python_on_windows(monkeypatch, "aicad.modal", script)
    from aicad import fea, modal

    forces = np.zeros((4, 3))
    forces[1, 0] = 1.0
    _displacement, _stress, detail = fea._run_ccx(
        _mesh(),
        np.array([0], dtype=np.int32),
        forces,
        {"youngs_modulus_mpa": 2000.0, "poisson": 0.3},
    )
    assert isinstance(detail, str)
    assert detail
    frequencies, _vectors, frequency_detail = modal._frequency_once([], [], [], 1, 5)
    assert frequencies is None
    assert isinstance(frequency_detail, str)
    assert frequency_detail
    assert marker.read_text(encoding="utf-8") == "untouched\n"
