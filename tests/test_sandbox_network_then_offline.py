"""An offline run works in a project that a network-on run used, and stays closed to the network container.

A network-on run granted aicadscriptnet the project, and the grant stayed.
The next offline run's private folder inherited it, and the offline
container refused a folder the network container could open, so the run
failed. aicad now takes its own network container off the project after a
network run, and before an offline one for a project that already has it.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows app containers")


def _network_sid() -> str:
    from aicad import container_folders

    return container_folders.container_sid(container_folders._NETWORK_CONTAINER_NAME)


def _tree_names(folder, sid) -> list[str]:
    listed = subprocess.run(["icacls", str(folder), "/T", "/C"], capture_output=True, text=True)
    return [line for line in listed.stdout.splitlines() if sid in line]


def _project(tmp_path, monkeypatch):
    from aicad.sandbox import allow_design_scripts

    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    project = tmp_path / "project"
    project.mkdir()
    allow_design_scripts(project)
    return project


def test_an_offline_run_after_a_network_run_succeeds_and_the_network_container_is_off(tmp_path, monkeypatch):
    from aicad.sandbox import _drop_job, _spawn, run_design_script

    project = _project(tmp_path, monkeypatch)
    opened = _spawn(project, "probe", None, network=True)
    _drop_job(opened)
    assert isinstance(opened.get("probe"), dict), opened.get("error")
    sid = _network_sid()
    assert _tree_names(project, sid) == [], "the network run left its container on the project"

    ran = run_design_script(project, "print('offline after network')\n")
    assert ran["ok"] is True, ran.get("error")
    assert "offline after network" in ran["stdout"]
    assert _tree_names(project, sid) == []


def test_a_project_that_already_has_the_network_grant_is_cleared_before_an_offline_run(tmp_path, monkeypatch):
    """A project a network run used before this fix keeps that grant until the next offline run."""
    from aicad.sandbox import run_design_script

    project = _project(tmp_path, monkeypatch)
    sid = _network_sid()
    subprocess.run(["icacls", str(project), "/grant", f"*{sid}:(OI)(CI)M", "/T", "/Q"], check=True, capture_output=True)
    assert _tree_names(project, sid)

    ran = run_design_script(project, "print('cleared')\n")
    assert ran["ok"] is True, ran.get("error")
    assert _tree_names(project, sid) == []
