"""A robot mesh folder that is a link is not written.

The marker is SANDBOX_CANARY.txt outside the export folder. Exporting must
not write a mesh there, and must not change the marker.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from aicad.errors import GeometryError
from aicad.robot import export_robot

_SPEC = {
    "name": "arm",
    "instances": [{"name": "base", "part": "base", "material": "steel"}],
    "joints": [],
}


def _link_dir(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            pytest.skip("this machine cannot create a junction")
        return
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("this machine cannot create a directory symlink")


def _writer(shape, path, **kwargs):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"STL")


def _export(out: Path) -> None:
    export_robot(_SPEC, {"base": object()}, out)


def test_a_robot_mesh_folder_that_leaves_the_project_is_not_written(tmp_path, monkeypatch):
    monkeypatch.setattr("aicad.paths.nofollow_writes", lambda: False)
    monkeypatch.setattr("aicad.robot.kernel.export_stl_file", _writer)
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "SANDBOX_CANARY.txt"
    canary.write_text("keep\n", encoding="utf-8")
    out = tmp_path / "robot"
    out.mkdir()
    _link_dir(out / "meshes", outside)
    with pytest.raises(GeometryError):
        _export(out)
    assert list(outside.glob("*.stl")) == []
    assert canary.read_text(encoding="utf-8") == "keep\n"


def test_a_robot_mesh_folder_that_stays_inside_is_not_followed(tmp_path, monkeypatch):
    monkeypatch.setattr("aicad.paths.nofollow_writes", lambda: False)
    monkeypatch.setattr("aicad.robot.kernel.export_stl_file", _writer)
    out = tmp_path / "robot"
    kept = out / "kept"
    kept.mkdir(parents=True)
    _link_dir(out / "meshes", kept)
    with pytest.raises(GeometryError, match="link and was not followed"):
        _export(out)
    assert list(kept.glob("*.stl")) == []


def test_a_robot_export_still_creates_a_mesh_folder(tmp_path):
    out = tmp_path / "robot"
    with pytest.raises(GeometryError, match="needs a shape"):
        export_robot(_SPEC, {}, out)
    meshes = out / "meshes"
    assert meshes.is_dir()
    assert not meshes.is_symlink()
