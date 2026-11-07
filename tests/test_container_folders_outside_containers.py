"""A script's private folder may inherit another app container, such as Claude Desktop's.

Claude Desktop gives its own app container full control of the whole user
profile, so every project in the profile inherits that entry. Python's
owner-only folder then either failed or locked the script container out of
its own folder, and aicad could not start in the container: every design
script failed with "The script job could not be read". A private folder now
accepts an app container it only inherits, names it in the run's notes, and
still refuses users and groups, and aicad's network container.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from aicad import container_folders as folders

ALLOWED = frozenset({"S-1-5-18", "S-1-5-32-544", "S-1-3-4", "S-1-5-21-1-2-3-1001", "S-1-15-2-1-2-3-4-5-6-7"})
OUTSIDE = "S-1-15-2-11-22-33-44-55-66-77"
NETWORK = "S-1-15-2-99-98-97-96-95-94-93"
ALLOW = folders._ACCESS_ALLOWED_ACE_TYPE
DENY = folders._ACCESS_DENIED_ACE_TYPE


def test_an_inherited_app_container_is_accepted_and_reported():
    entries = [(ALLOW, "S-1-5-18", True), (ALLOW, OUTSIDE, True), (ALLOW, OUTSIDE, True)]
    assert folders.private_folder_check(entries, ALLOWED, frozenset({NETWORK})) == ([], [], [OUTSIDE])


def test_an_app_container_set_on_the_folder_itself_is_broader():
    assert folders.private_folder_check([(ALLOW, OUTSIDE, False)], ALLOWED) == ([OUTSIDE], [], [])


@pytest.mark.parametrize("sid", ["S-1-5-32-545", "S-1-1-0", "S-1-5-11", "S-1-5-21-9-9-9-1002"])
@pytest.mark.parametrize("inherited", [True, False])
def test_users_groups_and_other_accounts_are_still_refused(sid, inherited):
    """Users, Everyone, Authenticated Users, and another account, inherited or not."""
    assert folders.private_folder_check([(ALLOW, sid, inherited)], ALLOWED) == ([sid], [], [])


def test_the_network_container_is_refused_even_when_inherited():
    assert folders.private_folder_check([(ALLOW, NETWORK, True)], ALLOWED, frozenset({NETWORK})) == ([], [NETWORK], [])


def test_a_deny_entry_is_ignored():
    assert folders.private_folder_check([(DENY, "S-1-1-0", True)], ALLOWED) == ([], [], [])


def test_the_container_names_match_the_sandbox():
    from aicad import sandbox_setup_windows, sandbox_windows

    assert folders._OFFLINE_CONTAINER_NAME == sandbox_setup_windows.PROFILE_NAME
    assert folders._NETWORK_CONTAINER_NAME == sandbox_windows._NETWORK_PROFILE_NAME


windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows app containers")


def _inherit_full_control(folder, sid):
    """The entry Claude Desktop sets on the profile: full control, inherited by every folder below."""
    folder.mkdir(parents=True)
    subprocess.run(["icacls", str(folder), "/grant", f"*{sid}:(OI)(CI)F", "/Q"], check=True, capture_output=True)


def _project(tmp_path, monkeypatch, sid):
    from aicad.sandbox import allow_design_scripts
    from aicad.workspace import Workspace

    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    home = tmp_path / "profile"
    _inherit_full_control(home, sid)
    project = home / "project"
    workspace = Workspace(project)
    allow_design_scripts(project)
    return workspace


@windows_only
def test_a_script_runs_in_a_project_that_inherits_another_app_container(tmp_path, monkeypatch):
    outside = folders.container_sid("aicadtestoutsidecontainer")
    workspace = _project(tmp_path, monkeypatch, outside)
    built = workspace.execute(
        "create_part_from_script", {"name": "box", "script": "from build123d import Box\npart = Box(12, 8, 5)\n"}, "ui"
    )
    assert built["part"]
    measured = workspace.execute("measure_part", {"name": "box"}, "ui")
    measurement = measured.get("measurement", measured)
    assert measurement["volume_mm3"] == pytest.approx(480)
    ran = workspace.execute("run_design_script", {"script": "print('ran')"}, "ui")
    assert "ran" in ran["stdout"]
    assert any(outside in note for note in ran.get("notes", [])), ran.get("notes")


@windows_only
def test_a_project_that_inherits_the_network_container_is_refused_clearly(tmp_path, monkeypatch):
    from aicad.errors import ScriptError

    network = folders.container_sid(folders._NETWORK_CONTAINER_NAME)
    workspace = _project(tmp_path, monkeypatch, network)
    with pytest.raises(ScriptError, match="network script container"):
        workspace.execute("run_design_script", {"script": "print('ran')"}, "ui")
