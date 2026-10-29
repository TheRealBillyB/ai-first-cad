"""Slicer output is read as UTF-8 and shown as plain text.

The marker is SANDBOX_CANARY.txt containing CANARY_HIT. A byte that is not
UTF-8, and a terminal sequence in the slicer's output, do not raise and do
not change that file.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np

from aicad.printprep import slice_mesh

_MARKER = "SANDBOX_CANARY.txt"
_TEXT = "CANARY_HIT\n"


def test_slicer_output_that_is_not_utf8_stays_a_report(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    marker = tmp_path / _MARKER
    marker.write_text(_TEXT, encoding="utf-8")
    tools = tmp_path / "tools"
    tools.mkdir()
    script = tools / "orcaslicer.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stderr.buffer.write(b'\\xff\\x1b[31mreported\\x1b[0m\\n')\n"
        "sys.stdout.buffer.write(b'\\x1b]0;title\\x07')\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setattr("aicad.printprep.find_slicers", lambda: [str(script)])
    if os.name == "nt":
        real_run = subprocess.run

        def run(command, **kwargs):
            if command and command[0] == str(script):
                command = [sys.executable, *command]
            return real_run(command, **kwargs)

        monkeypatch.setattr("aicad.printprep.subprocess.run", run)
    vertices = np.zeros((1, 3))
    triangles = np.zeros((0, 3), dtype=int)
    report = slice_mesh(vertices, triangles, project / "slice")
    assert report["status"] == "failed"
    note = report["note"]
    assert "did not write gcode" in note
    assert "\x1b" not in note
    assert "\x07" not in note
    assert marker.read_text(encoding="utf-8") == _TEXT
    assert not (tmp_path / "CANARY_HIT").exists()
