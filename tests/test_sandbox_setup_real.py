"""The real install and removal. This changes the machine's firewall.

It only runs when AICAD_SANDBOX_SETUP_REAL is set, on a disposable Windows
runner with administrator rights, in a CI step of its own. It never runs on a
person's computer, and a sandbox test running beside it would see the rules move.
"""

from __future__ import annotations

import os
import sys

import pytest

from aicad import sandbox_setup_windows as setup
from aicad.errors import ScriptError
from aicad.sandbox import _drop_job, _spawn, allow_design_scripts, run_design_script, script_log_path

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or not os.environ.get("AICAD_SANDBOX_SETUP_REAL"),
    reason="changes the firewall, so it only runs on a disposable runner",
)


@pytest.fixture(autouse=True)
def _nothing_left_behind():
    yield
    setup.run_setup(remove=True)


def test_setup_is_complete_can_run_twice_and_removal_clears_it(capsys):
    assert setup.is_admin(), "this step has to run with administrator rights"
    assert setup.run_setup(remove=True) == 0
    assert setup.filters_present() == 0
    assert setup.rules_present() is False

    assert setup.run_setup() == 0
    assert setup.filters_present() == len(setup.BLOCK_LAYERS)
    assert setup.rules_present() is True
    assert "in place" in capsys.readouterr().out

    assert setup.run_setup() == 0
    assert setup.is_installed() is True

    assert setup.run_setup(remove=True) == 0
    assert setup.filters_present() == 0
    assert setup.rules_present() is False
    assert setup.run_setup(remove=True) == 0


def _sends(project, network: bool) -> dict:
    """A real confined process tries the network. A design script may not import socket."""
    payload = _spawn(project, "probe", None, network=network)
    _drop_job(payload)
    assert isinstance(payload.get("probe"), dict), payload.get("error")
    return payload["probe"]


def _assert_refused(probe: dict) -> None:
    for name in ("tcp", "udp", "udp_connected"):
        assert probe[name].startswith("denied:"), probe


def test_a_network_on_run_leaves_the_block_in_place(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    project = tmp_path / "project"
    project.mkdir()
    assert setup.run_setup() == 0

    blocked = _sends(project, False)
    _assert_refused(blocked)
    assert blocked["mechanism"] in ("appcontainer+job", "lpac+job")

    opened = _sends(project, True)
    assert opened["udp"] == "sent", opened
    assert opened["udp_connected"] == "sent", opened

    # The bug this fixes: a network-on run used to take the rules down.
    assert setup.is_installed() is True
    _assert_refused(_sends(project, False))


def test_a_design_script_records_which_container_ran(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    project = tmp_path / "project"
    project.mkdir()
    allow_design_scripts(project)
    assert setup.run_setup() == 0
    payload = run_design_script(project, "print('hello')\n")
    assert payload["ok"] is True, payload.get("error")
    assert payload["mechanism"] in ("appcontainer+job", "lpac+job")
    assert payload["mechanism"] in script_log_path(project).read_text(encoding="utf-8")


def test_a_run_without_setup_is_refused_and_names_the_command(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    project = tmp_path / "project"
    project.mkdir()
    allow_design_scripts(project)
    assert setup.run_setup(remove=True) == 0
    with pytest.raises(ScriptError, match="aicad sandbox-setup"):
        run_design_script(project, "print('no')\n")
    # Refusing a run changes nothing on the machine.
    assert setup.filters_present() == 0
    assert setup.rules_present() is False
