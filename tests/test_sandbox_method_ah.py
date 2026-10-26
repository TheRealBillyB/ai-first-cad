"""A helper does not inherit the parent environment, and an 8.3 name is refused.

The marker is CANARY_HIT. The drive prefix of an absolute path is not treated
as a file name.
"""

from __future__ import annotations

import sys

import pytest

from aicad.errors import GeometryError
from aicad.paths import refuse_short_name_components, require_export_path
from aicad.sandbox import program_environment

_MARKER = "CANARY_HIT"


def test_a_helper_does_not_inherit_the_parent_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("AICAD_CANARY_MARKER", _MARKER)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("FONTCONFIG_FILE", str(tmp_path / "fonts.conf"))
    monkeypatch.setenv("GMSH_HOME", str(tmp_path))
    env = program_environment()
    upper = {key.upper() for key in env}
    for name in ("AICAD_CANARY_MARKER", "HOME", "USERPROFILE", "APPDATA", "FONTCONFIG_FILE", "GMSH_HOME"):
        assert name not in upper
    assert _MARKER not in env.values()
    assert env.get("PATH")
    assert "PYTHONPATH" not in upper


def test_an_absolute_short_name_is_refused(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    refuse_short_name_components(r"C:\exports\part.stl", "Export path", GeometryError)
    with pytest.raises(GeometryError, match="not a file name"):
        refuse_short_name_components(r"C:\PROJE~1\part.stl", "Export path", GeometryError)
    with pytest.raises(GeometryError, match="not a file name"):
        require_export_path(project, "exports/PROJE~1/part.stl", "Export path", GeometryError)


@pytest.mark.skipif(sys.platform != "win32", reason="8.3 names are a Windows path")
def test_the_project_short_name_cannot_leave_the_folder(tmp_path):
    import ctypes
    import os
    from ctypes import wintypes

    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("CANARY_HIT\n", encoding="utf-8")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get = kernel32.GetShortPathNameW
    get.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    get.restype = wintypes.DWORD
    size = int(get(os.fspath(project), None, 0))
    if size <= 0:
        pytest.skip("this volume has no 8.3 name")
    buffer = ctypes.create_unicode_buffer(size)
    if int(get(os.fspath(project), buffer, size)) <= 0:
        pytest.skip("this volume has no 8.3 name")
    short = buffer.value
    climbed = os.path.join(short, "..", "outside.txt")
    with pytest.raises(GeometryError):
        require_export_path(project, climbed, "Export path", GeometryError)
    assert outside.read_text(encoding="utf-8") == "CANARY_HIT\n"
