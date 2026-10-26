"""A slicer does not run in the project directory and does not follow a link.

The marker is CANARY_HIT. The slicer working directory is the program
directory, and an output folder that is a link is not used.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

from aicad.printprep import slice_mesh


def _mesh():
    vertices = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    triangles = np.array([[0, 1, 2], [0, 1, 3]])
    return vertices, triangles


def _slicer(tools: Path, witness: Path, seen: Path) -> Path:
    script = tools / "orcaslicer"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "from pathlib import Path\n"
        f"Path({str(witness)!r}).write_text(os.getcwd(), encoding='utf-8')\n"
        f"Path({str(seen)!r}).write_text('yes' if Path('CANARY_HIT').exists() else 'no', encoding='utf-8')\n"
        "for arg in sys.argv:\n"
        "    if str(arg).endswith('.gcode'):\n"
        "        Path(arg).write_text('; sliced\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def test_a_slicer_does_not_run_in_the_project(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "CANARY_HIT").write_text("CANARY_HIT\n", encoding="utf-8")
    tools = tmp_path / "tools"
    tools.mkdir()
    witness = tmp_path / "cwd.txt"
    seen = tmp_path / "seen.txt"
    script = _slicer(tools, witness, seen)
    monkeypatch.setattr("aicad.printprep.find_slicers", lambda: [str(script)])
    if os.name == "nt":
        # Windows will not start a shebang script. The interpreter runs the
        # same file. The working directory still comes from the slicer path.
        from aicad.printprep import _slice_command

        def command(executable, gcode, stl, price_per_kg, export):
            built = _slice_command(executable, gcode, stl, price_per_kg, export)
            return [sys.executable, *built]

        monkeypatch.setattr("aicad.printprep._slice_command", command)
    monkeypatch.chdir(project)
    out = project / "slice"
    vertices, triangles = _mesh()
    slice_mesh(vertices, triangles, out)
    cwd = Path(witness.read_text(encoding="utf-8"))
    assert cwd == tools.resolve()
    assert seen.read_text(encoding="utf-8") == "no"
    assert (project / "CANARY_HIT").read_text(encoding="utf-8") == "CANARY_HIT\n"


def test_a_slicer_output_link_is_not_followed(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "CANARY_HIT"
    marker.write_text("CANARY_HIT\n", encoding="utf-8")
    link = tmp_path / "linked"
    link.symlink_to(outside, target_is_directory=True)
    tools = tmp_path / "tools"
    tools.mkdir()
    script = tools / "orcaslicer"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setattr("aicad.printprep.find_slicers", lambda: [str(script)])
    vertices, triangles = _mesh()
    with pytest.raises(OSError):
        slice_mesh(vertices, triangles, link)
    assert marker.read_text(encoding="utf-8") == "CANARY_HIT\n"
    assert not (outside / "oriented.stl").exists()
