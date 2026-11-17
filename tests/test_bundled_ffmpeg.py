"""Animation videos can use the ffmpeg build the optional imageio-ffmpeg package ships.

ffmpeg on PATH still comes first. The bundled binary is read from the
package's own folder: the environment variable that would name another
program is not read, and a binary inside an open project is not started.
When neither is there, the message says how to add one.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path

import pytest

from aicad import prereqs


def _fake_package(monkeypatch, folder: Path) -> Path:
    """An installed-looking imageio_ffmpeg at ``folder`` with one ffmpeg binary."""

    package = folder / "imageio_ffmpeg"
    binaries = package / "binaries"
    binaries.mkdir(parents=True)
    (package / "__init__.py").write_text("raise RuntimeError('imported')\n", encoding="utf-8")
    (binaries / "README.md").write_text("not a program\n", encoding="utf-8")
    name = "ffmpeg-win-x86_64-v7.1.exe" if os.name == "nt" else "ffmpeg-linux-x86_64-v7.0.2"
    binary = binaries / name
    binary.write_bytes(b"binary")
    binary.chmod(0o755)
    spec = importlib.machinery.ModuleSpec("imageio_ffmpeg", None, is_package=True)
    spec.submodule_search_locations = [str(package)]
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: spec if name == "imageio_ffmpeg" else real(name, *a))
    return binary


def test_the_bundled_binary_is_found_without_importing_the_package(tmp_path, monkeypatch):
    binary = _fake_package(monkeypatch, tmp_path / "site")
    found = prereqs.bundled_ffmpeg()
    assert found == str(binary.resolve())
    assert "imageio_ffmpeg" not in sys.modules


def test_the_environment_cannot_name_another_program(tmp_path, monkeypatch):
    binary = _fake_package(monkeypatch, tmp_path / "site")
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", str(tmp_path / "elsewhere" / "ffmpeg.exe"))
    assert prereqs.bundled_ffmpeg() == str(binary.resolve())


def test_a_bundled_binary_inside_the_project_is_not_used(tmp_path, monkeypatch):
    project = tmp_path / "project"
    _fake_package(monkeypatch, project / "site")
    assert prereqs.bundled_ffmpeg(project=project) is None


def test_no_package_means_no_bundled_binary(monkeypatch):
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None if name == "imageio_ffmpeg" else real(name, *a))
    assert prereqs.bundled_ffmpeg() is None


def test_ffmpeg_on_path_comes_before_the_bundled_one(tmp_path, monkeypatch):
    from aicad import animation

    calls = []

    def encode(executable, *_args):
        calls.append(executable)
        raise RuntimeError("stop here")

    monkeypatch.setattr(animation, "find_program", lambda *_a, **_k: "/usr/bin/ffmpeg")
    monkeypatch.setattr(animation, "bundled_ffmpeg", lambda **_k: "/site/imageio_ffmpeg/binaries/ffmpeg")
    monkeypatch.setattr(animation, "_encode", encode)
    from PIL import Image

    targets = {"videos": {"mp4": tmp_path / "clip.mp4"}}
    animation._write_videos(tmp_path, targets, [Image.new("RGB", (4, 4))], 4, ["mp4"])
    assert calls == ["/usr/bin/ffmpeg"]


def test_the_bundled_one_is_used_when_path_has_none(tmp_path, monkeypatch):
    from aicad import animation

    calls = []

    def encode(executable, *_args):
        calls.append(executable)
        raise RuntimeError("stop here")

    monkeypatch.setattr(animation, "find_program", lambda *_a, **_k: None)
    monkeypatch.setattr(animation, "bundled_ffmpeg", lambda **_k: "/site/imageio_ffmpeg/binaries/ffmpeg")
    monkeypatch.setattr(animation, "_encode", encode)
    from PIL import Image

    targets = {"videos": {"mp4": tmp_path / "clip.mp4"}}
    videos, message = animation._write_videos(tmp_path, targets, [Image.new("RGB", (4, 4))], 4, ["mp4"])
    assert calls == ["/site/imageio_ffmpeg/binaries/ffmpeg"]
    assert videos == {} and "could not write the MP4" in message


@pytest.mark.parametrize(
    ("system", "command"),
    [
        ("win32", "winget install Gyan.FFmpeg"),
        ("darwin", "brew install ffmpeg"),
        ("linux", "sudo apt-get install -y ffmpeg"),
    ],
)
def test_the_missing_message_says_how_to_add_an_encoder(system, command):
    text = prereqs.ffmpeg_hint(system)
    assert text.startswith("ffmpeg was not found, so no MP4 or WebM was written. The GIF is the animation.")
    assert "python install.py --with-ffmpeg" in text
    assert 'python -m pip install "imageio-ffmpeg>=0.5"' in text
    assert command in text


def test_the_installer_adds_the_video_extra_only_when_asked(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "install.py"
    spec = importlib.util.spec_from_file_location("aicad_install_for_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    commands = []
    monkeypatch.setattr(module.subprocess, "run", lambda command, **_k: commands.append(command))
    module._pip_install(Path("python"))
    module._pip_install(Path("python"), video=True)
    installs = [command[-1] for command in commands if command[-1] != "pip"]
    assert installs == [".[dev,robot]", ".[dev,robot,video]"]
