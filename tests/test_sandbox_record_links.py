"""A script log with another name is not read.

The canary is SANDBOX_CANARY.txt containing CANARY_HIT, outside the project.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from aicad.errors import ScriptError
from aicad.sandbox import allow_design_scripts, run_design_script, sandbox_supported, script_log_path


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


def test_script_log_with_another_name_is_not_read(tmp_path, config_home):
    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (project / ".aicad").mkdir()
    canary = outside / CANARY_NAME
    canary.write_text(CANARY_TEXT, encoding="utf-8")
    log = script_log_path(project)
    try:
        os.link(canary, log)
    except OSError as exc:
        pytest.skip(f"hard links are not available: {exc}")
    allow_design_scripts(project)
    with pytest.raises(ScriptError, match="more than one name"):
        run_design_script(project, "print('hello')\n")
    assert canary.read_text(encoding="utf-8") == CANARY_TEXT
    assert log.read_text(encoding="utf-8") == CANARY_TEXT
    assert canary.stat().st_nlink > 1
    assert "print('hello')" not in log.read_text(encoding="utf-8")
