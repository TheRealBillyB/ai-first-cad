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
    if os.name != "nt":
        return
    real_run = subprocess.run

    def run(command, **kwargs):
        if command and command[0] == str(script):
            command = [sys.executable, *command]
        return real_run(command, **kwargs)

    monkeypatch.setattr(f"{module}.subprocess.run", run)


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


def _slicer(tools: Path, witness: Path, seen: Path, argv: Path, stl: Path) -> Path:
    return _script(
        tools,
        "orcaslicer",
        "import os, sys\n"
        "from pathlib import Path\n"
        f"Path({str(witness)!r}).write_text(os.getcwd(), encoding='utf-8')\n"
        f"Path({str(seen)!r}).write_text('yes' if Path('CANARY_HIT').exists() else 'no', encoding='utf-8')\n"
        f"Path({str(argv)!r}).write_text('\\n'.join(sys.argv[1:]), encoding='utf-8')\n"
        f"Path({str(stl)!r}).write_text('yes' if Path('oriented.stl').is_file() else 'no', encoding='utf-8')\n"
        "for arg in sys.argv:\n"
        "    if str(arg).endswith('.gcode'):\n"
        "        Path(arg).write_text('; sliced\\n', encoding='utf-8')\n",
    )


def test_a_slicer_does_not_run_in_the_project(tmp_path, monkeypatch):
    project = _project(tmp_path)
    _point_temp_at(monkeypatch, project)
    tools = tmp_path / "tools"
    witness = tmp_path / "cwd.txt"
    seen = tmp_path / "seen.txt"
    argv = tmp_path / "argv.txt"
    stl = tmp_path / "stl.txt"
    script = _slicer(tools, witness, seen, argv, stl)
    monkeypatch.setattr("aicad.printprep.find_slicers", lambda: [str(script)])
    if os.name == "nt":
        # Windows will not start a shebang script. The interpreter runs the
        # same file. The working directory still comes from the slicer call.
        from aicad.printprep import _slice_command

        def command(executable, gcode, stl, price_per_kg, export, layer_height_mm=None):
            built = _slice_command(executable, gcode, stl, price_per_kg, export, layer_height_mm)
            return [sys.executable, *built]

        monkeypatch.setattr("aicad.printprep._slice_command", command)
    out = project / "slice"
    vertices, triangles = _mesh()
    slice_mesh(vertices, triangles, out)
    cwd = Path(witness.read_text(encoding="utf-8"))
    _outside(cwd, project)
    assert cwd != tools.resolve()
    assert stl.read_text(encoding="utf-8") == "yes"
    assert seen.read_text(encoding="utf-8") == "no"
    recorded = argv.read_text(encoding="utf-8")
    _no_project_path(recorded, project)
    assert "oriented.stl" in recorded.splitlines()
    assert "print.gcode" in recorded.splitlines()
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
    witness = tmp_path / "cwd.txt"
    seen = tmp_path / "seen.txt"
    argv = tmp_path / "argv.txt"
    frames = tmp_path / "frames.txt"
    script = _script(
        tools,
        "ffmpeg",
        "import os, sys\n"
        "from pathlib import Path\n"
        f"Path({str(witness)!r}).write_text(os.getcwd(), encoding='utf-8')\n"
        f"Path({str(seen)!r}).write_text('yes' if Path('CANARY_HIT').exists() else 'no', encoding='utf-8')\n"
        f"Path({str(argv)!r}).write_text('\\n'.join(sys.argv[1:]), encoding='utf-8')\n"
        f"Path({str(frames)!r}).write_text('yes' if Path('frame_0000.png').is_file() else 'no', encoding='utf-8')\n"
        "Path(sys.argv[-1]).write_bytes(b'video')\n",
    )
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
    cwd = Path(witness.read_text(encoding="utf-8"))
    _outside(cwd, project)
    assert seen.read_text(encoding="utf-8") == "no"
    assert frames.read_text(encoding="utf-8") == "yes"
    recorded = argv.read_text(encoding="utf-8")
    _no_project_path(recorded, project)
    assert "frame_%04d.png" in recorded.splitlines()
    assert "video.mp4" in recorded.splitlines()
    assert message is None
    assert videos["mp4"] == str(destination.resolve())
    assert destination.read_bytes() == b"video"
    assert not (project / "renders" / ".clip-frames").exists()
    assert (project / "CANARY_HIT").read_text(encoding="utf-8") == "CANARY_HIT\n"


def test_calculix_runs_in_a_private_folder(tmp_path, monkeypatch):
    project = _project(tmp_path)
    _point_temp_at(monkeypatch, project)
    tools = tmp_path / "tools"
    witness = tmp_path / "cwd.txt"
    seen = tmp_path / "seen.txt"
    argv = tmp_path / "argv.txt"
    deck = tmp_path / "deck.txt"
    script = _script(
        tools,
        "ccx",
        "import os, sys\n"
        "from pathlib import Path\n"
        f"seen = Path({str(witness)!r})\n"
        "seen.write_text((seen.read_text(encoding='utf-8') if seen.exists() else '') + os.getcwd() + '\\n', encoding='utf-8')\n"
        f"Path({str(seen)!r}).write_text('yes' if Path('CANARY_HIT').exists() else 'no', encoding='utf-8')\n"
        f"args = Path({str(argv)!r})\n"
        "args.write_text((args.read_text(encoding='utf-8') if args.exists() else '') + '\\n'.join(sys.argv[1:]) + '\\n', encoding='utf-8')\n"
        f"Path({str(deck)!r}).write_text('yes' if Path('job.inp').is_file() else 'no', encoding='utf-8')\n",
    )
    monkeypatch.setattr("aicad.prereqs.find_program", lambda name, project=None: str(script))
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
    folders = [Path(line) for line in witness.read_text(encoding="utf-8").splitlines() if line]
    assert len(folders) == 2
    assert folders[0] != folders[1]
    for cwd in folders:
        _outside(cwd, project)
        assert not (project.resolve() in cwd.resolve().parents)
    assert seen.read_text(encoding="utf-8") == "no"
    assert deck.read_text(encoding="utf-8") == "yes"
    recorded = argv.read_text(encoding="utf-8").strip().splitlines()
    assert recorded == ["-i", "job", "-i", "job"]
    _no_project_path("\n".join(recorded), project)
    assert not (project / "job.inp").exists()
    assert (project / "CANARY_HIT").read_text(encoding="utf-8") == "CANARY_HIT\n"
