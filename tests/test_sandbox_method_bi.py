"""A slicer and ffmpeg keep the directory that was opened, not a later name.

The marker is SANDBOX_CANARY.txt containing CANARY_HIT. Replacing the helper
directory's name with a link does not make the helper read or write that link.
"""

from __future__ import annotations

import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import numpy as np
from PIL import Image

import aicad.tool_directory as tool_directory
from aicad.printprep import slice_mesh

_MARKER = "SANDBOX_CANARY.txt"
_TEXT = "CANARY_HIT\n"


def _marker(tmp_path: Path) -> Path:
    marker = tmp_path / _MARKER
    marker.write_text(_TEXT, encoding="utf-8")
    return marker


def _mesh():
    vertices = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    triangles = np.array([[0, 1, 2], [0, 1, 3]])
    return vertices, triangles


def _script(tools: Path, name: str, body: str) -> Path:
    tools.mkdir(parents=True, exist_ok=True)
    script = tools / name
    script.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    script.chmod(0o755)
    return script


def _swap_before_run(monkeypatch, module: str, script: Path, canary: Path) -> None:
    """Point the directory name at ``canary`` after it has been opened."""
    helds: list = []
    real_directory = tool_directory.held_subprocess_directory

    @contextmanager
    def wrapped():
        with real_directory() as held:
            helds.append(held)
            yield held

    monkeypatch.setattr(tool_directory, "held_subprocess_directory", wrapped)
    real_run = subprocess.run

    def run(command, **kwargs):
        directory = helds[-1].original
        moved = directory.with_name(directory.name + "-moved")
        try:
            directory.rename(moved)
            directory.symlink_to(canary, target_is_directory=True)
        except OSError:
            if moved.exists() and not directory.exists():
                moved.rename(directory)
        if os.name == "nt" and command and Path(command[0]) == script:
            command = [sys.executable, *command]
        return real_run(command, **kwargs)

    monkeypatch.setattr(f"{module}.subprocess.run", run)


def test_a_slicer_keeps_the_directory_it_opened(tmp_path, monkeypatch):
    marker = _marker(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    canary = tmp_path / "canary"
    canary.mkdir()
    (canary / "print.gcode").write_text(_TEXT, encoding="utf-8")
    (canary / "oriented.stl").write_bytes(b"CANARY")
    script = _script(
        tmp_path / "tools",
        "orcaslicer",
        "from pathlib import Path\n"
        "import sys\n"
        "for arg in sys.argv:\n"
        "    if str(arg).endswith('.gcode'):\n"
        "        Path(arg).write_text('; sliced\\n', encoding='utf-8')\n",
    )
    monkeypatch.setattr("aicad.printprep.find_slicers", lambda: [str(script)])
    _swap_before_run(monkeypatch, "aicad.printprep", script, canary)
    vertices, triangles = _mesh()
    slice_mesh(vertices, triangles, project / "slice")
    assert (project / "slice" / "print.gcode").read_text(encoding="utf-8") == "; sliced\n"
    assert (canary / "print.gcode").read_text(encoding="utf-8") == _TEXT
    assert (canary / "oriented.stl").read_bytes() == b"CANARY"
    assert marker.read_text(encoding="utf-8") == _TEXT


def test_ffmpeg_keeps_the_directory_it_opened(tmp_path, monkeypatch):
    marker = _marker(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    (project / "session.json").write_text("{}\n", encoding="utf-8")
    canary = tmp_path / "canary"
    canary.mkdir()
    (canary / "video.mp4").write_bytes(b"CANARY_HIT\n")
    (canary / "frame_0000.png").write_bytes(b"CANARY")
    script = _script(
        tmp_path / "tools",
        "ffmpeg",
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[-1]).write_bytes(b'video')\n",
    )
    monkeypatch.setattr("aicad.animation.find_program", lambda *_args, **_kwargs: str(script))
    _swap_before_run(monkeypatch, "aicad.animation", script, canary)
    from aicad.animation import _write_videos

    destination = project / "renders" / "clip.mp4"
    videos, message = _write_videos(
        project,
        {"videos": {"mp4": destination}, "frames": project / "renders" / ".clip-frames"},
        [Image.new("RGB", (8, 8), "red")],
        4,
        ["mp4"],
    )
    assert message is None
    assert videos["mp4"] == str(destination.resolve())
    assert destination.read_bytes() == b"video"
    assert (canary / "video.mp4").read_bytes() == b"CANARY_HIT\n"
    assert (canary / "frame_0000.png").read_bytes() == b"CANARY"
    assert marker.read_text(encoding="utf-8") == _TEXT


def test_a_replaced_directory_name_is_not_removed(tmp_path):
    """Closing the helper removes the opened directory, not the name that replaced it."""
    marker = _marker(tmp_path)
    canary = tmp_path / "canary"
    canary.mkdir()
    (canary / "keep.txt").write_text(_TEXT, encoding="utf-8")
    with tool_directory.held_subprocess_directory() as held:
        directory = held.original
        tool_directory.held_write_bytes(held, "own.txt", b"ORIGINAL\n")
        moved = directory.with_name(directory.name + "-moved")
        moved = directory.with_name(directory.name + "-moved")
        renamed = False
        try:
            directory.rename(moved)
            renamed = True
        except OSError:
            assert tool_directory.held_read_bytes(held, "own.txt", maximum=100) == b"ORIGINAL\n"
            assert not (canary / "own.txt").exists()
        if renamed:
            directory.symlink_to(canary, target_is_directory=True)
            assert tool_directory.held_read_bytes(held, "own.txt", maximum=100) == b"ORIGINAL\n"
            assert tool_directory.held_read_bytes(held, "keep.txt", maximum=100) == b""
    assert (canary / "keep.txt").read_text(encoding="utf-8") == _TEXT
    assert marker.read_text(encoding="utf-8") == _TEXT
    if renamed:
        assert not moved.exists()
