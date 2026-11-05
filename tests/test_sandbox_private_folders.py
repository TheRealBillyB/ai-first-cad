"""A design script runs on the first click and the next.

aicad makes a private temporary folder while it loads inside the container.
On Windows with Python 3.11.10 or later, the container could not make one,
and tempfile spun until the CPU limit, so no script ran. The second run also
needs its new job folder to inherit the container's grant. CI's Windows Python
is 3.11.9, so the account check in CI runs this with a current Python too.
"""

from __future__ import annotations

import time

from aicad.sandbox import allow_design_scripts
from aicad.workspace import Workspace

_SCRIPT = """print("private folders work")
"""


def test_a_script_runs_on_the_first_click_and_the_next(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    project = tmp_path / "project"
    workspace = Workspace(project)
    allow_design_scripts(project)
    for _attempt in (1, 2):
        began = time.perf_counter()
        result = workspace.execute("run_design_script", {"script": _SCRIPT}, "ui")
        assert "private folders work" in result["stdout"]
        # A spin would run until the CPU limit before failing.
        assert time.perf_counter() - began < 120
