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
from aicad.sandbox import allow_design_scripts, run_design_script

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


SEND = """
import socket

def send(connected):
    packet = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        if connected:
            packet.connect(("1.1.1.1", 53))
            packet.send(b"aicad-setup-check")
        else:
            packet.bind(("0.0.0.0", 0))
            packet.sendto(b"aicad-setup-check", ("1.1.1.1", 53))
        return "sent"
    except OSError as exc:
        return f"refused {exc.winerror}"
    finally:
        packet.close()

print("connected", send(True))
print("unconnected", send(False))
"""


def test_a_network_on_run_leaves_the_block_in_place(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    project = tmp_path / "project"
    project.mkdir()
    allow_design_scripts(project)
    assert setup.run_setup() == 0

    blocked = run_design_script(project, SEND)
    assert blocked["ok"] is True, blocked.get("error")
    assert "sent" not in blocked["stdout"]
    assert blocked["mechanism"] in ("appcontainer+job", "lpac+job")
    print("network-off run used", blocked["mechanism"])

    opened = run_design_script(project, SEND, network=True)
    assert opened["ok"] is True, opened.get("error")
    assert opened["stdout"].count("sent") == 2

    # The bug this fixes: a network-on run used to take the rules down.
    assert setup.is_installed() is True
    again = run_design_script(project, SEND)
    assert again["ok"] is True, again.get("error")
    assert "sent" not in again["stdout"]


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
