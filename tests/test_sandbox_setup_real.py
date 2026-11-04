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
from aicad import sandbox_windows

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


def test_todays_read_back_accepts_the_rules_setup_creates():
    assert setup.run_setup() == 0
    sandbox_windows._bind()
    sid = setup.package_sid()
    assert sandbox_windows._firewall_block_is_present(sid) is True
