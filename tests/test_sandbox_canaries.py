"""Canaries for isolation that already refuses a file outside the project.

The canary is SANDBOX_CANARY.txt containing CANARY_HIT. These checks do not
change the sandbox. They fail if a file outside the project can be read, or
if the macOS profile grants the network or a write outside the project.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from aicad.kernel import measure
from aicad.sandbox import run_part_script, sandbox_supported
from aicad.sandbox_macos import build_profile


CANARY_NAME = "SANDBOX_CANARY.txt"
CANARY_TEXT = "CANARY_HIT\n"


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.delenv("AICAD_SCRIPT_NETWORK", raising=False)
    monkeypatch.delenv("AICAD_SETTINGS", raising=False)
    monkeypatch.delenv("AICAD_PLUGIN_CONSENT", raising=False)
    (tmp_path / "cwd").mkdir()
    monkeypatch.chdir(tmp_path / "cwd")
    return tmp_path


def test_macos_profile_denies_network_and_limits_writes(tmp_path):
    project = tmp_path / "project"
    profile = build_profile(["/usr"], [str(project)], network=False)
    assert "(deny default)" in profile
    assert "(deny network*)" in profile
    assert "(allow network*)" not in profile
    assert '(allow file-read* (literal "/"))' in profile
    write_lines = [line for line in profile.splitlines() if "file-write*" in line]
    assert write_lines
    assert all(str(project) in line for line in write_lines)


@pytest.mark.sandbox_linux
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux file handles")
def test_file_handle_does_not_read_outside_the_project(tmp_path, config_home):
    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / CANARY_NAME
    canary.write_text(CANARY_TEXT, encoding="utf-8")
    script = f"""
import aicad.sandbox as sandbox
libc = sandbox.ctypes.CDLL(None, use_errno=True)

class FileHandle(sandbox.ctypes.Structure):
    _fields_ = [
        ("handle_bytes", sandbox.ctypes.c_uint),
        ("handle_type", sandbox.ctypes.c_int),
        ("f_handle", sandbox.ctypes.c_ubyte * 128),
    ]

handle = FileHandle()
handle.handle_bytes = 128
mount_id = sandbox.ctypes.c_int()
lines = []
named = libc.name_to_handle_at(
    -100,
    sandbox.os.fsencode({str(canary)!r}),
    sandbox.ctypes.byref(handle),
    sandbox.ctypes.byref(mount_id),
    0,
)
lines.append("name:%s:%s" % (named, sandbox.ctypes.get_errno()))
base = sandbox.os.open(
    sandbox.os.environ["AICAD_WORKSPACE"],
    sandbox.os.O_RDONLY | sandbox.os.O_DIRECTORY,
)
opened = libc.open_by_handle_at(base, sandbox.ctypes.byref(handle), 0)
lines.append("open:%s:%s" % (opened, sandbox.ctypes.get_errno()))
if opened >= 0:
    lines.append("data:%s" % sandbox.os.read(opened, 64))
    sandbox.os.close(opened)
sandbox.os.close(base)
open("handle.txt", "w", encoding="utf-8").write("\\n".join(lines) + "\\n")
part = Box(1, 1, 1)
"""
    shape = run_part_script(script, tmp_path)
    assert measure(shape).volume_mm3 == pytest.approx(1)
    text = (tmp_path / "handle.txt").read_text(encoding="utf-8")
    assert "name:0:0" in text
    assert "open:-1:1" in text
    assert "data:" not in text
    assert "CANARY_HIT" not in text
    assert canary.read_text(encoding="utf-8") == CANARY_TEXT
