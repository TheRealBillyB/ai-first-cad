"""The script container is granted only what it needs, never a whole broad folder.

Grants are permanent. A Python started from a script in Temp, or a sys.path
entry pointing at Temp, gave aicadscript read access to all of Temp, so a
design script could read every other file there.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from aicad import sandbox

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows container grants")


def _pretend_broad(monkeypatch, *folders: Path) -> None:
    """Treat these test folders as broad folders, the way Temp or the Desktop is."""
    from aicad import sandbox_setup_windows

    keys = {os.path.normcase(str(Path(folder).resolve())) for folder in folders}
    real = sandbox_setup_windows.broad_folders

    def broad():
        return real() + [("a test folder", Path(folder).resolve()) for folder in folders]

    monkeypatch.setattr(sandbox_setup_windows, "broad_folders", broad)
    assert keys


@windows_only
def test_a_helper_in_a_broad_folder_grants_only_the_program(tmp_path, monkeypatch):
    temp = tmp_path / "Temp"
    temp.mkdir()
    tool = temp / "tool.exe"
    tool.write_bytes(b"")
    _pretend_broad(monkeypatch, temp)
    roots = sandbox._helper_read_paths([str(tool)])
    assert tool.resolve() in roots
    assert temp.resolve() not in roots


@windows_only
def test_a_helper_in_its_own_install_folder_still_gets_that_folder(tmp_path):
    install = tmp_path / "solver" / "bin"
    install.mkdir(parents=True)
    tool = install / "ccx.exe"
    tool.write_bytes(b"")
    assert install.resolve() in sandbox._helper_read_paths([str(tool)])


def test_a_script_passed_to_python_grants_the_file_not_its_folder(tmp_path):
    script = tmp_path / "probe.py"
    script.write_text("print(1)\n", encoding="utf-8")
    roots = sandbox._helper_read_paths([sys.executable, "-I", str(script)])
    assert script.resolve() in roots
    assert tmp_path.resolve() not in roots


def test_a_sys_path_entry_outside_the_install_is_not_granted(tmp_path, monkeypatch):
    stray = tmp_path / "Temp"
    stray.mkdir()
    monkeypatch.setattr(sys, "path", [str(stray), *sys.path])
    roots = sandbox._read_roots()
    assert stray.resolve() not in roots
    assert Path(sys.prefix).resolve() in roots
    assert Path(sandbox.__file__).resolve().parent.parent in roots


@windows_only
@pytest.mark.parametrize("kind", ["read", "workspace"])
def test_a_broad_folder_is_refused_before_any_grant(tmp_path, monkeypatch, kind):
    from aicad import sandbox_windows as win

    called = []
    monkeypatch.setattr(win, "_icacls", lambda *args, **kwargs: called.append(args))
    monkeypatch.setattr(win, "_grant_traverse", lambda *args, **kwargs: called.append(args))
    from aicad import sandbox_setup_windows as setup

    named = dict(setup.broad_folders())
    for folder in (named["your user folder"], named["your Temp folder"]):
        with pytest.raises(win.BroadFolderRefused, match="aicad will not give design scripts"):
            if kind == "read":
                win.grant_read_paths([folder], "S-1-15-2-1-2-3-4-5-6-7")
            else:
                win.grant_workspace(folder, "S-1-15-2-1-2-3-4-5-6-7")
    assert called == []


@windows_only
def test_the_broad_folders_include_the_drive_profile_and_temp():
    from aicad import sandbox_setup_windows as setup

    named = {name for name, _folder in setup.broad_folders()}
    assert {"a drive root", "your user folder", "your Temp folder", "your local app data folder"} <= named
    found = {str(folder).lower() for _name, folder in setup.broad_folders()}
    for folder in (Path.home(), Path(os.environ["LOCALAPPDATA"]) / "Temp", Path(Path.home().anchor)):
        assert str(folder.resolve()).lower() in found


@windows_only
def test_a_temp_variable_pointed_elsewhere_does_not_make_that_folder_broad(tmp_path, monkeypatch):
    """A process may point TEMP at a project. The project is still a project."""
    from aicad import sandbox_setup_windows as setup

    for name in ("TEMP", "TMP"):
        monkeypatch.setenv(name, str(tmp_path))
    assert setup.broad_folder_name(tmp_path) is None


@windows_only
def test_setup_removes_a_container_grant_from_a_broad_folder(tmp_path, monkeypatch):
    from aicad import sandbox_setup_windows as setup

    temp = tmp_path / "Temp"
    temp.mkdir()
    sid = setup._container_sid(setup.PROFILE_NAME)
    subprocess.run(["icacls", str(temp), "/grant", f"*{sid}:(OI)(CI)RX", "/Q"], check=True, capture_output=True)
    assert sid in subprocess.run(["icacls", str(temp)], capture_output=True, text=True).stdout
    monkeypatch.setattr(setup, "broad_folders", lambda: [("your Temp folder", temp.resolve())])
    lines: list[str] = []
    setup.remove_broad_container_grants(lines.append)
    assert lines == [f"Removed {setup.PROFILE_NAME}'s access to your Temp folder. A script container must not share that whole folder."]
    assert sid not in subprocess.run(["icacls", str(temp)], capture_output=True, text=True).stdout


def test_the_network_container_name_matches():
    from aicad import sandbox_setup_windows, sandbox_windows

    assert sandbox_setup_windows.NETWORK_PROFILE_NAME == sandbox_windows._NETWORK_PROFILE_NAME
