"""External programs are started from outside the project.

A copy of ffmpeg, CalculiX, or a slicer in the project writes a marker when
it runs. The lookup leaves that marker unwritten. The current directory is
not searched, including when it is the project.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from aicad.animation import write_frames
from aicad.fea import _run_ccx
from aicad.paths import project_roots, remember_project
from aicad.prereqs import ccx_status, find_program
from aicad.printprep import find_slicers, slice_mesh
from aicad.sandbox_macos import command_prefix


def _plant(directory: Path, name: str, canary: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        target = directory / f"{name}.cmd"
        target.write_text(f'@echo CANARY_HIT> "{canary}"\r\n', encoding="utf-8")
        return target
    target = directory / name
    target.write_text(
        "#!/bin/sh\nprintf '%s\\n' CANARY_HIT > " + _quote(canary) + "\n",
        encoding="utf-8",
    )
    target.chmod(target.stat().st_mode | 0o755)
    return target


def _quote(path: Path) -> str:
    return "'" + str(path).replace("'", "'\\''") + "'"


def _outside(path: str | None, project: Path) -> bool:
    if path is None:
        return True
    resolved = Path(path).resolve()
    root = project.resolve()
    return resolved != root and root not in resolved.parents


def _forget(project: Path) -> None:
    from aicad import paths

    paths._PROJECTS.discard(project.resolve())  # noqa: SLF001


def test_a_program_in_the_project_is_not_started(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "SANDBOX_CANARY.txt"
    for name in ("ffmpeg", "ccx", "prusa-slicer"):
        _plant(project, name, canary)
    remember_project(project)
    monkeypatch.chdir(project)
    monkeypatch.setenv("PATH", str(project) + os.pathsep + os.environ.get("PATH", ""))
    try:
        ffmpeg = find_program("ffmpeg", project=project)
        assert _outside(ffmpeg, project)
        image = Image.new("RGB", (16, 16), (20, 30, 40))
        other = Image.new("RGB", (16, 16), (40, 30, 20))
        report = write_frames(
            project,
            [image, other],
            fps=4,
            video="mp4",
            path="renders/clip.mp4",
            stem="clip",
        )
        assert not canary.exists()
        if report.get("mp4"):
            assert Path(report["mp4"]).is_file()
        else:
            assert report["video_message"]
            assert "CANARY_HIT" not in report["video_message"]

        status = ccx_status()
        assert _outside(status.path, project)
        points = np.array(
            [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        )
        forces = np.zeros((4, 3), dtype=np.float64)
        forces[3, 2] = -1.0
        _run_ccx(
            {"points": points, "elements": np.array([[0, 1, 2, 3]], dtype=np.int64), "order": 1},
            [0, 1, 2],
            forces,
            {"youngs_modulus_mpa": 210000.0, "poisson": 0.3},
        )
        assert not canary.exists()

        sliced = slice_mesh(
            np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float64),
            np.array([[0, 1, 2]], dtype=np.int64),
            project / "renders",
        )
        assert not canary.exists()
        assert all(_outside(path, project) for path in find_slicers())
        if sliced["status"] == "failed":
            assert not canary.exists()
    finally:
        _forget(project)


def test_the_current_directory_is_not_searched(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    canary = tmp_path / "outside" / "SANDBOX_CANARY.txt"
    canary.parent.mkdir()
    planted = _plant(project, "ffmpeg", canary)
    monkeypatch.chdir(project)
    system = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", os.pathsep.join((".", "", system)))
    found = find_program("ffmpeg")
    assert _outside(found, project)
    assert found is None or Path(found).resolve() != planted.resolve()
    assert not canary.exists()


def test_a_link_to_a_program_in_the_project_is_not_used(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    trusted = tmp_path / "bin"
    trusted.mkdir()
    canary = tmp_path / "outside" / "SANDBOX_CANARY.txt"
    canary.parent.mkdir()
    planted = _plant(project, "ffmpeg", canary)
    link = trusted / planted.name
    try:
        link.symlink_to(planted)
    except OSError:
        assert not canary.exists()
        return
    remember_project(project)
    monkeypatch.setenv("PATH", str(trusted))
    try:
        assert find_program("ffmpeg", project=project) is None
        assert not canary.exists()
    finally:
        _forget(project)


def test_sandbox_exec_is_not_taken_from_the_project(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    trusted = tmp_path / "bin"
    trusted.mkdir()
    canary = tmp_path / "outside" / "SANDBOX_CANARY.txt"
    canary.parent.mkdir()
    _plant(project, "sandbox-exec", canary)
    real = trusted / ("sandbox-exec.cmd" if sys.platform == "win32" else "sandbox-exec")
    if sys.platform == "win32":
        real.write_text("@echo off\r\n", encoding="utf-8")
    else:
        real.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        real.chmod(real.stat().st_mode | 0o755)
    remember_project(project)
    monkeypatch.setenv("PATH", str(project) + os.pathsep + str(trusted))
    try:
        found = find_program("sandbox-exec", project=project)
        assert found is not None
        assert Path(found).resolve() == real.resolve()
        prefix = command_prefix(str(tmp_path / "profile.sb"), project)
        assert Path(prefix[0]).resolve() == real.resolve()
        assert prefix[1:] == ["-f", str(tmp_path / "profile.sb")]
        assert not canary.exists()
    finally:
        _forget(project)


def test_project_roots_reports_an_open_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    remember_project(project)
    try:
        assert project.resolve() in project_roots()
    finally:
        _forget(project)
