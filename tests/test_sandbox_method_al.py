"""Helper programs run outside the project, on a private copy of their inputs.

The marker is CANARY_HIT. A slicer, ffmpeg, and CalculiX each get a private
folder. Their arguments are file names from that folder. An output folder
that is a link is not used.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from aicad.printprep import slice_mesh


def _mesh():
    vertices = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    triangles = np.array([[0, 1, 2], [0, 1, 3]])
    return vertices, triangles


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "session.json").write_text("{}\n", encoding="utf-8")
    (project / "CANARY_HIT").write_text("CANARY_HIT\n", encoding="utf-8")
    return project


def _point_temp_at(monkeypatch, project: Path) -> None:
    monkeypatch.setenv("TMPDIR", str(project))
    monkeypatch.setenv("TEMP", str(project))
    monkeypatch.setenv("TMP", str(project))
    monkeypatch.chdir(project)


def _script(tools: Path, name: str, body: str) -> Path:
    tools.mkdir(parents=True, exist_ok=True)
    script = tools / f"{name}.py"
    script.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    script.chmod(0o755)
    return script


def _run_script(monkeypatch, module: str, script: Path) -> None:
    # A confined helper starts a .py program with this interpreter.
    del monkeypatch, module, script


def _outside(cwd: Path, project: Path) -> None:
    resolved = cwd.resolve()
    assert resolved != project.resolve()
    assert project.resolve() not in resolved.parents


def _no_project_path(argv: str, project: Path) -> None:
    folder = str(project.resolve())
    for line in argv.splitlines():
        if not line:
            continue
        candidate = Path(line)
        if candidate.is_absolute():
            resolved = candidate.resolve()
            assert resolved != project.resolve()
            assert project.resolve() not in resolved.parents
        assert folder not in line


def _capture(monkeypatch) -> list[str]:
    from aicad.sandbox import run_confined_helper

    notes: list[str] = []

    def wrapped(command, **kwargs):
        result = run_confined_helper(command, **kwargs)
        notes.append(result.stdout or "")
        return result

    monkeypatch.setattr("aicad.sandbox.run_confined_helper", wrapped)
    return notes


def _field(text: str, name: str) -> str:
    for line in text.splitlines():
        if line.startswith(name + " "):
            return line.split(" ", 1)[1]
    raise AssertionError(text)


def _slicer(tools: Path) -> Path:
    return _script(
        tools,
        "orcaslicer",
        "import os, sys\n"
        "from pathlib import Path\n"
        "print('CWD', os.getcwd())\n"
        "print('SEEN', 'yes' if Path('CANARY_HIT').exists() else 'no')\n"
        "print('STL', 'yes' if Path('oriented.stl').is_file() else 'no')\n"
        "print('ARGV', '|'.join(sys.argv[1:]))\n"
        "for arg in sys.argv:\n"
        "    if str(arg).endswith('.gcode'):\n"
        "        Path(arg).write_text('; sliced\\n', encoding='utf-8')\n",
    )


def test_a_slicer_does_not_run_in_the_project(tmp_path, monkeypatch):
    project = _project(tmp_path)
    _point_temp_at(monkeypatch, project)
    tools = tmp_path / "tools"
    script = _slicer(tools)
    notes = _capture(monkeypatch)
    monkeypatch.setattr("aicad.printprep.find_slicers", lambda: [str(script)])
    if os.name == "nt":
        # Windows will not start a shebang script. The interpreter runs the
        # same file. The working directory still comes from the slicer call.
        from aicad.printprep import _slice_command

        def command(executable, gcode, stl, price_per_kg, export, layer_height_mm=None, supports=False):
            built = _slice_command(
                executable, gcode, stl, price_per_kg, export, layer_height_mm, supports
            )
            return [sys.executable, *built]

        monkeypatch.setattr("aicad.printprep._slice_command", command)
    out = project / "slice"
    vertices, triangles = _mesh()
    slice_mesh(vertices, triangles, out)
    text = notes[0]
    cwd = Path(_field(text, "CWD"))
    _outside(cwd, project)
    assert cwd != tools.resolve()
    assert _field(text, "STL") == "yes"
    assert _field(text, "SEEN") == "no"
    recorded = _field(text, "ARGV")
    _no_project_path(recorded, project)
    assert "oriented.stl" in recorded.split("|")
    assert "print.gcode" in recorded.split("|")
    assert (project / "CANARY_HIT").read_text(encoding="utf-8") == "CANARY_HIT\n"
    assert not (out / "oriented.stl").exists()
    assert (out / "print.gcode").read_text(encoding="utf-8") == "; sliced\n"


def test_a_slicer_output_link_is_not_followed(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "CANARY_HIT"
    marker.write_text("CANARY_HIT\n", encoding="utf-8")
    link = tmp_path / "linked"
    link.symlink_to(outside, target_is_directory=True)
    tools = tmp_path / "tools"
    script = tools / "orcaslicer"
    tools.mkdir()
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setattr("aicad.printprep.find_slicers", lambda: [str(script)])
    vertices, triangles = _mesh()
    with pytest.raises(OSError):
        slice_mesh(vertices, triangles, link)
    assert marker.read_text(encoding="utf-8") == "CANARY_HIT\n"
    assert not (outside / "oriented.stl").exists()


def test_ffmpeg_encodes_from_a_private_copy(tmp_path, monkeypatch):
    project = _project(tmp_path)
    _point_temp_at(monkeypatch, project)
    tools = tmp_path / "tools"
    script = _script(
        tools,
        "ffmpeg",
        "import os, sys\n"
        "from pathlib import Path\n"
        "print('CWD', os.getcwd())\n"
        "print('SEEN', 'yes' if Path('CANARY_HIT').exists() else 'no')\n"
        "print('FRAMES', 'yes' if Path('frame_0000.png').is_file() else 'no')\n"
        "print('ARGV', '|'.join(sys.argv[1:]))\n"
        "Path(sys.argv[-1]).write_bytes(b'video')\n",
    )
    notes = _capture(monkeypatch)
    monkeypatch.setattr("aicad.animation.find_program", lambda *_args, **_kwargs: str(script))
    _run_script(monkeypatch, "aicad.animation", script)
    destination = project / "renders" / "clip.mp4"
    from aicad.animation import _write_videos

    videos, message = _write_videos(
        project,
        {"videos": {"mp4": destination}, "frames": project / "renders" / ".clip-frames"},
        [Image.new("RGB", (8, 8), "red")],
        4,
        ["mp4"],
    )
    text = notes[0]
    cwd = Path(_field(text, "CWD"))
    _outside(cwd, project)
    assert _field(text, "SEEN") == "no"
    assert _field(text, "FRAMES") == "yes"
    recorded = _field(text, "ARGV")
    _no_project_path(recorded, project)
    assert "frame_%04d.png" in recorded.split("|")
    assert "video.mp4" in recorded.split("|")
    assert message is None
    assert videos["mp4"] == str(destination.resolve())
    assert destination.read_bytes() == b"video"
    assert not (project / "renders" / ".clip-frames").exists()
    assert (project / "CANARY_HIT").read_text(encoding="utf-8") == "CANARY_HIT\n"


def test_calculix_runs_in_a_private_folder(tmp_path, monkeypatch):
    project = _project(tmp_path)
    _point_temp_at(monkeypatch, project)
    tools = tmp_path / "tools"
    script = _script(
        tools,
        "ccx",
        "import os, sys\n"
        "from pathlib import Path\n"
        "print('CWD', os.getcwd())\n"
        "print('SEEN', 'yes' if Path('CANARY_HIT').exists() else 'no')\n"
        "print('DECK', 'yes' if Path('job.inp').is_file() else 'no')\n"
        "print('ARGV', ' '.join(sys.argv[1:]))\n",
    )
    notes = _capture(monkeypatch)
    from aicad.prereqs import find_program as real_find

    def find_ccx(name, project=None):
        if name == "ccx":
            return str(script)
        return real_find(name, project=project)

    monkeypatch.setattr("aicad.prereqs.find_program", find_ccx)
    _run_script(monkeypatch, "aicad.fea", script)
    _run_script(monkeypatch, "aicad.modal", script)
    from aicad import fea, modal

    mesh = {
        "points": np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]),
        "elements": np.array([[0, 1, 2, 3]]),
        "order": 1,
    }
    forces = np.zeros((4, 3))
    forces[1, 0] = 1.0
    fea._run_ccx(mesh, np.array([0], dtype=np.int32), forces, {"youngs_modulus_mpa": 2000.0, "poisson": 0.3})
    modal._frequency_once([], [], [], 1, 5)
    assert len(notes) == 2
    folders = [Path(_field(text, "CWD")) for text in notes]
    assert folders[0] != folders[1]
    for cwd in folders:
        _outside(cwd, project)
        assert not (project.resolve() in cwd.resolve().parents)
    assert [_field(text, "SEEN") for text in notes] == ["no", "no"]
    assert [_field(text, "DECK") for text in notes] == ["yes", "yes"]
    recorded = [_field(text, "ARGV") for text in notes]
    assert recorded == ["-i job", "-i job"]
    _no_project_path("\n".join(recorded), project)
    assert not (project / "job.inp").exists()
    assert (project / "CANARY_HIT").read_text(encoding="utf-8") == "CANARY_HIT\n"
