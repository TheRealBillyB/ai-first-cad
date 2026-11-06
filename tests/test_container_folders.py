"""Private folders inside the Windows script container. See aicad.container_folders."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from aicad import container_folders
from aicad.container_folders import unexpected_grants
from aicad.sandbox import make_run_folder

CONTAINER = "S-1-15-2-1111-2222"
USER = "S-1-5-21-1-2-3-1001"
ALLOWED = frozenset({"S-1-5-18", "S-1-5-32-544", "S-1-3-4", "S-1-3-0", CONTAINER, USER})
ALLOW, DENY = 0, 1


def test_the_folder_a_container_makes_in_its_workspace_counts_as_private():
    # What a plain folder in the script workspace was seen to inherit on Windows 10.
    aces = [(ALLOW, CONTAINER), (ALLOW, "S-1-5-18"), (ALLOW, "S-1-5-32-544"), (ALLOW, "S-1-3-4")]
    assert unexpected_grants(aces, ALLOWED) == []


@pytest.mark.parametrize(
    "sid",
    [
        "S-1-1-0",  # Everyone
        "S-1-5-32-545",  # Users
        "S-1-5-11",  # Authenticated Users
        "S-1-15-2-1",  # every app container
        "S-1-5-21-1-2-3-1002",  # another account
        "S-1-15-2-9999",  # another container
    ],
)
def test_any_broader_account_is_named(sid):
    assert unexpected_grants([(ALLOW, CONTAINER), (ALLOW, sid)], ALLOWED) == [sid]


def test_a_deny_entry_does_not_make_a_folder_less_private():
    assert unexpected_grants([(DENY, "S-1-1-0"), (ALLOW, USER)], ALLOWED) == []


def test_an_allow_entry_of_another_form_counts_as_broader():
    assert unexpected_grants([(5, USER)], ALLOWED) == [USER]


def test_outside_a_container_mkdir_is_left_alone():
    """The test process is not in a container, so Python's own owner-only folder is used."""
    before = os.mkdir
    container_folders.make_private_folders_work_in_a_container()
    assert os.mkdir is before
    assert not getattr(os.mkdir, "_aicad_container", False)


def test_a_run_folder_inherits_the_project_on_windows_and_stays_private_elsewhere(monkeypatch, tmp_path):
    seen = []

    def mkdir(self, mode=0o777, parents=False, exist_ok=False):
        seen.append(mode)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    make_run_folder(tmp_path / "job")
    assert seen == [0o777 if sys.platform == "win32" else 0o700]
