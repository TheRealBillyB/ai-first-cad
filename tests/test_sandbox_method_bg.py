"""Sandbox control files stay outside the project.

The marker is CANARY_HIT. A check record is created outside the project even
when the process temporary directory is that project.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

from aicad.sandbox import _control_directory, allow_design_scripts, run_design_script, sandbox_supported

_MARKER = "CANARY_HIT"


def test_a_control_directory_stays_outside_the_project(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "session.json").write_text("{}\n", encoding="utf-8")
    (project / "marker.txt").write_text(_MARKER + "\n", encoding="utf-8")
    for name in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(name, str(project))
    directory = _control_directory()
    try:
        resolved = directory.resolve()
        assert resolved != project.resolve()
        assert project.resolve() not in resolved.parents
        assert (project / "marker.txt").read_text(encoding="utf-8") == _MARKER + "\n"
    finally:
        from aicad.paths import remove_tree

        remove_tree(directory, ignore_errors=True)


def test_a_design_check_file_is_created_outside_the_project(tmp_path, monkeypatch):
    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)
    project = tmp_path / "project"
    project.mkdir()
    (project / "session.json").write_text("{}\n", encoding="utf-8")
    (project / "marker.txt").write_text(_MARKER + "\n", encoding="utf-8")
    for name in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(name, str(project))
    seen: list[str | None] = []
    profiles: list[str] = []
    real = tempfile.mkstemp

    def spy(*args, **kwargs):
        prefix = kwargs.get("prefix")
        if prefix is None and len(args) > 1:
            prefix = args[1]
        if isinstance(prefix, str) and prefix.startswith(("aicad-design-checks-", "aicad-seatbelt-")):
            directory = kwargs.get("dir")
            if directory is None and len(args) > 2:
                directory = args[2]
            seen.append(None if directory is None else str(directory))
        return real(*args, **kwargs)

    monkeypatch.setattr(tempfile, "mkstemp", spy)
    if sys.platform == "darwin":
        from aicad.sandbox_macos import command_prefix

        def wrapped(path, workspace):
            profiles.append(path)
            return command_prefix(path, workspace)

        monkeypatch.setattr("aicad.sandbox_macos.command_prefix", wrapped)
    allow_design_scripts(project)
    payload = run_design_script(project, "print('hello')\n")
    assert payload["ok"] is True
    assert seen
    for directory in seen:
        assert directory is not None
        resolved = Path(directory).resolve()
        assert resolved != project.resolve()
        assert project.resolve() not in resolved.parents
    if sys.platform == "darwin":
        assert profiles
        resolved = Path(profiles[0]).resolve()
        assert project.resolve() not in resolved.parents
    assert (project / "marker.txt").read_text(encoding="utf-8") == _MARKER + "\n"
