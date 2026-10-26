"""A picture and a robot mesh are read before another library opens a path.

The marker is SANDBOX_CANARY.txt containing CANARY_HIT, outside the project.
A directory link to it, including a Windows junction, is not followed.
"""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest

_CANARY_TEXT = "CANARY_HIT\n"
_MESH = b"solid inside\n"
_PNG = b"\x89PNG\r\n\x1a\nINSIDE"


def _link(path: Path, target: Path, *, directory: bool = False) -> None:
    try:
        path.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        if directory and sys.platform == "win32":
            completed = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(path), str(target)],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode == 0:
                return
        pytest.fail(f"this system cannot create a link ({exc})")


def _canary(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "SANDBOX_CANARY.txt"
    path.write_text(_CANARY_TEXT, encoding="utf-8")
    return path


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "session.json").write_text("{}\n", encoding="utf-8")
    return project


def test_a_picture_is_read_from_the_project(tmp_path):
    from aicad.server import _View, _with_images

    project = _project(tmp_path)
    picture = project / "renders" / "part.png"
    picture.parent.mkdir(parents=True)
    picture.write_bytes(_PNG)
    blocks = _with_images({"ok": True}, [_View("iso", str(picture))])
    image = blocks[-1]
    assert image.path is None
    assert image.data == _PNG


def test_a_picture_behind_a_directory_link_is_not_read(tmp_path):
    from aicad.server import _View, _with_images

    outside = tmp_path / "outside"
    canary = _canary(outside)
    picture = outside / "sheet.png"
    picture.write_bytes(b"\x89PNG\r\n\x1a\n" + _CANARY_TEXT.encode())
    project = _project(tmp_path)
    _link(project / "renders", outside, directory=True)
    blocks = _with_images({"ok": True}, [_View("iso", str(project / "renders" / "sheet.png"))])
    rendered = "\n".join(block if isinstance(block, str) else block.data.decode("utf-8", "replace") for block in blocks)
    assert _CANARY_TEXT not in rendered
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT


def test_a_swapped_picture_is_not_read(tmp_path, monkeypatch):
    from aicad import paths
    from aicad.server import _View, _with_images

    outside = tmp_path / "outside"
    canary = _canary(outside)
    foreign = outside / "sheet.png"
    foreign.write_bytes(b"\x89PNG\r\n\x1a\n" + _CANARY_TEXT.encode())
    project = _project(tmp_path)
    picture = project / "renders" / "part.png"
    picture.parent.mkdir(parents=True)
    picture.write_bytes(_PNG)

    def swap(root, relative):
        if Path(relative).name != "part.png":
            return
        current = Path(root) / relative
        if current.is_symlink() or not current.is_file():
            return
        current.unlink()
        _link(current, foreign)

    monkeypatch.setattr(paths, "_before_nofollow", swap)
    blocks = _with_images({"ok": True}, [_View("iso", str(picture))])
    rendered = "\n".join(block if isinstance(block, str) else block.data.decode("utf-8", "replace") for block in blocks)
    assert _CANARY_TEXT not in rendered
    assert _PNG not in rendered.encode()
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT


def _robot_dir(tmp_path: Path) -> tuple[Path, Path]:
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "SANDBOX_CANARY.txt"
    canary.write_text(_CANARY_TEXT, encoding="utf-8")
    (outside / "base.stl").write_bytes(_CANARY_TEXT.encode())
    folder = tmp_path / "robot"
    folder.mkdir()
    (folder / "meshes").mkdir()
    (folder / "meshes" / "base.stl").write_bytes(_MESH)
    (folder / "robot.xml").write_text(
        '<?xml version="1.0"?>\n<mujoco model="arm"><compiler meshdir="meshes"/>'
        '<asset><mesh name="base" file="base.stl"/></asset></mujoco>\n',
        encoding="utf-8",
    )
    (folder / "robot.urdf").write_text(
        '<?xml version="1.0"?>\n<robot name="arm"><link name="base"><visual><geometry>'
        '<mesh filename="meshes/base.stl"/></geometry></visual></link></robot>\n',
        encoding="utf-8",
    )
    return folder, canary


def test_a_good_robot_file_is_skipped_when_the_simulator_is_missing(tmp_path, monkeypatch):
    from aicad.robot import _try_mujoco, _try_pybullet

    folder, canary = _robot_dir(tmp_path)
    monkeypatch.setitem(sys.modules, "mujoco", None)
    monkeypatch.setitem(sys.modules, "pybullet", None)
    mujoco = _try_mujoco(folder / "robot.xml")
    bullet = _try_pybullet(folder / "robot.urdf")
    assert mujoco["status"] == "skipped"
    assert bullet["status"] == "skipped"
    assert _CANARY_TEXT not in mujoco["note"]
    assert _CANARY_TEXT not in bullet["note"]
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT


def test_a_linked_robot_mesh_is_not_handed_to_a_simulator(tmp_path, monkeypatch):
    from aicad.robot import _try_mujoco, _try_pybullet

    folder, canary = _robot_dir(tmp_path)
    meshes = folder / "meshes"
    held = tmp_path / "held"
    meshes.rename(held)
    _link(meshes, folder.parent / "outside", directory=True)
    seen: list[str] = []

    def refuse(path, *args, **kwargs):
        seen.append(str(path))
        raise AssertionError("simulator ran")

    mujoco = types.ModuleType("mujoco")
    mujoco.MjModel = types.SimpleNamespace(from_xml_path=refuse)
    mujoco.MjData = lambda model: object()
    mujoco.mj_step = lambda *args, **kwargs: None
    bullet = types.ModuleType("pybullet")
    bullet.DIRECT = 0
    bullet.connect = lambda *args, **kwargs: 1
    bullet.loadURDF = refuse
    bullet.stepSimulation = lambda **kwargs: None
    bullet.disconnect = lambda client: None
    monkeypatch.setitem(sys.modules, "mujoco", mujoco)
    monkeypatch.setitem(sys.modules, "pybullet", bullet)
    mujoco_result = _try_mujoco(folder / "robot.xml")
    bullet_result = _try_pybullet(folder / "robot.urdf")
    assert mujoco_result["status"] == "failed"
    assert bullet_result["status"] == "failed"
    assert seen == []
    assert _CANARY_TEXT not in mujoco_result["note"]
    assert _CANARY_TEXT not in bullet_result["note"]
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT


def test_a_simulator_opens_the_private_copy(tmp_path, monkeypatch):
    from aicad.robot import _try_mujoco

    folder, canary = _robot_dir(tmp_path)
    seen: dict[str, object] = {}

    class Model:
        nq = 1
        nbody = 1

    def load(path):
        opened = Path(path)
        seen["path"] = opened
        seen["mesh"] = (opened.parent / "meshes" / "base.stl").read_bytes()
        return Model()

    mujoco = types.ModuleType("mujoco")
    mujoco.MjModel = types.SimpleNamespace(from_xml_path=load)
    mujoco.MjData = lambda model: object()
    mujoco.mj_step = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "mujoco", mujoco)
    result = _try_mujoco(folder / "robot.xml")
    assert result["status"] == "loaded"
    assert Path(seen["path"]).parent != folder
    assert seen["mesh"] == _MESH
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT
