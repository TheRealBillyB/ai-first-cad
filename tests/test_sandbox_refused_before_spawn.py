"""A run this computer cannot confine is refused before it starts anything.

The check used to sit inside _spawn, after whatever comes first there. In the
tests _spawn also waits for the one-sandbox-at-a-time lock, so a refused run
from the page waited behind other sandbox tests and its request timed out.
"""

import pytest

from aicad import sandbox
from aicad.errors import ScriptError

MESSAGE = "This computer cannot confine a script yet."


@pytest.fixture
def refused(monkeypatch):
    monkeypatch.setattr(sandbox, "sandbox_supported", lambda: (False, MESSAGE))

    def no_spawn(*args, **kwargs):
        raise AssertionError("a refused run reached _spawn")

    monkeypatch.setattr(sandbox, "_spawn", no_spawn)


def test_a_refused_design_script_never_reaches_spawn(tmp_path, monkeypatch, refused):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    project = tmp_path / "project"
    project.mkdir()
    sandbox.allow_design_scripts(project)
    with pytest.raises(ScriptError, match=MESSAGE):
        sandbox.run_design_script(project, "print('no')\n")


def test_a_refused_part_script_never_reaches_spawn(tmp_path, refused):
    with pytest.raises(ScriptError, match=MESSAGE):
        sandbox.run_part_script("part = None\n", tmp_path)
